from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from helis.budget import CycleBudget
from helis.contact_gateway import ContactGateway
from helis.cycle import CycleReport, HelisCycle
from helis.engine import HelisEngine
from helis.model_provider import ModelProvider
from helis.portfolio import PortfolioAllocator, PortfolioBudget, PortfolioPlan, PortfolioStore
from helis.portfolio_reallocation import ReallocatingPortfolioControlLoop
from helis.portfolio_scheduler import PortfolioScheduler, SchedulerTickReport
from helis.prospect_gateway import ProspectGateway
from helis.resource_envelope import ResourceEnvelopeManager
from helis.source_registry import RegistryScanResult
from helis.validation_gateway import ApprovedValidationGateway


class AutopilotStopReason(StrEnum):
    REAL_WORLD_GATE = "real_world_gate"
    NO_ONLINE_OPPORTUNITIES = "no_online_opportunities"
    NO_FUNDED_VENTURES = "no_funded_ventures"
    NO_PROGRESS = "no_progress"
    ROUND_CAP = "round_cap"


class AutopilotPolicy(BaseModel):
    """Operator-owned ceilings for one zero-idea online venture run."""

    cash_cents: int = Field(default=0, ge=0, le=100_000_000)
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    portfolio_model_calls: int = Field(default=80, ge=1, le=10_000)
    reserve_fraction: float = Field(default=0.20, ge=0, le=0.90)
    max_ventures: int = Field(default=3, ge=1, le=20)
    max_concentration: float = Field(default=0.60, gt=0, le=1)
    observation_limit: int = Field(default=100, ge=1, le=1000)
    candidate_limit: int = Field(default=5, ge=1, le=50)
    discovery_model_calls: int = Field(default=8, ge=1, le=100)
    discovery_max_tokens: int = Field(default=40_000, ge=1, le=2_000_000)
    discovery_max_cost_cents: float = Field(default=25.0, ge=0, le=100_000)
    max_rounds: int = Field(default=12, ge=1, le=100)
    max_advances_per_round: int = Field(default=3, ge=1, le=20)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    def portfolio_budget(self) -> PortfolioBudget:
        return PortfolioBudget(
            cash_cents=self.cash_cents,
            currency=self.currency,
            model_calls=self.portfolio_model_calls,
            reserve_fraction=self.reserve_fraction,
            max_ventures=self.max_ventures,
            max_concentration=self.max_concentration,
        )


class AutopilotScanner(Protocol):
    def scan(self) -> RegistryScanResult: ...


class AutopilotDiscoveryReport(BaseModel):
    observations_fetched: int = Field(default=0, ge=0)
    observations_new: int = Field(default=0, ge=0)
    source_failures: int = Field(default=0, ge=0)
    observations_used: int = Field(default=0, ge=0)
    candidates_discovered: int = Field(default=0, ge=0)
    candidates_evaluated: int = Field(default=0, ge=0)
    experiments_planned: int = Field(default=0, ge=0)
    budget_exhausted: bool = False
    model_calls: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_cents: float = Field(default=0, ge=0)


class AutopilotReport(BaseModel):
    discovery: AutopilotDiscoveryReport
    portfolio_plan_id: UUID | None = None
    portfolio_bootstrapped: bool = False
    funded_ventures: int = Field(default=0, ge=0)
    scheduler_rounds: list[SchedulerTickReport] = Field(default_factory=list)
    stop_reason: AutopilotStopReason
    blockers: list[str] = Field(default_factory=list)
    stage_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def total_advanced(self) -> int:
        return sum(item.advanced for item in self.scheduler_rounds)


ScannerFactory = Callable[[], AutopilotScanner]


REAL_WORLD_GATE_MARKERS = (
    "approval",
    "waiting_result",
    "gateway_missing",
    "open_cash_commitment",
    "result_backlog",
)


