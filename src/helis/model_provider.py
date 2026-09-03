from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ALLOWED_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "max"})


class ModelResponseError(ValueError):
    """Safe, bounded description of an invalid structured model response."""


def normalize_json_object(content: object) -> str:
    """Return a validated JSON object, accepting one optional Markdown fence."""
    if not isinstance(content, str):
        raise ModelResponseError("model returned non-text final content")
    normalized = content.strip()
    if not normalized:
        raise ModelResponseError(
            "model returned empty final content; for a supported thinking model set "
            "HELIS_LLM_REASONING_EFFORT=none"
        )
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"}:
            raise ModelResponseError("model returned an invalid Markdown JSON fence")
        normalized = "\n".join(lines[1:-1]).strip()
    try:
        decoded = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ModelResponseError(
            f"model returned invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    if not isinstance(decoded, dict):
        raise ModelResponseError("model returned JSON whose top level is not an object")
    return normalized


def _reasoning_effort_from_env(*, base_url: str, model: str) -> str | None:
    configured = os.getenv("HELIS_LLM_REASONING_EFFORT")
    if configured is not None:
        return configured.strip() or None
    endpoint = urlparse(base_url)
    if endpoint.hostname in {"localhost", "127.0.0.1", "::1"} and model.lower().startswith(
        "qwen3.5"
    ):
        return "none"
    return None


@dataclass(frozen=True, slots=True)
class ModelResult:
    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_cents: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ModelProvider(Protocol):
    def complete(self, *, system: str, user: str) -> ModelResult: ...


@dataclass(slots=True)
class OpenAICompatibleProvider:
    """Tiny adapter for OpenAI-compatible chat-completions APIs, including local servers."""

    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: int = 120
    input_cost_per_million_tokens: float = 0.0
    output_cost_per_million_tokens: float = 0.0
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        if self.reasoning_effort is not None:
            normalized = self.reasoning_effort.strip().lower()
            if normalized not in ALLOWED_REASONING_EFFORTS:
                allowed = ", ".join(sorted(ALLOWED_REASONING_EFFORTS))
                raise ValueError(f"HELIS_LLM_REASONING_EFFORT must be one of: {allowed}")
            self.reasoning_effort = normalized

    @classmethod
    def from_env(cls) -> OpenAICompatibleProvider:
        base_url = os.getenv("HELIS_LLM_BASE_URL", "http://localhost:11434/v1")
        model = os.getenv("HELIS_LLM_MODEL", "qwen3.5:9b")
        reasoning_effort = _reasoning_effort_from_env(base_url=base_url, model=model)
        return cls(
            base_url=base_url,
            model=model,
            api_key=os.getenv("HELIS_LLM_API_KEY", ""),
            input_cost_per_million_tokens=float(os.getenv("HELIS_LLM_INPUT_COST_PER_M", "0")),
            output_cost_per_million_tokens=float(os.getenv("HELIS_LLM_OUTPUT_COST_PER_M", "0")),
            reasoning_effort=reasoning_effort,
        )

    def complete(self, *, system: str, user: str) -> ModelResult:
        payload = json.dumps(self.completion_payload(system=system, user=user)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))

        content = normalize_json_object(body["choices"][0]["message"]["content"])
        usage = body.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cost_dollars = (
            prompt_tokens * self.input_cost_per_million_tokens
            + completion_tokens * self.output_cost_per_million_tokens
        ) / 1_000_000
        return ModelResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_cents=cost_dollars * 100,
        )

    def completion_payload(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload
