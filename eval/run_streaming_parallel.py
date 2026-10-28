from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval.streaming_harness.audit import write_json_atomic
from eval.streaming_harness.assets import AssetManifest
from eval.streaming_harness.metrics import score_run
from eval.streaming_harness.dataset import compile_dataset
from eval.streaming_harness.prompts import PROMPT_VERSION
from eval.streaming_harness.cli_config import resolve_evaluation_config
from eval.streaming_harness.config import EvaluationConfig, load_api_credentials
from eval.streaming_harness.response_modes import EVALUATION_MODES, apply_evaluation_mode
from eval.tasks import TASK_DATASETS
from eval.streaming_harness.health import wait_for_streaming, wait_for_vllm
from eval.paths import RESULT_ROOT


JSONL_FILES = ("ticks.jsonl", "events.jsonl", "model_calls.jsonl")
RUN_ARTIFACTS = (*JSONL_FILES, "metrics.json", "run.json", "parallel_run.json")
SAFE_LABEL = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class PartTask:
    label: str
    video_id: str
    expected_event_count: int
    dataset_sha256: str
    dataset_path: Path
    command: list[str]
    stdout_path: Path
    stderr_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _part_label(video_id: str) -> str:
    if (
        not video_id
        or video_id in {".", ".."}
        or not SAFE_LABEL.fullmatch(video_id)
        or Path(video_id).name != video_id
    ):
        raise ValueError(f"video_id is unsafe for an output directory: {video_id!r}")
    return video_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one streaming evaluator process per video", allow_abbrev=False
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all", action="store_true", help="evaluate all eight tasks")
    selection.add_argument("--tasks", nargs="+", choices=tuple(TASK_DATASETS))
    parser.add_argument("--mode", choices=EVALUATION_MODES,
                        help="response format; defaults to multi-choice")
    parser.add_argument("--backend", choices=("api", "vllm", "streaming"))
    parser.add_argument("--model")
    parser.add_argument("--model-adapter", help="local model.<category>.<module>:<factory>")
    parser.add_argument("--base-url")
    parser.add_argument("--playback-speed", type=float)
    parser.add_argument("--rehydrate-seconds", type=float)
    parser.add_argument("--reasoning-effort", choices=("none", "low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--store-responses", action="store_true", default=None)
    parser.add_argument("--privacy-file", type=Path,
                        help="JSON {api_key, base_url?}; defaults to model/offline_api/.privacy.json when required")
    judge = parser.add_mutually_exclusive_group()
    judge.add_argument("--judge-model")
    judge.add_argument("--no-judge", action="store_true")
    parser.add_argument("--judge-base-url")
    parser.add_argument(
        "--judge-reasoning-effort",
        choices=("none", "low", "medium", "high", "xhigh", "max"),
    )
    parser.add_argument("--disable-thinking", action="store_true")
    parser.add_argument("--max-parallel", type=int, default=20)
    parser.add_argument("--ask-start-tolerance", type=float)
    parser.add_argument("--ask-end-tolerance", type=float)
    parser.add_argument("--answer-start-tolerance", type=float)
    parser.add_argument("--answer-end-tolerance", type=float)
    parser.add_argument("--request-timeout", type=float)
    parser.add_argument("--request-retries", type=int)
    parser.add_argument("--action-max-tokens", type=int)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--backend-health-timeout", type=float, default=120.0)
    parser.add_argument("--backend-health-interval", type=float, default=2.0)
    parser.add_argument(
        "--video-id",
        action="append",
        default=[],
        help="include only this video ID; repeat to select multiple videos",
    )
    parser.add_argument("--skip-video-id", action="append", default=[])
    args = parser.parse_args(argv)
    if args.max_parallel < 1:
        parser.error("--max-parallel must be positive")
    return args


def build_child_command(
    *,
    python_executable: str,
    dataset: Path,
    output: Path,
    backend: str,
    model: str,
    model_adapter: str | None = None,
    base_url: str,
    video_id: str,
    ask_start_tolerance: float | None,
    ask_end_tolerance: float | None,
    answer_start_tolerance: float | None = None,
    answer_end_tolerance: float | None = None,
    request_timeout: float,
    request_retries: int,
    privacy_file: Path | None = None,
    disable_thinking: bool = False,
    reasoning_effort: str | None = None,
    store_responses: bool = False,
    playback_speed: float | None = None,
    rehydrate_seconds: float | None = None,
    judge_model: str | None = None,
    judge_base_url: str | None = None,
    judge_reasoning_effort: str | None = None,
    action_max_tokens: int | None = None,
    temperature: float | None = None,
    evaluation_mode: str | None = None,
) -> list[str]:
    """Build one child command; the parent owns the shared backend service."""
    command = [
        python_executable,
        "-m",
        "eval.streaming_harness",
        "run",
        "--dataset",
        str(dataset),
        "--output",
        str(output),
        "--backend",
        backend,
        "--model",
        model,
        "--base-url",
        base_url,
        f"--video-id={video_id}",
        "--request-timeout",
        str(request_timeout),
        "--request-retries",
        str(request_retries),
        "--skip-backend-health-check",
    ]
    if evaluation_mode is not None:
        command.extend(["--mode", evaluation_mode])
    if model_adapter is not None:
        command.extend(["--model-adapter", model_adapter])
    if playback_speed is not None:
        command.extend(["--playback-speed", str(playback_speed)])
    if rehydrate_seconds is not None:
        command.extend(["--rehydrate-seconds", str(rehydrate_seconds)])
    if privacy_file is not None:
        command.extend(["--privacy-file", str(privacy_file.resolve())])
    if judge_model:
        command.extend(["--judge-model", judge_model])
    else:
        command.append("--no-judge")
    if judge_base_url:
        command.extend(["--judge-base-url", judge_base_url])
    if judge_reasoning_effort:
        command.extend(["--judge-reasoning-effort", judge_reasoning_effort])
    if action_max_tokens is not None:
        command.extend(["--action-max-tokens", str(action_max_tokens)])
    if temperature is not None:
        command.extend(["--temperature", str(temperature)])
    if ask_start_tolerance is not None:
        command.extend(["--ask-start-tolerance", str(ask_start_tolerance)])
    if ask_end_tolerance is not None:
        command.extend(["--ask-end-tolerance", str(ask_end_tolerance)])
    if answer_start_tolerance is not None:
        command.extend(["--answer-start-tolerance", str(answer_start_tolerance)])
    if answer_end_tolerance is not None:
        command.extend(["--answer-end-tolerance", str(answer_end_tolerance)])
    if disable_thinking:
        command.append("--disable-thinking")
    if reasoning_effort:
        command.extend(["--reasoning-effort", reasoning_effort])
    if store_responses:
        command.append("--store-responses")
    return command


def _default_output_dir(args: argparse.Namespace, config: EvaluationConfig) -> Path:
    model_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", config.model).strip("._-")[:80] or "model"
    timestamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S-%f")
    fingerprint = {
        "config": config.as_dict(),
        "selection": {
            name: getattr(args, name)
            for name in (
                "all", "tasks", "max_parallel", "video_id", "skip_video_id",
            )
        },
    }
    digest = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return RESULT_ROOT / "main" / f"{model_label}_{timestamp}_{config.evaluation_mode}_{digest}"


def main() -> int:
    args = parse_args()
    if args.mode is None:
        args.mode = "multi-choice"
    config, _key = load_api_credentials(resolve_evaluation_config(args), args.privacy_file)
    output = _default_output_dir(args, config)
    print(f"output={output}", flush=True)
    tasks = list(TASK_DATASETS) if args.all else list(dict.fromkeys(args.tasks))
    statuses = {}
    for task in tasks:
        print(f"task={task} mode={args.mode}", flush=True)
        statuses[task] = run_dataset(
            args, task=task, output=output / args.mode / task, config=config
        )
    write_json_atomic(output / args.mode / "suite.json", {
        "mode": args.mode,
        "status": "complete" if not any(statuses.values()) else "failed",
        "tasks": statuses,
    })
    return int(any(statuses.values()))


def run_dataset(
    args: argparse.Namespace, *, task: str, output: Path, config: EvaluationConfig
) -> int:
    dataset_path = TASK_DATASETS[task].resolve()
    dataset = apply_evaluation_mode(compile_dataset(dataset_path), config.evaluation_mode)
    if dataset.errors:
        details = "\n".join(issue.message for issue in dataset.errors)
        print(f"dataset validation failed:\n{details}", file=sys.stderr)
        return 1
    if config.backend == "streaming" and args.max_parallel != 1:
        raise ValueError(
            "native streaming workers hold one causal session at a time; "
            "use --max-parallel 1"
        )
    model = config.model
    base_url = config.base_url
    if config.model_adapter is None and config.backend == "vllm":
        wait_for_vllm(
            base_url,
            model,
            timeout_seconds=args.backend_health_timeout,
            interval_seconds=args.backend_health_interval,
        )
    elif config.model_adapter is None and config.backend == "streaming":
        wait_for_streaming(config, timeout_seconds=args.backend_health_timeout,
                           interval_seconds=args.backend_health_interval)

    effective_ask_start_tolerance = (
        args.ask_start_tolerance
        if args.ask_start_tolerance is not None
        else config.ask_start_tolerance_s
    )
    effective_ask_end_tolerance = (
        args.ask_end_tolerance
        if args.ask_end_tolerance is not None
        else config.ask_end_tolerance_s
    )
    video_ids = [video.video_id for video in dataset.videos]
    requested = set(args.video_id)
    skipped = set(args.skip_video_id)
    unknown_requests = requested - set(video_ids)
    unknown_skips = skipped - set(video_ids)
    if unknown_requests:
        raise ValueError(
            "unknown --video-id values: " + ", ".join(sorted(unknown_requests))
        )
    if unknown_skips:
        raise ValueError("unknown --skip-video-id values: " + ", ".join(sorted(unknown_skips)))
    selected_video_ids = (requested or set(video_ids)) - skipped
    if not selected_video_ids:
        raise RuntimeError("No videos selected; refusing to create an empty parallel run")
    assets = AssetManifest.from_dataset(dataset)
    missing_assets = selected_video_ids - set(assets.videos)
    if missing_assets:
        raise ValueError(
            "missing asset entries: " + ", ".join(sorted(missing_assets))
        )
    asset_errors = [
        error
        for video_id in sorted(selected_video_ids)
        for error in assets.validate_video(
            video_id,
            list(
                next(
                    video.events
                    for video in dataset.videos
                    if video.video_id == video_id
                )
            ),
        )
    ]
    if asset_errors:
        raise ValueError(
            "dataset asset validation failed before workers were started:\n- "
            + "\n- ".join(asset_errors)
        )

    output = output.resolve()
    existing_artifacts = [output / name for name in RUN_ARTIFACTS if (output / name).exists()]
    if existing_artifacts:
        raise FileExistsError(
            "parallel output already contains aggregate artifacts: "
            + ", ".join(path.name for path in existing_artifacts)
        )

    parts_root = output / "parts"
    logs_root = output / "parallel_logs"
    parts_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)
    assets_path = output / "assets.json"
    write_json_atomic(assets_path, assets.as_dict())
    tasks: list[PartTask] = []
    seen_labels: set[str] = set()
    for video_id in video_ids:
        if video_id not in selected_video_ids:
            continue
        label = _part_label(video_id)
        if label in seen_labels:
            raise ValueError(f"part label collision: {label!r}")
        seen_labels.add(label)
        part_output = parts_root / label
        if part_output.exists():
            raise FileExistsError(f"part output already exists: {part_output}")
        part_video = next(
            (video for video in dataset.videos if video.video_id == video_id),
            None,
        )
        if part_video is None:
            raise ValueError(f"dataset {dataset_path} does not contain {video_id!r}")
        if not part_video.events:
            raise ValueError(f"dataset {dataset_path} gives {video_id!r} no events")
        command = build_child_command(
            python_executable=sys.executable,
            dataset=dataset_path,
            output=part_output,
            backend=config.backend,
            model=model,
            model_adapter=config.model_adapter,
            base_url=base_url,
            video_id=video_id,
            ask_start_tolerance=effective_ask_start_tolerance,
            ask_end_tolerance=effective_ask_end_tolerance,
            answer_start_tolerance=(args.answer_start_tolerance if args.answer_start_tolerance is not None else config.answer_start_tolerance_s),
            answer_end_tolerance=(args.answer_end_tolerance if args.answer_end_tolerance is not None else config.answer_end_tolerance_s),
            request_timeout=config.request_timeout_seconds,
            request_retries=config.request_retries,
            privacy_file=args.privacy_file,
            disable_thinking=args.disable_thinking,
            reasoning_effort=config.reasoning_effort,
            store_responses=config.store_responses,
            playback_speed=config.playback_speed,
            rehydrate_seconds=config.rehydrate_seconds,
            judge_model=config.judge_model,
            judge_base_url=config.judge_base_url,
            judge_reasoning_effort=config.judge_reasoning_effort,
            action_max_tokens=config.action_max_tokens,
            temperature=config.temperature,
            evaluation_mode=config.evaluation_mode,
        )
        tasks.append(
            PartTask(
                label=label,
                video_id=video_id,
                expected_event_count=len(part_video.events),
                dataset_sha256=dataset.source_sha256,
                dataset_path=dataset_path,
                command=command,
                stdout_path=logs_root / f"{label}.stdout.log",
                stderr_path=logs_root / f"{label}.stderr.log",
            )
        )
    if not tasks:
        raise RuntimeError("No videos selected; refusing to create an empty parallel run")

    full_selection = {task.video_id for task in tasks} == set(video_ids)
    write_json_atomic(
        output / "parallel_run.json",
        {
            "schema_version": "pvb.streaming_parallel_run.v3",
            "status": "running",
            "evaluation_mode": config.evaluation_mode,
            "dataset_path": str(dataset_path),
            "dataset_sha256": dataset.source_sha256,
            "selected_video_ids": [task.video_id for task in tasks],
            "expected_event_counts": {
                task.video_id: task.expected_event_count for task in tasks
            },
            "parts": [task.label for task in tasks],
        },
    )

    running: list[tuple[PartTask, subprocess.Popen[bytes], object, object]] = []
    finished: list[tuple[PartTask, int]] = []
    next_task = 0
    print(
        f"scheduled={len(tasks)} max_parallel={args.max_parallel}",
        flush=True,
    )
    last_report = 0.0
    try:
        while next_task < len(tasks) or running:
            while next_task < len(tasks) and len(running) < args.max_parallel:
                task = tasks[next_task]
                stdout = task.stdout_path.open("wb")
                stderr = task.stderr_path.open("wb")
                try:
                    child_env = os.environ.copy()
                    child_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
                    process = subprocess.Popen(
                        task.command,
                        cwd=Path(__file__).resolve().parents[1],
                        env=child_env,
                        stdout=stdout,
                        stderr=stderr,
                    )
                except Exception:
                    stdout.close()
                    stderr.close()
                    raise
                running.append((task, process, stdout, stderr))
                next_task += 1

            time.sleep(1)
            for item in list(running):
                task, process, stdout, stderr = item
                return_code = process.poll()
                if return_code is None:
                    continue
                stdout.close()
                stderr.close()
                finished.append((task, return_code))
                running.remove(item)

            now = time.monotonic()
            if now - last_report >= 30:
                print(f"progress={len(finished)}/{len(tasks)}", flush=True)
                last_report = now
    except BaseException as exc:
        for _task, process, stdout, stderr in running:
            if process.poll() is None:
                process.terminate()
        for _task, process, stdout, stderr in running:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            stdout.close()
            stderr.close()
        write_json_atomic(
            output / "parallel_run.json",
            {
                "schema_version": "pvb.streaming_parallel_run.v3",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "finished_parts": [task.label for task, _ in finished],
                "expected_parts": [task.label for task in tasks],
            },
        )
        raise

    failures: list[str] = []
    failure_details: dict[str, dict[str, Any]] = {}
    for task, return_code in finished:
        print(f"{task.label} exit={return_code}", flush=True)
        if return_code:
            failures.append(task.label)
            detail: dict[str, Any] = {"return_code": return_code}
            child_manifest = output / "parts" / task.label / "run.json"
            if child_manifest.is_file():
                child_run = _read_json(child_manifest)
                if child_run.get("failure"):
                    detail["failure"] = child_run["failure"]
                if child_run.get("status"):
                    detail["child_status"] = child_run["status"]
            failure_details[task.label] = detail
    if failures:
        write_json_atomic(
            output / "parallel_run.json",
            {
                "schema_version": "pvb.streaming_parallel_run.v3",
                "status": "failed",
                "failed_parts": failures,
                "failure_details": failure_details,
                "finished_parts": [task.label for task, _ in finished],
                "expected_parts": [task.label for task in tasks],
            },
        )
        return 1
    try:
        _merge_outputs(
            output,
            tasks,
            dataset_path=dataset_path,
            dataset_sha256=dataset.source_sha256,
            full_selection=full_selection,
        )
    except Exception as exc:
        write_json_atomic(
            output / "parallel_run.json",
            {
                "schema_version": "pvb.streaming_parallel_run.v3",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "finished_parts": [task.label for task, _ in finished],
                "expected_parts": [task.label for task in tasks],
            },
        )
        raise
    return 0


