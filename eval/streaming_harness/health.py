from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .config import EvaluationConfig
from .streaming import StreamingRpcBackend


def wait_for_streaming(config: EvaluationConfig, *, timeout_seconds: float = 120.0,
                       interval_seconds: float = 2.0) -> dict[str, Any]:
    if timeout_seconds <= 0 or interval_seconds <= 0:
        raise ValueError("health timeout and interval must be positive")
    deadline = time.monotonic() + timeout_seconds
    while True:
        remaining = deadline - time.monotonic()
        client = StreamingRpcBackend(config.with_overrides(
            request_timeout_seconds=max(0.001, min(config.request_timeout_seconds, remaining))))
        try:
            return client.health()
        except (RuntimeError, OSError, ValueError) as exc:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"streaming model did not become healthy: {exc}") from exc
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))


@dataclass(frozen=True)
class VllmHealth:
    base_url: str
    health_url: str
    models_url: str
    served_models: tuple[str, ...]
    requested_model: str
    checked_at: str

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ready",
            "base_url": self.base_url,
            "health_url": self.health_url,
            "models_url": self.models_url,
            "served_models": list(self.served_models),
            "requested_model": self.requested_model,
            "checked_at": self.checked_at,
        }


def wait_for_vllm(
    base_url: str,
    model: str,
    *,
    timeout_seconds: float = 120.0,
    interval_seconds: float = 2.0,
    request_timeout_seconds: float = 5.0,
) -> VllmHealth:
    """Wait for vLLM health and verify that the requested model is served."""
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    health_url = _health_url(base_url)
    models_url = _models_url(base_url)
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while True:
        try:
            _get_json_or_empty(health_url, request_timeout_seconds)
            payload = _get_json(models_url, request_timeout_seconds)
            served_models = _model_ids(payload)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "vLLM backend health check failed for "
                    f"{base_url!r} and model {model!r}: {exc}. "
                    "Start an external OpenAI-compatible service "
                    "or pass a reachable --base-url."
                ) from exc
            time.sleep(min(interval_seconds, max(0.0, deadline - time.monotonic())))
        else:
            if model not in served_models:
                raise RuntimeError(
                    f"vLLM is healthy at {base_url}, but model {model!r} is not served; "
                    f"available models: {', '.join(served_models) or '<none>'}"
                )
            return VllmHealth(
                base_url=base_url.rstrip("/"),
                health_url=health_url,
                models_url=models_url,
                served_models=served_models,
                requested_model=model,
                checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
    raise AssertionError(last_error)


def _health_url(base_url: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    path = parsed.path
    if path == "/v1":
        path = ""
    elif path.endswith("/v1"):
        path = path[:-3]
    return urlunsplit((parsed.scheme, parsed.netloc, path + "/health", "", ""))


def _models_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/models"


def _get_json_or_empty(url: str, timeout: float) -> Any:
    request = Request(url, method="GET")
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    payload = _get_json_or_empty(url, timeout)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object from {url}")
    return payload


def _model_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("vLLM /v1/models response has no data list")
    return tuple(
        str(item["id"])
        for item in data
        if isinstance(item, dict) and item.get("id")
    )

