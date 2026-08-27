import json

from helis.budget import CycleBudget
from helis.cycle import HelisCycle
from helis.domain import Observation
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.store import HelisStore


class QueueProvider:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)

    def complete(self, *, system: str, user: str) -> ModelResult:
        return ModelResult(content=json.dumps(self.responses.pop(0)), prompt_tokens=10, completion_tokens=10)


def test_cycle_discovers_and_evaluates_evidence_bound_candidate(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="Customers repeatedly wait hours for manual quotes.", source="fixture")
    )
    provider = QueueProvider(
        [
            {
                "candidates": [
                    {
                        "title": "Faster quote workflow",
                        "problem": "Customers wait too long for manually prepared service quotes.",
                        "customer": "service businesses",
                        "proposed_value": "Reduce quote turnaround time.",
                        "supporting_observation_ids": [str(observation.id)],
                        "tags": ["workflow"],
                    }
                ]
            },
            {
                "dimensions": {
                    "pain": 8,
                    "frequency": 8,
                    "willingness_to_pay": 4,
                    "market_access": 5,
                    "automation_fit": 8,
                    "speed_to_test": 8,
                    "competition_gap": 3,
                    "evidence_strength": 4,
                    "capital_efficiency": 8,
                    "execution_risk": 3
                },
                "rationale": ["Observed recurring delay."],
                "uncertainties": ["No willingness-to-pay evidence yet."]
            }
        ]
    )
    budget = CycleBudget(max_model_calls=2, max_tokens=1000)

    report = HelisCycle(engine, provider, budget).run()

    assert report.candidates_discovered == 1
    assert report.candidates_evaluated == 1
    assert len(report.ranked) == 1
    assert budget.model_calls == 2
