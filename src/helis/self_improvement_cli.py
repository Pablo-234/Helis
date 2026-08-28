from __future__ import annotations

from pathlib import Path
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from helis.budget import CycleBudget
from helis.engine import HelisEngine
from helis.model_provider import OpenAICompatibleProvider
from helis.self_improvement_branch_gateway import ApprovedSelfImprovementBranchGateway
from helis.self_improvement_branch_manager import SelfImprovementBranchManager
from helis.self_improvement_branch_store import SelfImprovementBranchStore
from helis.self_improvement_gateway import ApprovedSelfImprovementEvaluationGateway
from helis.self_improvement_machine import SelfImprovementMachine
from helis.self_improvement_planner import ImprovementSignalCollector, NoImprovementSignal
from helis.self_improvement_store import SelfImprovementStore
from helis.store import HelisStore

app = typer.Typer(help="HELIS controlled self-improvement proposals and isolated evaluation")
console = Console()


def _engine(db: Path) -> HelisEngine:
    return HelisEngine(HelisStore(db))


def _machine(
    db: Path,
    repo_root: Path,
    sandbox_root: Path,
    *,
    max_calls: int = 1,
) -> SelfImprovementMachine:
    engine = _engine(db)
    return SelfImprovementMachine(
        engine,
        OpenAICompatibleProvider.from_env(),
        CycleBudget(max_model_calls=max_calls, max_tokens=60_000, max_cost_cents=25.0),
        repo_root=repo_root,
        sandbox_root=sandbox_root,
        evaluation_gateway=ApprovedSelfImprovementEvaluationGateway.from_env(),
    )


def _branch_manager(db: Path, sandbox_root: Path) -> SelfImprovementBranchManager:
    return SelfImprovementBranchManager(
        _engine(db),
        ApprovedSelfImprovementBranchGateway.from_env(),
        sandbox_root=str(sandbox_root),
    )


@app.command()
def signals(db: Path = Path("helis.db")) -> None:
    """Show deterministic recent audit signals that can justify a self-improvement proposal."""
    items = ImprovementSignalCollector(_engine(db)).collect()
    table = Table("When", "Event", "Summary")
    for item in items:
        table.add_row(item.created_at.isoformat(timespec="seconds"), item.event_type, item.summary)
    console.print(table)
    if not items:
        console.print("[yellow]no qualifying improvement signals[/]")


