from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

import typer
from rich.console import Console

from helis.agent_spec_domain import AgentMemoryScope, AgentSpecBundle, ChildAgentSpec
from helis.agent_spec_store import AgentSpecStore
from helis.bot_architect import architecture_input_hash
from helis.budget import CycleBudget
from helis.child_agent_factory import ChildAgentFactory
from helis.child_agent_runtime import ChildAgentRuntime
from helis.child_agent_store import ChildAgentArtifactStore
from helis.domain import Opportunity, ValidationOutcome, ValidationResult, VentureStage
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.store import HelisStore
from helis.venture_architecture_domain import (
    CapabilityImplementation,
    CapabilityNode,
    VentureArchitecture,
)
from helis.venture_architecture_store import VentureArchitectureStore

app = typer.Typer(help="Materialize and run HELIS venture-owned child agents")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


@app.command()
def materialize(
    opportunity_id: str,
    workspace_root: Path = Path(".helis/ventures"),
    db: Path = Path("helis.db"),
) -> None:
    """Materialize immutable child-agent artifacts from the latest approved spec bundle."""
    engine = _engine(db)
    report = ChildAgentFactory(engine, workspace_root=workspace_root).materialize_if_needed(
        UUID(opportunity_id)
    )
    if report.blocked_reason is not None:
        console.print(f"[red]blocked[/]: {report.blocked_reason}")
        raise typer.Exit(code=1)
    console.print(
        f"venture={report.opportunity_id} agents={len(report.artifacts)} "
        f"created={report.created_count}"
    )
    for artifact in report.artifacts:
        console.print(
            f"- {artifact.id} capability={artifact.capability_key} "
            f"hash={artifact.artifact_hash[:12]}… path={artifact.manifest_path}"
        )


@app.command("list")
def list_agents(
    opportunity_id: str,
    db: Path = Path("helis.db"),
) -> None:
    """List persisted child-agent artifacts without model or network calls."""
    engine = _engine(db)
    artifacts = ChildAgentArtifactStore(engine.store).list(UUID(opportunity_id))
    if not artifacts:
        console.print("no child agents")
        return
    for artifact in artifacts:
        console.print(
            f"{artifact.id} capability={artifact.capability_key} status={artifact.status.value} "
            f"hash={artifact.artifact_hash[:12]}…"
        )


@app.command()
def run(
    artifact_id: str,
    task: str = typer.Option(..., help="Task for this exact child-agent capability"),
    max_model_calls: int = typer.Option(4, min=1, max=12),
    max_tokens: int = typer.Option(12_000, min=1),
    max_model_cost_cents: float = typer.Option(5.0, min=0),
    workspace_root: Path = Path(".helis/ventures"),
    db: Path = Path("helis.db"),
) -> None:
    """Run one immutable child agent in reasoning-only v1 mode."""
    engine = _engine(db)
    budget = CycleBudget(
        max_model_calls=max_model_calls,
        max_tokens=max_tokens,
        max_cost_cents=max_model_cost_cents,
    )
    result = ChildAgentRuntime(
        engine,
        OpenAICompatibleProvider.from_env(),
        budget,
        workspace_root=workspace_root,
    ).run(UUID(artifact_id), task)
    console.print(
        f"status={result.status.value} turns={result.turns_used} stop={result.stop_reason}"
    )
    console.print(result.output)
    console.print(f"run={result.id} path={result.run_path}")


