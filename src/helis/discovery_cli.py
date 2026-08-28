from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from helis.discovery_wake import (
    DiscoveryRuntime,
    DiscoveryWakeController,
    DiscoveryWakePolicy,
    DiscoveryWakeResult,
    DiscoveryWakeStore,
)
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.source_registry import SourceRegistry
from helis.store import HelisStore

app = typer.Typer(help="HELIS scheduled market discovery and business-brain wake")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _policy(
    *,
    minimum_interval_seconds: int,
    lease_seconds: int,
    observation_limit: int,
    candidate_limit: int,
    max_model_calls: int,
    max_tokens: int,
    max_cost_cents: float,
) -> DiscoveryWakePolicy:
    return DiscoveryWakePolicy(
        minimum_interval_seconds=minimum_interval_seconds,
        lease_seconds=lease_seconds,
        observation_limit=observation_limit,
        candidate_limit=candidate_limit,
        max_model_calls=max_model_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_cost_cents,
    )


def _runtime(db: Path, config: Path) -> tuple[HelisEngine, DiscoveryRuntime]:
    helis = _engine(db)
    provider = OpenAICompatibleProvider.from_env()
    runtime = DiscoveryRuntime(
        helis,
        provider,
        lambda: SourceRegistry.from_toml(config),
    )
    return helis, runtime


def _print_result(result: DiscoveryWakeResult) -> None:
    console.print(
        f"discovery: disposition={result.disposition.value} work={result.did_work} "
        f"reason={result.reason}"
    )
    console.print(
        f"scan: fetched={result.observations_fetched} new={result.observations_new} "
        f"source_failures={result.source_failures}"
    )
    console.print(
        f"brain: observations={result.observations_used} "
        f"discovered={result.candidates_discovered} evaluated={result.candidates_evaluated} "
        f"experiments={result.experiments_planned} budget_exhausted={result.budget_exhausted}"
    )
    console.print(
        f"usage: calls={result.model_calls} tokens={result.tokens} "
        f"cost≈{result.cost_cents:.3f}¢"
    )


@app.command()
def run(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    observation_limit: int = 100,
    candidate_limit: int = 5,
    max_model_calls: int = 8,
    max_tokens: int = 40_000,
    max_cost_cents: float = 25.0,
) -> None:
    """Run one immediate configured source scan and one bounded resumable brain cycle."""
    _, runtime = _runtime(db, config)
    result = runtime.tick(
        _policy(
            minimum_interval_seconds=0,
            lease_seconds=900,
            observation_limit=observation_limit,
            candidate_limit=candidate_limit,
            max_model_calls=max_model_calls,
            max_tokens=max_tokens,
            max_cost_cents=max_cost_cents,
        )
    )
    _print_result(result)


@app.command()
def wake(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
    minimum_interval_seconds: int = 3600,
    lease_seconds: int = 900,
    observation_limit: int = 100,
    candidate_limit: int = 5,
    max_model_calls: int = 8,
    max_tokens: int = 40_000,
    max_cost_cents: float = 25.0,
) -> None:
    """Crash-safe cron/systemd wake with an independent discovery lease and due interval."""
    helis, runtime = _runtime(db, config)
    result = DiscoveryWakeController(helis, runtime).wake(
        _policy(
            minimum_interval_seconds=minimum_interval_seconds,
            lease_seconds=lease_seconds,
            observation_limit=observation_limit,
            candidate_limit=candidate_limit,
            max_model_calls=max_model_calls,
            max_tokens=max_tokens,
            max_cost_cents=max_cost_cents,
        )
    )
    _print_result(result)


@app.command()
def health(
    config: Path = Path("helis.toml"),
    db: Path = Path("helis.db"),
) -> None:
    """Show discovery configuration and persisted wake state without network/model calls."""
    helis = _engine(db)
    provider = OpenAICompatibleProvider.from_env()
    latest = DiscoveryWakeStore(helis).latest_result()

    table = Table("Item", "Value")
    table.add_row("database", str(db.resolve()))
    table.add_row("config", str(config.resolve()))
    table.add_row("config exists", "yes" if config.is_file() else "no")
    table.add_row("LLM endpoint", provider.base_url)
    table.add_row("LLM model", provider.model)

    if config.is_file():
        try:
            registry = SourceRegistry.from_toml(config)
            enabled = sum(spec.enabled for spec in registry.config.sources)
            table.add_row("configured sources", str(len(registry.config.sources)))
            table.add_row("enabled sources", str(enabled))
        except Exception as exc:  # noqa: BLE001 -- health reports config errors instead of scanning
            table.add_row("config parse", f"ERROR: {type(exc).__name__}: {exc}")
    else:
        table.add_row("configured sources", "0")
        table.add_row("enabled sources", "0")

    if latest is None:
        table.add_row("latest wake", "no discovery wake attempts yet")
    else:
        table.add_row(
            "latest wake",
            f"{latest.disposition.value} at {latest.attempted_at.isoformat(timespec='seconds')}",
        )
        table.add_row(
            "latest work",
            f"new={latest.observations_new} discovered={latest.candidates_discovered} "
            f"evaluated={latest.candidates_evaluated}",
        )
    console.print(table)
    console.print("[green]discovery health check completed without network/model calls[/]")


if __name__ == "__main__":
    app()
