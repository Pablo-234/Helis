from helis.domain import (
    Assumption,
    Experiment,
    ExperimentType,
    Opportunity,
    SkepticReport,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.store import HelisStore


def test_challenge_and_experiment_are_audited(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = engine.ingest(
        Opportunity(
            title="Manual work",
            problem="A repetitive workflow consumes many hours every week.",
            customer="small firms",
            proposed_value="Reduce manual processing.",
        )
    )
    report = SkepticReport(
        opportunity_id=opportunity.id,
        assumptions=[
            Assumption(
                statement="The workflow is painful enough to change.",
                failure_mode="Customers keep their current process.",
                falsifier="Users reject even a low-friction trial.",
                criticality=9,
                uncertainty=8,
            )
        ],
    )
    engine.record_skeptic_report(report)
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Evidence review",
        experiment_type=ExperimentType.DESK_RESEARCH,
        hypothesis="The pain appears across independent sources.",
        success_metric="source count",
        success_threshold="3 sources",
        effort_score=2,
        expected_information_gain=6,
    )
    engine.plan_experiment(experiment, executable=True)

    stored = engine.store.get_opportunity(opportunity.id)
    assert stored is not None
    assert stored.stage == VentureStage.VALIDATING
    assert engine.store.get_skeptic_report(opportunity.id) is not None
    assert engine.store.list_experiments(opportunity.id)[0].id == experiment.id
    assert [event.event_type for event in engine.store.list_events()] == [
        "experiment.planned",
        "opportunity.challenged",
        "opportunity.discovered",
    ]
