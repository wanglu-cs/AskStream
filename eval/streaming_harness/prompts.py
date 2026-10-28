from __future__ import annotations

from typing import Any, Iterable

from .frames import EncodedFrame
from .schema import TurnSpec, OptionSpec, ResponseMode


PROMPT_VERSION = "askstream-iclr2027-v30-icm-open-at-start"


GENERAL_SYSTEM_PROMPT = """You are a visual assistant that can proactively ask follow-up questions while observing a continuously arriving video stream to answer the user's question. While the video stream continues to arrive, you may choose among WAIT, ASK, and ANSWER. They respectively mean continuing to wait, proactively asking about information that may currently be missing, and answering the user's question. Each question permits only one ASK action and one ANSWER action.

When a question is active, choose one action listed under [AVAILABLE ACTIONS]. For choice actions, such as <ASK:A> or <ANSWER:F>, use the displayed form without adding anything else. For open-ended actions, put the request or answer inside the displayed form.

Decision rules:
1. The current question or the video may or may not contain missing information. If you cannot yet determine whether information is missing, choose <WAIT> to continue receiving subsequent information. If the meaning of the question is already clear but the evidence needed to trigger an answer has not yet appeared, also choose <WAIT>. Note: not knowing the answer does not mean that you should choose <ASK>. Choose <ASK> only when you have clearly confirmed that information essential to answering is missing and cannot be obtained by continuing to observe the video. When the available information satisfies the trigger condition and you can answer the question, choose <ANSWER>.
2. After ASK, you will receive visual or textual information provided by the user.
3. Use [CURRENT EVENT ACTION HISTORY] to determine whether ASK has already happened. These benchmark questions require one correct ASK before ANSWER. Before ASK, use WAIT or ASK; after ASK, use WAIT or ANSWER as shown by the available action forms. The displayed forms describe the output syntax; submitting ANSWER before ASK is premature even when its form is displayed. A task rule about answering once a trigger appears applies after the required ASK.
4. Never infer the answer from your own prior knowledge. Use only the current question, the video stream, the current event's action history, and the evidence delivered after a correct ASK.
5. For choice-based output, use the displayed option action exactly. For open-ended output, begin an ASK action with <ASK: or an ANSWER action with <ANSWER:, write the actual request or answer, and finish with >. Never output a bare free-text request or answer without its ASK or ANSWER envelope; such output is invalid.

FINAL OUTPUT CONSTRAINT: Your entire final response must be exactly one valid action of a type listed under [AVAILABLE ACTIONS]. Output no explanation, reasoning, Markdown, punctuation, timestamp, or additional text before or after that action."""


COMMON_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT


QU_TASK_PROMPT = """Task-specific rules: AUQ
1. If, together with the video content, you confirm that the question is ambiguous, choose <ASK> to identify the specific ambiguous part and request the clarification needed to resolve the question to one unique referent rather than multiple possible referents. Do not make a generic request for more information. After the correct ASK, continue choosing <WAIT> until the trigger condition specified in the question appears, then choose <ANSWER>.
2. AUQ is an exact-reproduction task. Reproduce the specified reminder message exactly, including capitalization and punctuation, without paraphrasing or adding words."""


FPV_TASK_PROMPT = """Task-specific rules: WUQ
1. If the question's trigger condition appears and you confirm that the question contains a visual error because the video content does not match it, choose <ASK> to identify the specific false or incorrect part, stating the visually supported correction or requesting confirmation of it. Do not make a generic request. Before that evidence appears, choose <WAIT>. After the correct ASK, answer when the corrected trigger condition is satisfied."""


PREFERENCE_MISSING_TASK_PROMPT = """Task-specific rules: UPM
1. When you confirm that answering the question requires knowing the user's personal preference and that the answer to the ASK option can uniquely and unambiguously distinguish one visible entity, choose <ASK> to request the concrete preference dimension needed to answer it, such as a particular attribute, style, or choice criterion. Do not ask for preferences generically. After receiving the user's reply to a correct <ASK>, continue choosing <WAIT> until the trigger condition specified in the question appears, then choose <ANSWER>. Incorrect ASKs concern features shared by multiple entities."""


PREFERENCE_MISSING_OPEN_TASK_PROMPT = """Task-specific rules: UPM
1. When you confirm that answering the question requires knowing the user's personal preference, choose <ASK> to request the concrete preference dimension needed to answer it, such as a particular attribute, style, or choice criterion. Do not ask for preferences generically. After receiving the user's reply to a correct <ASK>, continue choosing <WAIT> until the trigger condition specified in the question appears, then choose <ANSWER>."""


