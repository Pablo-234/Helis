from __future__ import annotations

import json

import pytest

from helis.domain import (
    Experiment,
    ExperimentRunStatus,
    ExperimentType,
    Opportunity,
    ValidationOutcome,
    ValidationResult,
    VentureStage,
)
from helis.engine import HelisEngine
from helis.policy import AutonomyPolicy
from helis.store import HelisStore
from helis.validation_execution import ValidationBudget, ValidationRunner
from helis.validation_gateway import ApprovedValidationGateway, GatewayConfigurationError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


def test_gateway_rejects_insecure_remote_url() -> None:
    with pytest.raises(GatewayConfigurationError):
        ApprovedValidationGateway(url="http://example.com/validate")


def test_gateway_forces_run_approval_and_dispatches_once(tmp_path, monkeypatch) -> None:
    sent = []

    def fake_urlopen(request, timeout):
        sent.append((request, timeout))
        return FakeResponse({"accepted": True, "dispatch_id": "dispatch-123"})

    monkeypatch.setattr("helis.validation_gateway.urlopen", fake_urlopen)
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Interview validation",
        problem="A recurring operational problem needs customer validation.",
        customer="operators",
        proposed_value="remove repetitive work",
        stage=VentureStage.VALIDATING,
    )
    engine.ingest(opportunity)
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Ask operators about the pain",
        experiment_type=ExperimentType.INTERVIEW,
        hypothesis="Operators experience this pain weekly.",
        success_metric="confirmed pain interviews",
        success_threshold=">= 3",
        max_cost_cents=0,
        requires_external_contact=False,
    )
    engine.plan_experiment(experiment, executable=True)
    gateway = ApprovedValidationGateway(url="https://gateway.example/helis")
    runner = ValidationRunner(
        engine,
        AutonomyPolicy(),
        ValidationBudget(max_executions=1, max_cash_cents=0),
        executors={ExperimentType.INTERVIEW: gateway},
    )

    assert runner.execute_next(opportunity) is None
    waiting = engine.store.list_experiment_runs(experiment_id=experiment.id)[0]
    assert waiting.status == ExperimentRunStatus.WAITING_APPROVAL
    assert sent == []

    runner.approve(waiting.id)
    outcome = runner.execute_next(opportunity)
    assert outcome is not None
    assert outcome.dispatch is not None
    assert outcome.dispatch.dispatch_id == "dispatch-123"
    assert outcome.run.status == ExperimentRunStatus.WAITING_RESULT
    assert outcome.run.external_ref == "dispatch-123"
    assert len(sent) == 1
    request = sent[0][0]
    assert request.full_url == "https://gateway.example/helis"
    assert request.get_header("Idempotency-key") == str(waiting.id)

    assert runner.execute_next(opportunity) is None
    assert len(sent) == 1


def test_external_result_must_match_waiting_run(tmp_path, monkeypatch) -> None:
    def fake_urlopen(request, timeout):
        return FakeResponse({"accepted": True, "dispatch_id": "dispatch-456"})

    monkeypatch.setattr("helis.validation_gateway.urlopen", fake_urlopen)
    engine = HelisEngine(HelisStore(tmp_path / "helis.db"))
    opportunity = Opportunity(
        title="Pricing validation",
        problem="A costly recurring problem needs willingness-to-pay validation.",
        customer="operators",
        proposed_value="save operator time",
        stage=VentureStage.VALIDATING,
    )
    engine.ingest(opportunity)
    experiment = Experiment(
        opportunity_id=opportunity.id,
        title="Test price acceptance",
        experiment_type=ExperimentType.PRICING,
        hypothesis="At least one operator accepts the proposed paid offer.",
        success_metric="accepted offers",
        success_threshold=">= 1",
        max_cost_cents=0,
    )
    engine.plan_experiment(experiment, executable=True)
    gateway = ApprovedValidationGateway(url="https://gateway.example/helis")
    runner = ValidationRunner(
        engine,
        AutonomyPolicy(),
        ValidationBudget(max_executions=1, max_cash_cents=0),
        executors={ExperimentType.PRICING: gateway},
    )
    runner.execute_next(opportunity)
    waiting = engine.store.list_experiment_runs(experiment_id=experiment.id)[0]
    runner.approve(waiting.id)
    dispatched = runner.execute_next(opportunity)
    assert dispatched is not None

    result = ValidationResult(
        run_id=dispatched.run.id,
        experiment_id=experiment.id,
        opportunity_id=opportunity.id,
        outcome=ValidationOutcome.POSITIVE,
        confidence=0.8,
        summary="One operator accepted the proposed paid offer.",
        source="validation_gateway",
    )
    completed = runner.complete_external(result)
    assert completed.status == ExperimentRunStatus.COMPLETED
    assert engine.store.list_validation_results(opportunity.id)[0].id == result.id

    with pytest.raises(ValueError):
        runner.complete_external(result)
