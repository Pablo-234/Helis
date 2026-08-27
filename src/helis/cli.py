from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.budget import CycleBudget
from helis.cycle import CycleReport, HelisCycle
from helis.domain import (
    ExperimentRunStatus,
    Observation,
    Opportunity,
    ScoreDimensions,
    ValidationResult,
    utc_now,
)
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.policy import AutonomyPolicy
from helis.scout import OpportunityScout
from helis.source_registry import RegistryScanResult, SourceRegistry
from helis.sources import GitHubIssuesSource, RSSSource
from helis.store import HelisStore
from helis.validation_execution import ValidationBudget, ValidationRunner
from helis.validation_machine import ValidationMachine, ValidationTickReport

app = typer.Typer(help="HELIS autonomous venture engine")
console = Console()


def engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def configured_budget(max_calls: int, max_tokens: int, max_cost_cents: float) -> CycleBudget:
    return CycleBudget(
        max_model_calls=max_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_cost_cents,
    )


def _save_observations(helis: HelisEngine, observations: list[Observation]) -> None:
    before = len(helis.store.list_observations())
    for observation in observations:
        helis.observe(observation)
    after = len(helis.store.list_observations())
    console.print(f"scan: fetched={len(observations)} new={after - before}")


def _print_scan_failures(result: RegistryScanResult) -> None:
    for failure in result.failures:
        console.print(f"[yellow]source failed[/] {failure.source_name}: {failure.error}")


def _print_cycle_report(report: CycleReport, budget: CycleBudget) -> None:
    console.print(
        f"cycle: observations={report.observations_used} "
        f"discovered={report.candidates_discovered} evaluated={report.candidates_evaluated} "
        f"budget_exhausted={report.budget_exhausted}"
    )
    console.print(
        f"validation-plan: target={report.validation_opportunity_id or '-'} "
        f"experiments={report.experiments_planned} "
        f"autonomous={report.executable_experiments} "
        f"approval_required={report.approval_required_experiments}"
    )
    console.print(
        f"usage: calls={budget.model_calls}/{budget.max_model_calls} "
        f"tokens={budget.tokens}/{budget.max_tokens} cost≈{budget.cost_cents:.3f}¢"
    )
    _print_ranked(report.ranked)
    if report.validation_reviews:
        table = Table("Priority", "Autonomous", "Cost cap", "Experiment")
        for review in report.validation_reviews:
            table.add_row(
                f"{review.priority:.2f}",
                "yes" if review.executable else "approval",
                f"{review.experiment.max_cost_cents}¢",
                review.experiment.title,
            )
        console.print(table)


def _print_validation_tick(report: ValidationTickReport) -> None:
    if report.opportunity_id is None:
        console.print("validation-exec: no venture has pending validation work")
        return
    console.print(
        f"validation-exec: venture={report.opportunity_id} "
        f"waiting_approval={report.waiting_approval} blocked={report.blocked} "
        f"model_budget_exhausted={report.model_budget_exhausted}"
    )
    if report.run is not None:
        console.print(
            f"run={report.run.id} status={report.run.status.value} "
            f"adapter={report.run.adapter or '-'} cost={report.run.actual_cost_cents:.3f}¢"
        )
    if report.result is not None:
        console.print(
            f"result={report.result.outcome.value} confidence={report.result.confidence:.2f} "
            f"— {report.result.summary}"
        )
    if report.decision is not None:
        console.print(
            f"[bold]venture decision:[/] {report.decision.decision.value} "
            f"confidence={report.decision.confidence:.2f}"
        )
        if report.decision.suggested_pivot:
            console.print(f"pivot → {report.decision.suggested_pivot}")


@app.command()
def init(db: Path = Path("helis.db")) -> None:
    engine(db)
    console.print(f"[bold green]HELIS store ready:[/] {db}")


@app.command()
def observe(path: Path, db: Path = Path("helis.db")) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else [payload]
    helis = engine(db)
    for raw in items:
        observation = Observation.model_validate(raw)
        helis.observe(observation)
        console.print(f"observed {observation.id} — {observation.source}")


@app.command("scan-rss")
def scan_rss(url: str, db: Path = Path("helis.db"), limit: int = 50) -> None:
    helis = engine(db)
    _save_observations(helis, RSSSource(url=url, limit=limit).scan())


@app.command("scan-github")
def scan_github(
    repository: str,
    db: Path = Path("helis.db"),
    state: str = "open",
    limit: int = 50,
) -> None:
    helis = engine(db)
    _save_observations(
        helis,
        GitHubIssuesSource(repository=repository, state=state, limit=limit).scan(),
    )


@app.command("scan-config")
def scan_config(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
) -> None:
    helis = engine(db)
    result = SourceRegistry.from_toml(config).scan()
    _save_observations(helis, result.observations)
    _print_scan_failures(result)


@app.command()
def scout(
    db: Path = Path("helis.db"),
    limit: int = 100,
    max_calls: int = 1,
    max_tokens: int = 20_000,
    max_cost_cents: float = 5.0,
) -> None:
    helis = engine(db)
    observations = helis.store.list_observations(limit=limit)
    provider = OpenAICompatibleProvider.from_env()
    budget = configured_budget(max_calls, max_tokens, max_cost_cents)
    discovered = OpportunityScout(provider, budget).discover(observations)
    for opportunity in discovered:
        helis.ingest(opportunity)
        console.print(f"[green]candidate[/] {opportunity.id} — {opportunity.title}")
    console.print(
        f"scout cycle: {len(discovered)} candidates, {budget.model_calls} model calls, "
        f"{budget.tokens} tokens, ~{budget.cost_cents:.3f}¢ configured model cost"
    )


