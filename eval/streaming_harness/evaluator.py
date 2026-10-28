from __future__ import annotations

from dataclasses import dataclass, field

from .actions import ActionType, ParsedAction, parse_action
from .interfaces import (
    DecisionOption,
    DecisionProtocol,
    GenerationBackend,
    ModelCallResult,
    PUBLIC_DECISION_STATE,
    StreamDecisionRequest,
    StreamingModelBackend,
)
from .config import EvaluationConfig
from .frames import EncodedFrame
from .judge import JudgeResult, OpenEndedJudge
from .memory import MemorySnapshot
from .prompts import (
    build_stream_continuation_prompt,
    build_tick_user_content,
    system_prompt_for_event,
)
from .protocol import EventRuntime, EventState, Transition


@dataclass(frozen=True)
class TurnTickInput:
    timestamp_s: float
    frames: tuple[EncodedFrame, ...]
    memory: MemorySnapshot
    evidence_text: str | None = None
    evidence_text_delivered_now: bool = False
    evidence_images: tuple[EncodedFrame, ...] = ()
    evidence_video_frame: EncodedFrame | None = None
    visual_reveal_judge_frame: EncodedFrame | None = None
    viewpoint_judge_frames: tuple[tuple[str, EncodedFrame], ...] = ()
    evidence_video_frames: tuple[EncodedFrame, ...] = ()
    answer_judge_frames: tuple[EncodedFrame, ...] = ()


@dataclass(frozen=True)
class TurnTickOutput:
    result: ModelCallResult
    parsed: ParsedAction
    transition: Transition
    judge: JudgeResult | None
    history_before: tuple[str, ...]
    history_after: tuple[str, ...]
    memory: MemorySnapshot
    model_input: dict[str, object]


