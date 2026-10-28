"""Model-independent contracts. Model implementations may live in model/ and import these contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, TypedDict

if TYPE_CHECKING:
    from .config import EvaluationConfig
    from .frames import EncodedFrame


STREAMING_PROTOCOL_VERSION = "pvb.streaming_model.v1"
PUBLIC_DECISION_STATE = "ACTIVE"
STREAMING_OPERATIONS = frozenset({"open", "observe", "decide", "reset", "abort", "close"})
AttemptCallback = Callable[[dict[str, object]], None]


@dataclass(frozen=True)
class ModelCallResult:
    call_type: str
    content: str
    model: str
    latency_ms: float
    usage: dict[str, object]
    response_id: str | None
    attempts: int
    raw_content: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class ObservationEvidence(TypedDict):
    kind: str
    delivered_now: bool
    text: str | None
    images: list[EncodedFrame]
    video_frame: EncodedFrame | None


@dataclass(frozen=True)
class StreamObservation:
    video_id: str
    tick_index: int
    timestamp_s: float
    frame: EncodedFrame
    active_turn_id: str | None
    evidence: ObservationEvidence | None = None
    replay: bool = False


@dataclass(frozen=True)
class DecisionOption:
    label: str
    text: str


@dataclass(frozen=True)
class DecisionProtocol:
    # Public lifecycle state only. It must never contain evaluator states
    # derived from annotated ASK/ANSWER scoring windows.
    state: str
    ask_mode: str
    answer_mode: str
    ask_options: tuple[DecisionOption, ...]
    answer_options: tuple[DecisionOption, ...]

    def __post_init__(self) -> None:
        if self.state != PUBLIC_DECISION_STATE:
            raise ValueError(
                "DecisionProtocol.state must not expose evaluator scoring-window state"
            )


@dataclass(frozen=True)
class StreamDecisionRequest:
    video_id: str
    turn_id: str
    timestamp_s: float
    action_history: tuple[str, ...]
    prompt: str
    continuation_prompt: str
    protocol: DecisionProtocol
    max_tokens: int


class StreamingWorkerError(RuntimeError):
    """A terminal failure of one video session."""


class StreamingOOMError(StreamingWorkerError):
    """The external model exhausted memory for this video session."""


class GenerationBackend(ABC):
    def __init__(self, config: EvaluationConfig, *, on_attempt: AttemptCallback | None = None):
        self.config = config
        self.on_attempt = on_attempt

    @abstractmethod
    def complete(self, messages: list[dict[str, Any]], *, max_tokens: int, call_type: str) -> ModelCallResult:
        raise NotImplementedError


class StreamingModelBackend(ABC):
    """One causal video session, with mandatory turn isolation and abort cleanup."""

    on_attempt: AttemptCallback | None = None

    @abstractmethod
    def open_video(self, *, video_id: str, metadata: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def push_observation(self, observation: StreamObservation) -> None:
        raise NotImplementedError

    @abstractmethod
    def decide(self, request: StreamDecisionRequest) -> ModelCallResult:
        raise NotImplementedError

    @abstractmethod
    def reset_context(self, *, video_id: str, metadata: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    def abort_video(self, *, video_id: str, metadata: dict[str, Any] | None = None) -> None:
        raise NotImplementedError

    @abstractmethod
    def close_video(self, *, video_id: str) -> None:
        raise NotImplementedError
