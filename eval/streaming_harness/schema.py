from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .judge_prompts import task_judge_prompt


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str
    video_id: str | None = None
    event_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "video_id": self.video_id,
            "event_id": self.event_id,
        }


@dataclass(frozen=True)
class TimeWindow:
    start_s: float
    end_s: float
    start_frame: int | None = None
    end_frame: int | None = None


@dataclass(frozen=True)
class OptionSpec:
    option_id: str
    text: str
    entity_ids: tuple[str, ...] = ()
    target_view_id: str | None = None


@dataclass(frozen=True)
class ViewSpec:
    view_id: str
    label: str
    uri: str
    role: str
    camera_id: str = ""
    stream_id: str = ""


class ResponseMode(str, Enum):
    CHOICE = "choice"
    OPEN = "open"


class VisualEvidenceKind(str, Enum):
    IMAGE = "image"
    VIDEO = "video"
    SYNCHRONIZED_VIEW = "synchronized_view"


class EvidenceTimebase(str, Enum):
    NONE = "none"
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class EvidencePresentation(str, Enum):
    APPEND = "append"
    REPLACE_STREAM = "replace_stream"
    SWITCH_STREAM = "switch_stream"


DEFAULT_OPEN_RESPONSE_JUDGE_PROMPT = """You are a strict semantic-equivalence judge for a video-question benchmark. You receive labeled Question, Candidate, and Reference text. Return exactly 1 when the candidate directly answers the question with the same meaning as the reference, allowing concise paraphrases, harmless tense changes, and equivalent referring expressions. Return exactly 0 if it is missing required information, contradicts the reference, adds a materially unsupported claim, answers a different question, or is ambiguous. Return only that single digit. Judge meaning only; do not require word-for-word identity and do not use outside knowledge."""


@dataclass(frozen=True)
class JudgeSpec:
    model: str = "gemini-3.8-flash"
    prompt: str = DEFAULT_OPEN_RESPONSE_JUDGE_PROMPT

    @property
    def configured(self) -> bool:
        return bool(self.prompt.strip())


@dataclass(frozen=True)
class TextEvidence:
    content: str
    role: str = "user_reply"
    persistent: bool = True


@dataclass(frozen=True)
class VisualEvidence:
    kind: VisualEvidenceKind
    uris: tuple[str, ...] = ()
    timebase: EvidenceTimebase = EvidenceTimebase.NONE
    presentation: EvidencePresentation = EvidencePresentation.APPEND
    start_s: float | None = None


@dataclass(frozen=True)
class EvidenceBundle:
    text: tuple[TextEvidence, ...] = ()
    visual: tuple[VisualEvidence, ...] = ()
    delivery_delay_ticks: int = 1

    @property
    def primary_kind(self) -> str:
        if self.visual:
            return self.visual[0].kind.value
        return "text" if self.text else "none"


