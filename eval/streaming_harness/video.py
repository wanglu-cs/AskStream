from __future__ import annotations

import logging
import math
import time
from collections import deque
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

from .assets import AssetManifest
from .audit import JsonlWriter, utc_now
from .config import EvaluationConfig
from .evaluator import TurnEvaluator, TurnTickInput
from .frames import EncodedFrame, VideoFrameSampler, load_image
from .interfaces import GenerationBackend, StreamingModelBackend, StreamObservation, StreamingOOMError, StreamingWorkerError
from .judge import OpenEndedJudge
from .memory import MemoryManager
from .protocol import EventRuntime, EventState
from .records import RunRecorder
from .schema import TurnSpec, VideoSpec


LOGGER = logging.getLogger(__name__)


def _last_sample_tick_before(end_s: float, tick_seconds: float) -> float:
    """Return the final discrete tick in the half-open interval [0, end_s)."""

    if tick_seconds <= 0:
        raise ValueError("tick_seconds must be positive")
    tick_index = max(0, math.ceil((end_s - 1e-9) / tick_seconds) - 1)
    return tick_index * tick_seconds


def _uniform_replay_frames(
    frames: list[EncodedFrame], max_frames: int
) -> list[EncodedFrame]:
    """Select a deterministic fixed-budget sample over the whole prefix."""

    if max_frames < 1:
        raise ValueError("max_frames must be positive")
    if len(frames) <= max_frames:
        return list(frames)
    if max_frames == 1:
        return [frames[-1]]
    final_index = len(frames) - 1
    indices = [
        round(slot * final_index / (max_frames - 1))
        for slot in range(max_frames)
    ]
    return [frames[index] for index in indices]


def _uses_relative_evidence_clock(event: TurnSpec) -> bool:
    return bool(
        event.evidence
        and event.evidence.visual
        and event.evidence.visual[0].timebase.value == "relative"
    )


