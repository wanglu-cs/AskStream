"""Select response formats without changing annotated reference semantics."""

from dataclasses import replace

from .schema import CompiledDataset, ResponseMode, TurnSpec

EVALUATION_MODES = ("multi-choice", "open-ended")


def apply_evaluation_mode(dataset: CompiledDataset, mode: str | None) -> CompiledDataset:
    if mode is None:
        return dataset
    if mode not in EVALUATION_MODES:
        raise ValueError(f"unsupported evaluation mode: {mode!r}")

    def convert(turn: TurnSpec) -> TurnSpec:
        open_ended = mode == "open-ended"
        ask_reference = turn.correct_ask_text or "\n".join(
            option.text for option in turn.action_options
            if option.option_id in turn.valid_action_option_ids
        )
        answer_reference = turn.answer_text or next(
            (option.text for option in turn.answer_options
             if option.option_id == turn.correct_answer_option_id),
            None,
        )
        # AUQ supplies open reference answers, not multiple-choice distractors.
        return replace(
            turn,
            ask_mode=ResponseMode.OPEN if open_ended else ResponseMode.CHOICE,
            answer_mode=(ResponseMode.OPEN if open_ended or not turn.answer_options
                         else ResponseMode.CHOICE),
            correct_ask_text=ask_reference,
            answer_text=answer_reference,
        )

    return replace(dataset, videos=tuple(
        replace(video, events=tuple(convert(turn) for turn in video.events))
        for video in dataset.videos
    ))
