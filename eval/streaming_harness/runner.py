from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .assets import AssetManifest
from .audit import JsonlWriter, write_json_atomic, utc_now
from .records import RunRecorder
from .video import VideoRunner
from .interfaces import (
    GenerationBackend,
    StreamingModelBackend,
)
from .generation import ApiBackend, VllmBackend
from .streaming import StreamingRpcBackend
from .provenance import _git_provenance, _sha256_file
from .config import EvaluationConfig, load_api_credentials
from .model_loader import load_model_adapter
from .judge import OpenEndedJudge
from .metrics import score_run
from .prompts import PROMPT_VERSION
from .schema import CompiledDataset, TurnSpec, VideoSpec
from .validation import issues_as_json


class HarnessRunner:
    def __init__(
        self,
        *,
        dataset: CompiledDataset,
        assets: AssetManifest,
        config: EvaluationConfig,
        output_dir: Path,
        privacy_file: Path | None = None,
        summaries_enabled: bool = True,
        skip_invalid: bool = False,
        model_backend: GenerationBackend | StreamingModelBackend | None = None,
        open_judge: OpenEndedJudge | None = None,
        backend_health: dict[str, object] | None = None,
    ) -> None:
        api_key = None
        if model_backend is None or (open_judge is None and config.judge_model):
            config, api_key = load_api_credentials(config, privacy_file)
        self.dataset = dataset
        self.assets = assets
        self.config = config
        self.output_dir = output_dir.resolve()
        self.summaries_enabled = summaries_enabled
        self.skip_invalid = skip_invalid
        self.backend_health = backend_health
        self.open_judge = open_judge
        self.run_id = str(uuid.uuid4())
        self._writers_closed = True
        self._selected_video_ids: list[str] = []
        self._expected_event_counts: dict[str, int] = {}
        self._asset_files: list[dict[str, object]] = []
        self._oom_aborted_videos: dict[str, dict[str, object]] = {}
        self._worker_aborted_videos: dict[str, dict[str, object]] = {}
        self._code_provenance = _git_provenance()
        existing_outputs = [
            self.output_dir / name
            for name in (
                "run.json",
                "ticks.jsonl",
                "events.jsonl",
                "model_calls.jsonl",
                "metrics.json",
            )
            if (self.output_dir / name).exists()
        ]
        if existing_outputs:
            names = ", ".join(path.name for path in existing_outputs)
            raise RuntimeError(
                f"Output directory already contains run artifacts ({names}); "
                "use a new output directory"
            )
        if model_backend is not None:
            self.model = model_backend
        elif config.model_adapter is not None:
            self.model = load_model_adapter(config, api_key=api_key)
        else:
            if config.backend == "api":
                self.model = ApiBackend(config, api_key or "", on_attempt=None)
            elif config.backend == "vllm":
                self.model = VllmBackend(config, on_attempt=None)
            else:
                self.model = StreamingRpcBackend(config, on_attempt=None)
        if self.open_judge is None and config.judge_model:
            assert config.judge_base_url and api_key
            judge_config = EvaluationConfig(
                backend="api",
                model=config.judge_model,
                base_url=config.judge_base_url,
                wire_api=config.wire_api,
                reasoning_effort=config.judge_reasoning_effort,
                enable_thinking=config.enable_thinking,
                action_max_tokens=1024,
                request_timeout_seconds=config.request_timeout_seconds,
                request_retries=config.request_retries,
            )
            self.open_judge = OpenEndedJudge(
                ApiBackend(judge_config, api_key, on_attempt=None)
            )
        if self.open_judge is None:
            self.open_judge = OpenEndedJudge()
        if isinstance(self.model, StreamingModelBackend):
            # Native models own their temporal memory. Calling the same model
            # for harness summaries would mutate its session and duplicate
            # frames, so summaries are always disabled for this backend.
            self.summaries_enabled = False
        # Create artifacts only after all credential and client validation has
        # succeeded. A failed constructor must leave no poisoned empty run.
        self.output_dir.mkdir(parents=True, exist_ok=True)
        created: list[Path] = []
        try:
            self.tick_writer = JsonlWriter(self.output_dir / "ticks.jsonl")
            created.append(self.output_dir / "ticks.jsonl")
            self.event_writer = JsonlWriter(self.output_dir / "events.jsonl")
            created.append(self.output_dir / "events.jsonl")
            self.call_writer = JsonlWriter(self.output_dir / "model_calls.jsonl")
            created.append(self.output_dir / "model_calls.jsonl")
            self._writers_closed = False
        except Exception:
            for writer_name in ("tick_writer", "event_writer", "call_writer"):
                writer = getattr(self, writer_name, None)
                if writer is not None:
                    writer.close()
            for path in created:
                path.unlink(missing_ok=True)
            raise
        self.recorder = RunRecorder(self.run_id, self.config, self.call_writer)
        if isinstance(self.model, (GenerationBackend, StreamingModelBackend)):
            self.model.on_attempt = lambda payload: self.call_writer.write(
                {**payload, "record_type": "backend_attempt", "run_id": self.run_id, "logged_at": utc_now()}
            )
        if isinstance(self.open_judge.backend, GenerationBackend):
            self.open_judge.backend.on_attempt = lambda payload: self.call_writer.write(
                {**payload, "record_type": "backend_attempt", "run_id": self.run_id, "logged_at": utc_now()}
            )

    def close(self) -> None:
        if self._writers_closed:
            return
        self.tick_writer.close()
        self.event_writer.close()
        self.call_writer.close()
        self._writers_closed = True

    def run(
        self,
        *,
        video_ids: set[str] | None = None,
        limit: int | None = None,
    ) -> None:
        if limit is not None and limit < 1:
            self._write_manifest(
                status="failed",
                metrics_computed=False,
                failure={"type": "ValueError", "message": "limit must be at least 1"},
            )
            self.close()
            raise ValueError("limit must be at least 1")
        if video_ids is not None:
            unknown = set(video_ids) - {
                video.video_id for video in self.dataset.videos
            }
            if unknown:
                message = "Unknown video IDs: " + ", ".join(sorted(unknown))
                self._write_manifest(
                    status="failed",
                    metrics_computed=False,
                    failure={"type": "ValueError", "message": message},
                )
                self.close()
                raise ValueError(message)
        videos = [
            video
            for video in self.dataset.videos
            if video_ids is None or video.video_id in video_ids
        ]
        if limit is not None:
            videos = videos[:limit]
        if not videos:
            message = "No videos selected"
            self._write_manifest(
                status="failed",
                metrics_computed=False,
                failure={"type": "RuntimeError", "message": message},
            )
            self.close()
            raise RuntimeError(message)
        self._selected_video_ids = [video.video_id for video in videos]
        self._expected_event_counts = {
            video.video_id: len(self._runnable_events(video)) for video in videos
        }
        try:
            for video in videos:
                self._validate_runnable_video(video)
            self._asset_files = self._fingerprint_selected_assets(videos)
            self._write_manifest(status="running", metrics_computed=False)
            for video in videos:
                # _run_video converts a native CUDA OOM into an aborted video
                # with zero-valued terminal turns.  The worker process and its
                # loaded weights stay alive, so the next video starts normally.
                self._run_video(video)
            self.close()
            self._write_manifest(status="finalizing", metrics_computed=False)
            metrics = score_run(
                self.output_dir,
                require_complete=False,
                expected_video_ids=set(self._selected_video_ids),
                expected_event_counts=self._expected_event_counts,
            )
        except BaseException as exc:
            try:
                self._write_manifest(
                    status="failed",
                    metrics_computed=False,
                    failure={"type": type(exc).__name__, "message": str(exc)},
                )
            except BaseException:
                # Preserve the evaluator exception even if the output volume
                # is unavailable; failure recording must never mask the cause.
                LOGGER.exception("unable to write failed run manifest")
            raise
        else:
            self._write_manifest(status="complete", metrics_computed=True)
            metrics["integrity"]["run_status"] = "complete"
            write_json_atomic(self.output_dir / "metrics.json", metrics)
        finally:
            self.close()

    def _fingerprint_selected_assets(
        self, videos: list[VideoSpec]
    ) -> list[dict[str, object]]:
        paths: set[Path] = set()
        for video in videos:
            assets = self.assets.videos[video.video_id]
            paths.add(self.assets.resolve(assets.stream_video_uri))
            paths.update(self.assets.resolve(uri) for uri in assets.view_video_uris.values())
            for event in self._runnable_events(video):
                evidence = assets.evidence[event.event_id]
                paths.update(self.assets.resolve(uri) for uri in evidence.uris)
        return [
            {
                "path": str(path),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for path in sorted(paths, key=str)
        ]

    def _validate_runnable_video(self, video: VideoSpec) -> None:
        runnable_events = self._runnable_events(video)
        event_ids = {event.event_id for event in runnable_events}
        video_errors = [
            issue
            for issue in self.dataset.errors
            if issue.video_id == video.video_id and issue.event_id is None
        ]
        event_errors = [
            issue
            for issue in self.dataset.errors
            if issue.video_id == video.video_id
            and issue.event_id in event_ids
        ]
        errors = video_errors + ([] if self.skip_invalid else event_errors)
        if errors:
            codes = ", ".join(sorted({issue.code for issue in errors}))
            raise RuntimeError(f"{video.video_id} has invalid events: {codes}")
        if not runnable_events:
            raise RuntimeError(f"{video.video_id} has no valid runnable events")
        asset_errors = self.assets.validate_video(video.video_id, runnable_events)
        if asset_errors:
            raise RuntimeError("; ".join(asset_errors))

    def _run_video(self, video: VideoSpec) -> None:
        completion = VideoRunner(
            self.config, self.assets, self.model, self.open_judge,
            self.summaries_enabled, self.recorder, self.tick_writer, self.event_writer,
        ).run(video, self._runnable_events(video))
        if completion["status"] == "oom_aborted":
            self._oom_aborted_videos[video.video_id] = completion["oom"]
        elif completion["status"] == "worker_aborted":
            self._worker_aborted_videos[video.video_id] = completion["worker_error"]

    def _runnable_events(self, video: VideoSpec) -> list[TurnSpec]:
        if not self.skip_invalid:
            return list(video.events)
        invalid_ids = {
            issue.event_id
            for issue in self.dataset.errors
            if issue.video_id == video.video_id and issue.event_id is not None
        }
        return [event for event in video.events if event.event_id not in invalid_ids]

    def _write_manifest(
        self,
        *,
        status: str,
        metrics_computed: bool,
        failure: dict[str, str] | None = None,
    ) -> None:
        asset_content_sha256 = hashlib.sha256(
            json.dumps(
                self._asset_files,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        write_json_atomic(
            self.output_dir / "run.json",
            {
                "schema_version": "pvb.streaming_run.v4",
                "run_id": self.run_id,
                "status": status,
                "updated_at": utc_now(),
                "dataset_path": str(self.dataset.source_path),
                "dataset_sha256": self.dataset.source_sha256,
                "asset_manifest_path": str(self.assets.source_path),
                "asset_manifest_sha256": self.assets.content_sha256,
                "asset_content_sha256": asset_content_sha256,
                "asset_files": self._asset_files,
                "prompt_version": PROMPT_VERSION,
                "code": self._code_provenance,
                "selected_video_ids": self._selected_video_ids,
                "expected_event_counts": self._expected_event_counts,
                "expected_event_count": sum(self._expected_event_counts.values()),
                "dataset_video_count": len(self.dataset.videos),
                "dataset_event_count": sum(
                    len(video.events) for video in self.dataset.videos
                ),
                "config": self.config.as_dict(),
                "backend_health": self.backend_health,
                "oom_aborted_videos": self._oom_aborted_videos,
                "worker_aborted_videos": self._worker_aborted_videos,
                "memory_mode": (
                    "native_event_isolated_uniform_causal_replay"
                    if isinstance(self.model, StreamingModelBackend)
                    else "harness_three_tier"
                    if self.summaries_enabled
                    else "harness_short_term_only"
                ),
                "metrics_computed": metrics_computed,
                **({"failure": failure} if failure else {}),
            },
        )


def validation_report(dataset: CompiledDataset) -> dict[str, Any]:
    return {
        "schema_version": "pvb.streaming_validation.v1",
        "generated_at": utc_now(),
        "dataset_path": str(dataset.source_path),
        "dataset_sha256": dataset.source_sha256,
        "source_schema_version": dataset.schema_version,
        "video_count": len(dataset.videos),
        "event_count": sum(len(video.events) for video in dataset.videos),
        "error_count": len(dataset.errors),
        "warning_count": len(dataset.warnings),
        "issues": issues_as_json(dataset.issues),
    }
