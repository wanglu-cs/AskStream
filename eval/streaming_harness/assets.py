from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schema import CompiledDataset, TurnSpec


EVIDENCE_KINDS = frozenset({"video", "image", "text", "none", "synchronized_view"})


@dataclass(frozen=True)
class EvidenceAsset:
    """Normalized evidence delivered after a correct ASK.

    Video evidence contains one time-aligned video URI. Image evidence contains
    one or more image URIs. Text evidence contains the user reply that is added
    to the next frame chunk. None represents an intentional empty delivery.
    """

    event_id: str
    kind: str
    uris: tuple[str, ...] = ()
    text: str | None = None
    entity_ids: tuple[str, ...] = ()
    start_s: float | None = None
    end_s: float | None = None

    @property
    def uri(self) -> str:
        return self.uris[0] if self.uris else ""


@dataclass(frozen=True)
class VideoAssets:
    video_id: str
    stream_video_uri: str
    evidence: dict[str, EvidenceAsset]
    stream_kind: str = "clear"
    view_video_uris: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetManifest:
    source_path: Path
    videos: dict[str, VideoAssets]

    @classmethod
    def from_dataset(
        cls,
        dataset: CompiledDataset,
        *,
        use_open_evidence: bool = False,
    ) -> "AssetManifest":
        """Build assets embedded in a self-contained release dataset."""

        videos: dict[str, VideoAssets] = {}
        for video in dataset.videos:
            evidence = {}
            for event in video.events:
                bundle = event.evidence_open if use_open_evidence and event.evidence_open is not None else event.evidence
                visual = next(iter(bundle.visual), None)
                kind = bundle.primary_kind
                if kind == "image" and (visual is None or not visual.uris):
                    raise ValueError(f"{event.event_id}: image evidence requires URIs")
                evidence[event.event_id] = EvidenceAsset(
                    event_id=event.event_id, kind=kind,
                    uris=visual.uris if visual else (),
                    text="\n\n".join(item.content for item in bundle.text) or None,
                    entity_ids=event.correct_entity_ids if kind == "video" else (),
                    start_s=visual.start_s if visual else None,
                )
            videos[video.video_id] = VideoAssets(
                video_id=video.video_id, stream_video_uri=video.source_uri, evidence=evidence,
                stream_kind="masked" if video.events and video.events[0].task == "KVEM" else "clear",
                view_video_uris={view.view_id: view.uri for view in video.views},
            )
        return cls(dataset.source_path, videos)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "pvb.streaming_assets.v3",
            "videos": {
                video_id: {
                    "stream_video_uri": assets.stream_video_uri,
                    "stream_kind": assets.stream_kind,
                    **(
                        {"view_video_uris": assets.view_video_uris}
                        if assets.view_video_uris
                        else {}
                    ),
                    "events": {
                        event_id: {"evidence": _evidence_as_dict(item)}
                        for event_id, item in assets.evidence.items()
                    },
                }
                for video_id, assets in self.videos.items()
            },
        }

    @property
    def content_sha256(self) -> str:
        """Hash the normalized manifest contents, independent of JSON layout."""

        payload = json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def resolve(self, uri: str) -> Path:
        candidate = Path(str(uri)).expanduser()
        if candidate.is_absolute():
            return candidate
        return (self.source_path.parent / candidate).resolve()

    def validate_video(self, video_id: str, events: list[TurnSpec]) -> list[str]:
        errors: list[str] = []
        assets = self.videos.get(video_id)
        if assets is None:
            return [f"{video_id}: missing video asset entry"]
        if not assets.stream_video_uri:
            errors.append(f"{video_id}: stream_video_uri is empty")
        elif not self.resolve(assets.stream_video_uri).is_file():
            errors.append(f"{video_id}: stream video does not exist")
        if assets.stream_kind not in {"clear", "masked"}:
            errors.append(
                f"{video_id}: stream_kind must be 'clear' or 'masked', got "
                f"{assets.stream_kind!r}"
            )
        for view_id, uri in assets.view_video_uris.items():
            if not uri:
                errors.append(f"{video_id}: view {view_id!r} URI is empty")
            elif not self.resolve(uri).is_file():
                errors.append(f"{video_id}: view video does not exist: {view_id!r}")

        for event in events:
            item = assets.evidence.get(event.event_id)
            if item is None:
                errors.append(f"{event.event_id}: evidence asset is missing")
                continue
            if item.kind not in EVIDENCE_KINDS:
                errors.append(
                    f"{event.event_id}: unsupported evidence kind {item.kind!r}"
                )
                continue
            if item.kind != event.evidence_kind:
                errors.append(
                    f"{event.event_id}: manifest evidence kind {item.kind!r} "
                    f"does not match annotation {event.evidence_kind!r}"
                )
            if item.kind in {"text", "image"}:
                expected_text = (event.evidence_text or "").strip()
                actual_text = (item.text or "").strip()
                if actual_text != expected_text:
                    errors.append(
                        f"{event.event_id}: text evidence does not match the dataset annotation"
                    )
            if item.kind == "video":
                if len(item.uris) != 1:
                    errors.append(
                        f"{event.event_id}: video evidence requires exactly one URI"
                    )
                elif not self.resolve(item.uri).is_file():
                    errors.append(f"{event.event_id}: evidence video does not exist")
                if item.entity_ids and set(item.entity_ids) != set(
                    event.correct_entity_ids
                ):
                    errors.append(
                        f"{event.event_id}: evidence entity_ids do not match annotation gold"
                    )
            elif item.kind == "image":
                if not item.uris:
                    errors.append(
                        f"{event.event_id}: image evidence requires at least one URI"
                    )
                for uri in item.uris:
                    if not self.resolve(uri).is_file():
                        errors.append(
                            f"{event.event_id}: evidence image does not exist: {uri}"
                        )
                expected_concepts = event.source_metadata.get("source_concept_names")
                if isinstance(expected_concepts, list) and not (item.text or "").strip():
                    errors.append(
                        f"{event.event_id}: concept image evidence requires the "
                        "original definition text"
                    )
                if (
                    isinstance(expected_concepts, list)
                    and len(item.uris) != len(expected_concepts)
                ):
                    errors.append(
                        f"{event.event_id}: expected one evidence image for each "
                        f"question concept ({len(expected_concepts)}), got {len(item.uris)}"
                    )
            elif item.kind == "text":
                if not (item.text or "").strip():
                    errors.append(f"{event.event_id}: text evidence is empty")
            elif item.kind == "none" and (item.uris or (item.text or "").strip()):
                errors.append(f"{event.event_id}: none evidence must not carry a payload")
            elif item.kind == "synchronized_view":
                required_views = {event.initial_view_id}
                required_views.update(
                    option.target_view_id or "" for option in event.action_options
                )
                for view_id in sorted(required_views - {""}):
                    if view_id not in assets.view_video_uris:
                        errors.append(
                            f"{event.event_id}: synchronized view {view_id!r} is missing"
                        )
        return errors


def _evidence_as_dict(item: EvidenceAsset) -> dict[str, Any]:
    payload: dict[str, Any] = {"kind": item.kind}
    if item.uris:
        payload["uris"] = list(item.uris)
    if item.text:
        payload["text"] = item.text
    if item.entity_ids:
        payload["entity_ids"] = list(item.entity_ids)
    if item.start_s is not None:
        payload["start_s"] = item.start_s
    if item.end_s is not None:
        payload["end_s"] = item.end_s
    return payload
