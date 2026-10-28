"""Task-specific rubrics for open-ended ASK/ANSWER judging.

This module intentionally has no imports from the schema or backend modules so
that the schema can use the same defaults when it materializes a ``TurnSpec``.
"""

from __future__ import annotations



TASKS = (
    "KVEM",
    "VIM",
    "ICM",
    "PACM",
    "AUQ",
    "WUQ",
    "UPM",
    "UPC",
)


_COMMON_OUTPUT_RULE = (
    "Return exactly 1 if the candidate is correct and exactly 0 if the candidate "
    "is incorrect. Return only that single digit and nothing else. Judge only "
    "the supplied labeled text and images; do not use outside knowledge."
)


_TEXT_ONLY_OUTPUT_RULE = (
    "Return exactly 1 if the candidate is correct and exactly 0 if the candidate "
    "is incorrect. Return only that single digit and nothing else. Judge only "
    "the supplied labeled text; do not use outside knowledge."
)


ASK_JUDGE_PROMPTS: dict[str, str] = {
    "KVEM": f"""You are the strict open-ended ASK judge for the KVEM task.
Using the supplied image, question, and reference, identify the single intended
occluded entity in the image. The candidate is correct only if it requests the
same entity as the reference and locates that entity by position or visible
spatial context. Return 0 if the
candidate merely requests the answer-bearing entity by its category name,
merely repeats an entity already explicitly mentioned in the question, asks
for a broad scene description, targets an unrelated entity or attribute, adds
an unrelated request, or gives missing or conflicting location information.
{_COMMON_OUTPUT_RULE}""",
    "VIM": f"""You are the strict open-ended ASK judge for the VIM task.
You will receive the question, the candidate ASK, and multi-view video frames
collected at the ASK timestamp to help you judge the candidate. The first
labeled frame is the initial viewpoint shown to the tested model. The remaining
labeled frames are the gold viewpoints from the annotation. Decide whether,
based on the current video frames, switching to the viewpoint requested by the
candidate would allow the current question to be answered. The candidate is
correct only if it requests a concrete useful viewpoint and the current video
frames indicate that switching to that viewpoint would answer the current
question. Return 0 for a vague or unrelated view, a view that the current
video frames indicate would remain insufficient, an unrelated additional
request, or a candidate that directly requests an entity already explicitly
mentioned in the question instead of requesting a useful viewpoint.
{_COMMON_OUTPUT_RULE}""",
    "ICM": f"""You are the strict open-ended ASK judge for the ICM task.
The ASK payload selects the private entity definitions to return. It must name
every private entity alias referred to in the question. Listing those aliases
alone is sufficient: for example, "{{Rhiley}} and {{Jaedyn}}." is valid when both
aliases occur in the question. Explicit requests for their definitions are
equally valid; request wording is not required. Reject omitted or wrong aliases,
unrelated additions, and answers that do not select the required definitions.
{_TEXT_ONLY_OUTPUT_RULE}""",
    "PACM": f"""You are the strict open-ended ASK judge for the PACM task.
The ASK payload selects the personalized action definitions to return. It must
explicitly name every personalized action referred to in the question. A list
of all required action names alone is sufficient; explicit definition-request
wording is optional. Reject omitted or wrong action names, unrelated additions,
and answers that do not select the required definitions.
{_COMMON_OUTPUT_RULE}""",
    "AUQ": f"""You are the strict open-ended ASK judge for the AUQ task.
The user's request contains an ambiguous part. Using
the supplied image, question, and reference as ground truth, decide whether the
candidate correctly identifies the specific ambiguity and requests the
clarification needed for the request to identify one unique referent rather
than multiple possible referents. A generic request for more information, an
unrelated request, or an answer to the question is incorrect.
{_COMMON_OUTPUT_RULE}""",
    "WUQ": f"""You are the strict open-ended ASK judge for the WUQ task.
The user's request contains a part that conflicts with the
video. Using the supplied image, question, and reference as ground truth,
decide whether the candidate correctly identifies the specific erroneous part
and challenges it by stating the visually supported correction or asking the
user to confirm that correction. A generic request that does not identify the
conflict, a correction of a different claim, or an unrelated additional
request is incorrect.
{_COMMON_OUTPUT_RULE}""",
    "UPM": f"""You are the strict open-ended ASK judge for the UPM task.
Using only the supplied ordered option images, Correct
Image label, and candidate, decide whether the candidate asks about one
concrete, visually grounded user-preference feature dimension. The value shown
by the Correct Image on that dimension must not be shared by any other option
image, so a user answer choosing that value would uniquely identify the Correct
Image. Return 0 if the requested feature is absent from or not visually
comparable across the option images, the Correct Image's value is shared by
another image, the candidate excludes or conflicts with the Correct Image's
value, the request uniquely identifies only a different image, combines
multiple preference dimensions, adds an unrelated request, or asks about
preferences only in general terms.
{_COMMON_OUTPUT_RULE}""",
    "UPC": f"""You are the strict open-ended ASK judge for the UPC task.
Using only the supplied ordered option images,
Correct Image label, and candidate, decide whether the candidate asks the user
to confirm or update one concrete, visually grounded preference feature
dimension. The value shown by the Correct Image on that dimension must not be
shared by any other option image, so a user answer choosing that value would
uniquely identify the Correct Image. Return 0 if the candidate does not ask for
a current or updated preference, the requested feature is absent from or not
visually comparable across the option images, the Correct Image's value is
shared by another image, the candidate excludes or conflicts with the Correct
Image's value, the request uniquely identifies only a different image,
combines multiple preference dimensions, adds an unrelated request, or asks
about preferences only in general terms.
{_COMMON_OUTPUT_RULE}""",
}


