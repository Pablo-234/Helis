import json

from helis.domain import Observation
from helis.model_provider import ModelResult
from helis.scout import OpportunityScout


class FakeProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def complete(self, *, system: str, user: str) -> ModelResult:
        assert "Do not invent evidence" in system
        return ModelResult(content=json.dumps(self.payload), prompt_tokens=10, completion_tokens=10)


def test_scout_only_accepts_candidates_with_real_observation_ids() -> None:
    observation = Observation(text="Customers wait hours for manual quotes.", source="fixture")
    provider = FakeProvider(
        {
            "candidates": [
                {
                    "title": "Fast quote workflow",
                    "problem": "Customers wait too long for manually prepared service quotes.",
                    "customer": "service businesses",
                    "proposed_value": "Reduce quote turnaround time.",
                    "supporting_observation_ids": [str(observation.id)],
                    "tags": ["quotes"],
                },
                {
                    "title": "Unsupported idea",
                    "problem": "This candidate has no evidence from the supplied observations.",
                    "customer": "unknown",
                    "proposed_value": "Something invented",
                    "supporting_observation_ids": ["00000000-0000-0000-0000-000000000000"],
                    "tags": [],
                },
            ]
        }
    )

    opportunities = OpportunityScout(provider).discover([observation])

    assert len(opportunities) == 1
    assert opportunities[0].evidence[0].observation_id == observation.id
