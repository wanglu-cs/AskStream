from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .release import SCHEMA_VERSION, canonical_document
from .schema import (CompiledDataset, EvidenceBundle, EvidencePresentation, EvidenceTimebase, JudgeSpec, OptionSpec, ResponseMode, TextEvidence, TimeWindow, TurnSpec, ValidationIssue, VideoSpec, ViewSpec, VisualEvidence, VisualEvidenceKind)
from .validation import _issue, _validate_event, _validate_non_overlapping_events


def compile_dataset(path: Path) -> CompiledDataset:
    path = path.resolve()
    raw_bytes = path.read_bytes()
    document = json.loads(raw_bytes)
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"dataset schema_version must be {SCHEMA_VERSION!r}")
    return _validate_non_overlapping_events(
        _compile_document(path, raw_bytes, canonical_document(path, document))
    )


def _parse_evidence(value: dict[str, Any]) -> EvidenceBundle:
    if not isinstance(value, dict):
        raise ValueError("evidence must be an object")
    if not isinstance(value.get("text"), list) or not isinstance(value.get("visual"), list):
        raise ValueError("evidence text and visual must be arrays")
    if type(value.get("delivery_delay_ticks")) is not int or value["delivery_delay_ticks"] != 1:
        raise ValueError("evidence delivery_delay_ticks must be exactly 1")
    return EvidenceBundle(
        text=tuple(TextEvidence(**item) for item in value["text"]),
        visual=tuple(
            VisualEvidence(
                kind=VisualEvidenceKind(item["kind"]),
                uris=tuple(item["uris"]),
                timebase=EvidenceTimebase(item["timebase"]),
                presentation=EvidencePresentation(item["presentation"]),
                start_s=item.get("start_s"),
            ) for item in value["visual"]
        ),
    )


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _integer(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _parse_window(value: Any) -> TimeWindow | None:
    if not isinstance(value, dict):
        return None
    start = _number(value.get("start_s"))
    end = _number(value.get("end_s"))
    if start is None and end is None:
        return None
    if start is None or end is None:
        return None
    return TimeWindow(
        start_s=start,
        end_s=end,
        start_frame=_integer(value.get("start_frame")),
        end_frame=_integer(value.get("end_frame")),
    )


def _parse_options(value: Any) -> tuple[OptionSpec, ...]:
    if not isinstance(value, list):
        return ()
    result: list[OptionSpec] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("option_id") or "").strip()
        text = str(item.get("text") or "").strip()
        raw_entity_ids = item.get("entity_ids", [])
        if not isinstance(raw_entity_ids, list):
            raw_entity_ids = []
        entity_ids = tuple(
            str(entity_id)
            for entity_id in raw_entity_ids
            if str(entity_id).strip()
        )
        if option_id and text:
            result.append(
                OptionSpec(
                    option_id,
                    text,
                    entity_ids,
                    str(item.get("target_view_id") or "").strip() or None,
                )
            )
    return tuple(result)


