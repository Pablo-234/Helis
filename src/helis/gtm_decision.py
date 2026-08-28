from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from helis.domain import AuditEvent, VentureStage, utc_now
from helis.engine import HelisEngine
from helis.gtm_metrics import GTMMetrics, collect_gtm_metrics
from helis.gtm_store import GTMStore


class GTMDecisionKind(StrEnum):
    CONTINUE = "continue"
    PAUSE = "pause"
    KILL = "kill"
    SCALE = "scale"


class GTMDecision(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    decision: GTMDecisionKind
    confidence: float = Field(ge=0, le=1)
    metrics: GTMMetrics
    snapshot_hash: str = Field(min_length=64, max_length=64)
    rationale: list[str] = Field(default_factory=list)
    decided_at: object = Field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class GTMDecisionPolicy:
    min_resolved_for_scale: int = 6
    min_resolved_for_pause: int = 8
    min_resolved_for_kill: int = 15
    scale_min_sales: int = 2
    scale_min_positive_rate: float = 0.30
    scale_min_sale_rate: float = 0.15
    pause_max_positive_rate: float = 0.10
    pause_max_sale_count: int = 0

    def __post_init__(self) -> None:
        if self.min_resolved_for_scale < 1:
            raise ValueError("min_resolved_for_scale must be positive")
        if self.min_resolved_for_pause < self.min_resolved_for_scale:
            raise ValueError("pause sample must be >= scale sample")
        if self.min_resolved_for_kill < self.min_resolved_for_pause:
            raise ValueError("kill sample must be >= pause sample")
        for value in (
            self.scale_min_positive_rate,
            self.scale_min_sale_rate,
            self.pause_max_positive_rate,
        ):
            if not 0 <= value <= 1:
                raise ValueError("GTM rates must be between 0 and 1")


class GTMDecisionStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS gtm_decisions (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    UNIQUE(opportunity_id, snapshot_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_gtm_decisions_venture
                    ON gtm_decisions(opportunity_id, decided_at);
                """
            )

    def get_for_snapshot(self, opportunity_id: UUID, snapshot_hash: str) -> GTMDecision | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_decisions "
                "WHERE opportunity_id = ? AND snapshot_hash = ?",
                (str(opportunity_id), snapshot_hash),
            ).fetchone()
        return GTMDecision.model_validate_json(row["payload"]) if row else None

    def latest(self, opportunity_id: UUID) -> GTMDecision | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_decisions WHERE opportunity_id = ? "
                "ORDER BY decided_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return GTMDecision.model_validate_json(row["payload"]) if row else None

    def save(self, decision: GTMDecision) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO gtm_decisions "
                "(id, opportunity_id, snapshot_hash, decision, payload, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(decision.id),
                    str(decision.opportunity_id),
                    decision.snapshot_hash,
                    decision.decision.value,
                    decision.model_dump_json(),
                    decision.decided_at.isoformat(),
                ),
            )


def metrics_snapshot_hash(metrics: GTMMetrics) -> str:
    payload = {
        "opportunity_id": str(metrics.opportunity_id),
        "contacts": metrics.contacts,
        "resolved_outcomes": metrics.resolved_outcomes,
        "replies": metrics.replies,
        "positive_outcomes": metrics.positive_outcomes,
        "meetings": metrics.meetings,
        "sales": metrics.sales,
        "no_responses": metrics.no_responses,
        "bounces": metrics.bounces,
        "negative_outcomes": metrics.negative_outcomes,
        "reply_rate": metrics.reply_rate,
        "positive_rate": metrics.positive_rate,
        "sale_rate": metrics.sale_rate,
        "bounce_rate": metrics.bounce_rate,
        "revenue_by_currency": dict(sorted(metrics.revenue_by_currency.items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GTMDecisionEngine:
    """Final GTM allocation decision is deterministic; models may not override these gates."""

    def __init__(
        self,
        engine: HelisEngine,
        policy: GTMDecisionPolicy | None = None,
    ) -> None:
        self.engine = engine
        self.gtm = GTMStore(engine.store)
        self.state = GTMDecisionStore(engine)
        self.policy = policy or GTMDecisionPolicy()

    def evaluate(self, opportunity_id: UUID) -> GTMDecision:
        opportunity = self.engine.store.get_opportunity(opportunity_id)
        if opportunity is None:
            raise ValueError(f"opportunity not found: {opportunity_id}")

        metrics = collect_gtm_metrics(self.gtm, opportunity_id)
        snapshot_hash = metrics_snapshot_hash(metrics)
        existing = self.state.get_for_snapshot(opportunity_id, snapshot_hash)
        if existing is not None:
            return existing

        decision, confidence, rationale = self._decide(metrics)
        result = GTMDecision(
            opportunity_id=opportunity_id,
            decision=decision,
            confidence=confidence,
            metrics=metrics,
            snapshot_hash=snapshot_hash,
            rationale=rationale,
        )
        self.state.save(result)
        self._apply_stage(result)
        self.engine.store.append_event(
            AuditEvent(
                event_type="gtm.decision",
                entity_id=opportunity_id,
                data={
                    "decision_id": str(result.id),
                    "decision": result.decision.value,
                    "confidence": result.confidence,
                    "snapshot_hash": result.snapshot_hash,
                    "contacts": metrics.contacts,
                    "resolved_outcomes": metrics.resolved_outcomes,
                    "positive_outcomes": metrics.positive_outcomes,
                    "sales": metrics.sales,
                    "revenue_by_currency": metrics.revenue_by_currency,
                },
            )
        )
        return result

    def _decide(self, metrics: GTMMetrics) -> tuple[GTMDecisionKind, float, list[str]]:
        resolved = metrics.resolved_outcomes
        p = self.policy

        if (
            resolved >= p.min_resolved_for_scale
            and metrics.sales >= p.scale_min_sales
            and metrics.positive_rate >= p.scale_min_positive_rate
            and metrics.sale_rate >= p.scale_min_sale_rate
        ):
            confidence = min(0.95, 0.72 + resolved * 0.02)
            return (
                GTMDecisionKind.SCALE,
                confidence,
                [
                    f"sales={metrics.sales} across {resolved} resolved outcomes",
                    f"positive_rate={metrics.positive_rate:.1%}",
                    f"sale_rate={metrics.sale_rate:.1%}",
                    "repeatable paid demand crossed the conservative scale threshold",
                ],
            )

        if (
            resolved >= p.min_resolved_for_kill
            and metrics.sales == 0
            and metrics.positive_outcomes == 0
        ):
            confidence = min(0.97, 0.80 + resolved * 0.01)
            return (
                GTMDecisionKind.KILL,
                confidence,
                [
                    f"0 positive outcomes and 0 sales across {resolved} resolved contacts",
                    "the acquisition hypothesis crossed the hard falsification threshold",
                ],
            )

        if (
            resolved >= p.min_resolved_for_pause
            and metrics.sales <= p.pause_max_sale_count
            and metrics.positive_rate < p.pause_max_positive_rate
        ):
            confidence = min(0.90, 0.62 + resolved * 0.02)
            return (
                GTMDecisionKind.PAUSE,
                confidence,
                [
                    f"positive_rate={metrics.positive_rate:.1%} after {resolved} resolved outcomes",
                    "signal is too weak to justify more outbound spend without changing the hypothesis",
                ],
            )

        confidence = min(0.78, 0.35 + resolved * 0.025)
        rationale = [
            f"resolved_outcomes={resolved}",
            f"positive_rate={metrics.positive_rate:.1%}",
            f"sales={metrics.sales}",
        ]
        if resolved < p.min_resolved_for_pause:
            rationale.append("sample is still too small for a pause/kill decision")
        else:
            rationale.append("evidence is mixed; continue the bounded acquisition experiment")
        return GTMDecisionKind.CONTINUE, confidence, rationale

    def _apply_stage(self, decision: GTMDecision) -> None:
        opportunity = self.engine.store.get_opportunity(decision.opportunity_id)
        if opportunity is None:
            return
        stage = {
            GTMDecisionKind.CONTINUE: VentureStage.MEASURING,
            GTMDecisionKind.PAUSE: VentureStage.PAUSED,
            GTMDecisionKind.KILL: VentureStage.KILLED,
            GTMDecisionKind.SCALE: VentureStage.SCALING,
        }[decision.decision]
        self.engine.store.save_opportunity(opportunity.model_copy(update={"stage": stage}))
