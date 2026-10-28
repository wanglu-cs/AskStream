from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .audit import write_json_atomic
from .protocol import EPSILON


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected a JSON object")
        records.append(value)
    return records


def _ratio(numerator: int | float, denominator: int | float) -> float | None:
    return numerator / denominator if denominator else None


def _timing_score(timestamp_s: Any, window: Any) -> float:
    """Return T(t; [s, e)): linear decay inside the half-open window."""

    if timestamp_s is None or not isinstance(window, dict):
        return 0.0
    try:
        timestamp = float(timestamp_s)
        start = float(window["start_s"])
        end = float(window["end_s"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if (
        not all(math.isfinite(value) for value in (timestamp, start, end))
        or end <= start
        or timestamp + EPSILON < start
        or timestamp + EPSILON >= end
    ):
        return 0.0
    return 1.0 - (max(timestamp, start) - start) / (end - start)


def _harmonic_mean(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    denominator = left + right
    return 2.0 * left * right / denominator if denominator else 0.0


def _is_waiting_tick(tick: dict[str, Any]) -> bool:
    return (
        tick.get("decision_required") is not False
        and (tick.get("transition") or {}).get("state_before") == "WAITING"
    )


def _task_name(record: dict[str, Any]) -> str:
    return record["source_event"]["task"]


def _event_metrics(events: list[dict[str, Any]]) -> dict[str, Any]:
    terminal_counts = Counter(str(event.get("terminal_reason") or "unknown") for event in events)
    correct_asks = 0
    correct_answers = 0
    answered_after_correct_ask = 0
    ask_timing_sum = 0.0
    answer_timing_sum = 0.0

    for event in events:
        source = event.get("source_event") or {}
        gold = source.get("gold") or {}
        asking = event["scoring_windows"]["asking"]
        answering = event["scoring_windows"]["answering"]
        response_modes = source["response_modes"]
        valid_action_ids = set(gold["action_option_ids"])
        if response_modes.get("ask") == "open":
            ask_content_correct = event.get("ask_judged_correct") is True
        else:
            ask_content_correct = event.get("ask_option_id") in valid_action_ids
        ask_correct = (
            event.get("ask_time_s") is not None and ask_content_correct
        )
        if response_modes["answer"] == "open":
            answer_correct = (
                event.get("answer_time_s") is not None
                and event.get("answer_match_normalized") is True
            )
        else:
            answer_option_id = gold.get("answer_option_id")
            answer_correct = (
                event.get("answer_time_s") is not None
                and answer_option_id is not None
                and event.get("answer_option_id") == answer_option_id
            )
        if ask_correct:
            correct_asks += 1
            if event.get("answer_time_s") is not None:
                answered_after_correct_ask += 1
            ask_timing_sum += _timing_score(event.get("ask_time_s"), asking)
        if ask_correct and answer_correct:
            correct_answers += 1
            answer_timing_sum += _timing_score(
                event.get("answer_time_s"), answering
            )

    total = len(events)
    s_ask = _ratio(ask_timing_sum, total)
    # S_ans measures answer quality after the model has successfully selected
    # the information it needs. Dividing by every event would penalize a
    # wrong/missed ASK once in S_ask and then a second time in S_ans. Keep the
    # answer-stage denominator conditional on correct ASK content instead.
    # When no ASK is correct, use zero so the composite Ask-F1 remains defined.
    s_ans = answer_timing_sum / correct_asks if correct_asks else 0.0
    return {
        "event_count": total,
        "correct_ask_count": correct_asks,
        "correct_answer_count": correct_answers,
        "answered_after_correct_ask_count": answered_after_correct_ask,
        "s_ask": s_ask,
        "s_ans": s_ans,
        "ask_f1": _harmonic_mean(s_ask, s_ans),
        "terminal_reason_counts": dict(sorted(terminal_counts.items())),
        "error_rates": {
            reason: _ratio(count, total)
            for reason, count in sorted(terminal_counts.items())
            if reason != "correct_answer"
        },
    }


def compute_metrics(
    event_records: Iterable[dict[str, Any]], tick_records: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    events = [record for record in event_records if record.get("record_type") == "event_terminal"]
    ticks = [record for record in tick_records if record.get("record_type") == "tick"]
    waiting_ticks = [
        tick
        for tick in ticks
        if _is_waiting_tick(tick)
    ]
    waiting_waits = sum(
        (tick.get("parsed_action") or {}).get("type") == "WAIT"
        for tick in waiting_ticks
    )
    idle_ticks = [tick for tick in ticks if tick.get("active_event_id") is None]
    type_i_events = [
        event
        for event in events
        if (event.get("source_event") or {}).get("waiting") is not None
        and (event.get("source_event") or {}).get("answering") is None
    ]
    early_asks = sum(
        event.get("terminal_reason") == "early_ask" for event in type_i_events
    )

    by_video_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_video_ticks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_task_ticks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_video_events[str(event.get("video_id") or "")].append(event)
        by_task_events[_task_name(event)].append(event)
    for tick in ticks:
        by_video_ticks[str(tick.get("video_id") or "")].append(tick)
        if tick.get("active_event_id") is not None:
            by_task_ticks[_task_name(tick)].append(tick)

    per_video: dict[str, Any] = {}
    for video_id in sorted(by_video_events):
        video_metrics = _event_metrics(by_video_events[video_id])
        video_waiting = [
            tick
            for tick in by_video_ticks[video_id]
            if _is_waiting_tick(tick)
        ]
        video_waits = sum(
            (tick.get("parsed_action") or {}).get("type") == "WAIT"
            for tick in video_waiting
        )
        video_metrics["waiting_compliance"] = _ratio(video_waits, len(video_waiting))
        per_video[video_id] = video_metrics

    per_task: dict[str, Any] = {}
    for task in sorted(by_task_events):
        task_metrics = _event_metrics(by_task_events[task])
        task_waiting = [
            tick
            for tick in by_task_ticks[task]
            if _is_waiting_tick(tick)
        ]
        task_waits = sum(
            (tick.get("parsed_action") or {}).get("type") == "WAIT"
            for tick in task_waiting
        )
        task_metrics["waiting_compliance"] = _ratio(task_waits, len(task_waiting))
        per_task[task] = task_metrics

    overall = _event_metrics(events)
    overall.update(
        {
            "video_count": len(by_video_events),
            "tick_count": len(ticks),
            "waiting_tick_count": len(waiting_ticks),
            "waiting_compliance": _ratio(waiting_waits, len(waiting_ticks)),
            "idle_tick_count": len(idle_ticks),
            "observation_only_tick_count": sum(
                tick.get("decision_required") is False for tick in ticks
            ),
            "post_ask_observation_only_tick_count": sum(
                tick.get("idle_reason") == "protocol_no_choice_waiting_after_ask"
                for tick in ticks
            ),
            "type_i_event_count": len(type_i_events),
            "early_ask_count": early_asks,
            "early_ask_rate": _ratio(early_asks, len(type_i_events)),
        }
    )
    return {
        "schema_version": "pvb.streaming_metrics.v6",
        "overall": overall,
        "per_task": per_task,
        "per_video": per_video,
        "definitions": {
            "s_ask": (
                "Correct ASK content weighted by T(ask_time; asking_window), "
                "averaged over all turns. Uses the runtime's effective "
                "window including configured start/end tolerances and hard limits."
            ),
            "s_ans": (
                "Correct ANSWER content after a correct ASK, weighted by "
                "T(answer_time; answering_window), averaged over turns with "
                "correct ASK content. It is zero when there are no correct "
                "ASK turns. "
                "When no separate answering window exists, the asking window "
                "is used. Uses the runtime's effective answer window, "
                "including configured start/end tolerances, evidence delivery, "
                "and hard limits."
            ),
            "ask_f1": (
                "Harmonic mean of s_ask and s_ans; zero when both component "
                "scores are zero."
            ),
            "waiting_compliance": (
                "Active pre-ASK WAITING decision ticks that emitted WAIT divided "
                "by those decision ticks. Post-ASK WAIT/ANSWER decisions are "
                "excluded from this pre-ASK compliance diagnostic."
            ),
            "early_ask_rate": (
                "Type-I events terminated by an ASK before the valid ASK window "
                "divided by Type-I events. Idle ticks are observation-only and "
                "do not call the action model."
            ),
        },
    }


def _read_run_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: run manifest must be an object")
    schema_version = value.get("schema_version")
    if schema_version not in {
        "pvb.streaming_run.v4",
        "pvb.streaming_parallel_aggregate.v3",
    }:
        raise ValueError(
            f"{path}: unsupported run schema_version {schema_version!r}"
        )
    return value


def _validate_run_integrity(
    *,
    run_dir: Path,
    event_records: list[dict[str, Any]],
    tick_records: list[dict[str, Any]],
    model_call_records: list[dict[str, Any]],
    require_complete: bool,
    expected_video_ids: set[str] | None,
    expected_event_counts: dict[str, int] | None,
) -> dict[str, Any]:
    manifest_path = run_dir / "run.json"
    manifest = _read_run_manifest(manifest_path) if manifest_path.is_file() else {}
    if require_complete:
        if not manifest:
            raise RuntimeError(f"{run_dir}: run.json is required for official scoring")
        if manifest.get("status") != "complete":
            raise RuntimeError(
                f"{run_dir}: refusing to score run with status "
                f"{manifest.get('status')!r}; expected 'complete'"
            )
        if manifest.get("metrics_computed") is not True:
            raise RuntimeError(
                f"{run_dir}: complete run must declare metrics_computed=true"
            )
        if manifest.get("schema_version") == "pvb.streaming_run.v4":
            code = manifest.get("code") or {}
            if not code.get("evaluator_source_sha256") or not code.get("source_files"):
                raise RuntimeError(
                    f"{run_dir}: evaluator source provenance is missing"
                )
            active_ticks = [
                record
                for record in tick_records
                if record.get("record_type") == "tick"
                and record.get("active_event_id")
            ]
            if any(not record.get("model_input") for record in active_ticks):
                raise RuntimeError(
                    f"{run_dir}: one or more active ticks lack model_input audit"
                )
            if any(
                not ((record.get("frame") or {}).get("sha256"))
                for record in active_ticks
            ):
                raise RuntimeError(
                    f"{run_dir}: one or more active ticks lack frame SHA-256"
                )
            decision_ticks = [
                record
                for record in active_ticks
                if record.get("decision_required") is not False
            ]
            successful_actions = [
                record
                for record in model_call_records
                if record.get("record_type") == "action_result"
                and record.get("status") == "success"
            ]
            if len(successful_actions) != len(decision_ticks):
                raise RuntimeError(
                    f"{run_dir}: successful action-call count "
                    f"{len(successful_actions)} does not match decision tick count "
                    f"{len(decision_ticks)}"
                )

    manifest_video_ids = manifest.get("selected_video_ids")
    if isinstance(manifest_video_ids, list):
        normalized_manifest_ids = [str(item).strip() for item in manifest_video_ids]
        if any(not item for item in normalized_manifest_ids) or len(
            normalized_manifest_ids
        ) != len(set(normalized_manifest_ids)):
            raise RuntimeError(
                f"{run_dir}: run.json selected_video_ids contains empty or duplicate IDs"
            )
    else:
        normalized_manifest_ids = None
    if expected_video_ids is None and isinstance(manifest_video_ids, list):
        expected_video_ids = set(normalized_manifest_ids or ())
    manifest_counts = manifest.get("expected_event_counts")
    if expected_event_counts is None and isinstance(manifest_counts, dict):
        expected_event_counts = {}
        for key, value in manifest_counts.items():
            video_id = str(key).strip()
            if (
                not video_id
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 1
            ):
                raise RuntimeError(
                    f"{run_dir}: invalid expected event count for {key!r}: {value!r}"
                )
            expected_event_counts[video_id] = value
    if expected_event_counts is not None and expected_video_ids is None:
        expected_video_ids = set(expected_event_counts)
    if require_complete and expected_video_ids is None:
        raise RuntimeError(
            f"{run_dir}: run.json lacks selected_video_ids; expected scope is unknown"
        )
    if (
        require_complete
        and expected_event_counts is None
        and not isinstance(manifest.get("expected_event_count"), int)
    ):
        raise RuntimeError(
            f"{run_dir}: run.json lacks expected event counts"
        )
    if (
        expected_video_ids is not None
        and expected_event_counts is not None
        and set(expected_event_counts) != expected_video_ids
    ):
        raise RuntimeError(
            f"{run_dir}: expected_event_counts keys do not match selected videos"
        )

    terminals = [
        record
        for record in event_records
        if record.get("record_type") == "event_terminal"
    ]
    completions = [
        record
        for record in event_records
        if record.get("record_type") == "video_complete"
    ]
    if not terminals:
        raise RuntimeError(f"{run_dir}: no terminal events; refusing to score an empty run")

    terminal_keys: list[tuple[str, str]] = []
    for record in terminals:
        video_id = str(record.get("video_id") or "").strip()
        event_id = str(record.get("event_id") or "").strip()
        if not video_id or not event_id:
            raise RuntimeError(f"{run_dir}: terminal event is missing video_id/event_id")
        terminal_keys.append((video_id, event_id))
    duplicate_events = sorted(
        key for key, count in Counter(terminal_keys).items() if count > 1
    )
    if duplicate_events:
        raise RuntimeError(
            f"{run_dir}: duplicate terminal events: {duplicate_events[:10]}"
        )

    completion_ids = [str(record.get("video_id") or "").strip() for record in completions]
    if any(not video_id for video_id in completion_ids):
        raise RuntimeError(f"{run_dir}: video_complete marker is missing video_id")
    duplicate_completions = sorted(
        video_id
        for video_id, count in Counter(completion_ids).items()
        if video_id and count > 1
    )
    if duplicate_completions:
        raise RuntimeError(
            f"{run_dir}: duplicate video_complete markers: {duplicate_completions}"
        )
    actual_video_ids = {video_id for video_id, _ in terminal_keys}
    completion_set = {video_id for video_id in completion_ids if video_id}
    if completion_set != actual_video_ids:
        raise RuntimeError(
            f"{run_dir}: video_complete markers do not match terminal events; "
            f"terminals={sorted(actual_video_ids)}, complete={sorted(completion_set)}"
        )
    if expected_video_ids is not None and actual_video_ids != expected_video_ids:
        raise RuntimeError(
            f"{run_dir}: selected videos mismatch; expected={sorted(expected_video_ids)}, "
            f"actual={sorted(actual_video_ids)}"
        )

    actual_counts = Counter(video_id for video_id, _ in terminal_keys)
    if expected_event_counts is not None:
        mismatches = {
            video_id: {
                "expected": expected_count,
                "actual": actual_counts.get(video_id, 0),
            }
            for video_id, expected_count in expected_event_counts.items()
            if actual_counts.get(video_id, 0) != expected_count
        }
        if mismatches:
            raise RuntimeError(
                f"{run_dir}: terminal event counts mismatch: {mismatches}"
            )
    manifest_expected_total = manifest.get("expected_event_count")
    if (
        isinstance(manifest_expected_total, int)
        and len(terminals) != manifest_expected_total
    ):
        raise RuntimeError(
            f"{run_dir}: expected {manifest_expected_total} terminal events, "
            f"found {len(terminals)}"
        )
    tick_video_ids = {
        str(record.get("video_id") or "").strip()
        for record in tick_records
        if record.get("record_type") == "tick"
    }
    # A native CUDA OOM may happen during session_open/reset before the first
    # decodable tick.  The runner still emits every event_terminal (with
    # terminal_reason=oom_aborted) and a video_complete marker so all turns stay
    # in the denominator.  Such a video legitimately has no model tick.
    aborted_video_ids = {
        str(record.get("video_id") or "").strip()
        for record in completions
        if record.get("status") == "oom_aborted"
    }
    missing_tick_videos = actual_video_ids - tick_video_ids - aborted_video_ids
    if missing_tick_videos:
        raise RuntimeError(
            f"{run_dir}: one or more completed videos have no tick records: "
            f"{sorted(missing_tick_videos)}"
        )
    return {
        "validated": True,
        "run_status": manifest.get("status"),
        "video_count": len(actual_video_ids),
        "event_count": len(terminals),
        "duplicate_event_count": 0,
        "video_complete_markers": len(completions),
        "oom_aborted_video_count": len(aborted_video_ids),
    }


def score_run(
    run_dir: Path,
    output: Path | None = None,
    *,
    require_complete: bool = True,
    expected_video_ids: set[str] | None = None,
    expected_event_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    event_records = _read_jsonl(run_dir / "events.jsonl")
    tick_records = _read_jsonl(run_dir / "ticks.jsonl")
    model_call_records = (
        _read_jsonl(run_dir / "model_calls.jsonl")
        if (run_dir / "model_calls.jsonl").is_file()
        else []
    )
    if require_complete:
        if not model_call_records:
            raise RuntimeError(
                f"{run_dir}: model_calls.jsonl is empty; refusing official scoring"
            )
    integrity = _validate_run_integrity(
        run_dir=run_dir,
        event_records=event_records,
        tick_records=tick_records,
        model_call_records=model_call_records,
        require_complete=require_complete,
        expected_video_ids=expected_video_ids,
        expected_event_counts=expected_event_counts,
    )
    metrics = compute_metrics(event_records, tick_records)
    metrics["integrity"] = integrity
    write_json_atomic((output or (run_dir / "metrics.json")).resolve(), metrics)
    return metrics
