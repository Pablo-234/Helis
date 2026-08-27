from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.request import Request, urlopen


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

    @classmethod
    def from_env(cls) -> "OpenAICompatibleProvider":
        base_url = os.getenv("HELIS_LLM_BASE_URL", "http://localhost:11434/v1")
        model = os.getenv("HELIS_LLM_MODEL", "qwen3.5:9b")
        return cls(
            base_url=base_url,
            model=model,
            api_key=os.getenv("HELIS_LLM_API_KEY", ""),
            input_cost_per_million_tokens=float(os.getenv("HELIS_LLM_INPUT_COST_PER_M", "0")),
            output_cost_per_million_tokens=float(os.getenv("HELIS_LLM_OUTPUT_COST_PER_M", "0")),
        )

    def complete(self, *, system: str, user: str) -> ModelResult:
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=payload,
            headers=headers,
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            body = json.loads(response.read().decode("utf-8"))

        content = body["choices"][0]["message"]["content"]
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