def _compile_document(
    path: Path,
    raw_bytes: bytes,
    document: dict[str, Any],
) -> CompiledDataset:
    issues: list[ValidationIssue] = []
    videos: list[VideoSpec] = []
    raw_videos = document.get("videos")
    if not isinstance(raw_videos, list):
        raise ValueError("release videos must be an array")
    seen_video_ids: set[str] = set()
    for video_index, raw_video in enumerate(raw_videos):
        if not isinstance(raw_video, dict):
            _issue(issues, "error", "invalid_video", "Video must be an object", "")
            continue
        video_id = str(raw_video.get("video_id") or "").strip()
        media = raw_video.get("media") if isinstance(raw_video.get("media"), dict) else {}
        frame_rate = media.get("frame_rate") if isinstance(media.get("frame_rate"), dict) else {}
        duration = _number(media.get("duration_s")) or 0.0
        width = _integer(media.get("width")) or 0
        height = _integer(media.get("height")) or 0
        frame_rate_num = _integer(frame_rate.get("num")) or 0
        frame_rate_den = _integer(frame_rate.get("den")) or 0
        if not video_id:
            _issue(issues, "error", "missing_video_id", f"Video {video_index} has no ID", "")
            continue
        if video_id in seen_video_ids:
            _issue(issues, "error", "duplicate_video_id", f"Duplicate video ID {video_id!r}", video_id)
            continue
        seen_video_ids.add(video_id)
        if min(duration, width, height, frame_rate_num, frame_rate_den) <= 0:
            _issue(issues, "error", "invalid_media", "Release media metadata must be positive", video_id)
            continue
        frame_period = frame_rate_den / frame_rate_num
        raw_turns = raw_video.get("turns")
        if not isinstance(raw_turns, list):
            _issue(issues, "error", "invalid_turns", "turns must be an array", video_id)
            raw_turns = []
        turns: list[TurnSpec] = []
        seen_turn_ids: set[str] = set()
        seen_orders: set[int] = set()
        for turn_index, raw_turn in enumerate(raw_turns):
            if not isinstance(raw_turn, dict):
                _issue(issues, "error", "invalid_turn", "Turn must be an object", video_id)
                continue
            turn_id = str(raw_turn.get("turn_id") or "").strip()
            if turn_id in seen_turn_ids:
                _issue(issues, "error", "duplicate_event_id", f"Duplicate turn ID {turn_id!r}", video_id, turn_id)
            seen_turn_ids.add(turn_id)
            order = _integer(raw_turn.get("order"))
            if order is None:
                order = turn_index
            if order in seen_orders:
                _issue(issues, "error", "duplicate_event_order", f"Duplicate turn order {order}", video_id, turn_id)
            seen_orders.add(order)
            segment = raw_turn.get("segment") if isinstance(raw_turn.get("segment"), dict) else {}
            windows = raw_turn.get("windows") if isinstance(raw_turn.get("windows"), dict) else {}
            asking = _parse_window(windows.get("asking"))
            if asking is None:
                _issue(issues, "error", "missing_asking_window", "Turn requires an Asking window", video_id, turn_id)
                continue
            question = raw_turn.get("question") if isinstance(raw_turn.get("question"), dict) else {}
            responses = raw_turn.get("responses") if isinstance(raw_turn.get("responses"), dict) else {}
            ask = responses.get("ask") if isinstance(responses.get("ask"), dict) else {}
            answer = responses.get("answer") if isinstance(responses.get("answer"), dict) else {}
            gold = raw_turn.get("gold") if isinstance(raw_turn.get("gold"), dict) else {}
            evidence = _parse_evidence(raw_turn["evidence"])
            open_evidence = (
                _parse_evidence(raw_turn["evidence_open"])
                if raw_turn.get("evidence_open") is not None else None
            )
            ask_judge_raw = ask.get("judge") if isinstance(ask.get("judge"), dict) else None
            answer_judge_raw = answer.get("judge") if isinstance(answer.get("judge"), dict) else None
            source = raw_turn.get("source") if isinstance(raw_turn.get("source"), dict) else {}
            valid_ask_ids = gold.get("ask_option_ids")
            if not isinstance(valid_ask_ids, list):
                valid_ask_ids = []
            entity_ids = gold.get("entity_ids")
            if not isinstance(entity_ids, list):
                entity_ids = []
            turn = TurnSpec(
                event_id=turn_id,
                video_id=video_id,
                order=order,
                segment_start_s=_number(segment.get("start_s")) or 0.0,
                segment_end_s=_number(segment.get("end_s")) or 0.0,
                waiting=_parse_window(windows.get("waiting")),
                waiting_open=_parse_window(windows.get("waiting_open")),
                asking=asking,
                answering=_parse_window(windows.get("answering")),
                question=str(question.get("text") or "").strip(),
                language=str(question.get("language") or "en").strip(),
                canonical_question=str(question.get("canonical") or "").strip(),
                question_time_s=_number(question.get("time_s")),
                historical_preference=str(question.get("historical_context") or "").strip() or None,
                action_options=_parse_options(ask.get("options")),
                answer_options=_parse_options(answer.get("options")),
                correct_action_option_id=str(gold.get("ask_option_id") or "").strip(),
                correct_action_option_ids=tuple(str(item) for item in valid_ask_ids if str(item).strip()),
                correct_ask_text=str(gold.get("ask_text") or "").strip() or None,
                correct_answer_option_id=str(gold.get("answer_option_id") or "").strip(),
                answer_text=str(gold.get("answer_text") or "").strip() or None,
                correct_entity_ids=tuple(str(item) for item in entity_ids if str(item).strip()),
                entity_ids=tuple(str(item) for item in raw_turn.get("entity_ids", []) if str(item).strip()),
                visual_prompt_ids=tuple(str(item) for item in raw_turn.get("visual_prompt_ids", []) if str(item).strip()),
                evidence=evidence,
                evidence_open=open_evidence,
                concept_name=str(raw_turn.get("concept_name") or ""),
                uncertainty_type=str(raw_turn.get("uncertainty_type") or "").strip(),
                task=str(raw_turn.get("task") or "").strip(),
                initial_view_id=str(raw_turn.get("initial_view_id") or "").strip(),
                ask_mode=ResponseMode(str(ask.get("mode") or "choice")),
                answer_mode=ResponseMode(str(answer.get("mode") or "choice")),
                ask_judge=(JudgeSpec(str(ask_judge_raw.get("model") or "gemini-3.8-flash"), str(ask_judge_raw.get("prompt") or "")) if ask_judge_raw else None),
                answer_judge=(JudgeSpec(str(answer_judge_raw.get("model") or "gemini-3.8-flash"), str(answer_judge_raw.get("prompt") or "")) if answer_judge_raw else None),
                preserve_post_terminal_observation=bool(raw_turn.get("preserve_post_terminal_observation")),
                original_segment_start_s=_number(source.get("original_segment_start_s")),
                original_segment_end_s=_number(source.get("original_segment_end_s")),
                dropped_between_events=tuple(str(item) for item in source.get("dropped_between_events", []) if str(item).strip()),
                shared_source_boundary=bool(source.get("shared_source_boundary")),
                source_metadata=(source.get("metadata") if isinstance(source.get("metadata"), dict) else {}),
            )
            _validate_event(turn, issues, frame_period)
            turns.append(turn)
        views = tuple(
            ViewSpec(
                view_id=str(item.get("view_id") or "").strip(),
                label=str(item.get("label") or "").strip(),
                uri=str(item.get("uri") or "").strip(),
                role=str(item.get("role") or "").strip(),
                camera_id=str(item.get("camera_id") or "").strip(),
                stream_id=str(item.get("stream_id") or "").strip(),
            )
            for item in raw_video.get("views", [])
            if isinstance(item, dict)
        )
        videos.append(
            VideoSpec(
                video_id=video_id,
                source_uri=str(raw_video.get("source_uri") or "").strip(),
                duration_s=duration,
                width=width,
                height=height,
                frame_rate_num=frame_rate_num,
                frame_rate_den=frame_rate_den,
                events=tuple(turns),
                views=views,
            )
        )
    return CompiledDataset(
        source_path=path,
        source_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        schema_version=SCHEMA_VERSION,
        videos=tuple(videos),
        issues=issues,
    )


