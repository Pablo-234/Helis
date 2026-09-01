from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from datetime import datetime
from enum import StrEnum
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.model_provider import OpenAICompatibleProvider
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy


class LocalModelState(StrEnum):
    INVALID_CONFIG = "invalid_config"
    ENDPOINT_DOWN = "endpoint_down"
    INCOMPATIBLE = "incompatible"
    MODEL_MISSING = "model_missing"
    READY = "ready"


class LocalModelReport(BaseModel):
    state: LocalModelState
    endpoint: str = Field(min_length=1, max_length=1000)
    configured_model: str = Field(min_length=1, max_length=300)
    endpoint_reachable: bool = False
    model_available: bool = False
    available_models: list[str] = Field(default_factory=list, max_length=100)
    ollama_cli: str | None = Field(default=None, max_length=1000)
    detail: str = Field(min_length=1, max_length=2000)
    next_command: str = Field(min_length=1, max_length=1000)
    inspected_at: datetime = Field(default_factory=utc_now)


class LocalModelSmokeReport(BaseModel):
    success: bool
    model: str = Field(min_length=1, max_length=300)
    endpoint: str = Field(min_length=1, max_length=1000)
    response_status: str | None = Field(default=None, max_length=80)
    latency_seconds: float = Field(default=0, ge=0)
    error: str | None = Field(default=None, max_length=2000)
    completed_at: datetime = Field(default_factory=utc_now)


def is_local_model_endpoint(base_url: str) -> bool:
    parsed = urlparse(base_url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        and parsed.username is None
        and parsed.password is None
    )


