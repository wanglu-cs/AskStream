from __future__ import annotations

import time
from typing import Any

import openai
from openai import OpenAI

from .config import EvaluationConfig
from .interfaces import AttemptCallback, GenerationBackend, ModelCallResult


class _ChatBackend(GenerationBackend):
    def __init__(
        self,
        config: EvaluationConfig,
        api_key: str | None = None,
        *,
        on_attempt: AttemptCallback | None = None,
    ) -> None:
        super().__init__(config, on_attempt=on_attempt)
        self.api_key = api_key or "EMPTY"
        self.client = None
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=config.base_url.rstrip("/"),
            timeout=config.request_timeout_seconds,
            max_retries=0,
        )

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        call_type: str,
    ) -> ModelCallResult:
        last_error: Exception | None = None
        for attempt in range(1, self.config.request_retries + 1):
            started = time.perf_counter()
            try:
                kwargs = self._request_kwargs(messages, max_tokens)
                assert self.client is not None
                response = self.client.chat.completions.create(**kwargs)
                latency_ms = (time.perf_counter() - started) * 1000.0
                content = response.choices[0].message.content or ""
                usage = (
                    response.usage.model_dump(mode="json")
                    if response.usage is not None
                    else {}
                )
                self._emit_attempt(
                    {
                        "call_type": call_type,
                        "attempt": attempt,
                        "status": "success",
                        "latency_ms": latency_ms,
                        "response_id": response.id,
                        "usage": usage,
                        "messages": redact_messages(messages),
                    }
                )
                return ModelCallResult(
                    call_type=call_type,
                    content=content,
                    model=response.model or self.config.model,
                    latency_ms=latency_ms,
                    usage=usage,
                    response_id=response.id,
                    attempts=attempt,
                )
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000.0
                last_error = exc
                retryable = _is_retryable(exc)
                self._emit_attempt(
                    {
                        "call_type": call_type,
                        "attempt": attempt,
                        "status": "error",
                        "latency_ms": latency_ms,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "retryable": retryable,
                        "messages": redact_messages(messages),
                    }
                )
                if not retryable or attempt == self.config.request_retries:
                    raise
                time.sleep(min(30.0, 2.0 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    def _request_kwargs(
        self, messages: list[dict[str, Any]], max_tokens: int
    ) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        if self.config.reasoning_effort is not None:
            payload["reasoning_effort"] = self.config.reasoning_effort
        if self.config.store_responses:
            payload["store"] = True
        if self.config.temperature is not None:
            payload["temperature"] = self.config.temperature
        if self.config.enable_thinking is not None:
            payload["extra_body"] = self._thinking_body(self.config.enable_thinking)
        return payload

    def _thinking_body(self, enabled: bool) -> dict[str, Any]:
        model = self.config.model.lower()
        if model.startswith("mimo-v2.5"):
            return {"thinking": {"type": "enabled" if enabled else "disabled"}}
        if model.startswith("gemini-"):
            # Gemini 3.x cannot fully disable thinking.  Low is its closest
            # supported setting. Disabled mode must not request thought
            # summaries even though the model may still use internal tokens.
            return {
                "google": {
                    "thinking_config": {
                        "thinking_level": "high" if enabled else "low",
                        "include_thoughts": enabled,
                    }
                }
            }
        return {"enable_thinking": enabled}

    def _emit_attempt(self, payload: dict[str, object]) -> None:
        if self.on_attempt is not None:
            self.on_attempt(payload)


def _is_retryable(exc: Exception) -> bool:
    if openai is None:
        return False
    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        ),
    ):
        return True
    return isinstance(exc, openai.APIStatusError) and exc.status_code >= 500


def redact_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            result.append(dict(message))
            continue
        cleaned: list[dict[str, Any]] = []
        for item in content:
            if item.get("type") == "image_url":
                cleaned.append({"type": "image_url", "image_url": "<redacted>"})
            else:
                cleaned.append(dict(item))
        result.append({**message, "content": cleaned})
    return result


class ApiBackend(_ChatBackend):
    def __init__(
        self,
        config: EvaluationConfig,
        api_key: str,
        *,
        on_attempt: AttemptCallback | None = None,
    ) -> None:
        if config.backend != "api":
            raise ValueError("ApiBackend requires backend='api'")
        if not api_key:
            raise ValueError("ApiBackend requires a non-empty API key")
        super().__init__(config, api_key, on_attempt=on_attempt)


class VllmBackend(_ChatBackend):
    def __init__(
        self,
        config: EvaluationConfig,
        api_key: str | None = None,
        *,
        on_attempt: AttemptCallback | None = None,
    ) -> None:
        if config.backend != "vllm":
            raise ValueError("VllmBackend requires backend='vllm'")
        super().__init__(config, api_key, on_attempt=on_attempt)

    def _thinking_body(self, enabled: bool) -> dict[str, Any]:
        return {"chat_template_kwargs": {"enable_thinking": enabled}}


