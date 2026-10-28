"""HTTP transport for the standard streaming contract, independent of model code."""

from __future__ import annotations

import base64
import json
import math
import time
import uuid
from dataclasses import asdict
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from .config import EvaluationConfig
from .frames import EncodedFrame
from .interfaces import (
    STREAMING_OPERATIONS,
    STREAMING_PROTOCOL_VERSION,
    AttemptCallback,
    ModelCallResult,
    StreamDecisionRequest,
    StreamObservation,
    StreamingModelBackend,
    StreamingOOMError,
    StreamingWorkerError,
)


class StreamingRpcBackend(StreamingModelBackend):
    def __init__(self, config: EvaluationConfig, *, on_attempt: AttemptCallback | None = None):
        if config.backend != "streaming":
            raise ValueError("StreamingRpcBackend requires backend='streaming'")
        self.config = config
        self.on_attempt = on_attempt
        self.base_url = config.base_url.rstrip("/")

    def health(self) -> dict[str, Any]:
        request = urllib_request.Request(self.base_url + "/health", headers={"Accept": "application/json"})
        with urllib_request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
            payload = _response_object(response.read())
        if payload.get("status") != "ok":
            raise StreamingWorkerError("streaming model is not ready")
        if payload.get("model") != self.config.model:
            raise StreamingWorkerError(f"requested model {self.config.model!r} is not served")
        operations = payload.get("operations")
        if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
            raise StreamingWorkerError("health operations must be an array of strings")
        if not STREAMING_OPERATIONS.issubset(operations):
            raise StreamingWorkerError("streaming model must implement every lifecycle operation")
        return payload

    def open_video(self, *, video_id: str, metadata: dict[str, Any]) -> None:
        self._post("open", {"video_id": video_id, "metadata": metadata}, call_type="session_open")

    def push_observation(self, observation: StreamObservation) -> None:
        self._post("observe", {
            "video_id": observation.video_id,
            "tick_index": observation.tick_index,
            "timestamp_s": observation.timestamp_s,
            "active_turn_id": observation.active_turn_id,
            "frame": serialize_stream_value(observation.frame),
            "evidence": serialize_stream_value(observation.evidence),
            "replay": observation.replay,
        }, call_type="observation")

    def decide(self, request: StreamDecisionRequest) -> ModelCallResult:
        payload, attempts = self._post("decide", asdict(request), call_type="action")
        required = {"content": str, "raw_content": str, "model": str, "usage": dict, "metadata": dict}
        for name, value_type in required.items():
            if not isinstance(payload.get(name), value_type):
                raise StreamingWorkerError(f"decision {name} must be {value_type.__name__}")
        latency = payload.get("latency_ms")
        if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
            raise StreamingWorkerError("decision latency_ms must be finite and nonnegative")
        if "response_id" not in payload or not isinstance(payload["response_id"], (str, type(None))):
            raise StreamingWorkerError("decision response_id must be a string or null")
        if payload["model"] != self.config.model:
            raise StreamingWorkerError("decision model does not match the requested model")
        return ModelCallResult(
            call_type="action", content=payload["content"], raw_content=payload["raw_content"],
            model=payload["model"], latency_ms=float(latency), usage=payload["usage"],
            response_id=payload["response_id"], attempts=attempts, metadata=payload["metadata"],
        )

    def reset_context(self, *, video_id: str, metadata: dict[str, Any]) -> None:
        self._post("reset", {"video_id": video_id, "metadata": metadata}, call_type="session_reset")

    def abort_video(self, *, video_id: str, metadata: dict[str, Any] | None = None) -> None:
        self._post("abort", {"video_id": video_id, "metadata": metadata or {}}, call_type="session_abort")

    def close_video(self, *, video_id: str) -> None:
        self._post("close", {"video_id": video_id}, call_type="session_close")

    def _post(self, operation: str, payload: dict[str, Any], *, call_type: str) -> tuple[dict[str, Any], int]:
        request_id = uuid.uuid4().hex
        body = {"protocol_version": STREAMING_PROTOCOL_VERSION, "request_id": request_id, **payload}
        encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        endpoint = f"/v1/sessions/{operation}"
        for attempt in range(1, self.config.request_retries + 1):
            started = time.perf_counter()
            request = urllib_request.Request(self.base_url + endpoint, data=encoded,
                                            headers={"Content-Type": "application/json", "Accept": "application/json"})
            try:
                with urllib_request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                    result = _response_object(response.read())
            except urllib_error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                try:
                    error = json.loads(detail).get("error", {})
                except (ValueError, AttributeError):
                    error = {}
                code = error.get("code") if isinstance(error, dict) else None
                retryable = code not in {"out_of_memory", "worker_error"} and exc.code in {429, 502, 503, 504}
                self._emit(call_type, attempt, "error", started, endpoint, request_id, error=detail, retryable=retryable)
                if not retryable:
                    error_type = StreamingOOMError if code == "out_of_memory" else StreamingWorkerError
                    raise error_type(f"{endpoint}: HTTP {exc.code}: {detail}") from exc
                if attempt == self.config.request_retries:
                    raise StreamingWorkerError(f"{endpoint}: retries exhausted: {detail}") from exc
            except (OSError, urllib_error.URLError) as exc:
                self._emit(call_type, attempt, "error", started, endpoint, request_id, error=str(exc), retryable=True)
                if attempt == self.config.request_retries:
                    raise StreamingWorkerError(f"{endpoint}: retries exhausted: {exc}") from exc
            else:
                self._emit(call_type, attempt, "success", started, endpoint, request_id)
                return result, attempt
            time.sleep(min(30.0, 2.0 ** (attempt - 1)))
        raise AssertionError("request_retries must be positive")

    def _emit(self, call_type: str, attempt: int, status: str, started: float,
              endpoint: str, request_id: str, **extra: object) -> None:
        if self.on_attempt is not None:
            self.on_attempt({"call_type": call_type, "attempt": attempt, "status": status,
                             "latency_ms": (time.perf_counter() - started) * 1000,
                             "endpoint": endpoint, "request_id": request_id, **extra})


def _response_object(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise StreamingWorkerError("streaming response must be JSON") from exc
    if not isinstance(payload, dict) or payload.get("protocol_version") != STREAMING_PROTOCOL_VERSION:
        raise StreamingWorkerError(f"streaming protocol_version must be {STREAMING_PROTOCOL_VERSION!r}")
    return payload


def serialize_stream_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, EncodedFrame):
        return {"timestamp_s": value.timestamp_s, "requested_timestamp_s": value.requested_timestamp_s,
                "width": value.width, "height": value.height, "source": value.source,
                "mime_type": "image/jpeg", "data_base64": base64.b64encode(value.jpeg_bytes).decode("ascii")}
    if isinstance(value, dict):
        return {key: serialize_stream_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_stream_value(item) for item in value]
    raise TypeError(f"unsupported streaming payload: {type(value).__name__}")