def _validate_part(output: Path, task: PartTask) -> dict[str, Any]:
    part = output / "parts" / task.label
    required = [part / name for name in (*JSONL_FILES, "run.json", "metrics.json")]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise RuntimeError(f"{task.label}: missing part artifacts: {missing}")
    empty = [path.name for path in required if path.stat().st_size == 0]
    if empty:
        raise RuntimeError(f"{task.label}: empty part artifacts: {empty}")
    run = _read_json(part / "run.json")
    if run.get("status") != "complete" or run.get("metrics_computed") is not True:
        raise RuntimeError(
            f"{task.label}: part run is not a scored completion "
            f"(status={run.get('status')!r}, metrics_computed={run.get('metrics_computed')!r})"
        )
    if run.get("dataset_sha256") != task.dataset_sha256:
        raise RuntimeError(f"{task.label}: child used an unexpected dataset")
    if run.get("selected_video_ids") != [task.video_id]:
        raise RuntimeError(
            f"{task.label}: expected selected_video_ids={[task.video_id]!r}, "
            f"found {run.get('selected_video_ids')!r}"
        )
    if run.get("prompt_version") != PROMPT_VERSION:
        raise RuntimeError(f"{task.label}: child prompt version mismatch")
    if not run.get("asset_content_sha256") or not isinstance(run.get("asset_files"), list):
        raise RuntimeError(f"{task.label}: child is missing asset content provenance")
    code = run.get("code")
    if (
        not isinstance(code, dict)
        or not code.get("evaluator_source_sha256")
        or not isinstance(code.get("source_files"), dict)
    ):
        raise RuntimeError(f"{task.label}: child is missing evaluator source provenance")

    event_rows = _read_jsonl(part / "events.jsonl")
    tick_rows = _read_jsonl(part / "ticks.jsonl")
    call_rows = _read_jsonl(part / "model_calls.jsonl")
    terminals = [row for row in event_rows if row.get("record_type") == "event_terminal"]
    completions = [row for row in event_rows if row.get("record_type") == "video_complete"]
    terminal_ids = [str(row.get("event_id") or "") for row in terminals]
    if len(terminals) != task.expected_event_count:
        raise RuntimeError(
            f"{task.label}: expected {task.expected_event_count} terminal events, "
            f"found {len(terminals)}"
        )
    if len(set(terminal_ids)) != len(terminal_ids) or any(not item for item in terminal_ids):
        raise RuntimeError(f"{task.label}: duplicate or empty terminal event IDs")
    if {str(row.get("video_id") or "") for row in terminals} != {task.video_id}:
        raise RuntimeError(f"{task.label}: terminal records contain the wrong video")
    if len(completions) != 1 or completions[0].get("video_id") != task.video_id:
        raise RuntimeError(f"{task.label}: expected exactly one matching video_complete record")
    if not tick_rows or {
        str(row.get("video_id") or "")
        for row in tick_rows
        if row.get("record_type") == "tick"
    } != {task.video_id}:
        raise RuntimeError(f"{task.label}: tick records are empty or contain the wrong video")
    if not call_rows:
        raise RuntimeError(f"{task.label}: model_calls.jsonl is empty")
    metrics = _read_json(part / "metrics.json")
    if (metrics.get("overall") or {}).get("event_count") != task.expected_event_count:
        raise RuntimeError(f"{task.label}: child metrics event count mismatch")
    if (metrics.get("integrity") or {}).get("validated") is not True:
        raise RuntimeError(f"{task.label}: child metrics lack a passed integrity check")
    return run