@app.command()
def cycle(
    db: Path = Path("helis.db"),
    observation_limit: int = 100,
    candidate_limit: int = 5,
    max_calls: int = 8,
    max_tokens: int = 40_000,
    max_cost_cents: float = 25.0,
) -> None:
    helis = engine(db)
    provider = OpenAICompatibleProvider.from_env()
    budget = configured_budget(max_calls, max_tokens, max_cost_cents)
    report = HelisCycle(helis, provider, budget).run(
        observation_limit=observation_limit,
        candidate_limit=candidate_limit,
    )
    _print_cycle_report(report, budget)


@app.command()
def validate(
    db: Path = Path("helis.db"),
    opportunity_id: str | None = None,
    max_calls: int = 2,
    max_tokens: int = 25_000,
    max_cost_cents: float = 10.0,
) -> None:
    """Execute one safe validation step and update the venture decision."""
    helis = engine(db)
    provider = OpenAICompatibleProvider.from_env()
    budget = configured_budget(max_calls, max_tokens, max_cost_cents)
    report = ValidationMachine(
        helis,
        provider,
        budget,
        validation_budget=ValidationBudget(max_executions=1, max_cash_cents=0),
    ).tick(UUID(opportunity_id) if opportunity_id else None)
    _print_validation_tick(report)
    console.print(
        f"validation usage: calls={budget.model_calls}/{budget.max_model_calls} "
        f"tokens={budget.tokens}/{budget.max_tokens} cost≈{budget.cost_cents:.3f}¢"
    )


@app.command("approve-run")
def approve_run(run_id: str, db: Path = Path("helis.db")) -> None:
    """Grant one-time approval to one specific waiting experiment run."""
    helis = engine(db)
    approved = ValidationRunner(helis, AutonomyPolicy()).approve(UUID(run_id))
    console.print(f"approved run {approved.id}; status={approved.status.value}")


@app.command("record-result")
def record_result(path: Path, db: Path = Path("helis.db")) -> None:
    """Ingest a result produced by an external/manual validation adapter."""
    helis = engine(db)
    result = ValidationResult.model_validate_json(path.read_text(encoding="utf-8"))
    helis.record_validation_result(result)
    run = helis.store.get_experiment_run(result.run_id)
    if run is not None and run.status != ExperimentRunStatus.COMPLETED:
        completed = run.model_copy(
            update={
                "status": ExperimentRunStatus.COMPLETED,
                "completed_at": utc_now(),
                "actual_cost_cents": result.actual_cost_cents,
                "error": None,
                "updated_at": utc_now(),
            }
        )
        helis.record_experiment_run(completed, event_type="experiment.completed_external")
    console.print(
        f"recorded validation result {result.id}: {result.outcome.value} "
        f"confidence={result.confidence:.2f}"
    )


@app.command()
def run(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    observation_limit: int = 100,
    candidate_limit: int = 5,
    max_calls: int = 10,
    max_tokens: int = 60_000,
    max_cost_cents: float = 25.0,
) -> None:
    """Scan markets, discover/evaluate ventures, then execute one safe validation step."""
    helis = engine(db)
    scan_result = SourceRegistry.from_toml(config).scan()
    _save_observations(helis, scan_result.observations)
    _print_scan_failures(scan_result)

    provider = OpenAICompatibleProvider.from_env()
    budget = configured_budget(max_calls, max_tokens, max_cost_cents)
    report = HelisCycle(helis, provider, budget).run(
        observation_limit=observation_limit,
        candidate_limit=candidate_limit,
    )
    _print_cycle_report(report, budget)

    validation_report = ValidationMachine(
        helis,
        provider,
        budget,
        validation_budget=ValidationBudget(max_executions=1, max_cash_cents=0),
    ).tick(report.validation_opportunity_id)
    _print_validation_tick(validation_report)
    console.print(
        f"total usage: calls={budget.model_calls}/{budget.max_model_calls} "
        f"tokens={budget.tokens}/{budget.max_tokens} cost≈{budget.cost_cents:.3f}¢"
    )


@app.command()
def ingest(path: Path, db: Path = Path("helis.db")) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else [payload]
    helis = engine(db)
    for raw in items:
        opportunity = Opportunity.model_validate(raw)
        helis.ingest(opportunity)
        console.print(f"ingested {opportunity.id} — {opportunity.title}")


@app.command()
def evaluate(
    opportunity_id: str,
    scores: Path,
    db: Path = Path("helis.db"),
) -> None:
    helis = engine(db)
    opportunity = helis.store.get_opportunity(UUID(opportunity_id))
    if opportunity is None:
        raise typer.BadParameter("opportunity not found")
    dimensions = ScoreDimensions.model_validate_json(scores.read_text(encoding="utf-8"))
    card = helis.evaluate(opportunity, dimensions)
    console.print(f"score={card.total:.2f} recommendation={card.recommendation.value}")


def _print_ranked(ranked: list) -> None:
    table = Table("Score", "Decision", "Stage", "Customer", "Opportunity")
    for item in ranked:
        table.add_row(
            f"{item.scorecard.total:.1f}",
            item.scorecard.recommendation.value,
            item.opportunity.stage.value,
            item.opportunity.customer,
            item.opportunity.title,
        )
    console.print(table)


@app.command()
def rank(db: Path = Path("helis.db")) -> None:
    _print_ranked(engine(db).ranked_queue())


if __name__ == "__main__":
    app()
