from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from helis.domain import AuditEvent, Scorecard, VentureStage, utc_now
from helis.engine import HelisEngine
from helis.gtm_decision import GTMDecisionKind, GTMDecisionStore
from helis.portfolio_value import VentureValueEstimate, VentureValueEstimator


class PortfolioBudget(BaseModel):
    cash_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="PLN", min_length=3, max_length=3)
    model_calls: int = Field(default=0, ge=0)
    reserve_fraction: float = Field(default=0.20, ge=0, le=0.90)
    max_ventures: int = Field(default=4, ge=1, le=50)
    max_concentration: float = Field(default=0.60, gt=0, le=1)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.upper()

    @property
    def allocatable_cash_cents(self) -> int:
        return math.floor(self.cash_cents * (1 - self.reserve_fraction))

    @property
    def allocatable_model_calls(self) -> int:
        return math.floor(self.model_calls * (1 - self.reserve_fraction))


class PortfolioCandidate(BaseModel):
    opportunity_id: UUID
    stage: VentureStage
    priority_score: float = Field(ge=0)
    scorecard_total: float = Field(ge=0, le=100)
    capital_efficiency: float = Field(ge=0, le=10)
    execution_risk: float = Field(ge=0, le=10)
    gtm_decision: str | None = None
    sales: int = Field(default=0, ge=0)
    positive_rate: float = Field(default=0, ge=0, le=1)
    value_estimate: VentureValueEstimate
    rationale: list[str] = Field(default_factory=list)


class VentureAllocation(BaseModel):
    opportunity_id: UUID
    priority_score: float = Field(ge=0)
    cash_cents: int = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    share_of_allocatable_cash: float = Field(default=0, ge=0, le=1)
    rationale: list[str] = Field(default_factory=list)


