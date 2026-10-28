"""Portable, single-manifest dataset release v2.

The public format stays independent of the evaluator's internal dataclasses.
Conversion reuses the canonical validator, rather than introducing a second
implementation of option, timing, evidence, or judge semantics.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = "pvb.streaming_dataset.v2"
EXPECTED_COUNTS = {
    "KVEM": (78, 130), "VIM": (50, 243), "AUQ": (50, 121),
    "WUQ": (60, 219), "PACM": (50, 50), "ICM": (49, 147),
    "UPM": (50, 174), "UPC": (50, 177),
}


def resolve_resource(path: Path, uri: str) -> str:
    """Resolve a v2 resource without cwd, web routes, or legacy fallbacks."""
    candidate = Path(uri)
    if candidate.is_absolute() or ".." in candidate.parts or not uri or urlsplit(uri).scheme:
        raise ValueError(f"release resources must be local relative paths: {uri!r}")
    resolved = (path.parent / candidate).resolve()
    if not resolved.is_relative_to(path.parent.resolve()):
        raise ValueError(f"resource escapes release: {uri!r}")
    return str(resolved)


def canonical_document(path: Path, document: dict[str, Any]) -> dict[str, Any]:
    subset = document.get("subset")
    if subset not in EXPECTED_COUNTS:
        raise ValueError(f"unknown release subset: {subset!r}")
    if not isinstance(document.get("cases"), list):
        raise ValueError("release cases must be an array")

    def resource_bundle(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
        if bundle is None:
            return None
        bundle = copy.deepcopy(bundle)
        for visual in bundle.get("visual", []):
            visual["uris"] = [resolve_resource(path, uri) for uri in visual.get("uris", [])]
        return bundle

    videos = []
    seen_ids = set()
    for case in document["cases"]:
        case_id = case['video_id']
        if not re.fullmatch(rf'{subset}_[0-9]{{6}}', case_id) or case_id in seen_ids:
            raise ValueError(f'invalid or duplicate release case ID: {case_id}')
        seen_ids.add(case_id)
        media = case["media"]
        if media['fps']['num'] <= 0 or media['fps']['den'] <= 0 or not math.isclose(
                media['fps']['value'], media['fps']['num'] / media['fps']['den'], rel_tol=1e-9):
            raise ValueError(f'{case_id}: inconsistent fps rational')
        metadata = case.get("metadata", {})
        turns = []
        for index, turn in enumerate(case["turns"]):
            if turn['turn_id'] != f'{case_id}_turn_{index + 1:06d}':
                raise ValueError(f'{case_id}: turn IDs must follow source order')
            if set(turn['windows']) != {'waiting', 'asking', 'answering'}:
                raise ValueError(f'{turn["turn_id"]}: expected all three windows')
            if not isinstance(turn['query_time_s'], (int, float)) or not math.isfinite(turn['query_time_s']):
                raise ValueError(f'{turn["turn_id"]}: query_time_s must be finite seconds')
            meta = copy.deepcopy(turn.get("metadata", {}))
            evaluator = meta.get("evaluation", {})
            source_metadata = meta.get("source_metadata", {})
            source_metadata["release_source"] = {
                **case.get("source", {}),
                "original_turn_id": meta.get("original_turn_id"),
                "annotation_sha256": document.get("metadata", {}).get("annotation_sha256"),
            }
            # Only these fields are runtime resources. Historical source paths
            # elsewhere in metadata are provenance, never fallback locations.
            source_metadata["option_images"] = [
                resolve_resource(path, uri) for uri in source_metadata.get("option_images", [])
            ]
            for frame in source_metadata.get("definition_frames", []):
                frame["uri"] = resolve_resource(path, frame["uri"])
            overrides = meta.get("mode_overrides", {}).get("open_ended", {})
            windows = copy.deepcopy(turn["windows"])
            windows["waiting_open"] = overrides.get("waiting")
            ask, answer = turn["ask"], turn["answer"]
            turns.append({
                "turn_id": turn["turn_id"], "order": evaluator.get("order", index),
                "segment": turn["time_range"], "windows": windows,
                "question": {**turn["query"], "time_s": turn["query_time_s"],
                             "canonical": evaluator.get("canonical_question", ""),
                             "historical_context": evaluator.get("historical_preference")},
                "responses": {"ask": ask, "answer": answer},
                "gold": {
                    "ask_option_id": ask.get("correct_option_id", ""),
                    "ask_option_ids": ask.get("correct_option_ids", []),
                    "ask_text": ask.get("reference_text"),
                    "answer_option_id": answer.get("correct_option_id", ""),
                    "answer_text": answer.get("reference_text"),
                    "entity_ids": evaluator.get("correct_entity_ids", []),
                },
                "evidence": resource_bundle(turn["evidence"]),
                "evidence_open": resource_bundle(overrides.get("evidence")),
                "task": subset, "uncertainty_type": evaluator.get("uncertainty_type", ""),
                "initial_view_id": evaluator.get("initial_view_id", ""),
                "concept_name": evaluator.get("concept_name", ""),
                "entity_ids": evaluator.get("entity_ids", []),
                "visual_prompt_ids": evaluator.get("visual_prompt_ids", []),
                "preserve_post_terminal_observation": evaluator.get("preserve_post_terminal_observation", False),
                "source": {**evaluator.get("source", {}), "metadata": source_metadata},
            })
        videos.append({
            "video_id": case["video_id"], "source_uri": resolve_resource(path, media["uri"]),
            "media": {"duration_s": media["duration_s"], "width": media["width"],
                      "height": media["height"], "frame_rate": {
                          "num": media["fps"]["num"], "den": media["fps"]["den"]}},
            "views": [{**view, "uri": resolve_resource(path, view["uri"])} for view in media.get("views", [])],
            "turns": turns,
        })
    return {"videos": videos}