PREFERENCE_STALENESS_TASK_PROMPT = """Task-specific rules: UPC
1. When you confirm that the preference mentioned in the question is outdated or conflicts with the current request, and that the answer to the ASK option can uniquely and unambiguously distinguish one visible entity, choose <ASK> to request the concrete preference dimension that needs confirmation or updating. Do not ask the user to restate all preferences. After receiving the user's reply to a correct <ASK>, continue choosing <WAIT> until the trigger condition specified in the question appears, then choose <ANSWER>. Choose the ASK whose answer can uniquely distinguish one visible entity; incorrect ASKs concern features shared by multiple entities."""


PREFERENCE_STALENESS_OPEN_TASK_PROMPT = """Task-specific rules: UPC
1. When you confirm that the preference mentioned in the question is outdated or conflicts with the current request, choose <ASK> to request the concrete preference dimension that needs confirmation or updating. Do not ask the user to restate all preferences. After receiving the user's reply to a correct <ASK>, continue choosing <WAIT> until the trigger condition specified in the question appears, then choose <ANSWER>."""


VISUAL_REVEAL_TASK_PROMPT = """Task-specific rules: KVEM
1. You will receive a video in which some entities are occluded. When you confirm that the relevant entity is occluded, choose <ASK> only to request the missing visual information needed to identify that entity, naming its accurate location or visual referent. Do not merely request an entity already explicitly mentioned in the question, ask for a broad scene description, or ask for an unrelated attribute. Before confirming the occlusion, choose <WAIT>. After the correct ASK, answer when the question's trigger condition is satisfied."""


VIEWPOINT_MISSING_TASK_PROMPT = """Task-specific rules: VIM
1. Once the question's trigger condition has occurred and you confirm that the current viewpoint cannot answer the question, choose <ASK> to request a concrete camera angle, side, or view that you believe can answer it. Any specific viewpoint that would genuinely provide sufficient evidence is valid; vague or unrelated view requests are not. Before confirming the missing viewpoint, choose <WAIT>. After the correct ASK, answer when sufficient evidence is available."""


PERSONAL_ENTITY_TASK_PROMPT = """Task-specific rules: ICM
1. When you confirm that the video content cannot answer the user's question and that the ASK option can uniquely and unambiguously identify the entity referred to by each private entity alias mentioned in the question, choose <ASK> to select a clear personalized definition for every private entity alias referred to in the question. Identify each alias explicitly and do not ask for a generic definition. If the ASK option cannot yet uniquely and unambiguously identify the private entity referred to by each alias, continue choosing <WAIT> until it can. After receiving the definitions, continue choosing <WAIT> until the trigger condition specified in the question appears, then choose <ANSWER>."""


PERSONAL_ENTITY_OPEN_TASK_PROMPT = """Task-specific rules: ICM
1. When the personalized definitions needed to answer the question are missing, choose <ASK> to select the definition for every private entity alias in the question. You may ASK as soon as the question becomes active; do not wait for the people to appear or become visually distinguishable. List every required alias; explicit request wording is optional. After receiving the definitions, continue choosing <WAIT> until the trigger condition appears, then choose <ANSWER>."""


PERSONAL_ACTION_TASK_PROMPT = """Task-specific rules: PACM
1. When the personalized action definitions needed to answer the question are missing, choose the ASK option identifying every required personalized action. The ASK selects action definitions, not a person performing the action. After receiving the definitions, continue choosing <WAIT> until the trigger condition appears, then choose <ANSWER>."""


PERSONAL_ACTION_OPEN_TASK_PROMPT = """Task-specific rules: PACM
1. When the personalized action definitions needed to answer the question are missing, choose <ASK> to select the video definition for every action in the question. List every required action name; explicit request wording is optional. After receiving the definitions, continue choosing <WAIT> until the trigger condition appears, then choose <ANSWER>."""


QU_OPEN_ASK_APPENDIX = """Note: An open-ended ASK must address a specific ambiguity so the user can uniquely identify the intended referent.

Example:
Question: Let me know when that person picks up the cup.
Visible scene: Two people are present, one in white on the left and one in black on the right.
Correct open-ended ASK: <ASK:Do you mean the person in white on the left or the person in black on the right?>"""


