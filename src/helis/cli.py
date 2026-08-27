from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.budget import CycleBudget
from helis.cycle import CycleReport, HelisCycle
from helis.domain import Observation, Opportunity, ScoreDimensions
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.scout import OpportunityScout
from helis.source_registry import RegistryScanResult, SourceRegistry
from helis.sources import GitHubIssuesSource, RSSSource
from helis.store import HelisStore

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
        f"validation: target={report.validation_opportunity_id or '-'} "
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
def run(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    observation_limit: int = 100,
    candidate_limit: int = 5,
    max_calls: int = 8,
    max_tokens: int = 40_000,
    max_cost_cents: float = 25.0,
) -> None:
    """Scan configured markets, then run one bounded venture cycle."""
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
    table = Table("Score", "Decision", "Customer", "Opportunity")
    for item in ranked:
        table.add_row(
            f"{item.scorecard.total:.1f}",
            item.scorecard.recommendation.value,
            item.opportunity.customer,
            item.opportunity.title,
        )
    console.print(table)


@app.command()
def rank(db: Path = Path("helis.db")) -> None:
    _print_ranked(engine(db).ranked_queue())


if __name__ == "__main__":
    app()
