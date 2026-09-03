import json

from helis.budget import CycleBudget
from helis.cycle import HelisCycle
from helis.domain import Observation
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.store import HelisStore


class QueueProvider:
    def __init__(self, responses: list[dict | str]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def complete(self, *, system: str, user: str) -> ModelResult:
        self.calls += 1
        response = self.responses.pop(0)
        content = response if isinstance(response, str) else json.dumps(response)
        return ModelResult(content=content, prompt_tokens=10, completion_tokens=10)


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


def test_empty_scout_pass_gets_one_focused_retry(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="Teams repeatedly assemble customer reports manually.", source="fixture")
    )
    provider = QueueProvider(
        [
            {"candidates": []},
            {
                "candidates": [
                    {
                        "title": "Reporting workflow",
                        "problem": "Teams repeatedly assemble customer reports manually.",
                        "customer": "service teams",
                        "proposed_value": "Reduce repetitive reporting work.",
                        "supporting_observation_ids": [str(observation.id)],
                        "tags": ["workflow", "hypothesis"],
                    }
                ]
            },
            {
                "dimensions": {},
                "rationale": ["A directly observed manual workflow."],
                "uncertainties": ["Willingness to pay is not observed."],
            },
        ]
    )

    report = HelisCycle(
        engine,
        provider,
        CycleBudget(max_model_calls=3, max_tokens=1000),
    ).run()

    assert provider.calls == 3
    assert report.candidates_discovered == 1
    assert report.candidates_evaluated == 1


def test_invalid_scout_schema_gets_one_structured_recovery_pass(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="Teams repeatedly assemble customer reports manually.", source="fixture")
    )
    provider = QueueProvider(
        [
            '{"candidates": "not-a-list"}',
            {
                "candidates": [
                    {
                        "title": "Reporting workflow",
                        "problem": "Teams repeatedly assemble customer reports manually.",
                        "customer": "service teams",
                        "proposed_value": "Reduce repetitive reporting work.",
                        "supporting_observation_ids": [str(observation.id)],
                    }
                ]
            },
            {"dimensions": {}, "rationale": [], "uncertainties": []},
        ]
    )

    report = HelisCycle(
        engine,
        provider,
        CycleBudget(max_model_calls=3, max_tokens=1000),
    ).run()

    assert provider.calls == 3
    assert report.candidates_discovered == 1
    assert report.candidates_evaluated == 1


def test_cycle_replays_processed_history_when_no_idea_exists(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="Teams repeatedly assemble customer reports manually.", source="fixture")
    )
    engine.store.mark_observations_processed([observation.id])
    provider = QueueProvider(
        [
            {
                "candidates": [
                    {
                        "title": "Reporting workflow",
                        "problem": "Teams repeatedly assemble customer reports manually.",
                        "customer": "service teams",
                        "proposed_value": "Reduce repetitive reporting work.",
                        "supporting_observation_ids": [str(observation.id)],
                    }
                ]
            },
            {"dimensions": {}, "rationale": [], "uncertainties": []},
        ]
    )

    report = HelisCycle(
        engine,
        provider,
        CycleBudget(max_model_calls=2, max_tokens=1000),
    ).run()

    assert report.observations_replayed is True
    assert report.observations_used == 1
    assert report.candidates_discovered == 1


def test_empty_scout_result_keeps_new_observations_pending(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = engine.observe(
        Observation(text="A customer describes a recurring manual workflow.", source="fixture")
    )
    provider = QueueProvider([{"candidates": []}, {"candidates": []}])

    report = HelisCycle(
        engine,
        provider,
        CycleBudget(max_model_calls=2, max_tokens=1000),
    ).run()

    assert report.candidates_discovered == 0
    assert [item.id for item in engine.store.list_unprocessed_observations()] == [observation.id]
