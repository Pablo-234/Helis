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


class FailIfCalledProvider:
    def complete(self, *, system: str, user: str) -> ModelResult:
        raise AssertionError("model should not be called without new or pending work")


def test_completed_cycle_does_not_reprocess_old_observations(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="Teams repeatedly lose leads while preparing quotes manually.", source="fixture")
    )
    provider = QueueProvider(
        [
            {
                "candidates": [
                    {
                        "title": "Quote workflow",
                        "problem": "Teams repeatedly lose leads while preparing quotes manually.",
                        "customer": "service teams",
                        "proposed_value": "Reduce quote turnaround time.",
                        "supporting_observation_ids": [str(observation.id)],
                        "tags": ["workflow"],
                    }
                ]
            },
            {
                "dimensions": {
                    "pain": 9,
                    "frequency": 9,
                    "willingness_to_pay": 8,
                    "market_access": 8,
                    "automation_fit": 9,
                    "speed_to_test": 9,
                    "competition_gap": 7,
                    "evidence_strength": 7,
                    "capital_efficiency": 9,
                    "execution_risk": 2
                },
                "rationale": [],
                "uncertainties": [],
            },
            {
                "assumptions": [
                    {
                        "statement": "Fast response materially affects buying behavior.",
                        "failure_mode": "Faster replies do not improve outcomes.",
                        "falsifier": "Comparable buyers show no preference for speed.",
                        "criticality": 9,
                        "uncertainty": 8,
                    }
                ],
                "contradictions": [],
                "missing_evidence": [],
            },
            {
                "experiments": [
                    {
                        "opportunity_id": "PLACEHOLDER",
                        "title": "Evidence review",
                        "experiment_type": "desk_research",
                        "hypothesis": "Response speed affects conversion.",
                        "success_metric": "independent sources",
                        "success_threshold": "3 credible sources",
                        "targeted_assumptions": [0],
                        "expected_information_gain": 7,
                        "effort_score": 2,
                        "max_cost_cents": 0,
                        "max_duration_hours": 2,
                        "requires_external_contact": False,
                        "requires_publication": False,
                    }
                ]
            },
        ]
    )
    original_complete = provider.complete

    def complete_with_dynamic_id(*, system: str, user: str) -> ModelResult:
        result = original_complete(system=system, user=user)
        if "Experiment Designer" in system:
            payload = json.loads(result.content)
            candidate_id = json.loads(user.split("\n", 1)[1])["opportunity"]["id"]
            payload["experiments"][0]["opportunity_id"] = candidate_id
            return ModelResult(content=json.dumps(payload), prompt_tokens=10, completion_tokens=10)
        return result

    provider.complete = complete_with_dynamic_id
    first_budget = CycleBudget(max_model_calls=4, max_tokens=1000)
    first = HelisCycle(engine, provider, first_budget).run(candidate_limit=1)

    second_budget = CycleBudget(max_model_calls=4, max_tokens=1000)
    second = HelisCycle(engine, FailIfCalledProvider(), second_budget).run(candidate_limit=1)

    assert first.experiments_planned == 1
    assert second.observations_used == 0
    assert second.candidates_discovered == 0
    assert second.candidates_evaluated == 0
    assert second_budget.model_calls == 0