@app.command()
def smoke(
    task: str = typer.Option(
        "Uporządkuj ten chaos w trzy krótkie kroki: klient chce szybszej odpowiedzi, "
        "ale nie podał budżetu ani terminu.",
        help="Diagnostic task sent to an isolated temporary child agent",
    ),
    max_model_calls: int = typer.Option(3, min=1, max=6),
) -> None:
    """FIRST BOOT diagnostic using the real Factory + Runtime and configured local model."""
    with TemporaryDirectory(prefix="helis-first-boot-") as temporary:
        root = Path(temporary)
        engine = HelisEngine(HelisStore(root / "smoke.db"))
        opportunity = Opportunity(
            title="HELIS first boot smoke venture",
            problem="A diagnostic workflow contains an ambiguous customer request.",
            customer="diagnostic operator",
            proposed_value="Turn ambiguity into a concise structured next step.",
            stage=VentureStage.VALIDATED,
        )
        engine.store.save_opportunity(opportunity)
        validation = ValidationResult(
            run_id=uuid4(),
            experiment_id=uuid4(),
            opportunity_id=opportunity.id,
            outcome=ValidationOutcome.POSITIVE,
            confidence=1.0,
            summary="Diagnostic fixture only; no market claim is implied.",
            metrics={"smoke_fixture": 1.0},
            source="local_smoke_fixture",
        )
        engine.store.save_validation_result(validation)
        snapshot = architecture_input_hash(opportunity, [validation])
        capability = CapabilityNode(
            key="resolve_ambiguity",
            name="Resolve ambiguous request",
            goal="Turn an ambiguous supplied request into a concise structured next action.",
            implementation=CapabilityImplementation.AI_AGENT,
            inputs=["ambiguous request"],
            outputs=["structured next action"],
            required_actions=[],
            success_metric="output is concise, structured and grounded only in supplied facts",
            rationale="The smoke task intentionally requires language reasoning but no tools.",
            handles_customer_data=False,
            venture_isolation_required=True,
        )
        architecture = VentureArchitecture(
            opportunity_id=opportunity.id,
            input_hash=snapshot,
            capabilities=[capability],
            architecture_assumptions=["This is a diagnostic fixture, not a validated business."],
        )
        VentureArchitectureStore(engine.store).save(architecture)
        spec = ChildAgentSpec(
            architecture_id=architecture.id,
            opportunity_id=opportunity.id,
            capability_key=capability.key,
            name=capability.name,
            goal=capability.goal,
            inputs=capability.inputs,
            outputs=capability.outputs,
            allowed_tools=[],
            memory_scope=AgentMemoryScope.NONE,
            constraints=[
                "Use only facts present in the task.",
                "Do not contact anyone or claim any external action occurred.",
            ],
            stop_conditions=["A concise structured next action is produced."],
            success_metric=capability.success_metric,
            max_model_turns=max_model_calls,
            max_tool_calls_per_run=0,
            handles_customer_data=False,
            venture_isolation_required=True,
        )
        semantic = json.dumps(
            spec.model_dump(mode="json", exclude={"id"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        bundle_hash = hashlib.sha256(semantic).hexdigest()
        bundle = AgentSpecBundle(
            architecture_id=architecture.id,
            opportunity_id=opportunity.id,
            architecture_input_hash=snapshot,
            bundle_hash=bundle_hash,
            agent_specs=[spec],
        )
        AgentSpecStore(engine.store).save(bundle)
        factory = ChildAgentFactory(engine, workspace_root=root / "ventures")
        report = factory.materialize_if_needed(opportunity.id)
        if report.blocked_reason is not None or len(report.artifacts) != 1:
            reason = report.blocked_reason or "unexpected agent count"
            raise RuntimeError(f"smoke factory failed: {reason}")
        artifact = report.artifacts[0]
        console.print(
            f"[bold green]FIRST BOOT[/] artifact={artifact.id} "
            f"capability={artifact.capability_key}"
        )
        budget = CycleBudget(
            max_model_calls=max_model_calls,
            max_tokens=12_000,
            max_cost_cents=5.0,
        )
        result = ChildAgentRuntime(
            engine,
            OpenAICompatibleProvider.from_env(),
            budget,
            workspace_root=root / "ventures",
        ).run(artifact.id, task)
        console.print(
            f"status={result.status.value} turns={result.turns_used} stop={result.stop_reason}"
        )
        console.print(result.output)


if __name__ == "__main__":
    app()
