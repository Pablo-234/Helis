import pytest
from pydantic import ValidationError

from helis.contact_result_gateway import ContactResultAck
from helis.gtm_domain import LeadResponseKind


def test_pending_result_cannot_claim_revenue() -> None:
    with pytest.raises(ValidationError, match="pending contact result"):
        ContactResultAck(
            ready=False,
            revenue_cents=5000,
            currency="PLN",
        )


def test_non_sale_result_cannot_claim_revenue() -> None:
    with pytest.raises(ValidationError, match="revenue is only valid for a sale"):
        ContactResultAck(
            ready=True,
            kind=LeadResponseKind.INTERESTED,
            summary="Prospect asked for more information.",
            revenue_cents=5000,
            currency="PLN",
        )


def test_observed_sale_may_report_positive_revenue() -> None:
    result = ContactResultAck(
        ready=True,
        kind=LeadResponseKind.SALE,
        summary="Payment was observed by the operator-owned sales system.",
        revenue_cents=12_500,
        currency="PLN",
    )

    assert result.ready is True
    assert result.kind == LeadResponseKind.SALE
    assert result.revenue_cents == 12_500
