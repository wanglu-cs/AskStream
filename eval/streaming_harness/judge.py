from __future__ import annotations


import base64
import mimetypes
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Sequence

from .actions import ActionType, ParsedAction
from .interfaces import GenerationBackend, ModelCallResult
from .schema import (
    DEFAULT_OPEN_RESPONSE_JUDGE_PROMPT,
    JudgeSpec,
    ResponseMode,
    TurnSpec,
)


DEFAULT_JUDGE_MODEL = "gemini-3.8-flash"
OPEN_ENDED_JUDGE_PROMPT = DEFAULT_OPEN_RESPONSE_JUDGE_PROMPT
ASK_TIME_FRAME_JUDGE_TASKS = frozenset(
    {
        "KVEM",
        "VIM",
        "AUQ",
        "WUQ",
    }
)
OPTION_IMAGE_JUDGE_TASKS = frozenset(
    {"UPM", "UPC"}
)
ANSWER_TIME_FRAME_JUDGE_TASKS = frozenset(
    {"KVEM", "VIM", "WUQ", "ICM", "PACM", "UPM", "UPC"}
)
VISUAL_FRAME_JUDGE_TASKS = (
    ASK_TIME_FRAME_JUDGE_TASKS
    | OPTION_IMAGE_JUDGE_TASKS
    | ANSWER_TIME_FRAME_JUDGE_TASKS
)


@dataclass(frozen=True)
class JudgeResult:
    configured: bool
    correct: bool | None
    reason: str
    raw_output: str | None = None
    model_result: ModelCallResult | None = None


