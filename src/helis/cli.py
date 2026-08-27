from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.budget import CycleBudget
from helis.domain import Observation, Opportunity, ScoreDimensions
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.scout import OpportunityScout
from helis.store import HelisStore

app = typer.Typer(help="HELIS autonomous venture engine")
console = Console()


def engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


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
    budget = CycleBudget(
        max_model_calls=max_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_cost_cents,
    )
    discovered = OpportunityScout(provider, budget).discover(observations)
    for opportunity in discovered:
        helis.ingest(opportunity)
        console.print(f"[green]candidate[/] {opportunity.id} — {opportunity.title}")
    console.print(
        f"scout cycle: {len(discovered)} candidates, {budget.model_calls} model calls, "
        f"{budget.tokens} tokens, ~{budget.cost_cents:.3f}¢ configured model cost"
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


@app.command()
def rank(db: Path = Path("helis.db")) -> None:
    ranked = engine(db).ranked_queue()
    table = Table("Score", "Decision", "Customer", "Opportunity")
    for item in ranked:
        table.add_row(
            f"{item.scorecard.total:.1f}",
            item.scorecard.recommendation.value,
            item.opportunity.customer,
            item.opportunity.title,
        )
    console.print(table)


if __name__ == "__main__":
    app()
