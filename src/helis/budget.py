from __future__ import annotations

from dataclasses import dataclass

from helis.model_provider import ModelResult


class BudgetExceeded(RuntimeError):
    pass


@dataclass(slots=True)
class CycleBudget:
    max_model_calls: int = 8
    max_tokens: int = 40_000
    max_cost_cents: float = 25.0
    model_calls: int = 0
    tokens: int = 0
    cost_cents: float = 0.0

    def ensure_call_available(self) -> None:
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded("model call budget exhausted")

    def record(self, result: ModelResult) -> None:
        self.model_calls += 1
        self.tokens += result.total_tokens
        self.cost_cents += result.estimated_cost_cents
        if self.model_calls > self.max_model_calls:
            raise BudgetExceeded("model call budget exceeded")
        if self.tokens > self.max_tokens:
            raise BudgetExceeded("token budget exceeded")
        if self.cost_cents > self.max_cost_cents:
            raise BudgetExceeded("cost budget exceeded")
