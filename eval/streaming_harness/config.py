from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from eval.paths import DEFAULT_PRIVACY_FILE


CONFIG_SCHEMA_VERSION = "pvb.streaming_eval_config.v3"
API_DEFAULT_MODEL = "gpt-5.4"
VLLM_DEFAULT_MODEL = "Qwen3-VL-4B-Instruct"
VLLM_DEFAULT_BASE_URL = "http://127.0.0.1:18003/v1"
STREAMING_DEFAULT_MODEL = "native-streaming"
STREAMING_DEFAULT_BASE_URL = "http://127.0.0.1:18100"


def read_privacy_file(path: Path, *, option_name: str) -> tuple[str, str | None]:
    """Read a process-local API key and optional endpoint without logging either."""

    privacy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(privacy, dict):
        raise ValueError(f"{option_name} must contain a JSON object")
    key = privacy.get("api_key")
    endpoint = privacy.get("base_url")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"{option_name} requires a nonempty string 'api_key'")
    if endpoint is not None and (not isinstance(endpoint, str) or not endpoint.strip()):
        raise ValueError(f"{option_name} 'base_url' must be a nonempty string")
    return key, endpoint


@dataclass(frozen=True)
class EvaluationConfig:
    """Resolved configuration shared by every benchmark task."""

    backend: str = "api"
    evaluation_mode: str | None = None
    model: str = API_DEFAULT_MODEL
    model_adapter: str | None = None
    base_url: str = ""
    wire_api: str = "chat_completions"
    reasoning_effort: str | None = None
    store_responses: bool = False
    judge_model: str | None = "gemini-3.8-flash"
    judge_base_url: str | None = None
    judge_reasoning_effort: str | None = None
    playback_speed: float = 1.0
    # Zero means the complete causal video prefix.  A positive value is an
    # explicit bounded-history ablation for native stateful models.
    rehydrate_seconds: float = 0.0
    rehydrate_max_frames: int = 4
    tick_seconds: float = 1.0
    ask_start_tolerance_s: float = 1.0
    ask_end_tolerance_s: float = 1.0
    answer_start_tolerance_s: float = 1.0
    answer_end_tolerance_s: float = 1.0
    short_term_seconds: float = 10.0
    mid_term_capacity: int = 4
    long_term_capacity: int = 8
    max_frame_pixels: int = 262_144
    mid_term_max_tokens: int = 1_024
    long_term_max_tokens: int = 1_024
    # Choice actions are only a few tokens, but open ASK/ANSWER payloads may be
    # substantially longer under native tokenization. Keep enough room to avoid
    # scoring transport truncation as an invalid cognitive decision.
    action_max_tokens: int = 1_024
    temperature: float | None = None
    enable_thinking: bool | None = None
    request_timeout_seconds: float = 120.0
    request_retries: int = 5
    jpeg_quality: int = 85

    def __post_init__(self) -> None:
        if self.backend not in {"api", "vllm", "streaming"}:
            raise ValueError(f"unsupported backend: {self.backend!r}")
        if self.model_adapter is not None:
            from .model_loader import adapter_entrypoint

            if not isinstance(self.model_adapter, str):
                raise ValueError("model_adapter must be a module:factory string")
            adapter_entrypoint(self.backend, self.model_adapter)
        if self.wire_api != "chat_completions":
            raise ValueError("wire_api must be chat_completions")
        if self.evaluation_mode not in {None, "multi-choice", "open-ended"}:
            raise ValueError("unsupported evaluation_mode")
        for name in ("playback_speed", "tick_seconds", "short_term_seconds", "request_timeout_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("rehydrate_seconds", "ask_start_tolerance_s", "ask_end_tolerance_s",
                     "answer_start_tolerance_s", "answer_end_tolerance_s"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("rehydrate_max_frames", "mid_term_capacity", "long_term_capacity",
                     "max_frame_pixels", "mid_term_max_tokens", "long_term_max_tokens",
                     "action_max_tokens", "request_retries"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.jpeg_quality) is not int or not 1 <= self.jpeg_quality <= 100:
            raise ValueError("jpeg_quality must be an integer between 1 and 100")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a nonempty string")

    @property
    def short_term_frame_count(self) -> int:
        return max(1, math.ceil(self.short_term_seconds / self.tick_seconds))

    @property
    def short_term_effective_span_s(self) -> float:
        return self.short_term_frame_count * self.tick_seconds

    @property
    def backend_capabilities(self) -> dict[str, bool]:
        return {
            "chat_completions": self.backend != "streaming",
            "native_video_session": self.backend == "streaming",
            "requires_api_key": self.backend == "api",
        }

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            **asdict(self),
        }
        payload["effective"] = {
            "short_term_frame_count": self.short_term_frame_count,
            "short_term_span_s": self.short_term_effective_span_s,
            "backend_capabilities": self.backend_capabilities,
        }
        return payload

    def with_overrides(self, **overrides: Any) -> "EvaluationConfig":
        return replace(self, **overrides)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "EvaluationConfig":
        if not isinstance(value, Mapping):
            raise ValueError("config must be an object")
        schema_version = value.get("schema_version")
        if schema_version != CONFIG_SCHEMA_VERSION:
            raise ValueError(f"config schema_version must be {CONFIG_SCHEMA_VERSION!r}")
        provided = {name: item for name, item in value.items() if name != "schema_version"}
        provided.pop("effective", None)
        unknown = set(provided) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown config fields: {', '.join(sorted(unknown))}")
        backend = str(provided.get("backend") or "api")
        resolved = defaults_for_backend(backend)
        resolved.update(provided)
        resolved.pop("backend", None)
        return cls(backend=backend, **resolved)

    @classmethod
    def from_json(cls, path: Path) -> "EvaluationConfig":
        return cls.from_mapping(json.loads(path.read_text(encoding="utf-8")))