FPV_OPEN_ASK_APPENDIX = """Note: An open-ended ASK must identify the specific contradiction between the question and the video, either by stating the visually supported correction or by asking the user to confirm that correction. Both forms are valid.

Example:
Question: Let me know when the person in the red jacket picks up the cup.
Visible scene: The person in question is wearing a blue jacket.
Correct open-ended ASK: <ASK:The question says red jacket, but the person in the video is wearing a blue jacket. Did you mean the person in the blue jacket?>"""


PREFERENCE_MISSING_OPEN_ASK_APPENDIX = """Note: Multiple visible entities can differ in color, material, shape, style, and other dimensions. Ask about a feature whose possible values uniquely identify one entity, not a feature shared by multiple entities.

Example:
Question: Which of these three chairs would suit me best?
Visible scene: The chairs have overlapping colors, but their materials are wood, metal, and upholstery, one chair per material.
Correct open-ended ASK: <ASK:For the chair's material, do you prefer wood, metal, or upholstery?>"""


PREFERENCE_STALENESS_OPEN_ASK_APPENDIX = """Note: Multiple visible entities can have features along several dimensions. Ask for an updated preference on a dimension whose possible values uniquely identify one entity, not a feature shared by multiple entities.

Example:
Question: Which of these three table lamps would suit me best?
Previous preference: The user preferred black lamps, but all three lamps are black.
Visible scene: The lampshades are round, conical, and square, one lamp per shape.
Correct open-ended ASK: <ASK:Do you now prefer a round, conical, or square lampshade?>"""


VISUAL_REVEAL_OPEN_ASK_APPENDIX = """Note: Locate only the occluded entity relevant to the question, not unrelated entities. Do not request the answer-bearing entity by its category name (for example, "show the tea bag"); identify it using its position and visible spatial context instead.

Example:
Question: How many tea bags are on the table?
Visible scene: Several objects are occluded. The relevant occluded object is in the lower left, to the right of a red mug and behind a wooden box; another occluded object in the upper right is unrelated.
Correct open-ended ASK: <ASK:Please reveal the occluded object in the lower left, to the right of the red mug and behind the wooden box.>"""


VIEWPOINT_MISSING_OPEN_ASK_APPENDIX = """Note: Describe the needed viewpoint using camera direction, relative position, angle, and spatial coverage. Do not merely request a view of an entity named in the question.

Example:
Question: Which drawer did the person put the key into?
Visible scene: From the current front-facing view, the person's body blocks the lower front of the table.
Correct open-ended ASK: <ASK:Please show an elevated view from the table's front-right corner, angled toward its left side, covering the entire lower front of the table and the area beneath the tabletop.>"""


PERSONAL_ENTITY_OPEN_ASK_APPENDIX = """Note: An open-ended ASK selects the personalized definitions for every private entity alias in the question. Name every alias. Listing the aliases alone is sufficient; explicit request wording is optional. Do not ask for a generic identity explanation.

Example:
Question: What did {Noni} give {Buddy}?
Visible scene: The relevant people have appeared, but their private alias mappings have not been provided.
Correct open-ended ASK: <ASK:{Noni} and {Buddy}.>
Also correct: <ASK:Please provide the personalized entity definitions for {Noni} and {Buddy}.>"""


PERSONAL_ACTION_OPEN_ASK_APPENDIX = """Note: An open-ended ASK selects the video definitions for every personalized action in the question. Name every action. Listing all required action names alone is sufficient; explicit request wording is optional. Do not ask for a generic action definition.

Example:
Question: Did {Action A} happen before {Action B}?
Visible scene: The personalized video action definitions for {Action A} and {Action B} have not been provided.
Correct open-ended ASK: <ASK:{Action A} and {Action B}.>
Also correct: <ASK:Please provide the video action definitions for {Action A} and {Action B}.>"""


QU_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + QU_TASK_PROMPT
FPV_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + FPV_TASK_PROMPT
PREFERENCE_MISSING_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + PREFERENCE_MISSING_TASK_PROMPT
PREFERENCE_STALENESS_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + PREFERENCE_STALENESS_TASK_PROMPT
VISUAL_REVEAL_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + VISUAL_REVEAL_TASK_PROMPT
VIEWPOINT_MISSING_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + VIEWPOINT_MISSING_TASK_PROMPT
PERSONAL_ENTITY_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + PERSONAL_ENTITY_TASK_PROMPT
PERSONAL_ACTION_SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT + "\n\n" + PERSONAL_ACTION_TASK_PROMPT
SYSTEM_PROMPT = GENERAL_SYSTEM_PROMPT


