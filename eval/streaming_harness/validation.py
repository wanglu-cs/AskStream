from __future__ import annotations

import math
from typing import Iterable

from .actions import normalize_answer_text
from .schema import CompiledDataset, TurnSpec, ValidationIssue, OptionSpec, ResponseMode, VisualEvidenceKind, EvidencePresentation


def _issue(
    issues: list[ValidationIssue],
    severity: str,
    code: str,
    message: str,
    video_id: str,
    event_id: str | None = None,
) -> None:
    issues.append(ValidationIssue(severity, code, message, video_id, event_id))


def _validate_non_overlapping_events(dataset: CompiledDataset) -> CompiledDataset:
    """Apply the runner's single-active-event invariant to the release."""

    for video in dataset.videos:
        events = sorted(video.events, key=lambda item: (item.start_s, item.order))
        for left, right in zip(events, events[1:]):
            left_end_s = (
                left.answering.end_s
                if left.answering is not None
                else left.asking.end_s
            )
            if right.start_s < left_end_s - 1e-6:
                _issue(
                    dataset.issues,
                    "error",
                    "overlapping_active_events",
                    (
                        f"{left.event_id!r} remains active until {left_end_s:.6f}s "
                        f"but {right.event_id!r} starts at {right.start_s:.6f}s"
                    ),
                    video.video_id,
                    right.event_id,
                )
    return dataset