class LocalModelInspector:
    """Inspect and smoke-test a credential-free localhost OpenAI-compatible runtime."""

    def __init__(
        self,
        provider: OpenAICompatibleProvider,
        *,
        opener: Callable[..., Any] | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self.provider = provider
        self.opener = opener or urlopen
        self.which = which or shutil.which

    def inspect(self, *, timeout_seconds: float = 3.0) -> LocalModelReport:
        invalid = self._invalid_reason()
        cli = self.which("ollama")
        if invalid is not None:
            return LocalModelReport(
                state=LocalModelState.INVALID_CONFIG,
                endpoint=self.provider.base_url or "missing",
                configured_model=self.provider.model or "missing",
                ollama_cli=cli,
                detail=invalid,
                next_command="set HELIS_LLM_BASE_URL to http://localhost:11434/v1",
            )
        allowed, reason = self._network_read_allowed("inspect local model inventory")
        if not allowed:
            return LocalModelReport(
                state=LocalModelState.INVALID_CONFIG,
                endpoint=self.provider.base_url,
                configured_model=self.provider.model,
                ollama_cli=cli,
                detail=reason,
                next_command="review HELIS network-read policy",
            )
        request = Request(
            f"{self.provider.base_url.rstrip('/')}/models",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self.opener(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                payload = response.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 -- diagnostic boundary returns next action
            next_command = "ollama serve" if cli else "install Ollama from https://ollama.com/download"
            return LocalModelReport(
                state=LocalModelState.ENDPOINT_DOWN,
                endpoint=self.provider.base_url,
                configured_model=self.provider.model,
                ollama_cli=cli,
                detail=f"{type(exc).__name__}: {exc}"[:2000],
                next_command=next_command,
            )
        try:
            if not 200 <= status < 300:
                raise RuntimeError(f"metadata endpoint returned HTTP {status}")
            body = json.loads(payload)
            available = self._model_ids(body)
        except Exception as exc:  # noqa: BLE001 -- classify an incompatible local API
            return LocalModelReport(
                state=LocalModelState.INCOMPATIBLE,
                endpoint=self.provider.base_url,
                configured_model=self.provider.model,
                endpoint_reachable=True,
                ollama_cli=cli,
                detail=f"{type(exc).__name__}: {exc}"[:2000],
                next_command=(
                    "verify OpenAI compatibility at "
                    "https://docs.ollama.com/api/openai-compatibility"
                ),
            )

        if self.provider.model not in available:
            command = (
                f"ollama pull {self.provider.model}"
                if cli
                else "install Ollama from https://ollama.com/download"
            )
            return LocalModelReport(
                state=LocalModelState.MODEL_MISSING,
                endpoint=self.provider.base_url,
                configured_model=self.provider.model,
                endpoint_reachable=True,
                available_models=available[:100],
                ollama_cli=cli,
                detail=f"endpoint is healthy but {self.provider.model!r} is not installed",
                next_command=command,
            )
        return LocalModelReport(
            state=LocalModelState.READY,
            endpoint=self.provider.base_url,
            configured_model=self.provider.model,
            endpoint_reachable=True,
            model_available=True,
            available_models=available[:100],
            ollama_cli=cli,
            detail="configured model is present in the local OpenAI-compatible inventory",
            next_command="helis-live model-smoke",
        )

    def smoke(self, *, timeout_seconds: float = 60.0) -> LocalModelSmokeReport:
        inventory = self.inspect(timeout_seconds=min(timeout_seconds, 5.0))
        if inventory.state != LocalModelState.READY:
            return LocalModelSmokeReport(
                success=False,
                model=self.provider.model or "missing",
                endpoint=self.provider.base_url or "missing",
                error=f"{inventory.state.value}: {inventory.detail}; next: {inventory.next_command}",
            )
        allowed, reason = self._network_read_allowed("request one bounded local model smoke result")
        if not allowed:
            return LocalModelSmokeReport(
                success=False,
                model=self.provider.model,
                endpoint=self.provider.base_url,
                error=reason,
            )
        payload = json.dumps(
            {
                "model": self.provider.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "Return one small JSON object and no prose.",
                    },
                    {
                        "role": "user",
                        "content": 'Return exactly this JSON meaning: {"status":"ok"}.',
                    },
                ],
                "temperature": 0,
                "max_tokens": 96,
                "response_format": {"type": "json_object"},
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self.provider.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        started = perf_counter()
        try:
            with self.opener(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8")
            if not 200 <= status < 300:
                raise RuntimeError(f"completion endpoint returned HTTP {status}")
            body = json.loads(raw)
            content = body["choices"][0]["message"]["content"]
            decoded = json.loads(self._strip_fence(content))
            if not isinstance(decoded, dict) or decoded.get("status") != "ok":
                raise ValueError("model did not return the required status object")
        except Exception as exc:  # noqa: BLE001 -- smoke result is a diagnostic artifact
            return LocalModelSmokeReport(
                success=False,
                model=self.provider.model,
                endpoint=self.provider.base_url,
                latency_seconds=perf_counter() - started,
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
        return LocalModelSmokeReport(
            success=True,
            model=self.provider.model,
            endpoint=self.provider.base_url,
            response_status="ok",
            latency_seconds=perf_counter() - started,
        )

    def _invalid_reason(self) -> str | None:
        if not self.provider.model or not self.provider.base_url:
            return "model name or base URL is missing"
        if not is_local_model_endpoint(self.provider.base_url):
            return "local pilot requires an uncredentialed localhost endpoint"
        if self.provider.api_key:
            return "local pilot refuses model API credentials"
        if any(
            (
                self.provider.input_cost_per_million_tokens,
                self.provider.output_cost_per_million_tokens,
            )
        ):
            return "local pilot requires configured input/output token prices of zero"
        return None

    @staticmethod
    def _model_ids(payload: object) -> list[str]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise TypeError("metadata response does not contain an OpenAI-compatible data list")
        ids = [
            item.get("id")
            for item in payload["data"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ]
        return sorted(set(ids))

    @staticmethod
    def _strip_fence(content: object) -> str:
        if not isinstance(content, str):
            raise TypeError("completion content is not text")
        stripped = content.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    @staticmethod
    def _network_read_allowed(description: str) -> tuple[bool, str]:
        decision = AutonomyPolicy().evaluate(
            ActionRequest(kind=ActionKind.NETWORK_READ, description=description)
        )
        return decision.allowed and not decision.requires_approval, decision.reason