OPEN_ASK_APPENDICES = {
    "AUQ": QU_OPEN_ASK_APPENDIX,
    "WUQ": FPV_OPEN_ASK_APPENDIX,
    "UPM": PREFERENCE_MISSING_OPEN_ASK_APPENDIX,
    "UPC": PREFERENCE_STALENESS_OPEN_ASK_APPENDIX,
    "KVEM": VISUAL_REVEAL_OPEN_ASK_APPENDIX,
    "VIM": VIEWPOINT_MISSING_OPEN_ASK_APPENDIX,
    "ICM": PERSONAL_ENTITY_OPEN_ASK_APPENDIX,
    "PACM": PERSONAL_ACTION_OPEN_ASK_APPENDIX,
}


SYSTEM_PROMPTS = {
    "AUQ": QU_SYSTEM_PROMPT,
    "WUQ": FPV_SYSTEM_PROMPT,
    "UPM": PREFERENCE_MISSING_SYSTEM_PROMPT,
    "UPC": PREFERENCE_STALENESS_SYSTEM_PROMPT,
    "VIM": VIEWPOINT_MISSING_SYSTEM_PROMPT,
    "ICM": PERSONAL_ENTITY_SYSTEM_PROMPT,
    "PACM": PERSONAL_ACTION_SYSTEM_PROMPT,
    "KVEM": VISUAL_REVEAL_SYSTEM_PROMPT,
}


def system_prompt_for_event(event: TurnSpec) -> str:
    task = event.task
    if task not in SYSTEM_PROMPTS:
        raise ValueError(f"unknown task: {task!r}")
    prompt = SYSTEM_PROMPTS[task]
    if event.ask_mode == ResponseMode.OPEN:
        open_task_prompts = {
            "UPM": PREFERENCE_MISSING_OPEN_TASK_PROMPT,
            "UPC": PREFERENCE_STALENESS_OPEN_TASK_PROMPT,
            "ICM": PERSONAL_ENTITY_OPEN_TASK_PROMPT,
            "PACM": PERSONAL_ACTION_OPEN_TASK_PROMPT,
        }
        if task in open_task_prompts:
            prompt = GENERAL_SYSTEM_PROMPT + "\n\n" + open_task_prompts[task]
        if task in OPEN_ASK_APPENDICES:
            prompt += "\n\n" + OPEN_ASK_APPENDICES[task]
    return prompt


MID_TERM_SYSTEM_PROMPT = """Summarize the supplied streaming-video frames exactly as they are shown. Preserve temporal order, visible objects, actions, scene changes, etc. Return concise factual memory text."""


LONG_TERM_SYSTEM_PROMPT = """Consolidate the supplied streaming-video memory summaries into one chronological memory block. Merge duplicate content and preserve causal order and visible state changes."""


