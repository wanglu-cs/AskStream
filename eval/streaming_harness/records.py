from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .audit import JsonlWriter, utc_now
from .config import EvaluationConfig
from .frames import EncodedFrame
from .interfaces import ModelCallResult
from .memory import MemorySnapshot, MemoryUpdate
from .protocol import EventRuntime, Transition
from .schema import TurnSpec, VideoSpec


@dataclass
class RunRecorder:
    run_id: str
    config: EvaluationConfig
    call_writer: JsonlWriter | None = None

    def action(self, video_id: str, turn_id: str, timestamp_s: float, result: ModelCallResult) -> None:
        if self.call_writer is not None:
            self.call_writer.write({
                "record_type": "action_result", "run_id": self.run_id,
                "video_id": video_id, "event_id": turn_id, "timestamp_s": timestamp_s,
                "call_type": "action", "status": "success", "logged_at": utc_now(),
                **asdict(result),
            })

    def memory_update(
        self, video_id: str, timestamp_s: float, update: MemoryUpdate
    ) -> None:
        for tier, result in (
            ("mid_term", update.mid_term_result),
            ("long_term", update.long_term_result),
        ):
            if result is not None and self.call_writer is not None:
                self.call_writer.write(
                    {
                        "record_type": "memory_result",
                        "run_id": self.run_id,
                        "video_id": video_id,
                        "timestamp_s": timestamp_s,
                        "tier": tier,
                        "raw_output": result.content,
                        "model": result.model,
                        "latency_ms": result.latency_ms,
                        "usage": result.usage,
                        "response_id": result.response_id,
                    }
                )

    def tick(
        self,
        *,
        video: VideoSpec,
        tick_index: int,
        timestamp_s: float,
        frame: EncodedFrame,
        frame_revealed: bool,
        stream_kind: str,
        evidence_kind: str | None,
        evidence_delivered_now: bool,
        evidence_elapsed_s: float | None,
        evidence_frame: EncodedFrame | None = None,
        event: TurnSpec | None,
        result: ModelCallResult | None,
        parsed: Any,
        transition: Transition | None,
        idle_reason: str | None,
        memory_snapshot: MemorySnapshot,
        action_history_before: tuple[str, ...],
        action_history_after: tuple[str, ...],
        judge_result: Any = None,
        model_input: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        return {
            "record_type": "tick",
            "run_id": self.run_id,
            "video_id": video.video_id,
            "tick_index": tick_index,
            "timestamp_s": timestamp_s,
            "frame": frame.audit_dict(),
            "frame_mode": (
                "synchronized_view"
                if frame_revealed and evidence_kind == "synchronized_view"
                else "correct_entity_revealed"
                if frame_revealed and stream_kind == "masked"
                else "video_evidence"
                if frame_revealed
                else "all_entities_masked"
                if stream_kind == "masked"
                else "stream_video"
            ),
            "stream_kind": stream_kind,
            "reveal_delivered": frame_revealed,
            "evidence_kind": evidence_kind,
            "evidence_delivered": evidence_delivered_now,
            "evidence_elapsed_s": evidence_elapsed_s,
            "evidence_frame": (
                evidence_frame.audit_dict() if evidence_frame is not None else None
            ),
            "evidence_text_delivered": (
                evidence_delivered_now and evidence_kind in {"text", "image"}
            ),
            "active_event_id": event.event_id if event else None,
            "source_event": (
                _source_event(event, judge_model=self.config.judge_model)
                if event
                else None
            ),
            "raw_model_output": (
                (result.raw_content if result.raw_content is not None else result.content)
                if result
                else None
            ),
            "normalized_model_output": result.content if result else None,
            "parsed_action": (
                {
                    "type": parsed.type.value,
                    "option_label": parsed.option_label,
                    "ask_text": parsed.ask_text,
                    "answer_text": parsed.answer_text,
                    "error": parsed.error,
                    "judged_correct": parsed.judged_correct,
                    "judge_reason": parsed.judge_reason,
                }
                if parsed is not None
                else None
            ),
            "transition": asdict(transition) if transition else None,
            "idle_reason": idle_reason,
            "decision_required": event is not None,
            "model": result.model if result else None,
            "latency_ms": result.latency_ms if result else None,
            "usage": result.usage if result else {},
            "response_id": result.response_id if result else None,
            "backend_metadata": result.metadata if result else {},
            "model_input": model_input,
            "history": {
                "scope": "current_turn",
                "action_history_before": list(action_history_before),
                "action_history_after": list(action_history_after),
                "memory": memory_snapshot.audit_dict(),
            },
            "judge": (
                {
                    "configured": judge_result.configured,
                    "correct": judge_result.correct,
                    "reason": judge_result.reason,
                    "raw_output": judge_result.raw_output,
                }
                if judge_result is not None
                else None
            ),
            "logged_at": utc_now(),
        }

    def event(self, runtime: EventRuntime) -> dict[str, Any]:
        return {
            "record_type": "event_terminal",
            "run_id": self.run_id,
            "video_id": runtime.spec.video_id,
            "event_id": runtime.spec.event_id,
            "source_event": _source_event(
                runtime.spec,
                judge_model=self.config.judge_model,
            ),
            "terminal_reason": runtime.terminal_reason,
            "first_non_wait_raw": runtime.first_non_wait_raw,
            "ask_time_s": runtime.ask_time_s,
            "ask_option_id": runtime.ask_option_id,
            "ask_text": runtime.ask_text,
            "ask_judged_correct": runtime.ask_judged_correct,
            "ask_judge_reason": runtime.ask_judge_reason,
            "selected_view_id": runtime.selected_view_id,
            "reveal_at_s": runtime.reveal_at_s,
            "answer_window_end_s": runtime.answer_window_end_s,
            "answer_time_s": runtime.answer_time_s,
            "answer_option_id": runtime.answer_option_id,
            "answer_text": runtime.answer_text,
            "answer_match_normalized": runtime.answer_match_normalized,
            "answer_judged_correct": runtime.answer_judged_correct,
            "answer_judge_reason": runtime.answer_judge_reason,
            "scoring_windows": runtime.scoring_windows(),
            "logged_at": utc_now(),
        }


def _source_event(
    event: TurnSpec,
    *,
    judge_model: str | None = None,
) -> dict[str, Any]:
    def judge_payload(spec: Any) -> dict[str, Any] | None:
        if spec is None:
            return None
        payload = asdict(spec)
        if judge_model:
            payload["model"] = judge_model
        return payload

    return {
        "event_id": event.event_id,
        "segment": {"start_s": event.segment_start_s, "end_s": event.segment_end_s},
        "waiting": (
            asdict(event.effective_waiting) if event.effective_waiting else None
        ),
        "asking": asdict(event.effective_asking),
        "answering": asdict(event.answering) if event.answering else None,
        "question": event.question,
        "question_time_s": event.question_time_s,
        "historical_preference": event.historical_preference,
        "language": event.language,
        "action_options": [asdict(option) for option in event.action_options],
        "answer_options": [asdict(option) for option in event.answer_options],
        "gold": {
            "action_option_id": event.correct_action_option_id,
            "action_option_ids": list(event.valid_action_option_ids),
            "answer_option_id": event.correct_answer_option_id,
            "answer_text": event.answer_text,
            "ground_truth": event.answer_text,
            "entity_ids": list(event.correct_entity_ids),
            "ask_text": event.correct_ask_text,
        },
        "response_modes": {
            "ask": event.ask_mode.value,
            "answer": event.answer_mode.value,
        },
        "judges": {
            "ask": judge_payload(event.ask_judge),
            "answer": judge_payload(event.answer_judge),
        },
        "intervention": {
            "evidence_kind": event.evidence_kind,
            "bundle": asdict(event.evidence) if event.evidence else None,
        },
        "uncertainty_type": event.uncertainty_type,
        "canonical_question": event.canonical_question,
        "task": event.task,
        "question_mode": "blurred_query" if event.canonical_question else "standard",
        "original_event_start_s": event.original_segment_start_s,
        "original_event_end_s": event.original_segment_end_s,
        "effective_event_start_s": event.segment_start_s,
        "effective_event_end_s": event.segment_end_s,
        "dropped_between_events": list(event.dropped_between_events),
        "entity_ids": list(event.entity_ids),
        "visual_prompt_ids": list(event.visual_prompt_ids),
        "metadata": event.source_metadata,
    }

