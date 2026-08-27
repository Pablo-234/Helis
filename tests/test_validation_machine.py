from __future__ import annotations

import json

from helis.budget import CycleBudget
from helis.decision import VentureDecisionEngine
from helis.domain import (
    Experiment,
    ExperimentRunStatus,
    ExperimentType,
    Observation,
    Opportunity,
    ValidationOutcome,
    ValidationResult,
    VentureDecisionKind,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.model_provider import ModelResult
from helis.policy import AutonomyPolicy
from helis.store import HelisStore
from helis.validation_execution import ValidationBudget, ValidationRunner
from helis.validation_machine import ValidationMachine


class FakeProvider:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)

    def complete(self, *, system: str, user: str) -> ModelResult:
        payload = self.payloads.pop(0)
        return ModelResult(content=json.dumps(payload), prompt_tokens=10, completion_tokens=5)


def test_external_contact_waits_for_approval(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Interview test",
        problem="Customers report a repeated operational bottleneck.",
        customer="operators",
        proposed_value="remove the bottleneck",
        stage=VentureStage.VALIDATING,
    )
    engine.ingest(opportunity)
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Interview five operators",
        experiment_type=ExperimentType.INTERVIEW,
        hypothesis="Operators will confirm the bottleneck is costly.",
        success_metric="confirmed interviews",
        success_threshold=">= 3",
        requires_external_contact=True,
    )
    engine.plan_experiment(experiment, executable=False)

    runner = ValidationRunner(engine, AutonomyPolicy(), ValidationBudget())
    assert runner.execute_next(opportunity) is None
    runs = engine.store.list_experiment_runs(experiment_id=experiment.id)
    assert runs[0].status == ExperimentRunStatus.WAITING_APPROVAL


def test_desk_research_executes_decides_and_plans_follow_up(tmp_path) -> None:
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    observation = Observation(
        text="Operators complain that manual quoting takes hours every week.",
        source="test://market",
    )
    engine.observe(observation)
    opportunity = Opportunity(
        title="Quote automation",
        problem="Manual quoting consumes recurring operator time.",
        customer="service operators",
        proposed_value="automate quote preparation",
        stage=VentureStage.VALIDATING,
    )
    engine.ingest(opportunity)
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Check repeated quoting pain",
        experiment_type=ExperimentType.DESK_RESEARCH,
        hypothesis="Manual quoting is a recurring pain.",
        success_metric="independent pain signals",
        success_threshold=">= 1",
        max_cost_cents=0,
    )
    engine.plan_experiment(experiment, executable=True)
    provider = FakeProvider(
        [
            {
                "outcome": "positive",
                "confidence": 0.75,
                "summary": "The observation directly reports recurring manual quoting pain.",
                "supporting_observation_ids": [str(observation.id)],
                "metrics": {"pain_signals": 1},
                "pivot_signal": None,
            },
            {
                "experiment": {
                    "opportunity_id": str(opportunity.id),
                    "title": "Test willingness to pay",
                    "experiment_type": "pricing",
                    "hypothesis": "Operators will accept a paid offer for faster quoting.",
                    "success_metric": "paid offer acceptances",
                    "success_threshold": ">= 1",
                    "targeted_assumptions": [],
                    "expected_information_gain": 9,
                    "effort_score": 4,
                    "max_cost_cents": 0,
                    "max_duration_hours": 24,
                    "requires_external_contact": True,
                    "requires_publication": False,
                },
                "reason": "Pain is supported, but willingness to pay remains untested.",
            },
        ]
    )
    machine = ValidationMachine(
        engine,
        provider,
        CycleBudget(max_model_calls=3),
        validation_budget=ValidationBudget(max_cash_cents=0),
    )
    report = machine.tick(opportunity.id)

    assert report.result is not None
    assert report.result.outcome == ValidationOutcome.POSITIVE
    assert report.result.supporting_observation_ids == [observation.id]
    assert report.run is not None
    assert report.run.status == ExperimentRunStatus.COMPLETED
    assert report.decision is not None
    assert report.decision.decision == VentureDecisionKind.CONTINUE
    assert report.follow_up_planned is not None
    assert report.follow_up_planned.experiment_type == ExperimentType.PRICING


def test_decision_engine_kills_strongly_falsified_venture() -> None:
    opportunity = Opportunity(
        title="Bad bet",
        problem="A proposed workflow problem might not actually exist.",
        customer="teams",
        proposed_value="automate it",
    )
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Falsify demand",
        experiment_type=ExperimentType.DESK_RESEARCH,
        hypothesis="The problem appears repeatedly.",
        success_metric="signals",
        success_threshold=">= 1",
    )
    result = ValidationResult(
        run_id=experiment.id,
        experiment_id=experiment.id,
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.NEGATIVE,
        confidence=0.92,
        summary="Strong evidence contradicts the demand hypothesis.",
    )
    decision = VentureDecisionEngine().decide(opportunity, [experiment], [result])
    assert decision.decision == VentureDecisionKind.KILL


def test_decision_engine_advances_only_with_independent_positive_tests() -> None:
    opportunity = Opportunity(
        title="Validated bet",
        problem="A recurring costly workflow is repeatedly reported.",
        customer="teams",
        proposed_value="automate it",
    )
    desk = Experiment(
        opportunity_id=opportunity.id,
        title="Research pain",
        experiment_type=ExperimentType.DESK_RESEARCH,
        hypothesis="Pain exists.",
        success_metric="signals",
        success_threshold=">= 2",
    )
    pricing = Experiment(
        opportunity_id=opportunity.id,
        title="Test price",
        experiment_type=ExperimentType.PRICING,
        hypothesis="Customers accept a paid offer.",
        success_metric="acceptance",
        success_threshold=">= 1",
    )
    results = [
        ValidationResult(
            run_id=desk.id,
            experiment_id=desk.id,
            opportunity_id=opportunity.id,
            outcome=ValidationOutcome.POSITIVE,
            confidence=0.75,
            summary="Pain is repeatedly observed.",
        ),
        ValidationResult(
            run_id=pricing.id,
            experiment_id=pricing.id,
            opportunity_id=opportunity.id,
            outcome=ValidationOutcome.POSITIVE,
            confidence=0.75,
            summary="A paid offer was accepted.",
        ),
    ]
    decision = VentureDecisionEngine().decide(opportunity, [desk, pricing], results)
    assert decision.decision == VentureDecisionKind.ADVANCE