def build_tick_user_content(
    *,
    timestamp_s: float,
    frames: list[EncodedFrame],
    event: TurnSpec | None,
    post_ask: bool = False,
    memory_text: str,
    action_history: Iterable[str],
    evidence_text: str | None = None,
    evidence_text_delivered_now: bool = False,
    evidence_images: list[EncodedFrame] | None = None,
    evidence_video_frame: EncodedFrame | None = None,
    evidence_video_frames: list[EncodedFrame] | None = None,
) -> list[dict[str, Any]]:
    if not frames:
        raise ValueError("at least one sliding-window frame is required")
    actions = "\n".join(action_history) or "None"
    if event is None:
        # Observation-only ticks advance video memory and have no action
        # decision for the model to make.
        question_section = (
            "Summarize the supplied video frames chronologically for video memory. "
            "Do not output an action."
        )
    else:
        before_ask = not post_ask
        # After a successful ASK, the model remains responsible for deciding
        # whether to wait for the annotated Answering window or answer now.
        # An early ANSWER is still evaluated by the protocol (and can be
        # marked premature), but it must not be hidden from the model.
        answer_available = post_ask
        available_actions = ["<WAIT>"]
        if before_ask:
            if event.ask_mode == ResponseMode.CHOICE:
                available_actions.extend(
                    f"<ASK:{chr(ord('A') + index)}>"
                    for index in range(len(event.action_options))
                )
            else:
                available_actions.append("ASK (follow [ASK FORMAT])")
            if event.answer_mode == ResponseMode.CHOICE:
                available_actions.extend(
                    f"<ANSWER:{chr(ord('A') + len(event.action_options) + index)}>"
                    for index in range(len(event.answer_options))
                )
            else:
                available_actions.append("ANSWER (follow [ANSWER FORMAT])")
        elif answer_available:
            if event.answer_mode == ResponseMode.CHOICE:
                available_actions.extend(
                    f"<ANSWER:{chr(ord('A') + len(event.action_options) + index)}>"
                    for index in range(len(event.answer_options))
                )
            else:
                available_actions.append("ANSWER (follow [ANSWER FORMAT])")
        sections = [f"[QUESTION]\n{event.question}"]
        if event.historical_preference:
            sections.append(
                "[USER MEMORY]\n" + event.historical_preference
            )
        sections.append("[AVAILABLE ACTIONS]\n" + "\n".join(available_actions))
        if before_ask:
            if event.ask_mode == ResponseMode.CHOICE:
                sections.append(
                    "[ASK OPTIONS]\n"
                    + format_options(event.action_options)
                )
            else:
                sections.append(
                    "[ASK FORMAT]\nBegin with <ASK:, write your actual concise "
                    "information request (or all required definition names for ICM/PACM), and finish with >."
                )
        if event.answer_mode == ResponseMode.CHOICE:
            sections.append(
                "[ANSWER OPTIONS]"
                + "\n"
                + format_options(
                    event.answer_options, start_index=len(event.action_options)
                )
            )
        else:
            sections.append(
                "[ANSWER FORMAT]\nBegin with <ANSWER:, write your actual concise "
                "free-text answer, and finish with >."
            )
        question_section = "\n\n".join(sections)
    header = (
        f"[TIMESTAMP]\n{timestamp_s:.3f}s\n\n"
        f"{question_section}\n\n"
        f"[RECENT SLIDING-WINDOW FRAMES]\n"
        f"The following {len(frames)} frame(s) are ordered from oldest to newest."
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": header}]
    for index, frame in enumerate(frames, start=1):
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"MAIN VIDEO sliding-window frame {index}/{len(frames)}: "
                        f"timestamp={frame.timestamp_s:.3f}s"
                    ),
                },
                {"type": "image_url", "image_url": {"url": frame.data_url}},
            ]
        )
    if evidence_text:
        reply_text = f"[USER REPLY]\n{evidence_text}"
        content.append({"type": "text", "text": reply_text})
    if evidence_images:
        content.append(
            {
                "type": "text",
                "text": (
                    "[REQUESTED IMAGE EVIDENCE - CURRENT EVENT]\n"
                    f"{len(evidence_images)} image(s) follow."
                ),
            }
        )
        for index, evidence_image in enumerate(evidence_images, start=1):
            content.extend(
                [
                    {
                        "type": "text",
                        "text": f"Requested evidence image {index}/{len(evidence_images)}",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": evidence_image.data_url},
                    },
                ]
            )
    definition_frames = evidence_video_frames or (
        [evidence_video_frame] if evidence_video_frame is not None else []
    )
    if definition_frames:
        content.append({"type": "text", "text": (
            "[ACTION DEFINITION VIDEO - INDEPENDENT TIMELINE]\n"
            "These ordered frames are the user-provided action definition, "
            "retained from the start of evidence playback through the latest "
            "available frame. Definition-video timestamps are local to this "
            "separate clip and must not be compared with MAIN VIDEO timestamps."
        )})
        for index, definition_frame in enumerate(definition_frames, 1):
            content.extend([
                {"type": "text", "text": (
                    f"Action definition frame {index}/{len(definition_frames)}: "
                    f"clip timestamp={definition_frame.timestamp_s:.3f}s; "
                    f"source={definition_frame.source}"
                )},
                {"type": "image_url", "image_url": {"url": definition_frame.data_url}},
            ])
    if event is None:
        content.append(
            {
                "type": "text",
                "text": f"[VIDEO MEMORY]\n{memory_text or 'None'}",
            }
        )
    else:
        content.append(
            {
                "type": "text",
                "text": (
                    f"[VIDEO MEMORY]\n"
                    f"{memory_text or 'None'}\n\n"
                    f"[CURRENT EVENT ACTION HISTORY]\n"
                    f"{actions}"
                ),
            }
        )
    return content