def _validate_event(
    event: TurnSpec,
    issues: list[ValidationIssue],
    frame_period: float,
) -> None:
    if not event.event_id:
        _issue(issues, "error", "missing_event_id", "Event ID is empty", event.video_id)
    if not event.question:
        _issue(issues, "error", "missing_question", "Question is empty", event.video_id, event.event_id)
    for prefix, options in (
        ("action", event.action_options),
        ("answer", event.answer_options),
    ):
        if len(options) > 26:
            _issue(
                issues,
                "error",
                f"too_many_{prefix}_options",
                f"{prefix.upper()} options cannot exceed 26",
                event.video_id,
                event.event_id,
            )
        ids = [option.option_id for option in options]
        if len(ids) != len(set(ids)):
            _issue(
                issues,
                "error",
                f"duplicate_{prefix}_option_id",
                f"{prefix.upper()} option IDs must be unique within an event",
                event.video_id,
                event.event_id,
            )
        gold_ids = (
            set(event.valid_action_option_ids)
            if prefix == "action"
            else {event.correct_answer_option_id}
        )
        texts: dict[str, list[str]] = {}
        semantic_keys: dict[tuple[str, ...], list[OptionSpec]] = {}
        for option in options:
            text_key = normalize_answer_text(option.text)
            if text_key:
                texts.setdefault(text_key, []).append(option.option_id)
                # Flag lexical paraphrases for review, without pretending this
                # conservative heuristic proves general semantic equivalence.
                tokens = tuple(sorted(
                    "gray" if token == "grey" else token
                    for token in text_key.split() if token not in {"a", "an", "the"}
                ))
                if prefix == "answer" and tokens:
                    semantic_keys.setdefault(tokens, []).append(option)
        for key, option_ids in texts.items():
            if len(option_ids) < 2:
                continue
            is_gold = bool(gold_ids.intersection(option_ids))
            _issue(
                issues, "error",
                f"duplicate_{prefix}_{'gold_' if is_gold else ''}option_text",
                f"Equivalent normalized {prefix} text {key!r} appears in options {option_ids}",
                event.video_id, event.event_id,
            )
        for candidates in semantic_keys.values():
            if len({normalize_answer_text(option.text) for option in candidates}) > 1:
                _issue(
                    issues, "warning", f"possible_semantic_duplicate_{prefix}_options",
                    f"Review potentially equivalent candidates: {[(o.option_id, o.text) for o in candidates]}",
                    event.video_id, event.event_id,
                )
    if len(event.action_options) + len(event.answer_options) > 26:
        _issue(
            issues,
            "error",
            "too_many_combined_options",
            "ASK and ANSWER options together cannot exceed 26",
            event.video_id,
            event.event_id,
        )
    temporal_values = {
        "segment_start_s": event.segment_start_s,
        "segment_end_s": event.segment_end_s,
        "asking_start_s": event.asking.start_s,
        "asking_end_s": event.asking.end_s,
        **(
            {
                "waiting_start_s": event.waiting.start_s,
                "waiting_end_s": event.waiting.end_s,
            }
            if event.waiting
            else {}
        ),
        **(
            {
                "waiting_open_start_s": event.waiting_open.start_s,
                "waiting_open_end_s": event.waiting_open.end_s,
            }
            if event.waiting_open
            else {}
        ),
        **(
            {
                "answering_start_s": event.answering.start_s,
                "answering_end_s": event.answering.end_s,
            }
            if event.answering
            else {}
        ),
    }
    if any(not math.isfinite(value) for value in temporal_values.values()):
        _issue(
            issues,
            "error",
            "non_finite_time",
            "All event times must be finite numbers",
            event.video_id,
            event.event_id,
        )
    if event.segment_start_s < 0 or event.segment_end_s <= event.segment_start_s:
        _issue(
            issues,
            "error",
            "invalid_segment_window",
            "Segment times must be non-negative with start before end",
            event.video_id,
            event.event_id,
        )
    if event.ask_mode == ResponseMode.CHOICE and len(event.action_options) < 2:
        _issue(
            issues,
            "error",
            "insufficient_action_options",
            "At least two ASK options are required",
            event.video_id,
            event.event_id,
        )
    if event.ask_mode == ResponseMode.OPEN and not event.correct_ask_text:
        _issue(
            issues,
            "error",
            "missing_open_ask_reference",
            "Open ASK evaluation requires a reference request",
            event.video_id,
            event.event_id,
        )
    if event.answer_mode == ResponseMode.CHOICE and len(event.answer_options) < 2:
        _issue(issues, "error", "insufficient_answer_options", "At least two ANSWER options are required", event.video_id, event.event_id)
    if event.answer_mode == ResponseMode.OPEN and not event.answer_text:
        _issue(issues, "error", "missing_answer_text", "Open ANSWER evaluation requires a reference answer", event.video_id, event.event_id)
    if event.waiting is None and event.answering is not None:
        gap = event.answering.start_s - event.asking.end_s
        tolerance = max(frame_period + 0.001, 0.05)
        if abs(gap) > tolerance:
            _issue(
                issues,
                "error",
                "non_contiguous_asking_answering_windows",
                f"Asking and Answering differ by {gap:.6f}s",
                event.video_id,
                event.event_id,
            )
        if (
            event.asking.end_frame is not None
            and event.answering.start_frame is not None
            and abs(event.answering.start_frame - event.asking.end_frame) > 1
        ):
            _issue(
                issues,
                "error",
                "non_contiguous_asking_answering_frames",
                "Asking end and Answering start must be equal or one frame apart",
                event.video_id,
                event.event_id,
            )
    action_ids = {option.option_id for option in event.action_options}
    answer_ids = {option.option_id for option in event.answer_options}
    valid_action_ids = event.valid_action_option_ids
    if event.ask_mode == ResponseMode.CHOICE and (
        not valid_action_ids or any(option_id not in action_ids for option_id in valid_action_ids)
    ):
        _issue(
            issues,
            "error",
            "invalid_gold_action",
            "At least one valid gold ASK option is required",
            event.video_id,
            event.event_id,
        )
    if event.answer_mode == ResponseMode.CHOICE and (not event.correct_answer_option_id or event.correct_answer_option_id not in answer_ids):
        _issue(issues, "error", "invalid_gold_answer", "Exactly one valid gold ANSWER option is required", event.video_id, event.event_id)
    if event.evidence_kind not in {"video", "image", "text", "none", "synchronized_view"}:
        _issue(
            issues,
            "error",
            "invalid_evidence_kind",
            f"Unsupported evidence kind {event.evidence_kind!r}",
            event.video_id,
            event.event_id,
        )
    primary_visual = event.evidence.visual[0] if event.evidence and event.evidence.visual else None
    if (
        primary_visual is not None
        and primary_visual.kind == VisualEvidenceKind.VIDEO
        and primary_visual.presentation == EvidencePresentation.REPLACE_STREAM
        and not event.correct_entity_ids
    ):
        _issue(
            issues,
            "error",
            "missing_gold_entities",
            "Video evidence requires at least one gold entity",
            event.video_id,
            event.event_id,
        )
    if primary_visual is not None and primary_visual.kind == VisualEvidenceKind.SYNCHRONIZED_VIEW:
        if not event.initial_view_id:
            _issue(
                issues,
                "error",
                "missing_initial_view",
                "Viewpoint-missing events require an initial view",
                event.video_id,
                event.event_id,
            )
        for option in event.action_options:
            if not option.target_view_id:
                _issue(
                    issues,
                    "error",
                    "missing_target_view",
                    f"ASK option {option.option_id!r} does not map to a target view",
                    event.video_id,
                    event.event_id,
                )
    if event.asking.start_s >= event.asking.end_s:
        _issue(issues, "error", "invalid_asking_window", "Asking start must be before asking end", event.video_id, event.event_id)
    if event.asking.start_frame is None or event.asking.end_frame is None:
        _issue(issues, "error", "missing_asking_frames", "Asking start/end frames are required", event.video_id, event.event_id)
    if event.answering:
        if event.answering.start_s >= event.answering.end_s:
            _issue(issues, "error", "invalid_answering_window", "Answering start must be before answering end", event.video_id, event.event_id)
        if event.answering.start_s < event.asking.start_s:
            _issue(issues, "error", "answering_before_asking", "Answering cannot start before Asking", event.video_id, event.event_id)
        if event.answering.start_frame is None or event.answering.end_frame is None:
            _issue(issues, "error", "missing_answering_frames", "Answering start/end frames are required when Answering exists", event.video_id, event.event_id)
    if event.waiting:
        if event.waiting.start_s > event.waiting.end_s:
            _issue(issues, "error", "invalid_waiting_window", "Waiting start must not exceed waiting end", event.video_id, event.event_id)
        if event.waiting.start_frame is None or event.waiting.end_frame is None:
            _issue(issues, "error", "missing_waiting_frames", "Waiting start/end frames are required when Waiting exists", event.video_id, event.event_id)
        gap = event.asking.start_s - event.waiting.end_s
        tolerance = max(frame_period + 0.001, 0.05)
        if abs(gap) > tolerance:
            _issue(issues, "error", "non_contiguous_windows", f"Waiting and Asking differ by {gap:.6f}s", event.video_id, event.event_id)
        if (
            event.waiting.end_frame is not None
            and event.asking.start_frame is not None
            and abs(event.asking.start_frame - event.waiting.end_frame) > 1
        ):
            _issue(issues, "error", "non_contiguous_window_frames", "Waiting and Asking boundaries must be equal or one frame apart", event.video_id, event.event_id)
    if event.waiting_open:
        if event.waiting_open.start_s > event.waiting_open.end_s:
            _issue(
                issues,
                "error",
                "invalid_open_waiting_window",
                "Open Waiting start must not exceed its end",
                event.video_id,
                event.event_id,
            )
        if (
            event.waiting_open.start_frame is None
            or event.waiting_open.end_frame is None
        ):
            _issue(
                issues,
                "error",
                "missing_open_waiting_frames",
                "Open Waiting start/end frames are required when Open Waiting exists",
                event.video_id,
                event.event_id,
            )
        if event.waiting_open.end_s >= event.asking.end_s:
            _issue(
                issues,
                "error",
                "open_waiting_consumes_asking_window",
                "Open Waiting must end before the Asking window ends",
                event.video_id,
                event.event_id,
            )
        if (
            event.waiting_open.start_s < event.segment_start_s - 0.001
            or event.waiting_open.end_s > event.segment_end_s + 0.001
        ):
            _issue(
                issues,
                "error",
                "open_waiting_outside_segment",
                "Open Waiting must stay inside the segment",
                event.video_id,
                event.event_id,
            )
    event_end_s = event.answering.end_s if event.answering else event.asking.end_s
    if event.start_s < event.segment_start_s - 0.001 or event_end_s > event.segment_end_s + 0.001:
        _issue(issues, "error", "window_outside_segment", "Event windows must stay inside the segment", event.video_id, event.event_id)
    if (
        primary_visual is not None
        and primary_visual.kind == VisualEvidenceKind.VIDEO
        and primary_visual.presentation == EvidencePresentation.REPLACE_STREAM
        and not any(option.entity_ids for option in event.action_options)
    ):
        _issue(issues, "warning", "missing_action_entity_mapping", "ASK options do not yet map to reveal entity IDs", event.video_id, event.event_id)


def issues_as_json(issues: Iterable[ValidationIssue]) -> list[dict[str, object]]:
    return [issue.as_dict() for issue in issues]


