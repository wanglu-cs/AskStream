from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .actions import ActionType, ParsedAction
from .schema import TurnSpec, ResponseMode


EPSILON = 1e-6


class EventState(str, Enum):
    WAITING = "WAITING"
    ASKING = "ASKING"
    WAITING_AFTER_ASK = "WAITING_AFTER_ASK"
    ANSWERING = "ANSWERING"
    TERMINAL = "TERMINAL"


@dataclass(frozen=True)
class Transition:
    event_id: str
    timestamp_s: float
    state_before: EventState
    state_after: EventState
    action_type: ActionType
    selected_option_id: str | None
    reason: str
    reveal_at_s: float | None = None
    answer_window_end_s: float | None = None
    selected_view_id: str | None = None


@dataclass
class EventRuntime:
    spec: TurnSpec
    tick_seconds: float = 1.0
    ask_start_tolerance_s: float = 0.0
    ask_end_tolerance_s: float = 0.0
    answer_start_tolerance_s: float = 0.0
    answer_end_tolerance_s: float = 0.0
    hard_end_s: float | None = None
    state: EventState | None = None
    terminal_reason: str | None = None
    first_non_wait_raw: str | None = None
    ask_time_s: float | None = None
    ask_option_id: str | None = None
    ask_text: str | None = None
    ask_judged_correct: bool | None = None
    ask_judge_reason: str | None = None
    reveal_at_s: float | None = None
    answer_window_end_s: float | None = None
    answer_time_s: float | None = None
    answer_option_id: str | None = None
    answer_text: str | None = None
    answer_match_normalized: bool | None = None
    answer_judged_correct: bool | None = None
    answer_judge_reason: str | None = None
    selected_view_id: str | None = None

    @property
    def asking_start_s(self) -> float:
        return max(
            self.spec.start_s,
            self.spec.effective_asking.start_s - self.ask_start_tolerance_s,
        )

    @property
    def asking_end_s(self) -> float:
        end = self.spec.asking.end_s + self.ask_end_tolerance_s
        return min(end, self.hard_end_s) if self.hard_end_s is not None else end

    @property
    def answering_start_s(self) -> float:
        annotated = (
            self.spec.answering.start_s
            if self.spec.answering is not None
            else self.spec.asking.start_s
        )
        reveal = self.reveal_at_s if self.reveal_at_s is not None else annotated
        return max(annotated - self.answer_start_tolerance_s, reveal)

    @property
    def answering_end_s(self) -> float:
        window = self.spec.answering or self.spec.asking
        end = window.end_s + self.answer_end_tolerance_s
        return min(end, self.hard_end_s) if self.hard_end_s is not None else end

    def scoring_windows(self) -> dict[str, dict[str, float]]:
        return {
            "asking": {"start_s": self.asking_start_s, "end_s": self.asking_end_s},
            "answering": {"start_s": self.answering_start_s, "end_s": self.answering_end_s},
        }

    def activate(self, timestamp_s: float) -> None:
        if self.state is not None:
            return
        if timestamp_s + EPSILON < self.spec.start_s:
            raise ValueError("event cannot activate before its start")
        self.state = (
            EventState.WAITING
            if timestamp_s + EPSILON < self.asking_start_s
            else EventState.ASKING
        )

    @property
    def terminal(self) -> bool:
        return self.state == EventState.TERMINAL

    def terminate(self, reason: str) -> None:
        self.state = EventState.TERMINAL
        self.terminal_reason = reason

    def expire(self, timestamp_s: float) -> str | None:
        """Close an event before building a model prompt at an expired tick."""

        if self.state == EventState.WAITING and timestamp_s + EPSILON >= self.asking_start_s:
            self.state = EventState.ASKING
        if self.state == EventState.ASKING and timestamp_s + EPSILON >= self.asking_end_s:
            self.terminate("missed_ask")
            return self.terminal_reason
        if (
            self.state == EventState.WAITING_AFTER_ASK
            and timestamp_s + EPSILON >= self.answering_start_s
        ):
            self.state = EventState.ANSWERING
        if (
            self.state in {EventState.WAITING_AFTER_ASK, EventState.ANSWERING}
            and self.answer_window_end_s is not None
            and timestamp_s + EPSILON >= self.answer_window_end_s
        ):
            self.terminate("missed_answer")
            return self.terminal_reason
        return None

    def step(self, timestamp_s: float, action: ParsedAction) -> Transition:
        if self.state is None:
            self.activate(timestamp_s)
        if self.state == EventState.TERMINAL:
            raise RuntimeError("terminal event cannot accept another action")

        if self.state == EventState.WAITING and timestamp_s + EPSILON >= self.asking_start_s:
            self.state = EventState.ASKING
        if (
            self.state == EventState.WAITING_AFTER_ASK
            and timestamp_s + EPSILON >= self.answering_start_s
        ):
            self.state = EventState.ANSWERING

        state_before = self.state
        if state_before == EventState.ASKING and timestamp_s + EPSILON >= self.asking_end_s:
            self.terminate("missed_ask")
            return self._transition(timestamp_s, state_before, action, None, "missed_ask")
        if state_before in {EventState.WAITING_AFTER_ASK, EventState.ANSWERING} and timestamp_s + EPSILON >= float(self.answer_window_end_s):
            self.terminate("missed_answer")
            return self._transition(timestamp_s, state_before, action, None, "missed_answer")

        if action.type == ActionType.INVALID:
            self._bind_non_wait(action)
            self.terminate("invalid_action")
            return self._transition(timestamp_s, state_before, action, None, "invalid_action")

        if state_before == EventState.WAITING:
            return self._step_waiting(timestamp_s, action, state_before)
        if state_before == EventState.ASKING:
            return self._step_asking(timestamp_s, action, state_before)
        if state_before == EventState.WAITING_AFTER_ASK:
            return self._step_waiting_after_ask(timestamp_s, action, state_before)
        return self._step_answering(timestamp_s, action, state_before)

    def _step_waiting(
        self, timestamp_s: float, action: ParsedAction, state_before: EventState
    ) -> Transition:
        if action.type == ActionType.WAIT:
            return self._transition(timestamp_s, state_before, action, None, "wait")
        self._bind_non_wait(action)
        reason = "early_ask" if action.type == ActionType.ASK else "premature_answer"
        self.terminate(reason)
        option_id = (
            self.spec.action_id_for_label(action.option_label)
            if action.type == ActionType.ASK
            else self.spec.answer_id_for_label(action.option_label)
        )
        return self._transition(timestamp_s, state_before, action, option_id, reason)

    def _step_asking(
        self, timestamp_s: float, action: ParsedAction, state_before: EventState
    ) -> Transition:
        if action.type == ActionType.WAIT:
            return self._transition(timestamp_s, state_before, action, None, "wait")
        self._bind_non_wait(action)
        if action.type == ActionType.ANSWER:
            option_id = self.spec.answer_id_for_label(action.option_label)
            self.answer_time_s = timestamp_s
            self.answer_option_id = option_id
            self.answer_text = action.answer_text
            self.answer_judged_correct = action.judged_correct
            self.answer_judge_reason = action.judge_reason
            self.terminate("premature_answer")
            return self._transition(timestamp_s, state_before, action, option_id, "premature_answer")

        option_id = self.spec.action_id_for_label(action.option_label)
        self.ask_time_s = timestamp_s
        self.ask_option_id = option_id
        self.ask_text = action.ask_text
        self.ask_judged_correct = action.judged_correct
        self.ask_judge_reason = action.judge_reason
        ask_correct = (
            action.judged_correct is True
            if self.spec.ask_mode == ResponseMode.OPEN
            else self.spec.is_correct_action_option(option_id)
        )
        if not ask_correct:
            self.terminate("wrong_ask")
            return self._transition(timestamp_s, state_before, action, option_id, "wrong_ask")

        self.selected_view_id = self.spec.target_view_for_action(option_id)
        if self.selected_view_id is None and self.spec.is_open_ask:
            # Open ASK text has no option label to map back to a camera.  Once
            # the semantic judge accepts it, choose the first annotated valid
            # target view so the reveal remains deterministic and reproducible.
            self.selected_view_id = self.spec.first_valid_target_view()
        reveal_at = timestamp_s + self.tick_seconds
        answer_end = self.answering_end_s
        if reveal_at + EPSILON >= answer_end:
            self.terminate("late_ask_no_answer_tick")
            return self._transition(
                timestamp_s, state_before, action, option_id, "late_ask_no_answer_tick"
            )
        self.reveal_at_s = reveal_at
        self.answer_window_end_s = answer_end
        # Type-II events carry an explicit future Answering interval. Type-I
        # events ask inside their answer window and may answer as soon as the
        # requested evidence arrives on the next tick.
        requires_post_ask_wait = self.spec.answering is not None
        self.state = (
            EventState.WAITING_AFTER_ASK
            if requires_post_ask_wait
            else EventState.ANSWERING
        )
        return self._transition(timestamp_s, state_before, action, option_id, "correct_ask")

    def _step_waiting_after_ask(
        self, timestamp_s: float, action: ParsedAction, state_before: EventState
    ) -> Transition:
        if action.type == ActionType.WAIT:
            return self._transition(
                timestamp_s, state_before, action, None, "wait_after_ask"
            )
        if action.type == ActionType.ASK:
            self.terminate("redundant_ask")
            return self._transition(
                timestamp_s, state_before, action, None, "redundant_ask"
            )
        self.answer_time_s = timestamp_s
        self.answer_text = action.answer_text
        self.answer_judged_correct = action.judged_correct
        self.answer_judge_reason = action.judge_reason
        self.terminate("premature_answer")
        return self._transition(
            timestamp_s, state_before, action, None, "premature_answer"
        )

    def _step_answering(
        self, timestamp_s: float, action: ParsedAction, state_before: EventState
    ) -> Transition:
        if timestamp_s + EPSILON < float(self.reveal_at_s):
            self.terminate("action_before_reveal")
            return self._transition(timestamp_s, state_before, action, None, "action_before_reveal")
        if action.type == ActionType.WAIT:
            return self._transition(timestamp_s, state_before, action, None, "wait_after_reveal")
        if action.type == ActionType.ASK:
            option_id = self.spec.action_id_for_label(action.option_label)
            self.terminate("redundant_ask")
            return self._transition(timestamp_s, state_before, action, option_id, "redundant_ask")

        if self.spec.answer_mode == ResponseMode.OPEN:
            self.answer_time_s = timestamp_s
            self.answer_text = action.answer_text
            self.answer_judged_correct = action.judged_correct
            self.answer_judge_reason = action.judge_reason
            self.answer_match_normalized = bool(action.answer_text) and action.judged_correct is True
            reason = "correct_answer" if self.answer_match_normalized else "wrong_answer"
            self.terminate(reason)
            return self._transition(timestamp_s, state_before, action, None, reason)

        option_id = self.spec.answer_id_for_label(action.option_label)
        self.answer_time_s = timestamp_s
        self.answer_option_id = option_id
        reason = (
            "correct_answer"
            if option_id == self.spec.correct_answer_option_id
            else "wrong_answer"
        )
        self.terminate(reason)
        return self._transition(timestamp_s, state_before, action, option_id, reason)

    def _bind_non_wait(self, action: ParsedAction) -> None:
        if self.first_non_wait_raw is None:
            self.first_non_wait_raw = action.raw

    def _transition(
        self,
        timestamp_s: float,
        state_before: EventState,
        action: ParsedAction,
        selected_option_id: str | None,
        reason: str,
    ) -> Transition:
        assert self.state is not None
        return Transition(
            event_id=self.spec.event_id,
            timestamp_s=timestamp_s,
            state_before=state_before,
            state_after=self.state,
            action_type=action.type,
            selected_option_id=selected_option_id,
            reason=reason,
            reveal_at_s=self.reveal_at_s,
            answer_window_end_s=self.answer_window_end_s,
            selected_view_id=self.selected_view_id,
        )