class AutonomousOnlineVentureOperator:
    """One command: internet evidence -> online ventures -> portfolio -> bounded execution.

    It never asks the operator for a business idea. Side effects still obey the existing HELIS
    approval/gateway boundaries; reaching such a boundary is a successful stop condition rather
    than permission for the model to bypass it.
    """

    def __init__(
        self,
        engine: HelisEngine,
        provider: ModelProvider,
        scanner_factory: ScannerFactory,
        *,
        workspace_root: str | Path = ".helis/workspaces",
        validation_gateway: ApprovedValidationGateway | None = None,
        prospect_gateway: ProspectGateway | None = None,
        contact_gateway: ContactGateway | None = None,
    ) -> None:
        self.engine = engine
        self.provider = provider
        self.scanner_factory = scanner_factory
        self.workspace_root = Path(workspace_root)
        self.validation_gateway = validation_gateway
        self.prospect_gateway = prospect_gateway
        self.contact_gateway = contact_gateway

    def run(self, policy: AutopilotPolicy | None = None) -> AutopilotReport:
        selected = policy or AutopilotPolicy()
        discovery = self._discover(selected)
        plan, bootstrapped = self._ensure_portfolio(selected)
        funded = len(plan.allocations) if plan is not None else 0

        if plan is None or funded == 0:
            stop = (
                AutopilotStopReason.NO_ONLINE_OPPORTUNITIES
                if not self._has_online_candidates()
                else AutopilotStopReason.NO_FUNDED_VENTURES
            )
            return self._report(
                discovery,
                plan,
                bootstrapped,
                [],
                stop,
                ["no venture received a resource allocation"],
            )

        scheduler = PortfolioScheduler(
            self.engine,
            self.provider,
            workspace_root=self.workspace_root,
            validation_gateway=self.validation_gateway,
            prospect_gateway=self.prospect_gateway,
            contact_gateway=self.contact_gateway,
        )
        control = ReallocatingPortfolioControlLoop(self.engine, scheduler)
        rounds: list[SchedulerTickReport] = []
        blockers: list[str] = []
        stop_reason = AutopilotStopReason.ROUND_CAP

        for _ in range(selected.max_rounds):
            # Only re-bootstrap an empty plan. Never mint a fresh full budget after any funded plan
            # exists; ReallocatingPortfolioControlLoop owns safe remaining-treasury rollover.
            current, _ = self._ensure_portfolio(selected)
            if current is None or not current.allocations:
                stop_reason = AutopilotStopReason.NO_FUNDED_VENTURES
                blockers = ["portfolio has no active allocations"]
                break

            tick = control.tick(max_advances=selected.max_advances_per_round)
            rounds.append(tick)
            reasons = [item.reason for item in tick.items if item.reason]
            gates = sorted({reason for reason in reasons if self._is_real_world_gate(reason)})

            if tick.advanced > 0:
                # Durable progress happened; continue until the next checkpoint/gate or round cap.
                continue
            if gates:
                stop_reason = AutopilotStopReason.REAL_WORLD_GATE
                blockers = gates
                break
            stop_reason = AutopilotStopReason.NO_PROGRESS
            blockers = sorted(set(reasons)) or ["scheduler had no actionable funded venture"]
            break

        return self._report(
            discovery,
            PortfolioStore(self.engine).latest(),
            bootstrapped,
            rounds,
            stop_reason,
            blockers,
        )

    def _discover(self, policy: AutopilotPolicy) -> AutopilotDiscoveryReport:
        scan = self.scanner_factory().scan()
        before = self._observation_count()
        for observation in scan.observations:
            self.engine.observe(observation)
        after = self._observation_count()

        budget = CycleBudget(
            max_model_calls=policy.discovery_model_calls,
            max_tokens=policy.discovery_max_tokens,
            max_cost_cents=policy.discovery_max_cost_cents,
        )
        cycle: CycleReport = HelisCycle(
            self.engine,
            self.provider,
            budget,
            online_only=True,
        ).run(
            observation_limit=policy.observation_limit,
            candidate_limit=policy.candidate_limit,
        )
        return AutopilotDiscoveryReport(
            observations_fetched=len(scan.observations),
            observations_new=max(0, after - before),
            source_failures=len(scan.failures),
            observations_used=cycle.observations_used,
            candidates_discovered=cycle.candidates_discovered,
            candidates_evaluated=cycle.candidates_evaluated,
            experiments_planned=cycle.experiments_planned,
            budget_exhausted=cycle.budget_exhausted,
            model_calls=budget.model_calls,
            tokens=budget.tokens,
            cost_cents=budget.cost_cents,
        )

    def _ensure_portfolio(
        self,
        policy: AutopilotPolicy,
    ) -> tuple[PortfolioPlan | None, bool]:
        store = PortfolioStore(self.engine)
        latest = store.latest()
        if latest is not None and latest.allocations:
            return latest, False

        previous_id = latest.id if latest is not None else None
        plan = PortfolioAllocator(self.engine).plan(policy.portfolio_budget())
        if plan.allocations:
            ResourceEnvelopeManager(self.engine).activate(plan)
        return plan, previous_id != plan.id

    def _has_online_candidates(self) -> bool:
        return any(
            opportunity.business_model is not None and "online_venture" in opportunity.tags
            for opportunity in self.engine.store.list_opportunities()
        )

    def _observation_count(self) -> int:
        with self.engine.store.connect() as db:
            row = db.execute("SELECT COUNT(*) AS count FROM observations").fetchone()
        return int(row["count"] if row else 0)

    @staticmethod
    def _is_real_world_gate(reason: str) -> bool:
        lowered = reason.lower()
        return any(marker in lowered for marker in REAL_WORLD_GATE_MARKERS)

    def _stage_counts(self) -> dict[str, int]:
        return dict(Counter(item.stage.value for item in self.engine.store.list_opportunities()))

    def _report(
        self,
        discovery: AutopilotDiscoveryReport,
        plan: PortfolioPlan | None,
        bootstrapped: bool,
        rounds: list[SchedulerTickReport],
        stop_reason: AutopilotStopReason,
        blockers: list[str],
    ) -> AutopilotReport:
        return AutopilotReport(
            discovery=discovery,
            portfolio_plan_id=plan.id if plan is not None else None,
            portfolio_bootstrapped=bootstrapped,
            funded_ventures=len(plan.allocations) if plan is not None else 0,
            scheduler_rounds=rounds,
            stop_reason=stop_reason,
            blockers=blockers,
            stage_counts=self._stage_counts(),
        )