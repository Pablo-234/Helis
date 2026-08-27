import pytest

from helis.budget import BudgetExceeded, CycleBudget
from helis.model_provider import ModelResult


def test_cycle_budget_stops_extra_model_call() -> None:
    budget = CycleBudget(max_model_calls=1)
    budget.record(ModelResult(content="{}"))
    with pytest.raises(BudgetExceeded):
        budget.ensure_call_available()


def test_cycle_budget_tracks_tokens() -> None:
    budget = CycleBudget(max_tokens=10)
    with pytest.raises(BudgetExceeded):
        budget.record(ModelResult(content="{}", prompt_tokens=8, completion_tokens=4))
