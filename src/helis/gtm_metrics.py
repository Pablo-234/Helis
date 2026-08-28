from __future__ import annotations

from collections import Counter
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from helis.domain import utc_now
from helis.gtm_domain import LeadResponseKind
from helis.gtm_store import GTMStore


class GTMMetrics(BaseModel):
    opportunity_id: UUID
    contacts: int = Field(ge=0)
    resolved_outcomes: int = Field(ge=0)
    replies: int = Field(ge=0)
    positive_outcomes: int = Field(ge=0)
    meetings: int = Field(ge=0)
    sales: int = Field(ge=0)
    no_responses: int = Field(ge=0)
    bounces: int = Field(ge=0)
    negative_outcomes: int = Field(ge=0)
    reply_rate: float = Field(ge=0, le=1)
    positive_rate: float = Field(ge=0, le=1)
    sale_rate: float = Field(ge=0, le=1)
    bounce_rate: float = Field(ge=0, le=1)
    revenue_by_currency: dict[str, int] = Field(default_factory=dict)
    measured_at: datetime = Field(default_factory=utc_now)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def collect_gtm_metrics(state: GTMStore, opportunity_id: UUID) -> GTMMetrics:
    runs = [
        run
        for run in state.list_outreach_runs(opportunity_id)
        if run.dispatched_at is not None
    ]
    responses = state.list_responses(opportunity_id)
    counts = Counter(response.kind for response in responses)

    replies = sum(
        counts[kind]
        for kind in (
            LeadResponseKind.INTERESTED,
            LeadResponseKind.NOT_INTERESTED,
            LeadResponseKind.MEETING,
            LeadResponseKind.SALE,
        )
    )
    positive = sum(
        counts[kind]
        for kind in (
            LeadResponseKind.INTERESTED,
            LeadResponseKind.MEETING,
            LeadResponseKind.SALE,
        )
    )
    negative = counts[LeadResponseKind.NOT_INTERESTED] + counts[LeadResponseKind.BOUNCE]
    resolved = len(responses)

    revenue_by_currency: dict[str, int] = {}
    for event in state.list_revenue(opportunity_id):
        currency = event.currency.upper()
        revenue_by_currency[currency] = revenue_by_currency.get(currency, 0) + event.amount_cents

    return GTMMetrics(
        opportunity_id=opportunity_id,
        contacts=len(runs),
        resolved_outcomes=resolved,
        replies=replies,
        positive_outcomes=positive,
        meetings=counts[LeadResponseKind.MEETING],
        sales=counts[LeadResponseKind.SALE],
        no_responses=counts[LeadResponseKind.NO_RESPONSE],
        bounces=counts[LeadResponseKind.BOUNCE],
        negative_outcomes=negative,
        reply_rate=_rate(replies, resolved),
        positive_rate=_rate(positive, resolved),
        sale_rate=_rate(counts[LeadResponseKind.SALE], resolved),
        bounce_rate=_rate(counts[LeadResponseKind.BOUNCE], resolved),
        revenue_by_currency=revenue_by_currency,
    )