class PortfolioPlan(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    budget: PortfolioBudget
    candidates: list[PortfolioCandidate] = Field(default_factory=list)
    allocations: list[VentureAllocation] = Field(default_factory=list)
    reserved_cash_cents: int = Field(ge=0)
    reserved_model_calls: int = Field(ge=0)
    snapshot_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def allocated_cash_cents(self) -> int:
        return sum(item.cash_cents for item in self.allocations)

    @property
    def allocated_model_calls(self) -> int:
        return sum(item.model_calls for item in self.allocations)


@dataclass(frozen=True, slots=True)
class PortfolioPolicy:
    minimum_priority: float = 10.0


class PortfolioStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS portfolio_plans (
                    id TEXT PRIMARY KEY,
                    snapshot_hash TEXT UNIQUE NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def get_for_snapshot(self, snapshot_hash: str) -> PortfolioPlan | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM portfolio_plans WHERE snapshot_hash = ?",
                (snapshot_hash,),
            ).fetchone()
        return PortfolioPlan.model_validate_json(row["payload"]) if row else None

    def save(self, plan: PortfolioPlan) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO portfolio_plans (id, snapshot_hash, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (str(plan.id), plan.snapshot_hash, plan.model_dump_json(), plan.created_at.isoformat()),
            )

    def latest(self) -> PortfolioPlan | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM portfolio_plans ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return PortfolioPlan.model_validate_json(row["payload"]) if row else None


_STAGE_MULTIPLIER = {
    VentureStage.DISCOVERED: 0.55,
    VentureStage.EVALUATED: 0.70,
    VentureStage.VALIDATING: 0.80,
    VentureStage.VALIDATED: 1.00,
    VentureStage.BUILDING: 1.05,
    VentureStage.READY_PREVIEW: 1.10,
    VentureStage.LAUNCHED: 1.15,
    VentureStage.MEASURING: 1.20,
    VentureStage.SCALING: 1.50,
    VentureStage.PIVOTED: 0.55,
    VentureStage.PAUSED: 0.0,
    VentureStage.KILLED: 0.0,
}

_GTM_MULTIPLIER = {
    GTMDecisionKind.CONTINUE: 1.05,
    GTMDecisionKind.PAUSE: 0.0,
    GTMDecisionKind.KILL: 0.0,
    GTMDecisionKind.SCALE: 1.40,
}


class PortfolioAllocator:
    """Builds a resource plan only. It never spends money or invokes models itself."""

    def __init__(
        self,
        engine: HelisEngine,
        policy: PortfolioPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.policy = policy or PortfolioPolicy()
        self.state = PortfolioStore(engine)
        self.gtm_decisions = GTMDecisionStore(engine)
        self.value = VentureValueEstimator(engine)

    def plan(self, budget: PortfolioBudget) -> PortfolioPlan:
        candidates = self._candidates(budget.currency)[: budget.max_ventures]
        snapshot_hash = self._snapshot_hash(budget, candidates)
        existing = self.state.get_for_snapshot(snapshot_hash)
        if existing is not None:
            return existing

        eligible = [
            candidate
            for candidate in candidates
            if candidate.priority_score >= self.policy.minimum_priority
        ]
        cash = self._allocate_integer(
            budget.allocatable_cash_cents,
            eligible,
            budget.max_concentration,
        )
        calls = self._allocate_integer(
            budget.allocatable_model_calls,
            eligible,
            budget.max_concentration,
        )

        allocations: list[VentureAllocation] = []
        for candidate in eligible:
            cash_amount = cash.get(candidate.opportunity_id, 0)
            call_amount = calls.get(candidate.opportunity_id, 0)
            if cash_amount == 0 and call_amount == 0:
                continue
            share = (
                cash_amount / budget.allocatable_cash_cents
                if budget.allocatable_cash_cents
                else 0.0
            )
            estimate = candidate.value_estimate
            allocations.append(
                VentureAllocation(
                    opportunity_id=candidate.opportunity_id,
                    priority_score=candidate.priority_score,
                    cash_cents=cash_amount,
                    model_calls=call_amount,
                    share_of_allocatable_cash=round(share, 4),
                    rationale=[
                        f"portfolio priority={candidate.priority_score:.2f}",
                        f"stage={candidate.stage.value}",
                        f"scorecard={candidate.scorecard_total:.1f}/100",
                        (
                            "expected net per next resolved contact="
                            f"{estimate.expected_net_per_next_resolved_contact_cents:.1f} "
                            f"{budget.currency} cents"
                        ),
                        f"economics confidence={estimate.evidence_confidence:.1%}",
                    ],
                )
            )

        allocated_cash = sum(item.cash_cents for item in allocations)
        allocated_calls = sum(item.model_calls for item in allocations)
        result = PortfolioPlan(
            budget=budget,
            candidates=candidates,
            allocations=allocations,
            reserved_cash_cents=budget.cash_cents - allocated_cash,
            reserved_model_calls=budget.model_calls - allocated_calls,
            snapshot_hash=snapshot_hash,
        )
        self.state.save(result)
        self.engine.store.append_event(
            AuditEvent(
                event_type="portfolio.plan",
                entity_id=result.id,
                data={
                    "snapshot_hash": result.snapshot_hash,
                    "cash_budget_cents": budget.cash_cents,
                    "currency": budget.currency,
                    "model_call_budget": budget.model_calls,
                    "allocated_cash_cents": result.allocated_cash_cents,
                    "allocated_model_calls": result.allocated_model_calls,
                    "reserved_cash_cents": result.reserved_cash_cents,
                    "reserved_model_calls": result.reserved_model_calls,
                    "venture_count": len(result.allocations),
                },
            )
        )
        return result

    def _candidates(self, currency: str) -> list[PortfolioCandidate]:
        scorecards = {card.opportunity_id: card for card in self.engine.store.list_scorecards()}
        output: list[PortfolioCandidate] = []
        for opportunity in self.engine.store.list_opportunities():
            stage_multiplier = _STAGE_MULTIPLIER.get(opportunity.stage, 0.0)
            if stage_multiplier <= 0:
                continue

            scorecard = scorecards.get(opportunity.id)
            score_total, capital_efficiency, execution_risk = self._score_inputs(scorecard)
            latest_gtm = self.gtm_decisions.latest(opportunity.id)
            estimate = self.value.estimate(opportunity.id, currency)
            gtm_multiplier = 1.0
            sales = 0
            positive_rate = 0.0
            gtm_name: str | None = None
            rationale = [f"stage multiplier={stage_multiplier:.2f}"]
            if latest_gtm is not None:
                gtm_name = latest_gtm.decision.value
                gtm_multiplier = _GTM_MULTIPLIER[latest_gtm.decision]
                sales = latest_gtm.metrics.sales
                positive_rate = latest_gtm.metrics.positive_rate
                rationale.append(f"gtm decision={gtm_name} multiplier={gtm_multiplier:.2f}")
            if gtm_multiplier <= 0:
                continue

            quality = (
                score_total * 0.68
                + capital_efficiency * 3.2
                + (10 - execution_risk) * 1.8
            )
            traction_bonus = min(20.0, sales * 4.0 + positive_rate * 12.0)
            economics_adjustment = self._economics_adjustment(estimate)
            exploration_bonus = estimate.uncertainty * score_total * 0.06
            priority = round(
                max(
                    0.0,
                    quality + traction_bonus + economics_adjustment + exploration_bonus,
                )
                * stage_multiplier
                * gtm_multiplier,
                4,
            )
            rationale.extend(
                [
                    f"economics confidence={estimate.evidence_confidence:.1%}",
                    (
                        "expected net/contact="
                        f"{estimate.expected_net_per_next_resolved_contact_cents:.1f} {currency} cents"
                    ),
                    f"exploration bonus={exploration_bonus:.2f}",
                ]
            )
            output.append(
                PortfolioCandidate(
                    opportunity_id=opportunity.id,
                    stage=opportunity.stage,
                    priority_score=priority,
                    scorecard_total=score_total,
                    capital_efficiency=capital_efficiency,
                    execution_risk=execution_risk,
                    gtm_decision=gtm_name,
                    sales=sales,
                    positive_rate=positive_rate,
                    value_estimate=estimate,
                    rationale=rationale,
                )
            )
        return sorted(output, key=lambda item: (-item.priority_score, str(item.opportunity_id)))

    @staticmethod
    def _economics_adjustment(estimate: VentureValueEstimate) -> float:
        expected = estimate.expected_net_per_next_resolved_contact_cents
        confidence = estimate.evidence_confidence
        if expected == 0 or confidence == 0:
            return 0.0
        magnitude = math.log1p(abs(expected) / 100) * 6 * confidence
        bounded = min(25.0, magnitude)
        return bounded if expected > 0 else -bounded

    @staticmethod
    def _score_inputs(scorecard: Scorecard | None) -> tuple[float, float, float]:
        if scorecard is None:
            return 35.0, 5.0, 7.0
        return (
            scorecard.total,
            scorecard.dimensions.capital_efficiency,
            scorecard.dimensions.execution_risk,
        )

    @staticmethod
    def _allocate_integer(
        total: int,
        candidates: list[PortfolioCandidate],
        max_concentration: float,
    ) -> dict[UUID, int]:
        if total <= 0 or not candidates:
            return {}
        cap = math.floor(total * max_concentration)
        if cap <= 0:
            return {}

        remaining = total
        active = list(candidates)
        allocated = {candidate.opportunity_id: 0 for candidate in candidates}
        while remaining > 0 and active:
            weight_sum = sum(candidate.priority_score for candidate in active)
            if weight_sum <= 0:
                break
            progress = 0
            for candidate in list(active):
                room = cap - allocated[candidate.opportunity_id]
                if room <= 0:
                    active.remove(candidate)
                    continue
                ideal = remaining * candidate.priority_score / weight_sum
                amount = min(room, max(1, math.floor(ideal)))
                amount = min(amount, remaining)
                allocated[candidate.opportunity_id] += amount
                remaining -= amount
                progress += amount
                if allocated[candidate.opportunity_id] >= cap:
                    active.remove(candidate)
                if remaining <= 0:
                    break
            if progress == 0:
                break
        return allocated

    @staticmethod
    def _snapshot_hash(
        budget: PortfolioBudget,
        candidates: list[PortfolioCandidate],
    ) -> str:
        payload = {
            "budget": budget.model_dump(mode="json"),
            "candidates": [
                {
                    "opportunity_id": str(item.opportunity_id),
                    "stage": item.stage.value,
                    "priority_score": item.priority_score,
                    "scorecard_total": item.scorecard_total,
                    "capital_efficiency": item.capital_efficiency,
                    "execution_risk": item.execution_risk,
                    "gtm_decision": item.gtm_decision,
                    "sales": item.sales,
                    "positive_rate": item.positive_rate,
                    "value_estimate": item.value_estimate.model_dump(mode="json"),
                }
                for item in candidates
            ],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