@dataclass(frozen=True)
class TurnSpec:
    event_id: str
    video_id: str
    order: int
    segment_start_s: float
    segment_end_s: float
    waiting: TimeWindow | None
    asking: TimeWindow
    question: str
    language: str
    action_options: tuple[OptionSpec, ...]
    answer_options: tuple[OptionSpec, ...]
    correct_action_option_id: str
    correct_answer_option_id: str
    correct_action_option_ids: tuple[str, ...] = ()
    correct_entity_ids: tuple[str, ...] = ()
    entity_ids: tuple[str, ...] = ()
    visual_prompt_ids: tuple[str, ...] = ()
    uncertainty_type: str = "visual_information"
    canonical_question: str = ""
    answering: TimeWindow | None = None
    task: str = ""
    answer_text: str | None = None
    original_segment_start_s: float | None = None
    original_segment_end_s: float | None = None
    dropped_between_events: tuple[str, ...] = ()
    shared_source_boundary: bool = False
    concept_name: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)
    question_time_s: float | None = None
    historical_preference: str | None = None
    initial_view_id: str = ""
    ask_mode: ResponseMode = ResponseMode.CHOICE
    answer_mode: ResponseMode = ResponseMode.CHOICE
    ask_judge: JudgeSpec | None = None
    answer_judge: JudgeSpec | None = None
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
    preserve_post_terminal_observation: bool = False
    correct_ask_text: str | None = None
    waiting_open: TimeWindow | None = None
    evidence_open: EvidenceBundle | None = None

    def __post_init__(self) -> None:
        ask_mode = self.ask_mode
        answer_mode = self.answer_mode
        if (
            answer_mode == ResponseMode.OPEN
            and self.answer_judge is None
        ):
            object.__setattr__(
                self,
                "answer_judge",
                JudgeSpec(
                    prompt=task_judge_prompt(
                        self.task,
                        "answer",
                        DEFAULT_OPEN_RESPONSE_JUDGE_PROMPT,
                    )
                ),
            )
        if (
            ask_mode == ResponseMode.OPEN
            and self.ask_judge is None
        ):
            object.__setattr__(
                self,
                "ask_judge",
                JudgeSpec(
                    prompt=task_judge_prompt(
                        self.task,
                        "ask",
                        DEFAULT_OPEN_RESPONSE_JUDGE_PROMPT,
                    )
                ),
            )
        if self.task == "ICM":
            # Both response modes deliver the complete choice-mode identity
            # mappings, including aliases needed by the answer. Open ASK only
            # selects names already supplied in the question, so it may start
            # at activation without waiting for a visual identity match.
            object.__setattr__(self, "evidence_open", self.evidence)
            if self.ask_mode == ResponseMode.OPEN:
                waiting = self.waiting_open or self.waiting
                start_s = waiting.start_s if waiting is not None else self.asking.start_s
                start_frame = (
                    waiting.start_frame if waiting is not None else self.asking.start_frame
                )
                object.__setattr__(
                    self, "waiting_open", TimeWindow(start_s, start_s, start_frame, start_frame)
                )
        if self.ask_mode == ResponseMode.OPEN and self.evidence_open is not None:
            object.__setattr__(self, "evidence", self.evidence_open)

    @property
    def evidence_kind(self) -> str:
        return self.evidence.primary_kind

    @property
    def evidence_text(self) -> str | None:
        return "\n\n".join(item.content for item in self.evidence.text) or None

    @property
    def evidence_uri(self) -> str:
        visual = self.evidence.visual
        return visual[0].uris[0] if visual and visual[0].uris else ""

    @property
    def start_s(self) -> float:
        waiting = self.effective_waiting
        return waiting.start_s if waiting else self.asking.start_s

    @property
    def effective_waiting(self) -> TimeWindow | None:
        if self.ask_mode == ResponseMode.OPEN and self.waiting_open is not None:
            return self.waiting_open
        return self.waiting

    @property
    def effective_asking(self) -> TimeWindow:
        waiting = self.effective_waiting
        if waiting is None:
            return self.asking
        return TimeWindow(
            start_s=waiting.end_s,
            end_s=self.asking.end_s,
            start_frame=waiting.end_frame,
            end_frame=self.asking.end_frame,
        )

    def action_id_for_label(self, label: str | None) -> str | None:
        return _option_id_for_label(self.action_options, label)

    def answer_id_for_label(self, label: str | None) -> str | None:
        return _option_id_for_label(
            self.answer_options, label, start_index=len(self.action_options)
        )

    @property
    def action_option_labels(self) -> tuple[str, ...]:
        return _option_labels(len(self.action_options))

    @property
    def answer_option_labels(self) -> tuple[str, ...]:
        return _option_labels(
            len(self.answer_options), start_index=len(self.action_options)
        )

    @property
    def valid_action_option_ids(self) -> tuple[str, ...]:
        if self.correct_action_option_ids:
            return self.correct_action_option_ids
        return (self.correct_action_option_id,) if self.correct_action_option_id else ()

    def is_correct_action_option(self, option_id: str | None) -> bool:
        return bool(option_id) and option_id in self.valid_action_option_ids

    def target_view_for_action(self, option_id: str | None) -> str | None:
        if not option_id:
            return None
        return next(
            (
                option.target_view_id
                for option in self.action_options
                if option.option_id == option_id
            ),
            None,
        )

    def first_valid_target_view(self) -> str | None:
        """Return the first target view among the annotated correct ASK options.

        Open-ended ASK responses do not carry a choice label, so the protocol
        still needs a deterministic synchronized view to reveal after a
        semantically correct ASK.  ``valid_action_option_ids`` preserves the
        annotation order for multi-gold viewpoint events.
        """

        for option_id in self.valid_action_option_ids:
            target_view_id = self.target_view_for_action(option_id)
            if target_view_id:
                return target_view_id
        return None

    @property
    def is_open_ask(self) -> bool:
        return self.ask_mode == ResponseMode.OPEN

    @property
    def is_open_answer(self) -> bool:
        return self.answer_mode == ResponseMode.OPEN


@dataclass(frozen=True)
class VideoSpec:
    video_id: str
    source_uri: str
    duration_s: float
    width: int
    height: int
    frame_rate_num: int
    frame_rate_den: int
    events: tuple[TurnSpec, ...]
    views: tuple[ViewSpec, ...] = ()

    @property
    def source_fps(self) -> float:
        return self.frame_rate_num / self.frame_rate_den


@dataclass
class CompiledDataset:
    source_path: Path
    source_sha256: str
    schema_version: str
    videos: tuple[VideoSpec, ...]
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "warning"]


def _option_id_for_label(
    options: tuple[OptionSpec, ...],
    label: str | None,
    *,
    start_index: int = 0,
) -> str | None:
    if label is None or len(label) != 1:
        return None
    index = ord(label) - ord("A") - start_index
    if index < 0 or index >= len(options):
        return None
    return options[index].option_id


def _option_labels(count: int, *, start_index: int = 0) -> tuple[str, ...]:
    return tuple(chr(ord("A") + start_index + index) for index in range(count))
