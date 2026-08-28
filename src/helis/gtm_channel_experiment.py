from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from itertools import combinations
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from helis.domain import AuditEvent, utc_now
from helis.engine import HelisEngine
from helis.gtm_domain import Lead, LeadChannel, LeadResponseKind, lead_contact_options
from helis.gtm_experiment_domain import GTMExperimentStatus
from helis.gtm_experiment_store import GTMExperimentStore
from helis.gtm_store import GTMStore, lead_identity


class GTMChannelExperimentStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"


class GTMChannelExperimentArm(BaseModel):
    key: str = Field(pattern=r"^(control|variant)$")
    channel: LeadChannel


class GTMChannelExperiment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    opportunity_id: UUID
    arms: list[GTMChannelExperimentArm] = Field(min_length=2, max_length=2)
    minimum_resolved_per_arm: int = Field(default=2, ge=2, le=10)
    max_resolved_per_arm: int = Field(default=5, ge=2, le=20)
    max_assignments_per_arm: int = Field(default=5, ge=2, le=20)
    minimum_lift: float = Field(default=0.20, gt=0, le=1)
    status: GTMChannelExperimentStatus = GTMChannelExperimentStatus.ACTIVE
    winner_arm_key: str | None = None
    conclusion: str | None = Field(default=None, max_length=1200)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_experiment(self) -> GTMChannelExperiment:
        by_key = {arm.key: arm for arm in self.arms}
        if set(by_key) != {"control", "variant"}:
            raise ValueError("channel experiments require exactly control and variant arms")
        channels = {arm.channel for arm in self.arms}
        if len(channels) != 2:
            raise ValueError("channel experiment arms must use two distinct channels")
        if LeadChannel.OTHER in channels:
            raise ValueError("channel experiments require explicit contact channels")
        if self.max_resolved_per_arm < self.minimum_resolved_per_arm:
            raise ValueError("max_resolved_per_arm must be >= minimum_resolved_per_arm")
        if self.max_assignments_per_arm < self.max_resolved_per_arm:
            raise ValueError("max_assignments_per_arm must be >= max_resolved_per_arm")
        if self.winner_arm_key is not None and self.winner_arm_key not in by_key:
            raise ValueError("winner_arm_key must reference a channel experiment arm")
        return self


class GTMChannelArmMetrics(BaseModel):
    arm_key: str
    channel: LeadChannel
    assigned: int = Field(default=0, ge=0)
    resolved: int = Field(default=0, ge=0)
    sales: int = Field(default=0, ge=0)
    meetings: int = Field(default=0, ge=0)
    interested: int = Field(default=0, ge=0)
    revenue_cents: int = Field(default=0, ge=0)
    outcome_score: float = Field(default=0, ge=0, le=1)


class GTMChannelExperimentSnapshot(BaseModel):
    experiment_id: UUID
    arms: list[GTMChannelArmMetrics]
    completed: bool = False
    winner_arm_key: str | None = None
    conclusion: str = Field(min_length=3, max_length=1200)


@dataclass(frozen=True, slots=True)
class GTMChannelAssignment:
    experiment_id: UUID
    arm_key: str
    channel: LeadChannel
    endpoint: str


@dataclass(slots=True)
class GTMChannelPlanResult:
    experiment: GTMChannelExperiment | None
    created: bool = False