def defaults_for_backend(backend: str) -> dict[str, object]:
    if backend == "api":
        return {
            "model": API_DEFAULT_MODEL,
            "base_url": "",
        }
    if backend == "vllm":
        return {
            "model": VLLM_DEFAULT_MODEL,
            "base_url": VLLM_DEFAULT_BASE_URL,
            "wire_api": "chat_completions",
            "reasoning_effort": None,
            "store_responses": False,
        }
    if backend == "streaming":
        return {
            "model": STREAMING_DEFAULT_MODEL,
            "base_url": STREAMING_DEFAULT_BASE_URL,
            "wire_api": "chat_completions",
            "reasoning_effort": None,
            "store_responses": False,
        }
    raise ValueError(f"unsupported backend: {backend!r}")


__all__ = [
    "API_DEFAULT_MODEL",
    "CONFIG_SCHEMA_VERSION",
    "EvaluationConfig",
    "VLLM_DEFAULT_BASE_URL",
    "VLLM_DEFAULT_MODEL",
    "STREAMING_DEFAULT_BASE_URL",
    "STREAMING_DEFAULT_MODEL",
    "defaults_for_backend",
    "load_api_credentials",
]


def load_api_credentials(
    config: EvaluationConfig, privacy_file: Path | None
) -> tuple[EvaluationConfig, str | None]:
    """Load the sole credential source, shared by the evaluated model and judge."""
    if config.backend != "api" and not config.judge_model:
        if privacy_file is not None:
            raise ValueError("--privacy-file requires an API backend or --judge-model")
        return config, None
    if privacy_file is None:
        privacy_file = DEFAULT_PRIVACY_FILE
    if not privacy_file.is_file():
        raise ValueError(
            f"API evaluation or judging requires --privacy-file or {DEFAULT_PRIVACY_FILE}"
        )
    key, endpoint = read_privacy_file(privacy_file, option_name="--privacy-file")
    if endpoint and config.backend == "api":
        config = config.with_overrides(base_url=endpoint)
    if config.backend == "api" and not config.base_url.strip():
        raise ValueError("API backend requires --base-url or 'base_url' in --privacy-file")
    if config.judge_model and not config.judge_base_url:
        config = config.with_overrides(
            judge_base_url=endpoint or (config.base_url if config.backend == "api" else None)
        )
    if config.judge_model and not (config.judge_base_url or "").strip():
        raise ValueError("Judge requires --judge-base-url or 'base_url' in --privacy-file")
    return config, key