def _append_jsonl(source: Path, writer: Any) -> None:
    for line in source.read_text(encoding="utf-8").splitlines():
        if line.strip():
            writer.write(line + "\n")


def _merge_outputs(
    output: Path,
    tasks: list[PartTask],
    *,
    dataset_path: Path,
    dataset_sha256: str,
    full_selection: bool,
) -> None:
    if not tasks:
        raise RuntimeError("refusing to merge zero parts")
    part_runs = {task.label: _validate_part(output, task) for task in tasks}
    configurations = {
        json.dumps(run.get("config"), sort_keys=True, separators=(",", ":"))
        for run in part_runs.values()
    }
    if len(configurations) != 1:
        raise RuntimeError("child runs used different harness configurations")
    code_payloads = {
        json.dumps(run.get("code"), sort_keys=True, separators=(",", ":"))
        for run in part_runs.values()
    }
    if len(code_payloads) != 1:
        raise RuntimeError("evaluator source changed between child runs")
    code = json.loads(next(iter(code_payloads)))
    expected_video_ids = {task.video_id for task in tasks}
    expected_event_counts = {
        task.video_id: task.expected_event_count for task in tasks
    }
    temporary = Path(tempfile.mkdtemp(prefix=".parallel-merge-", dir=output))
    try:
        for filename in JSONL_FILES:
            with (temporary / filename).open(
                "w", encoding="utf-8", newline="\n"
            ) as writer:
                for task in sorted(tasks, key=lambda item: item.label):
                    _append_jsonl(output / "parts" / task.label / filename, writer)

        asset_files_by_path: dict[str, dict[str, object]] = {}
        for run in part_runs.values():
            for item in run.get("asset_files", []):
                path = str(item.get("path") or "")
                if not path:
                    continue
                previous = asset_files_by_path.get(path)
                if previous is not None and previous.get("sha256") != item.get("sha256"):
                    raise RuntimeError(f"asset changed between child runs: {path}")
                asset_files_by_path[path] = item
        aggregate_status = "complete" if full_selection else "partial"
        asset_files = sorted(asset_files_by_path.values(), key=lambda item: str(item["path"]))
        asset_content_sha256 = hashlib.sha256(
            json.dumps(
                asset_files,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        run_payload = {
            "schema_version": "pvb.streaming_parallel_aggregate.v3",
            "status": "finalizing",
            "metrics_computed": False,
            "dataset_path": str(dataset_path),
            "dataset_sha256": dataset_sha256,
            "selected_video_ids": [task.video_id for task in tasks],
            "expected_event_counts": expected_event_counts,
            "expected_event_count": sum(expected_event_counts.values()),
            "prompt_version": PROMPT_VERSION,
            "asset_content_sha256": asset_content_sha256,
            "asset_files": asset_files,
            "config": next(iter(part_runs.values())).get("config"),
            "code": code,
            "parts": [
                {
                    "label": task.label,
                    "video_id": task.video_id,
                    "dataset_path": str(task.dataset_path),
                    "dataset_sha256": task.dataset_sha256,
                    "run_id": part_runs[task.label].get("run_id"),
                    "code": part_runs[task.label].get("code"),
                    "asset_manifest_sha256": part_runs[task.label].get(
                        "asset_manifest_sha256"
                    ),
                    "asset_content_sha256": part_runs[task.label].get(
                        "asset_content_sha256"
                    ),
                }
                for task in tasks
            ],
        }
        write_json_atomic(temporary / "run.json", run_payload)
        metrics = score_run(
            temporary,
            require_complete=False,
            expected_video_ids=expected_video_ids,
            expected_event_counts=expected_event_counts,
        )
        run_payload.update(status=aggregate_status, metrics_computed=True)
        write_json_atomic(temporary / "run.json", run_payload)
        metrics["integrity"]["run_status"] = aggregate_status
        write_json_atomic(temporary / "metrics.json", metrics)

        for filename in (*JSONL_FILES, "metrics.json", "run.json"):
            destination = output / filename
            if destination.exists():
                raise FileExistsError(destination)
            os.replace(temporary / filename, destination)
        write_json_atomic(
            output / "parallel_run.json",
            {
                "schema_version": "pvb.streaming_parallel_run.v3",
                "status": aggregate_status,
                "partial_reason": (
                    None if full_selection else "explicitly skipped dataset videos"
                ),
                "parts": [task.label for task in tasks],
                "selected_video_ids": [task.video_id for task in tasks],
                "event_count": metrics["overall"]["event_count"],
                "video_count": metrics["overall"]["video_count"],
                "integrity": metrics["integrity"],
                "evaluator_source_sha256": code["evaluator_source_sha256"],
            },
        )
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


if __name__ == "__main__":
    main()
