from helis.domain import Evidence, EvidenceKind, Observation, Opportunity
from helis.engine import HelisEngine
from helis.store import HelisStore


def test_candidates_supported_by_same_observation_are_merged(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = Observation(text="Teams lose hours preparing quotes manually.", source="fixture")
    evidence = Evidence(
        kind=EvidenceKind.WORKFLOW,
        claim=observation.text,
        source=observation.source,
        observation_id=observation.id,
    )
    first = engine.ingest(
        Opportunity(
            title="Faster quoting",
            problem="Teams lose substantial time preparing service quotes manually.",
            customer="service teams",
            proposed_value="Reduce manual quote preparation.",
            evidence=[evidence],
        )
    )
    second = engine.ingest(
        Opportunity(
            title="Quote automation",
            problem="Manual service quote preparation consumes recurring staff time.",
            customer="service companies",
            proposed_value="Speed up repetitive quote work.",
            evidence=[evidence],
        )
    )

    assert second.id == first.id
    assert len(engine.store.list_opportunities()) == 1
    assert engine.store.list_events()[0].event_type == "opportunity.reinforced"