def build_stream_continuation_prompt(
    *,
    timestamp_s: float,
    event: TurnSpec,
    post_ask: bool,
    evidence_text: str | None,
    evidence_delivered_now: bool,
) -> str:
    """Build the small per-tick delta used by persistent native sessions.

    The first decision receives the complete system/task/question prompt. A
    stateful native model already retains it, so repeating that document every
    second wastes context and can cause an out-of-memory failure unrelated to
    the benchmark task.
    """

    actions = ["<WAIT>"]
    if not post_ask:
        if event.ask_mode == ResponseMode.CHOICE:
            actions.extend(
                f"<ASK:{chr(ord('A') + index)}>"
                for index in range(len(event.action_options))
            )
        else:
            actions.append("ASK (follow [ASK FORMAT])")
        if event.answer_mode == ResponseMode.CHOICE:
            actions.extend(
                f"<ANSWER:{chr(ord('A') + len(event.action_options) + index)}>"
                for index in range(len(event.answer_options))
            )
        else:
            actions.append("ANSWER (follow [ANSWER FORMAT])")
    else:
        if event.answer_mode == ResponseMode.CHOICE:
            actions.extend(
                f"<ANSWER:{chr(ord('A') + len(event.action_options) + index)}>"
                for index in range(len(event.answer_options))
            )
        else:
            actions.append("ANSWER (follow [ANSWER FORMAT])")
    evidence_section = ""
    if evidence_text:
        evidence_section = (
            "\n[USER REPLY]\n"
            + evidence_text
        )
    context_sections = ["[QUESTION]", event.question]
    if event.historical_preference:
        context_sections.extend(
            ["[USER MEMORY]", event.historical_preference]
        )
    if not post_ask:
        if event.ask_mode == ResponseMode.CHOICE:
            context_sections.extend(
                ["[ASK OPTIONS]", format_options(event.action_options)]
            )
        else:
            context_sections.extend(
                [
                    "[ASK FORMAT]",
                    "Begin with <ASK:, write your actual concise information "
                    "request, and finish with >.",
                ]
            )
        if event.answer_mode == ResponseMode.CHOICE:
            context_sections.extend(
                [
                    "[ANSWER OPTIONS]",
                    format_options(
                        event.answer_options,
                        start_index=len(event.action_options),
                    ),
                ]
            )
        else:
            context_sections.extend(
                [
                    "[ANSWER FORMAT]",
                    "Begin with <ANSWER:, write your actual concise free-text "
                    "answer, and finish with >.",
                ]
            )
    else:
        if event.answer_mode == ResponseMode.CHOICE:
            context_sections.extend(
                [
                    "[ANSWER OPTIONS]",
                    format_options(
                        event.answer_options,
                        start_index=len(event.action_options),
                    ),
                ]
            )
        else:
            context_sections.extend(
                [
                    "[ANSWER FORMAT]",
                    "Begin with <ANSWER:, write your actual concise free-text "
                    "answer, and finish with >.",
                ]
            )
    context_refresh = "\n".join(context_sections)
    return (
        f"[NEXT TICK {timestamp_s:.3f}s] Same event and rules; use the newly "
        f"streamed frame.\n"
        f"{evidence_section}\n"
        f"{context_refresh}\n"
        f"[AVAILABLE ACTIONS] {' | '.join(actions)}"
    )


def format_options(
    options: tuple[OptionSpec, ...], *, start_index: int = 0
) -> str:
    return "\n".join(
        f"{chr(ord('A') + start_index + index)}. {option.text}"
        for index, option in enumerate(options)
    ) or "None"


def build_mid_term_messages(frames: list[EncodedFrame]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Create one chronological memory summary for this short-term frame chunk.",
        }
    ]
    for frame in frames:
        content.extend(
            [
                {
                    "type": "text",
                    "text": (
                        f"Frame timestamp: {frame.timestamp_s:.3f}s"
                    ),
                },
                {"type": "image_url", "image_url": {"url": frame.data_url}},
            ]
        )
    return [
        {"role": "system", "content": MID_TERM_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def build_long_term_messages(summaries: list[str]) -> list[dict[str, Any]]:
    joined = "\n\n".join(
        f"[MID-TERM SUMMARY {index}]\n{summary}"
        for index, summary in enumerate(summaries, start=1)
    )
    return [
        {"role": "system", "content": LONG_TERM_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": "Consolidate these summaries chronologically:\n\n" + joined,
        },
    ]


def strip_image_data(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return an auditable prompt copy without base64 image bytes."""

    redacted: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            redacted.append(dict(message))
            continue
        items: list[dict[str, Any]] = []
        image_index = 0
        for item in content:
            if item.get("type") == "image_url":
                image_index += 1
                items.append({"type": "image_url", "image_url": "<redacted>", "index": image_index})
            else:
                items.append(dict(item))
        redacted.append({**message, "content": items})
    return redacted