@dataclass
class TurnEvaluator:
    """Evaluate one active turn while keeping turn-local history isolated."""

    config: EvaluationConfig
    model: GenerationBackend | StreamingModelBackend
    judge: OpenEndedJudge = field(default_factory=OpenEndedJudge)
    _runtime: EventRuntime | None = None
    _action_history: list[str] = field(default_factory=list)

    @property
    def action_history(self) -> tuple[str, ...]:
        return tuple(self._action_history)

    def bind(self, runtime: EventRuntime) -> None:
        if self._runtime is not None:
            raise RuntimeError("cannot bind a second turn before resetting the evaluator")
        self._runtime = runtime
        self._action_history.clear()

    def reset(self) -> None:
        self._runtime = None
        self._action_history.clear()

    def evaluate(self, tick: TurnTickInput) -> TurnTickOutput:
        runtime = self._runtime
        if runtime is None:
            raise RuntimeError("turn evaluator has no active runtime")
        history_before = tuple(self._action_history)
        content = build_tick_user_content(
            timestamp_s=tick.timestamp_s,
            frames=list(tick.frames),
            event=runtime.spec,
            # Model-facing prompts depend only on whether this interaction has
            # completed ASK. They never receive annotated scoring-window state.
            post_ask=runtime.reveal_at_s is not None,
            memory_text=tick.memory.text,
            action_history=history_before,
            evidence_text=tick.evidence_text,
            evidence_text_delivered_now=tick.evidence_text_delivered_now,
            evidence_images=list(tick.evidence_images),
            evidence_video_frame=tick.evidence_video_frame,
            evidence_video_frames=list(tick.evidence_video_frames),
        )
        messages = [
            {"role": "system", "content": system_prompt_for_event(runtime.spec)},
            {"role": "user", "content": content},
        ]
        ordered_images = [
            *tick.frames,
            *tick.evidence_images,
            *(tick.evidence_video_frames or (
                (tick.evidence_video_frame,)
                if tick.evidence_video_frame is not None
                else ()
            )),
        ]
        if isinstance(self.model, StreamingModelBackend):
            prompt = "\n\n".join(
                str(item.get("text") or "")
                for item in content
                if item.get("type") == "text" and item.get("text")
            )
            request = StreamDecisionRequest(
                    video_id=runtime.spec.video_id,
                    turn_id=runtime.spec.event_id,
                    timestamp_s=tick.timestamp_s,
                    action_history=history_before,
                    prompt=(
                        system_prompt_for_event(runtime.spec)
                        + "\n\n"
                        + prompt
                    ),
                    continuation_prompt=build_stream_continuation_prompt(
                        timestamp_s=tick.timestamp_s,
                        event=runtime.spec,
                        post_ask=runtime.reveal_at_s is not None,
                        evidence_text=tick.evidence_text,
                        evidence_delivered_now=tick.evidence_text_delivered_now
                        or bool(tick.evidence_images)
                        or tick.evidence_video_frame is not None,
                    ),
                    protocol=DecisionProtocol(
                        # The evaluator's EventState is derived from annotated
                        # scoring windows. Exposing WAITING/ASKING/ANSWERING to
                        # the evaluated backend would reveal when an action is
                        # considered valid. Keep the wire field for protocol
                        # compatibility, but publish only a non-oracle state.
                        state=PUBLIC_DECISION_STATE,
                        ask_mode=runtime.spec.ask_mode.value,
                        answer_mode=runtime.spec.answer_mode.value,
                        ask_options=tuple(DecisionOption(label, option.text) for label, option in
                                          zip(runtime.spec.action_option_labels, runtime.spec.action_options)),
                        answer_options=tuple(DecisionOption(label, option.text) for label, option in
                                             zip(runtime.spec.answer_option_labels, runtime.spec.answer_options)),
                    ),
                    max_tokens=self.config.action_max_tokens,
                )
            result = self.model.decide(request)
            model_input: dict[str, object] = {
                "interface": "native_video_session",
                "decision_request": {
                    "video_id": request.video_id,
                    "turn_id": runtime.spec.event_id,
                    "timestamp_s": request.timestamp_s,
                    "state": request.protocol.state,
                    "max_tokens": request.max_tokens,
                    "prompt": request.prompt,
                    "continuation_prompt": request.continuation_prompt,
                    "action_history": list(request.action_history),
                    "memory": tick.memory.audit_dict(),
                },
                "current_observation": tick.frames[-1].audit_dict(),
                "evidence_images": [
                    frame.audit_dict() for frame in tick.evidence_images
                ],
                "evidence_video_frame": (
                    tick.evidence_video_frame.audit_dict()
                    if tick.evidence_video_frame is not None
                    else None
                ),
                "retained_evidence_video_frames": [
                    frame.audit_dict() for frame in tick.evidence_video_frames
                ],
            }
        else:
            result = self.model.complete(
                messages,
                max_tokens=self.config.action_max_tokens,
                call_type="action",
            )
            model_input = {
                "interface": "multimodal_messages",
                "messages": _audit_messages(messages, ordered_images),
            }
        parsed = parse_action(
            result.content,
            allow_open_ask=runtime.spec.is_open_ask,
            allow_open_answer=runtime.spec.is_open_answer,
        )
        judgeable_now = (
            parsed.type == ActionType.ASK and runtime.state == EventState.ASKING
        ) or (
            parsed.type == ActionType.ANSWER
            and runtime.state == EventState.ANSWERING
        )
        if judgeable_now:
            parsed, judge_result = self.judge.judge_action(
                runtime.spec,
                parsed,
                frames=tick.frames,
                visual_reveal_frame=tick.visual_reveal_judge_frame,
                viewpoint_frames=tick.viewpoint_judge_frames,
                answer_frames=tick.answer_judge_frames,
                evidence_video_frames=tick.evidence_video_frames or (
                    (tick.evidence_video_frame,) if tick.evidence_video_frame is not None else ()
                ),
                evidence_images=tick.evidence_images,
                evidence_text=tick.evidence_text,
            )
        else:
            judge_result = None
        transition = runtime.step(tick.timestamp_s, parsed)
        self._action_history.append(
            f"[{tick.timestamp_s:.3f}s] {result.content.strip()}"
        )
        return TurnTickOutput(
            result=result,
            parsed=parsed,
            transition=transition,
            judge=judge_result,
            history_before=history_before,
            history_after=tuple(self._action_history),
            memory=tick.memory,
            model_input=model_input,
        )


def _audit_messages(
    messages: list[dict[str, object]], images: list[EncodedFrame]
) -> list[dict[str, object]]:
    """Preserve the exact prompt structure while replacing base64 with hashes."""

    image_index = 0
    audited: list[dict[str, object]] = []
    for message in messages:
        raw_content = message.get("content")
        if not isinstance(raw_content, list):
            audited.append(dict(message))
            continue
        audited_content: list[dict[str, object]] = []
        for raw_item in raw_content:
            item = dict(raw_item)
            if item.get("type") == "image_url":
                if image_index >= len(images):
                    raise RuntimeError("prompt image audit is missing an encoded frame")
                audited_content.append(
                    {
                        "type": "image_url",
                        "image": images[image_index].audit_dict(),
                    }
                )
                image_index += 1
            else:
                audited_content.append(item)
        audited.append({**message, "content": audited_content})
    if image_index != len(images):
        raise RuntimeError("prompt image audit contains unused encoded frames")
    return audited


__all__ = ["TurnEvaluator", "TurnTickInput", "TurnTickOutput"]