class OpenEndedJudge:
    """Judge open ASK/ANSWER responses with task-specific semantic rubrics."""

    def __init__(self, backend: GenerationBackend | None = None) -> None:
        self.backend = backend

    def evaluate(
        self,
        *,
        kind: str,
        candidate: str,
        reference: str,
        question: str,
        spec: JudgeSpec,
        task: str | None = None,
        metadata: dict[str, Any] | None = None,
        frames: Sequence[Any] = (),
        frame_labels: Sequence[str] = (),
        evidence_text: str | None = None,
    ) -> JudgeResult:
        normalized_task = task
        if kind == "answer" and normalized_task == "AUQ":
            correct = bool(reference.strip()) and candidate.strip() == reference.strip()
            return JudgeResult(True, correct, "exact_reminder_match" if correct else "exact_reminder_mismatch")
        needs_visual_context = (
            kind == "ask"
            and normalized_task
            in (ASK_TIME_FRAME_JUDGE_TASKS | OPTION_IMAGE_JUDGE_TASKS)
        ) or (
            kind == "answer"
            and normalized_task in ANSWER_TIME_FRAME_JUDGE_TASKS
        )
        if not spec.configured or self.backend is None:
            raise ValueError("open response evaluation requires a judge prompt and backend")
        input_text = _judge_text_input(
            task=normalized_task,
            kind=kind,
            question=question,
            reference=reference,
            candidate=candidate,
        )
        if kind == "answer" and evidence_text:
            input_text += f"\n\nDelivered user reply / definitions:\n{evidence_text}"
        user_content: Any = input_text
        if needs_visual_context:
            usable_frames = (
                [
                    frame
                    for frame in frames
                    if getattr(frame, "data_url", None)
                ]
                if (kind == "ask" and normalized_task in ASK_TIME_FRAME_JUDGE_TASKS)
                or (kind == "answer" and normalized_task in ANSWER_TIME_FRAME_JUDGE_TASKS)
                else []
            )
            option_image_urls = (
                _metadata_image_urls(metadata)
                if kind == "ask" and normalized_task in OPTION_IMAGE_JUDGE_TASKS
                else ()
            )
            if (
                kind == "ask" and normalized_task in OPTION_IMAGE_JUDGE_TASKS
                and len(option_image_urls) < 2
            ):
                return JudgeResult(
                    True,
                    None,
                    "insufficient_option_images_for_preference_judge",
                )
            if kind == "ask" and normalized_task in OPTION_IMAGE_JUDGE_TASKS:
                expected_image_count = _metadata_integer(
                    metadata,
                    "option_image_count",
                )
                if (
                    expected_image_count is None
                    or len(option_image_urls) != expected_image_count
                ):
                    return JudgeResult(
                        True,
                        None,
                        "unaligned_option_images_for_preference_judge",
                    )
                correct_image_index = _metadata_integer(
                    metadata,
                    "correct_option_image_index",
                )
                if (
                    correct_image_index is None
                    or not 0 <= correct_image_index < len(option_image_urls)
                ):
                    return JudgeResult(
                        True,
                        None,
                        "missing_correct_option_image_for_preference_judge",
                    )
                input_text = _preference_judge_text_input(
                    candidate=candidate,
                    image_count=len(option_image_urls),
                    correct_image_index=correct_image_index,
                )
            user_content: list[dict[str, Any]] = []
            labeled_frames = (
                list(zip(frame_labels, usable_frames))
                if len(frame_labels) == len(usable_frames)
                else [(f"Auxiliary video frame {index}", frame)
                      for index, frame in enumerate(usable_frames, start=1)]
            )
            if frame_labels and len(frame_labels) == len(usable_frames):
                user_content.append(
                    {
                        "type": "text",
                        "text": (
                            "[VISUAL EVIDENCE FOR JUDGING]\n"
                            "Use the labels to distinguish observed video frames, "
                            "delivered definitions, and judge-only reference images."
                            if kind == "answer" else
                            "[AUXILIARY JUDGE-ONLY VIDEO FRAMES]\n"
                            "These frames are visual aids for judging the candidate; "
                            "they are not part of the tested model's prompt."
                        ),
                    }
                )
            for label, frame in labeled_frames:
                if frame_labels and len(frame_labels) == len(usable_frames):
                    user_content.append(
                        {"type": "text", "text": f"[{label}]"}
                    )
                user_content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": frame.data_url},
                    }
                )
            user_content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": image_url},
                }
                for image_url in option_image_urls
            )
            if kind == "answer" and normalized_task in OPTION_IMAGE_JUDGE_TASKS:
                user_content.extend([
                    {"type": "text", "text": (
                        "[CORRECT ANSWER OPTION IMAGE - JUDGE ONLY]\n"
                        "This image depicts the annotated correct entity/outfit. "
                        "Its position in this prompt is not a video outfit number. "
                        "Use visible video labels to resolve numbered answers."
                    )},
                    {"type": "image_url", "image_url": {
                        "url": _correct_option_image_url(metadata),
                    }},
                ])
            user_content.append({"type": "text", "text": input_text})
        result = self.backend.complete(
            [
                {
                    "role": "system",
                    "content": spec.prompt,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            max_tokens=1024,
            call_type=f"{kind}_judge",
        )
        normalized = result.content.strip()
        if normalized == "1":
            return JudgeResult(True, True, "llm_judge_correct", result.content, result)
        if normalized == "0":
            return JudgeResult(True, False, "llm_judge_incorrect", result.content, result)
        return JudgeResult(True, None, "invalid_judge_output", result.content, result)

    def judge_action(
        self,
        turn: TurnSpec,
        action: ParsedAction,
        *,
        frames: Sequence[Any] = (),
        visual_reveal_frame: Any | None = None,
        viewpoint_frames: Sequence[tuple[str, Any]] = (),
        answer_frames: Sequence[Any] = (),
        evidence_video_frames: Sequence[Any] = (),
        evidence_images: Sequence[Any] = (),
        evidence_text: str | None = None,
    ) -> tuple[ParsedAction, JudgeResult | None]:
        metadata = _turn_judge_metadata(turn)
        normalized_task = turn.task
        if (
            action.type == ActionType.ASK
            and turn.ask_mode == ResponseMode.OPEN
            and normalized_task == "KVEM"
            and visual_reveal_frame is None
        ):
            result = JudgeResult(
                True,
                None,
                "missing_correct_entity_frame_for_visual_reveal_judge",
            )
            return (
                replace(
                    action,
                    judged_correct=result.correct,
                    judge_reason=result.reason,
                ),
                result,
            )
        judge_frames: Sequence[Any] = (
            (visual_reveal_frame,)
            if normalized_task == "KVEM"
            and visual_reveal_frame is not None
            else frames[-1:]
            if normalized_task != "KVEM" and frames
            else ()
        )
        judge_frame_labels: Sequence[str] = ()
        if normalized_task == "VIM" and viewpoint_frames:
            judge_frame_labels = tuple(label for label, _frame in viewpoint_frames)
            judge_frames = tuple(frame for _label, frame in viewpoint_frames)
        if action.type == ActionType.ASK and turn.ask_mode == ResponseMode.OPEN:
            result = self.evaluate(
                kind="ask",
                candidate=action.ask_text or "",
                reference=turn.correct_ask_text or "",
                question=turn.question,
                spec=turn.ask_judge or JudgeSpec(),
                task=turn.task,
                metadata=metadata,
                frames=judge_frames,
                frame_labels=judge_frame_labels,
            )
        elif action.type == ActionType.ANSWER and turn.answer_mode == ResponseMode.OPEN:
            # Only observed frames and evidence already delivered to this turn
            # belong here. ASK-only oracle views/reveals must not be reused.
            main_frames = tuple(answer_frames or frames)
            if normalized_task in OPTION_IMAGE_JUDGE_TASKS:
                main_frames = _preference_answer_frames(main_frames, frames)
            judge_frames = main_frames
            answer_frame_labels = tuple(
                f"MAIN VIDEO | t={frame.timestamp_s:.3f}s | {frame.source}"
                for frame in main_frames
            )
            if normalized_task == "PACM":
                judge_frames = (*main_frames, *evidence_video_frames)
                answer_frame_labels = (
                    *(f"MAIN VIDEO | t={frame.timestamp_s:.3f}s | {frame.source}" for frame in main_frames),
                    *(f"ACTION DEFINITION VIDEO | clip t={frame.timestamp_s:.3f}s | {frame.source}" for frame in evidence_video_frames),
                )
            elif normalized_task == "ICM":
                judge_frames = (*main_frames, *evidence_images)
                answer_frame_labels = (
                    *answer_frame_labels,
                    *(f"DELIVERED IDENTITY DEFINITION IMAGE {index} | {frame.source}"
                      for index, frame in enumerate(evidence_images, 1)),
                )
            result = self.evaluate(
                kind="answer",
                candidate=action.answer_text or "",
                reference=turn.answer_text or "",
                question=turn.question,
                spec=turn.answer_judge or JudgeSpec(),
                task=turn.task,
                metadata=metadata,
                frames=judge_frames,
                frame_labels=answer_frame_labels,
                evidence_text=evidence_text,
            )
        else:
            return action, None
        return (
            replace(
                action,
                judged_correct=result.correct,
                judge_reason=result.reason,
            ),
            result,
        )


_QUESTION_CANDIDATE_ASK_TASKS = frozenset({"VIM"})
_QUESTION_RESPONSE_ASK_TASKS = frozenset({"ICM", "PACM"})
_QUESTION_REFERENCE_CANDIDATE_ASK_TASKS = frozenset(
    {"KVEM", "AUQ", "WUQ"}
)
_CANDIDATE_ONLY_ASK_TASKS = frozenset(
    {
        "UPM",
        "UPC",
    }
)


def _judge_text_input(
    *,
    task: str,
    kind: str,
    question: str,
    reference: str,
    candidate: str,
) -> str:
    """Build the minimal labeled text requested by each judge rubric."""

    if kind == "ask" and task in _QUESTION_CANDIDATE_ASK_TASKS:
        return f"Question:\n{question}\n\nCandidate:\n{candidate}"
    if kind == "ask" and task in _QUESTION_RESPONSE_ASK_TASKS:
        return f"Question:\n{question}\n\nResponse:\n{candidate}"
    if task in _QUESTION_RESPONSE_ASK_TASKS:
        return (
            f"Question:\n{question}\n\n"
            f"Response:\n{candidate}\n\n"
            f"Reference:\n{reference}"
        )
    if kind == "ask" and task in _QUESTION_REFERENCE_CANDIDATE_ASK_TASKS:
        return (
            f"Question:\n{question}\n\n"
            f"Candidate:\n{candidate}\n\n"
            f"Reference:\n{reference}"
        )
    if kind == "ask" and task in _CANDIDATE_ONLY_ASK_TASKS:
        return f"Candidate:\n{candidate}"
    return (
        f"Question:\n{question}\n\n"
        f"Candidate:\n{candidate}\n\n"
        f"Reference:\n{reference}"
    )


def _turn_judge_metadata(turn: TurnSpec) -> dict[str, Any]:
    """Expose existing annotation context without creating a second gold schema."""

    correct_option_image_index = next(
        (
            index
            for index, option in enumerate(turn.answer_options)
            if option.option_id == turn.correct_answer_option_id
        ),
        None,
    )
    return {
        "source_metadata": turn.source_metadata,
        "option_image_count": len(turn.answer_options),
        "correct_option_image_index": correct_option_image_index,
        "correct_entity_ids": list(turn.correct_entity_ids),
        "entity_ids": list(turn.entity_ids),
        "visual_prompt_ids": list(turn.visual_prompt_ids),
        "initial_view_id": turn.initial_view_id,
        "evidence_kind": turn.evidence_kind,
        "uncertainty_type": turn.uncertainty_type,
        "concept_name": turn.concept_name,
        "historical_preference": turn.historical_preference,
        "action_options": [asdict(option) for option in turn.action_options],
        "answer_options": [asdict(option) for option in turn.answer_options],
    }


def _metadata_integer(metadata: dict[str, Any] | None, key: str) -> int | None:
    value = (metadata or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _correct_option_image_url(metadata: dict[str, Any] | None) -> str:
    """Select by annotation position before loading, without dedup/reordering."""

    metadata = metadata or {}
    images = (metadata.get("source_metadata") or {}).get("option_images")
    index = _metadata_integer(metadata, "correct_option_image_index")
    count = _metadata_integer(metadata, "option_image_count")
    if (
        not isinstance(images, (list, tuple))
        or len(images) != count
        or index is None
        or not 0 <= index < len(images)
        or not isinstance(images[index], str)
    ):
        raise ValueError("preference ANSWER judge requires an aligned correct option image")
    urls = _metadata_image_urls({"image_url": images[index]})
    if len(urls) != 1:
        raise ValueError("preference ANSWER judge cannot load the correct option image")
    return urls[0]


def _preference_answer_frames(
    history: Sequence[Any], recent: Sequence[Any]
) -> tuple[Any, ...]:
    """Retain current detail plus a bounded causal history for video references."""

    max_history = 32
    if len(history) > max_history:
        history = tuple(history[round(i * (len(history) - 1) / (max_history - 1))]
                        for i in range(max_history))
    by_time_and_source = {
        (frame.timestamp_s, frame.source): frame for frame in (*history, *recent)
    }
    return tuple(sorted(by_time_and_source.values(), key=lambda frame: frame.timestamp_s))


def _preference_judge_text_input(
    *,
    candidate: str,
    image_count: int,
    correct_image_index: int,
) -> str:
    return (
        "Option Image Order:\n"
        f"The {image_count} supplied images are Option Image 1 through "
        f"Option Image {image_count} in the order they appear.\n\n"
        f"Correct Image:\nOption Image {correct_image_index + 1}\n\n"
        f"Candidate:\n{candidate}"
    )


def _metadata_image_urls(metadata: dict[str, Any] | None) -> tuple[str, ...]:
    """Collect renderable option-image URLs from annotation metadata.

    Preference annotations may expose images under different enrichment keys
    (for example ``image_url``, ``data_url``, or nested ``option_images``).
    Local option-image files are encoded as data URLs at call time so an API
    judge can render them. Metadata itself is never included in the judge's
    text input.
    """

    urls: list[str] = []
    seen: set[str] = set()

    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                visit(child_value, str(child_key).strip().lower())
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)
            return
        if not isinstance(value, str):
            return
        candidate = value.strip()
        if key not in {
            "image_url",
            "data_url",
            "image_uri",
            "image",
            "option_image",
            "option_images",
            "image_urls",
            "data_urls",
        }:
            return
        if candidate in seen:
            return
        seen.add(candidate)
        if candidate.startswith(("data:image/", "http://", "https://")):
            urls.append(candidate)
            return
        image_path = Path(candidate)
        if not image_path.is_file():
            return
        mime_type = mimetypes.guess_type(image_path.name)[0]
        if not mime_type or not mime_type.startswith("image/"):
            return
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        urls.append(f"data:{mime_type};base64,{encoded}")

    visit(metadata or {})
    return tuple(urls)


__all__ = [
    "ANSWER_TIME_FRAME_JUDGE_TASKS",
    "ASK_TIME_FRAME_JUDGE_TASKS",
    "DEFAULT_JUDGE_MODEL",
    "JudgeResult",
    "OPEN_ENDED_JUDGE_PROMPT",
    "OpenEndedJudge",
    "OPTION_IMAGE_JUDGE_TASKS",
    "VISUAL_FRAME_JUDGE_TASKS",
]
