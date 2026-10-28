from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class ActionType(str, Enum):
    WAIT = "WAIT"
    ASK = "ASK"
    ANSWER = "ANSWER"
    INVALID = "INVALID"


@dataclass(frozen=True)
class ParsedAction:
    type: ActionType
    raw: str
    option_label: str | None = None
    ask_text: str | None = None
    answer_text: str | None = None
    error: str | None = None
    judged_correct: bool | None = None
    judge_reason: str | None = None


WAIT_PATTERN = re.compile(r"^<WAIT>$")
# Keep the action envelope unambiguous: payloads may contain arbitrary text,
# but not a second angle-bracket delimiter or a line break that could turn one
# model response into multiple protocol actions.
ASK_PATTERN = re.compile(r"^<ASK:([^<>\n]+)>$")
ANSWER_PATTERN = re.compile(r"^<ANSWER:([^<>\n]+)>$")


def normalize_answer_text(value: str | None) -> str:
    """Normalize free-text answers without changing their meaning."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = re.sub(r"[\s\u3000]+", " ", text)
    text = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("P")
    )
    return re.sub(r"\s+", " ", text).strip()


def parse_action(
    raw_output: str,
    *,
    allow_open_ask: bool = False,
    allow_open_answer: bool = False,
) -> ParsedAction:
    """Parse exactly one protocol action without repairing model output."""

    raw = raw_output.strip()
    if WAIT_PATTERN.fullmatch(raw):
        return ParsedAction(ActionType.WAIT, raw)
    match = ASK_PATTERN.fullmatch(raw)
    if match:
        value = match.group(1).strip()
        if allow_open_ask and value:
            return ParsedAction(ActionType.ASK, raw, ask_text=value)
        if len(value) == 1 and value.isascii() and value.isalpha() and value.isupper():
            return ParsedAction(ActionType.ASK, raw, option_label=value)
        return ParsedAction(
            ActionType.INVALID,
            raw,
            error="ASK must contain one option letter unless open ASK is enabled",
        )
    match = ANSWER_PATTERN.fullmatch(raw)
    if match:
        value = match.group(1).strip()
        if not value:
            return ParsedAction(
                ActionType.INVALID,
                raw,
                error="ANSWER text must not be empty",
            )
        if allow_open_answer:
            return ParsedAction(ActionType.ANSWER, raw, answer_text=value)
        if len(value) == 1 and value.isascii() and value.isalpha() and value.isupper():
            return ParsedAction(ActionType.ANSWER, raw, option_label=value)
        return ParsedAction(
            ActionType.INVALID,
            raw,
            error="ANSWER must contain one uppercase option letter unless open ANSWER is enabled",
        )
    return ParsedAction(
        ActionType.INVALID,
        raw,
        error="output must be exactly <WAIT>, <ASK:X>, or <ANSWER:value>",
    )