@app.command()
def tick(
    repo_root: Path = Path("."),
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Advance at most one isolated self-improvement transition."""
    report = _machine(db, repo_root, sandbox_root).tick()
    console.print(
        f"self-improvement: work={report.did_work} reason={report.reason} "
        f"status={report.status.value if report.status else '-'} "
        f"proposal={report.proposal_id or '-'} candidate={report.candidate_id or '-'} "
        f"evaluation={report.evaluation_id or '-'}"
    )


@app.command()
def propose(
    objective: str | None = None,
    repo_root: Path = Path("."),
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Plan one bounded low-risk improvement; writes no live source file."""
    try:
        proposal = _machine(db, repo_root, sandbox_root).propose(objective)
    except NoImprovementSignal as exc:
        console.print(f"[yellow]{exc}[/]")
        return
    console.print(
        f"proposal={proposal.id} status={proposal.status.value} metric={proposal.metric_name} "
        f"min_improvement={proposal.minimum_improvement}"
    )
    console.print("targets: " + ", ".join(proposal.target_files))


@app.command()
def materialize(
    proposal_id: str,
    repo_root: Path = Path("."),
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Generate the approved proposal only inside the isolated candidate workspace."""
    candidate = _machine(db, repo_root, sandbox_root).materialize(UUID(proposal_id))
    console.print(
        f"candidate={candidate.id} hash={candidate.candidate_hash} workspace={candidate.workspace}"
    )
    console.print("files: " + ", ".join(item.path for item in candidate.files))


@app.command()
def evaluate(
    proposal_id: str,
    repo_root: Path = Path("."),
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Evaluate the exact hash-locked candidate; this command cannot merge it."""
    gateway = ApprovedSelfImprovementEvaluationGateway.from_env()
    if gateway is None:
        raise typer.BadParameter("HELIS_SELF_EVAL_GATEWAY_URL is not configured")
    evaluation = _machine(db, repo_root, sandbox_root, max_calls=1).evaluate(UUID(proposal_id))
    proposal = SelfImprovementStore(_engine(db).store).get_proposal(UUID(proposal_id))
    console.print(
        f"evaluation={evaluation.id} accepted={evaluation.accepted} reason={evaluation.reason}"
    )
    console.print(
        f"metric {evaluation.metric_name}: "
        f"{evaluation.baseline.metric_value} → {evaluation.candidate.metric_value}"
    )
    if proposal is not None:
        console.print(f"proposal status={proposal.status.value}; [bold]merge command does not exist[/]")


@app.command("prepare-branch")
def prepare_branch(
    proposal_id: str,
    base_revision: str,
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Prepare one exact candidate/base-SHA review-branch run; approval is still required."""
    run = _branch_manager(db, sandbox_root).prepare(
        UUID(proposal_id),
        base_revision=base_revision,
    )
    console.print(
        f"branch-run={run.id} status={run.status.value} branch={run.branch_name} "
        f"base={run.base_revision} candidate={run.candidate_hash}"
    )
    console.print("[yellow]explicit approve-branch is required before any branch gateway call[/]")


@app.command("approve-branch")
def approve_branch(
    run_id: str,
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Approve exactly one persisted candidate/base-SHA branch materialization run."""
    run = _branch_manager(db, sandbox_root).approve(UUID(run_id))
    console.print(
        f"branch-run={run.id} status={run.status.value} approved={run.approval_granted} "
        f"branch={run.branch_name}"
    )


@app.command("materialize-branch")
def materialize_branch(
    run_id: str,
    sandbox_root: Path = Path(".helis/self-improvement"),
    db: Path = Path("helis.db"),
) -> None:
    """Create the approved review branch through the configured gateway; never merge it."""
    if ApprovedSelfImprovementBranchGateway.from_env() is None:
        raise typer.BadParameter("HELIS_SELF_BRANCH_GATEWAY_URL is not configured")
    run = _branch_manager(db, sandbox_root).materialize(UUID(run_id))
    console.print(
        f"branch-run={run.id} status={run.status.value} branch={run.branch_name} "
        f"external_ref={run.external_ref or '-'}"
    )
    console.print("[bold]review branch only; merge command does not exist[/]")


@app.command("branch-status")
def branch_status(db: Path = Path("helis.db")) -> None:
    """Show review-branch materialization runs and their approval state."""
    runs = SelfImprovementBranchStore(_engine(db).store).list()
    table = Table("Status", "Approved", "Base", "Branch", "Candidate", "Run")
    for run in runs:
        table.add_row(
            run.status.value,
            str(run.approval_granted),
            run.base_revision[:12] + "…",
            run.branch_name,
            run.candidate_hash[:12] + "…",
            str(run.id),
        )
    console.print(table)


@app.command()
def status(db: Path = Path("helis.db")) -> None:
    """Show persisted proposals, candidates and evaluation outcome."""
    engine = _engine(db)
    state = SelfImprovementStore(engine.store)
    table = Table("Status", "Metric", "Targets", "Candidate", "Evaluation", "Proposal")
    for proposal in state.list_proposals():
        candidate = state.get_candidate_for_proposal(proposal.id)
        evaluation = state.get_evaluation_for_proposal(proposal.id)
        table.add_row(
            proposal.status.value,
            proposal.metric_name,
            ", ".join(proposal.target_files),
            candidate.candidate_hash[:12] + "…" if candidate else "-",
            ("accepted" if evaluation.accepted else "rejected") if evaluation else "-",
            str(proposal.id),
        )
    console.print(table)


@app.command("gateway-status")
def gateway_status() -> None:
    """Show evaluator configuration without making a request."""
    gateway = ApprovedSelfImprovementEvaluationGateway.from_env()
    if gateway is None:
        console.print("self-eval gateway: [yellow]not configured[/]")
    else:
        console.print(f"self-eval gateway: [green]{gateway.safe_destination}[/]")


@app.command("branch-gateway-status")
def branch_gateway_status() -> None:
    """Show review-branch gateway configuration without making a request."""
    gateway = ApprovedSelfImprovementBranchGateway.from_env()
    if gateway is None:
        console.print("self-branch gateway: [yellow]not configured[/]")
    else:
        console.print(f"self-branch gateway: [green]{gateway.safe_destination}[/]")


if __name__ == "__main__":
    app()