class GTMChannelExperimentStore:
    def __init__(self, engine: HelisEngine) -> None:
        self.store = engine.store
        self.initialize()

    def initialize(self) -> None:
        with self.store.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS gtm_channel_experiments (
                    id TEXT PRIMARY KEY,
                    opportunity_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_gtm_channel_experiments_venture_status
                    ON gtm_channel_experiments(opportunity_id, status, updated_at);
                """
            )

    def save(self, experiment: GTMChannelExperiment) -> None:
        with self.store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO gtm_channel_experiments "
                "(id, opportunity_id, status, payload, updated_at) VALUES (?, ?, ?, ?, ?)",
                (
                    str(experiment.id),
                    str(experiment.opportunity_id),
                    experiment.status.value,
                    experiment.model_dump_json(),
                    experiment.updated_at.isoformat(),
                ),
            )

    def latest(self, opportunity_id: UUID) -> GTMChannelExperiment | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_channel_experiments WHERE opportunity_id = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (str(opportunity_id),),
            ).fetchone()
        return GTMChannelExperiment.model_validate_json(row["payload"]) if row else None

    def active(self, opportunity_id: UUID) -> GTMChannelExperiment | None:
        with self.store.connect() as db:
            row = db.execute(
                "SELECT payload FROM gtm_channel_experiments "
                "WHERE opportunity_id = ? AND status = ? ORDER BY updated_at DESC LIMIT 1",
                (str(opportunity_id), GTMChannelExperimentStatus.ACTIVE.value),
            ).fetchone()
        return GTMChannelExperiment.model_validate_json(row["payload"]) if row else None

    def list(self, opportunity_id: UUID) -> list[GTMChannelExperiment]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT payload FROM gtm_channel_experiments WHERE opportunity_id = ? "
                "ORDER BY updated_at ASC",
                (str(opportunity_id),),
            ).fetchall()
        return [GTMChannelExperiment.model_validate_json(row["payload"]) for row in rows]


class GTMChannelExperimentManager:
    """Runs one deterministic two-channel test after commercial-offer testing is complete."""

    def __init__(self, engine: HelisEngine) -> None:
        self.engine = engine
        self.gtm = GTMStore(engine.store)
        self.commercial = GTMExperimentStore(engine.store)
        self.state = GTMChannelExperimentStore(engine)

    def plan_if_eligible(self, opportunity_id: UUID) -> GTMChannelPlanResult:
        existing = self.state.latest(opportunity_id)
        if existing is not None:
            return GTMChannelPlanResult(existing, created=False)

        commercial = self.commercial.latest(opportunity_id)
        if commercial is None or commercial.status != GTMExperimentStatus.COMPLETED:
            return GTMChannelPlanResult(None, created=False)

        candidates = [
            lead
            for lead in self.gtm.list_leads(opportunity_id)
            if lead.stage.value == "qualified" and self.gtm.get_draft_for_lead(lead.id) is None
        ]
        pair = self._best_pair(candidates)
        if pair is None:
            return GTMChannelPlanResult(None, created=False)

        supporting = [lead for lead in candidates if self._has_channels(lead, set(pair))]
        control_channel = self._control_channel(pair, supporting)
        variant_channel = pair[1] if pair[0] == control_channel else pair[0]
        experiment = GTMChannelExperiment(
            opportunity_id=opportunity_id,
            arms=[
                GTMChannelExperimentArm(key="control", channel=control_channel),
                GTMChannelExperimentArm(key="variant", channel=variant_channel),
            ],
        )
        self.state.save(experiment)
        self.engine.store.append_event(
            AuditEvent(
                event_type="gtm.channel_experiment_planned",
                entity_id=experiment.id,
                data={
                    "opportunity_id": str(opportunity_id),
                    "control_channel": control_channel.value,
                    "variant_channel": variant_channel.value,
                    "eligible_leads": len(supporting),
                    "max_assignments_per_arm": experiment.max_assignments_per_arm,
                },
            )
        )
        return GTMChannelPlanResult(experiment, created=True)

    def assign_for_leads(
        self,
        opportunity_id: UUID,
        leads: list[Lead],
    ) -> dict[UUID, GTMChannelAssignment]:
        if not leads:
            return {}
        experiment = self.state.latest(opportunity_id)
        if experiment is None:
            return {}
        by_key = {arm.key: arm for arm in experiment.arms}

        if experiment.status == GTMChannelExperimentStatus.COMPLETED:
            if experiment.winner_arm_key is None:
                return {}
            winner = by_key[experiment.winner_arm_key]
            return self._winner_assignments(experiment, winner, leads)

        counts = {key: 0 for key in by_key}
        for draft in self.gtm.list_drafts(opportunity_id):
            if (
                draft.channel_experiment_id != experiment.id
                or draft.channel_experiment_arm_key not in counts
            ):
                continue
            counts[draft.channel_experiment_arm_key] += 1

        required_channels = {arm.channel for arm in experiment.arms}
        eligible = [lead for lead in leads if self._has_channels(lead, required_channels)]
        assignments: dict[UUID, GTMChannelAssignment] = {}
        for lead in sorted(eligible, key=lead_identity):
            available = [
                key
                for key in sorted(by_key)
                if counts[key] < experiment.max_assignments_per_arm
            ]
            if not available:
                break
            smallest = min(counts[key] for key in available)
            tied = [key for key in available if counts[key] == smallest]
            digest = hashlib.sha256(lead_identity(lead).encode("utf-8")).digest()
            chosen_key = tied[int.from_bytes(digest[:4], "big") % len(tied)]
            arm = by_key[chosen_key]
            endpoint = self._endpoint_for(lead, arm.channel)
            if endpoint is None:
                continue
            assignments[lead.id] = GTMChannelAssignment(
                experiment_id=experiment.id,
                arm_key=arm.key,
                channel=arm.channel,
                endpoint=endpoint,
            )
            counts[chosen_key] += 1
        return assignments

    def refresh(self, opportunity_id: UUID) -> GTMChannelExperimentSnapshot | None:
        experiment = self.state.active(opportunity_id)
        if experiment is None:
            return None
        by_key = {
            arm.key: GTMChannelArmMetrics(arm_key=arm.key, channel=arm.channel)
            for arm in experiment.arms
        }
        scores = {arm.key: 0.0 for arm in experiment.arms}

        for draft in self.gtm.list_drafts(opportunity_id):
            if (
                draft.channel_experiment_id != experiment.id
                or draft.channel_experiment_arm_key not in by_key
            ):
                continue
            by_key[draft.channel_experiment_arm_key].assigned += 1

        for response in self.gtm.list_responses(opportunity_id):
            run = self.gtm.get_outreach_run(response.run_id)
            if run is None:
                continue
            draft = self.gtm.get_draft(run.draft_id)
            if (
                draft is None
                or draft.channel_experiment_id != experiment.id
                or draft.channel_experiment_arm_key not in by_key
            ):
                continue
            metrics = by_key[draft.channel_experiment_arm_key]
            metrics.resolved += 1
            metrics.revenue_cents += response.revenue_cents
            score = self._outcome_score(response.kind)
            scores[draft.channel_experiment_arm_key] += score
            if response.kind == LeadResponseKind.SALE:
                metrics.sales += 1
            elif response.kind == LeadResponseKind.MEETING:
                metrics.meetings += 1
            elif response.kind == LeadResponseKind.INTERESTED:
                metrics.interested += 1

        for key, metrics in by_key.items():
            if metrics.resolved:
                metrics.outcome_score = scores[key] / metrics.resolved

        control = by_key["control"]
        variant = by_key["variant"]
        completed = False
        winner: str | None = None
        conclusion = (
            f"collecting channel evidence: control={control.resolved}, "
            f"variant={variant.resolved} resolved"
        )
        enough = min(control.resolved, variant.resolved) >= experiment.minimum_resolved_per_arm
        if enough:
            lift = variant.outcome_score - control.outcome_score
            if abs(lift) >= experiment.minimum_lift:
                completed = True
                winner = "variant" if lift > 0 else "control"
                conclusion = (
                    f"{winner} channel won on deterministic outcome score; "
                    f"absolute lift={abs(lift):.3f}"
                )
            elif min(control.resolved, variant.resolved) >= experiment.max_resolved_per_arm:
                completed = True
                conclusion = "channel experiment reached its sample cap without minimum lift"

        if completed:
            experiment = experiment.model_copy(
                update={
                    "status": GTMChannelExperimentStatus.COMPLETED,
                    "winner_arm_key": winner,
                    "conclusion": conclusion,
                    "updated_at": utc_now(),
                }
            )
            self.state.save(experiment)
            self.engine.store.append_event(
                AuditEvent(
                    event_type="gtm.channel_experiment_completed",
                    entity_id=experiment.id,
                    data={
                        "opportunity_id": str(opportunity_id),
                        "winner_arm_key": winner,
                        "conclusion": conclusion,
                        "control_channel": control.channel.value,
                        "variant_channel": variant.channel.value,
                        "control_score": control.outcome_score,
                        "variant_score": variant.outcome_score,
                    },
                )
            )

        return GTMChannelExperimentSnapshot(
            experiment_id=experiment.id,
            arms=[control, variant],
            completed=completed,
            winner_arm_key=winner,
            conclusion=conclusion,
        )

    @staticmethod
    def _best_pair(leads: list[Lead]) -> tuple[LeadChannel, LeadChannel] | None:
        counts: dict[tuple[LeadChannel, LeadChannel], int] = {}
        explicit = {LeadChannel.EMAIL, LeadChannel.WEBFORM, LeadChannel.DM}
        for lead in leads:
            channels = sorted(
                {option.channel for option in lead_contact_options(lead)} & explicit,
                key=lambda item: item.value,
            )
            for first, second in combinations(channels, 2):
                pair = (first, second)
                counts[pair] = counts.get(pair, 0) + 1
        eligible = [(count, pair) for pair, count in counts.items() if count >= 2]
        if not eligible:
            return None
        eligible.sort(key=lambda item: (-item[0], item[1][0].value, item[1][1].value))
        return eligible[0][1]

    @staticmethod
    def _control_channel(
        pair: tuple[LeadChannel, LeadChannel],
        leads: list[Lead],
    ) -> LeadChannel:
        primary_counts = {channel: 0 for channel in pair}
        for lead in leads:
            if lead.contact_endpoint and lead.channel in primary_counts:
                primary_counts[lead.channel] += 1
        maximum = max(primary_counts.values())
        tied = sorted(
            (channel for channel, count in primary_counts.items() if count == maximum),
            key=lambda item: item.value,
        )
        return tied[0]

    @staticmethod
    def _has_channels(lead: Lead, channels: set[LeadChannel]) -> bool:
        available = {option.channel for option in lead_contact_options(lead)}
        return channels <= available

    @staticmethod
    def _endpoint_for(lead: Lead, channel: LeadChannel) -> str | None:
        endpoints = sorted(
            option.endpoint
            for option in lead_contact_options(lead)
            if option.channel == channel
        )
        return endpoints[0] if endpoints else None

    def _winner_assignments(
        self,
        experiment: GTMChannelExperiment,
        winner: GTMChannelExperimentArm,
        leads: list[Lead],
    ) -> dict[UUID, GTMChannelAssignment]:
        assignments: dict[UUID, GTMChannelAssignment] = {}
        for lead in leads:
            endpoint = self._endpoint_for(lead, winner.channel)
            if endpoint is None:
                continue
            assignments[lead.id] = GTMChannelAssignment(
                experiment_id=experiment.id,
                arm_key=winner.key,
                channel=winner.channel,
                endpoint=endpoint,
            )
        return assignments

    @staticmethod
    def _outcome_score(kind: LeadResponseKind) -> float:
        if kind == LeadResponseKind.SALE:
            return 1.0
        if kind == LeadResponseKind.MEETING:
            return 0.75
        if kind == LeadResponseKind.INTERESTED:
            return 0.5
        return 0.0
