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


def test_full_cycle_challenges_best_candidate_and_plans_experiment(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="Businesses repeatedly report losing leads while quoting manually.", source="fixture")
    )
    provider = QueueProvider(
        [
            {
                "candidates": [
                    {
                        "title": "Quote workflow",
                        "problem": "Businesses lose leads while manually preparing repetitive quotes.",
                        "customer": "service businesses",
                        "proposed_value": "Make quote preparation faster.",
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
                "rationale": ["Strong candidate."],
                "uncertainties": [],
            },
            {
                "assumptions": [
                    {
                        "statement": "Faster quotes materially improve conversion.",
                        "failure_mode": "Customers do not care enough about response speed.",
                        "falsifier": "Comparable buyers show no conversion difference.",
                        "criticality": 9,
                        "uncertainty": 8,
                    }
                ],
                "contradictions": [],
                "missing_evidence": ["No causal conversion evidence."],
            },
            {
                "experiments": [
                    {
                        "opportunity_id": "PLACEHOLDER",
                        "title": "Evidence search",
                        "experiment_type": "desk_research",
                        "hypothesis": "Response time affects lead conversion.",
                        "success_metric": "independent evidence sources",
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
    budget = CycleBudget(max_model_calls=4, max_tokens=1000)

    report = HelisCycle(engine, provider, budget).run(candidate_limit=1)

    assert report.candidates_evaluated == 1
    assert report.validation_opportunity_id is not None
    assert report.experiments_planned == 1
    assert report.executable_experiments == 1
    assert report.approval_required_experiments == 0
    assert not report.budget_exhausted
    assert budget.model_calls == 4