@dataclass
class VideoRunner:
    config: EvaluationConfig
    assets: AssetManifest
    model: GenerationBackend | StreamingModelBackend
    open_judge: OpenEndedJudge
    summaries_enabled: bool
    recorder: RunRecorder
    tick_writer: JsonlWriter
    event_writer: JsonlWriter

    @property
    def run_id(self) -> str:
        return self.recorder.run_id

    def run(self, video: VideoSpec, events: list[TurnSpec]) -> dict[str, Any]:
        video_assets = self.assets.videos[video.video_id]
        stream_path = self.assets.resolve(video_assets.stream_video_uri)
        is_viewpoint = bool(video.views or video_assets.view_video_uris)
        memory = MemoryManager(
            self.config,
            self.model,
            summaries_enabled=self.summaries_enabled,
        )
        turn_evaluator = TurnEvaluator(
            self.config,
            self.model,
            judge=self.open_judge,
        )
        self._validate_serial_events(video, events)
        next_event_index = 0
        active_runtime: EventRuntime | None = None
        recent_stream_frames: deque[EncodedFrame] = deque(
            maxlen=self.config.short_term_frame_count
        )
        rehydrate_frame_count = (
            None
            if self.config.rehydrate_seconds == 0
            else max(
                1,
                math.ceil(
                    self.config.rehydrate_seconds / self.config.tick_seconds
                ),
            )
        )
        rehydrate_frames: deque[EncodedFrame] = deque(maxlen=rehydrate_frame_count)
        event_recent_frames: deque[EncodedFrame] | None = None
        evidence_video_samplers: dict[str, VideoFrameSampler] = {}
        evidence_images: dict[str, list[EncodedFrame]] = {}
        evidence_video_history: dict[str, list[EncodedFrame]] = {}
        answer_main_history: list[EncodedFrame] = []
        evidence_delivered: set[str] = set()
        last_tick_timestamp_s: float | None = None
        current_view_id: str | None = (
            events[0].initial_view_id if is_viewpoint and events else None
        )
        # ``current_view_id`` may temporarily point at a requested camera for
        # the audit-only tail of a viewpoint turn.  Keep a separate baseline
        # view for shared visual history/memory so that intervention frames can
        # never become input to a later event.
        baseline_view_id = current_view_id
        preserve_final_answer_tail = is_viewpoint or any(
            event.preserve_post_terminal_observation for event in events
        )
        streaming_model = (
            self.model if isinstance(self.model, StreamingModelBackend) else None
        )
        streaming_video_open = False
        playback_started_at = time.monotonic()
        evaluation_end_s = float(video.duration_s)
        oom_aborted = False
        oom_details: str | None = None
        worker_aborted = False
        worker_details: str | None = None

        def reset_native_event_context(reason: str, event_id: str | None) -> None:
            if streaming_model is None:
                return
            streaming_model.reset_context(
                video_id=video.video_id,
                metadata={"reason": reason, "event_id": event_id},
            )
            replay = _uniform_replay_frames(
                list(rehydrate_frames), self.config.rehydrate_max_frames
            )
            for replay_index, replay_frame in enumerate(replay):
                streaming_model.push_observation(
                    StreamObservation(
                        video_id=video.video_id,
                        tick_index=replay_index - len(replay),
                        timestamp_s=replay_frame.timestamp_s,
                        frame=replay_frame,
                        active_turn_id=None,
                        evidence=None,
                        replay=True,
                    )
                )

        video_error: BaseException | None = None
        try:
            if streaming_model is not None:
                streaming_model.open_video(
                    video_id=video.video_id,
                    metadata={
                        "stream_kind": video_assets.stream_kind,
                        "duration_s": video.duration_s,
                        "tick_seconds": self.config.tick_seconds,
                        "playback_speed": self.config.playback_speed,
                    },
                )
                streaming_video_open = True
            with ExitStack() as stack:
                view_samplers: dict[str, VideoFrameSampler] = {}
                if is_viewpoint:
                    for view_id, uri in video_assets.view_video_uris.items():
                        view_samplers[view_id] = stack.enter_context(
                            VideoFrameSampler(
                                self.assets.resolve(uri),
                                max_pixels=self.config.max_frame_pixels,
                                jpeg_quality=self.config.jpeg_quality,
                                source_label=f"synchronized_view:{view_id}",
                            )
                        )
                    if not view_samplers:
                        raise RuntimeError(f"{video.video_id}: no synchronized views")
                    sampler = next(iter(view_samplers.values()))
                else:
                    sampler = stack.enter_context(
                        VideoFrameSampler(
                            stream_path,
                            max_pixels=self.config.max_frame_pixels,
                            jpeg_quality=self.config.jpeg_quality,
                            source_label=f"{video_assets.stream_kind}_stream_video",
                        )
                    )
                requested_end_s = max(
                    (
                        event.answering.end_s
                        + self.config.answer_end_tolerance_s
                        if event.answering is not None
                        else event.asking.end_s
                        + max(
                            self.config.ask_end_tolerance_s,
                            self.config.answer_end_tolerance_s,
                        )
                    )
                    for event in events
                )
                # Boundary tolerances enlarge the interval in which a sampled
                # action is accepted; they do not create media after the video
                # has ended.  Clamp the sampling horizon to the declared causal
                # stream duration while retaining the tolerance inside
                # EventRuntime's action checks.
                evaluation_end_s = min(requested_end_s, video.duration_s)
                last_required_tick_s = _last_sample_tick_before(
                    evaluation_end_s, self.config.tick_seconds
                )
                if is_viewpoint:
                    short_views = [
                        f"{view_id}={view_sampler.duration_s:.3f}s"
                        for view_id, view_sampler in view_samplers.items()
                        if (
                            view_sampler.duration_s
                            + view_sampler.end_tolerance_s
                            + 0.001
                            < last_required_tick_s
                        )
                    ]
                    if short_views:
                        raise RuntimeError(
                            f"{video.video_id}: synchronized views end before the last "
                            f"required sample at {last_required_tick_s:.3f}s: "
                            + ", ".join(short_views)
                        )
                physical_end = sampler.duration_s + sampler.end_tolerance_s
                if physical_end + 0.001 < last_required_tick_s:
                    # A container duration can round up to a discrete tick
                    # whose last decodable PTS is one frame/tick earlier.
                    # frame_at() clamps inside the physical stream, so accept
                    # only this narrow representational boundary and still
                    # reject genuinely missing media.
                    boundary_slack = max(
                        sampler.end_tolerance_s,
                        self.config.tick_seconds,
                    )
                    if sampler.duration_s + boundary_slack + 0.001 < last_required_tick_s:
                        raise RuntimeError(
                            f"{video.video_id}: stream video ends at {sampler.duration_s:.3f}s "
                            f"before the last required sample at {last_required_tick_s:.3f}s "
                            f"(media-clamped evaluation cutoff {evaluation_end_s:.3f}s; "
                            f"tolerant action cutoff {requested_end_s:.3f}s)"
                        )
                    LOGGER.warning(
                        "%s: accepting one-tick media boundary (%0.3fs physical, "
                        "%0.3fs requested)",
                        video.video_id,
                        sampler.duration_s,
                        last_required_tick_s,
                    )
                # Open every video-evidence asset before the first model call.
                # Time-aligned reveals must cover the same absolute
                # samples as their event; silently holding their last frame
                # would recreate the historical COIN/MCVI timeline bug.
                # A stream's declared duration is its presentation end, while
                # the last decodable frame PTS can be one frame interval earlier.
                # The sampler's end tolerance covers that representational
                # boundary without inventing a frame beyond the media.
                for event in events:
                    evidence_asset = video_assets.evidence[event.event_id]
                    if evidence_asset.kind != "video":
                        continue
                    evidence_sampler = VideoFrameSampler(
                        self.assets.resolve(evidence_asset.uri),
                        max_pixels=self.config.max_frame_pixels,
                        jpeg_quality=self.config.jpeg_quality,
                        source_label=f"video_evidence:{event.event_id}",
                    )
                    evidence_video_samplers[event.event_id] = evidence_sampler
                    if evidence_sampler.duration_s <= 0.0:
                        raise RuntimeError(
                            f"{event.event_id}: video evidence has no decodable frames"
                        )
                    if not _uses_relative_evidence_clock(event):
                        event_end_s = (
                            event.answering.end_s
                            + self.config.answer_end_tolerance_s
                            if event.answering is not None
                            else event.asking.end_s
                            + self.config.answer_end_tolerance_s
                        )
                        event_end_s = min(event_end_s, evaluation_end_s)
                        last_evidence_tick_s = _last_sample_tick_before(
                            event_end_s, self.config.tick_seconds
                        )
                        if (
                            evidence_sampler.duration_s
                            + evidence_sampler.end_tolerance_s
                            + 0.001
                            < last_evidence_tick_s
                        ):
                            raise RuntimeError(
                                f"{event.event_id}: time-aligned evidence video ends at "
                                f"{evidence_sampler.duration_s:.3f}s before the last "
                                f"required absolute sample at {last_evidence_tick_s:.3f}s"
                            )
                tick_count = int(math.ceil(evaluation_end_s / self.config.tick_seconds))
                for tick_index in range(tick_count):
                    timestamp_s = tick_index * self.config.tick_seconds
                    if streaming_model is not None and self.config.playback_speed > 0:
                        target_elapsed = timestamp_s / self.config.playback_speed
                        remaining = target_elapsed - (time.monotonic() - playback_started_at)
                        if remaining > 0:
                            time.sleep(remaining)

                    expired_runtime = (
                        active_runtime
                        if active_runtime is not None
                        and active_runtime.expire(timestamp_s) is not None
                        else None
                    )
                    if (
                        is_viewpoint
                        and expired_runtime is not None
                        and expired_runtime.selected_view_id
                    ):
                        # Keep the requested camera during the otherwise-idle
                        # tail of this turn.  A subsequent turn resets to its
                        # own initial view below; concurrent events never
                        # inherit another event's requested camera.
                        current_view_id = expired_runtime.selected_view_id
                    if expired_runtime is not None:
                        expired_event_id = expired_runtime.spec.event_id
                        self.event_writer.write(self.recorder.event(expired_runtime))
                        active_runtime = None
                        turn_evaluator.reset()
                        event_recent_frames = None
                        reset_native_event_context("event_expired", expired_event_id)

                    if active_runtime is None and next_event_index < len(events):
                        candidate = events[next_event_index]
                        if timestamp_s + 1e-6 >= candidate.start_s:
                            next_start_s = (
                                events[next_event_index + 1].start_s
                                if next_event_index + 1 < len(events)
                                else None
                            )
                            hard_end_s = (
                                min(next_start_s, evaluation_end_s)
                                if next_start_s is not None
                                else evaluation_end_s
                            )
                            active_runtime = EventRuntime(
                                candidate,
                                self.config.tick_seconds,
                                self.config.ask_start_tolerance_s,
                                self.config.ask_end_tolerance_s,
                                self.config.answer_start_tolerance_s,
                                self.config.answer_end_tolerance_s,
                                hard_end_s=hard_end_s,
                            )
                            active_runtime.activate(timestamp_s)
                            turn_evaluator.bind(active_runtime)
                            answer_main_history = []
                            event_recent_frames = deque(
                                recent_stream_frames,
                                maxlen=self.config.short_term_frame_count,
                            )
                            # Native sessions may have accumulated a long idle
                            # prefix before the question became active. Start
                            # every scored turn from a bounded, deterministic
                            # replay of that causal prefix so context pressure
                            # is independent of the event's absolute timestamp.
                            reset_native_event_context(
                                "event_activation", candidate.event_id
                            )
                            if candidate.initial_view_id:
                                current_view_id = candidate.initial_view_id
                                baseline_view_id = candidate.initial_view_id
                            next_event_index += 1

                    # The last event may expire at this boundary. There is no
                    # reason to decode or summarize the unused video tail.
                    if (
                        active_runtime is None
                        and next_event_index >= len(events)
                        and not preserve_final_answer_tail
                    ):
                        break

                    if is_viewpoint:
                        if not current_view_id or current_view_id not in view_samplers:
                            raise RuntimeError(
                                f"{video.video_id}: no sampler for active view {current_view_id!r}"
                            )
                        if not baseline_view_id or baseline_view_id not in view_samplers:
                            raise RuntimeError(
                                f"{video.video_id}: no sampler for baseline view "
                                f"{baseline_view_id!r}"
                            )
                        frame_cache: dict[str, EncodedFrame] = {}

                        def frame_for_view(view_id: str) -> EncodedFrame:
                            if view_id not in frame_cache:
                                frame_cache[view_id] = view_samplers[view_id].frame_at(
                                    timestamp_s
                                )
                            return frame_cache[view_id]

                        stream_frame = frame_for_view(current_view_id)
                        baseline_frame = frame_for_view(baseline_view_id)
                        effective_frame = (
                            frame_for_view(
                                active_runtime.spec.initial_view_id or baseline_view_id
                            )
                            if active_runtime is not None
                            else stream_frame
                        )
                    else:
                        stream_frame = sampler.frame_at(timestamp_s)
                        baseline_frame = stream_frame
                        effective_frame = stream_frame
                    frame_revealed = False
                    delivered_text: str | None = None
                    delivered_images: list[EncodedFrame] = []
                    delivered_video_frame: EncodedFrame | None = None
                    visual_reveal_judge_frame: EncodedFrame | None = None
                    viewpoint_judge_frames: tuple[tuple[str, EncodedFrame], ...] = ()
                    evidence_kind: str | None = None
                    evidence_delivered_now = False
                    evidence_elapsed: float | None = None
                    reveal_runtime = (
                        active_runtime
                        if active_runtime is not None
                        and active_runtime.reveal_at_s is not None
                        and timestamp_s + 1e-6 >= active_runtime.reveal_at_s
                        else None
                    )
                    if reveal_runtime is not None:
                        event_id = reveal_runtime.spec.event_id
                        evidence_asset = video_assets.evidence[event_id]
                        evidence_kind = evidence_asset.kind
                        evidence_delivered_now = event_id not in evidence_delivered
                        if evidence_asset.kind == "video":
                            evidence_sampler = evidence_video_samplers.get(event_id)
                            if evidence_sampler is None:
                                evidence_path = self.assets.resolve(evidence_asset.uri)
                                evidence_sampler = VideoFrameSampler(
                                    evidence_path,
                                    max_pixels=self.config.max_frame_pixels,
                                    jpeg_quality=self.config.jpeg_quality,
                                    source_label=f"video_evidence:{event_id}",
                                )
                                if evidence_sampler.duration_s <= 0.0:
                                    evidence_sampler.close()
                                    raise RuntimeError(
                                        f"{event_id}: video evidence has no decodable frames"
                                    )
                                evidence_video_samplers[event_id] = evidence_sampler
                            is_independent_evidence = _uses_relative_evidence_clock(
                                reveal_runtime.spec
                            )
                            if is_independent_evidence:
                                # Personalized-action evidence starts when it
                                # is delivered and has its own relative clock.
                                evidence_elapsed = max(
                                    0.0,
                                    timestamp_s - float(reveal_runtime.reveal_at_s),
                                )
                                evidence_frame = evidence_sampler.frame_at(
                                    (evidence_asset.start_s or 0.0) + evidence_elapsed
                                )
                                delivered_video_frame = evidence_frame
                                history = evidence_video_history.setdefault(event_id, [])
                                if not history or evidence_frame.timestamp_s > history[-1].timestamp_s:
                                    history.append(evidence_frame)
                            else:
                                evidence_frame = evidence_sampler.frame_at(timestamp_s)
                                effective_frame = evidence_frame
                                frame_revealed = True
                            if reveal_runtime.spec.evidence_text:
                                delivered_text = reveal_runtime.spec.evidence_text
                        elif evidence_asset.kind == "text":
                            delivered_text = evidence_asset.text
                        elif evidence_asset.kind == "image":
                            images = evidence_images.get(event_id, [])
                            if not images:
                                images = [
                                    load_image(
                                        self.assets.resolve(uri),
                                        max_pixels=self.config.max_frame_pixels,
                                        jpeg_quality=self.config.jpeg_quality,
                                        source_label=f"image_evidence:{event_id}:{index}",
                                    )
                                    for index, uri in enumerate(evidence_asset.uris)
                                ]
                                evidence_images[event_id] = images
                            delivered_images = images
                            # Image evidence may carry the original PEARL
                            # concept-definition text as part of the same
                            # delivery. Keep it scoped to this event and let
                            # the existing persistence logic include it on
                            # subsequent ticks.
                            if evidence_asset.text:
                                delivered_text = evidence_asset.text
                        elif evidence_asset.kind == "synchronized_view":
                            if not reveal_runtime.selected_view_id:
                                raise RuntimeError(
                                    f"{event_id}: correct ASK did not select a synchronized view"
                                )
                            selected_view_id = reveal_runtime.selected_view_id
                            if selected_view_id not in view_samplers:
                                raise RuntimeError(
                                    f"{event_id}: unknown synchronized view {selected_view_id!r}"
                                )
                            effective_frame = view_samplers[
                                selected_view_id
                            ].frame_at(timestamp_s)
                            frame_revealed = True
                        evidence_delivered.add(event_id)

                    if (
                        active_runtime is not None
                        and active_runtime.state == EventState.ASKING
                        and active_runtime.spec.is_open_ask
                        and active_runtime.spec.task
                        == "VIM"
                    ):
                        initial_view_id = active_runtime.spec.initial_view_id
                        if initial_view_id and initial_view_id in view_samplers:
                            judge_view_rows: list[tuple[str, EncodedFrame]] = [
                                (
                                    f"Initial viewpoint ({initial_view_id})",
                                    frame_for_view(initial_view_id),
                                )
                            ]
                            seen_judge_views = {initial_view_id}
                            for option_id in active_runtime.spec.valid_action_option_ids:
                                target_view_id = active_runtime.spec.target_view_for_action(
                                    option_id
                                )
                                if (
                                    target_view_id
                                    and target_view_id not in seen_judge_views
                                    and target_view_id in view_samplers
                                ):
                                    judge_view_rows.append(
                                        (
                                            f"Gold viewpoint ({target_view_id})",
                                            frame_for_view(target_view_id),
                                        )
                                    )
                                    seen_judge_views.add(target_view_id)
                            viewpoint_judge_frames = tuple(judge_view_rows)

                    if (
                        active_runtime is not None
                        and active_runtime.state == EventState.ASKING
                        and active_runtime.spec.is_open_ask
                        and active_runtime.spec.task
                        == "KVEM"
                    ):
                        judge_event_id = active_runtime.spec.event_id
                        judge_evidence = video_assets.evidence[judge_event_id]
                        if judge_evidence.kind != "video":
                            raise RuntimeError(
                                f"{judge_event_id}: VISUAL_REVEAL open ASK judge "
                                "requires time-aligned correct_entity video evidence"
                            )
                        judge_sampler = evidence_video_samplers[judge_event_id]
                        visual_reveal_judge_frame = judge_sampler.frame_at(timestamp_s)

                    recent_stream_frames.append(baseline_frame)
                    rehydrate_frames.append(baseline_frame)
                    if event_recent_frames is not None:
                        event_recent_frames.append(effective_frame)
                    if (
                        active_runtime is not None
                        and active_runtime.spec.task in {"PACM", "UPM", "UPC"}
                        and active_runtime.spec.is_open_answer
                    ):
                        answer_main_history.append(effective_frame)

                    if streaming_model is not None:
                        observation_frame = (
                            effective_frame if active_runtime is not None else baseline_frame
                        )
                        streaming_model.push_observation(
                            StreamObservation(
                                video_id=video.video_id,
                                tick_index=tick_index,
                                timestamp_s=timestamp_s,
                                frame=observation_frame,
                                active_turn_id=(
                                    active_runtime.spec.event_id
                                    if active_runtime is not None
                                    else None
                                ),
                                evidence=(
                                    {
                                        "kind": evidence_kind,
                                        "delivered_now": evidence_delivered_now,
                                        "text": delivered_text,
                                        "images": delivered_images,
                                        "video_frame": delivered_video_frame,
                                    }
                                    if evidence_kind is not None
                                    else None
                                ),
                            )
                        )

                    memory_snapshot = memory.snapshot()
                    if active_runtime is None:
                        self.tick_writer.write(
                            self.recorder.tick(
                                video=video, tick_index=tick_index,
                                timestamp_s=timestamp_s, frame=stream_frame,
                                frame_revealed=False,
                                stream_kind=video_assets.stream_kind,
                                evidence_kind=None,
                                evidence_delivered_now=False,
                                evidence_elapsed_s=None,
                                event=None, result=None, parsed=None,
                                transition=None,
                                idle_reason="observation_only_no_active_event",
                                memory_snapshot=memory_snapshot,
                                action_history_before=(),
                                action_history_after=(),
                                model_input=None,
                            )
                        )
                    else:
                        if event_recent_frames is None:
                            raise RuntimeError("active event is missing its frame history")
                        turn_output = turn_evaluator.evaluate(TurnTickInput(
                            timestamp_s=timestamp_s,
                            frames=tuple(event_recent_frames),
                            memory=memory_snapshot,
                            evidence_text=delivered_text,
                            evidence_text_delivered_now=evidence_delivered_now,
                            evidence_images=tuple(delivered_images),
                            evidence_video_frame=delivered_video_frame,
                            evidence_video_frames=tuple(evidence_video_history.get(active_runtime.spec.event_id, ())),
                            answer_judge_frames=tuple(answer_main_history),
                            visual_reveal_judge_frame=visual_reveal_judge_frame,
                            viewpoint_judge_frames=viewpoint_judge_frames,
                        ))
                        result = turn_output.result
                        self.recorder.action(video.video_id, active_runtime.spec.event_id, timestamp_s, result)
                        parsed = turn_output.parsed
                        transition = turn_output.transition
                        history_before = turn_output.history_before
                        history_after = turn_output.history_after
                        judge_result = turn_output.judge
                        model_input = turn_output.model_input
                        idle_reason = None
                        self.tick_writer.write(
                            self.recorder.tick(
                                video=video,
                                tick_index=tick_index,
                                timestamp_s=timestamp_s,
                                frame=effective_frame,
                                frame_revealed=frame_revealed,
                                stream_kind=video_assets.stream_kind,
                                evidence_kind=evidence_kind,
                                evidence_delivered_now=evidence_delivered_now,
                                event=active_runtime.spec,
                                evidence_elapsed_s=evidence_elapsed,
                                evidence_frame=delivered_video_frame,
                                result=result,
                                parsed=parsed,
                                transition=transition,
                                idle_reason=idle_reason,
                                memory_snapshot=memory_snapshot,
                                action_history_before=history_before,
                                action_history_after=history_after,
                                judge_result=judge_result,
                                model_input=model_input,
                            )
                        )
                    last_tick_timestamp_s = timestamp_s
                    if (
                        is_viewpoint
                        and active_runtime is not None
                        and active_runtime.terminal
                        and active_runtime.selected_view_id
                    ):
                        # Preserve the selected view only for this turn's idle
                        # tail.  Do not mutate another active event's stream.
                        current_view_id = active_runtime.selected_view_id
                    if active_runtime is not None and active_runtime.terminal:
                        terminal_event_id = active_runtime.spec.event_id
                        self.event_writer.write(self.recorder.event(active_runtime))
                        active_runtime = None
                        turn_evaluator.reset()
                        event_recent_frames = None
                        reset_native_event_context("event_terminal", terminal_event_id)

                    if (
                        active_runtime is None
                        and next_event_index >= len(events)
                        and not preserve_final_answer_tail
                    ):
                        break

                    # Global memory receives only the non-intervention stream,
                    # never an event-specific reveal or requested viewpoint.
                    update = memory.observe(baseline_frame)
                    self.recorder.memory_update(video.video_id, timestamp_s, update)

                if active_runtime is not None and not active_runtime.terminal:
                    if active_runtime.expire(evaluation_end_s) is None:
                        active_runtime.terminate("evaluation_cutoff")
                    self.event_writer.write(self.recorder.event(active_runtime))
                for event in events[next_event_index:]:
                    never_activated = EventRuntime(event, self.config.tick_seconds)
                    never_activated.terminate("evaluation_cutoff_before_activation")
                    self.event_writer.write(self.recorder.event(never_activated))
        except StreamingOOMError as exc:
            video_error = exc
            oom_aborted = True
            oom_details = str(exc)
            # The failed request may have happened during observation, reset,
            # or a decision.  Emit exactly one zero-valued terminal record for
            # the active turn and every not-yet-started turn so the scorer keeps
            # the complete video turn denominator.
            if active_runtime is not None and not active_runtime.terminal:
                active_runtime.terminate("oom_aborted")
                self.event_writer.write(self.recorder.event(active_runtime))
                active_runtime = None
            for event in events[next_event_index:]:
                aborted_runtime = EventRuntime(event, self.config.tick_seconds)
                aborted_runtime.terminate("oom_aborted")
                self.event_writer.write(self.recorder.event(aborted_runtime))
        except StreamingWorkerError as exc:
            # A worker-side 5xx terminates only this video.  The server clears
            # its session after the failed request, so the next video can open
            # a fresh session without taking down the background matrix.
            video_error = exc
            worker_aborted = True
            worker_details = str(exc)
            if active_runtime is not None and not active_runtime.terminal:
                active_runtime.terminate("worker_aborted")
                self.event_writer.write(self.recorder.event(active_runtime))
                active_runtime = None
            for event in events[next_event_index:]:
                aborted_runtime = EventRuntime(event, self.config.tick_seconds)
                aborted_runtime.terminate("worker_aborted")
                self.event_writer.write(self.recorder.event(aborted_runtime))
        except BaseException as exc:
            video_error = exc
            raise
        finally:
            for evidence_sampler in evidence_video_samplers.values():
                evidence_sampler.close()
            if streaming_model is not None and streaming_video_open:
                try:
                    if oom_aborted:
                        streaming_model.abort_video(
                            video_id=video.video_id,
                            metadata={
                                "reason": "cuda_oom",
                                "error": oom_details,
                            },
                        )
                    elif worker_aborted:
                        streaming_model.abort_video(
                            video_id=video.video_id,
                            metadata={
                                "reason": "worker_error",
                                "error": worker_details,
                            },
                        )
                    else:
                        streaming_model.close_video(video_id=video.video_id)
                except BaseException as cleanup_exc:
                    if video_error is None:
                        raise
                    LOGGER.warning(
                        "suppressed streaming cleanup failure for %s after "
                        "%s: %s",
                        video.video_id,
                        type(video_error).__name__,
                        cleanup_exc,
                    )

        completion = {
            "record_type": "video_complete",
            "run_id": self.run_id,
            "video_id": video.video_id,
            "status": (
                "oom_aborted"
                if oom_aborted
                else "worker_aborted"
                if worker_aborted
                else "complete"
            ),
            "evaluation_cutoff_s": evaluation_end_s,
            "stream_stopped_at_s": last_tick_timestamp_s,
            "completed_at": utc_now(),
        }
        if oom_aborted:
            completion["oom"] = {
                "error": oom_details,
                "remaining_turns_scored_as_zero": True,
            }
        elif worker_aborted:
            completion["worker_error"] = {
                "error": worker_details,
                "remaining_turns_scored_as_zero": True,
            }
        self.event_writer.write(completion)
        return completion

    @staticmethod
    def _validate_serial_events(video: VideoSpec, events: list[TurnSpec]) -> None:
        """Reject overlapping turns; the evaluator intentionally runs one event at a time."""

        for left, right in zip(events, events[1:]):
            left_end_s = (
                left.answering.end_s if left.answering is not None else left.asking.end_s
            )
            if right.start_s < left_end_s - 1e-6:
                raise RuntimeError(
                    f"{video.video_id}: overlapping active events are unsupported: "
                    f"{left.event_id!r} ends at {left_end_s:.3f}s but "
                    f"{right.event_id!r} starts at {right.start_s:.3f}s"
                )