def _answer_prompt(task: str) -> str:
    return f"""You are the strict open-ended ANSWER judge for the {task} task.
Judge whether the candidate directly answers the user's question with the same
meaning as the answer ground truth/reference. Allow concise paraphrases,
harmless tense changes, and equivalent referring expressions. Reject missing
required information, contradictions, unsupported material claims, answers to
another question, or ambiguity.
{_COMMON_OUTPUT_RULE}"""


ANSWER_JUDGE_PROMPTS: dict[str, str] = {
    task: _answer_prompt(task) for task in TASKS
}
for _task in ("KVEM", "VIM", "WUQ", "ICM", "UPM", "UPC"):
    ANSWER_JUDGE_PROMPTS[_task] += """
Use the labeled observed MAIN VIDEO frames and any delivered user reply to
resolve visual referring expressions in the candidate, such as appearance,
position, or an on-screen number. Accept a concise expression when the supplied
evidence establishes that it refers to the same answer as the Reference.
Do not invent a mapping when it is not supported by the supplied evidence.
"""
for _task in ("UPM", "UPC"):
    ANSWER_JUDGE_PROMPTS[_task] += """
The CORRECT ANSWER OPTION IMAGE is a judge-only visual reference for the target
entity/outfit. Compare the candidate with both this image and the textual
Reference. A video outfit number is valid when observed video frames establish
the mapping to this target. The order of images in the judge prompt is not a
video numbering scheme. Historical main-video frames may be sparsely sampled;
do not infer the ordinal order of outfits from the number of supplied frames.
"""
ANSWER_JUDGE_PROMPTS["ICM"] += """
Use the delivered identity definition images in their labeled order together
with the delivered definition text to map private aliases to visible people.
Definition images are supplied references, not timestamped main-video events.
"""
ANSWER_JUDGE_PROMPTS["AUQ"] = f"""You are the exact-reminder ANSWER judge for AUQ.
The candidate must reproduce the Reference exactly, including capitalization,
punctuation, and wording. Ignore only whitespace outside the response. Reject
paraphrases, tense changes, missing punctuation, or additional text.
{_TEXT_ONLY_OUTPUT_RULE}"""
ANSWER_JUDGE_PROMPTS["PACM"] = _answer_prompt("PACM") + """
You receive ordered MAIN VIDEO frames with main-video timestamps and ordered
ACTION DEFINITION VIDEO frames with separate clip-local timestamps. Use the
definition sequence to recognize the complete action, then follow the people
and their action completion order across the main sequence. Verify whether the
candidate and reference identify the same person, including when one uses
completion order and the other uses appearance or initial position. Never
equate the two video clocks or infer completion order from a single still.
"""


def task_judge_prompt(task: str | None, kind: str, fallback: str) -> str:
    """Return the built-in prompt for a task, or ``fallback`` when unknown."""

    normalized_task = task
    if kind == "ask":
        return ASK_JUDGE_PROMPTS.get(normalized_task, fallback)
    if kind == "answer":
        return ANSWER_JUDGE_PROMPTS.get(normalized_task, fallback)
    return fallback


__all__ = [
    "ANSWER_JUDGE_PROMPTS",
    "ASK_JUDGE_PROMPTS",
    "TASKS",
    "task_judge_prompt",
]
