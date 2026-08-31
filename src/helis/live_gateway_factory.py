from __future__ import annotations

from dataclasses import dataclass

from helis.brave_gateway import BraveSearchProspectGateway
from helis.commerce_gateway import ApprovedCommerceGateway, CommerceGateway
from helis.contact_gateway import ApprovedContactGateway, ContactGateway
from helis.contact_result_gateway import ApprovedContactResultGateway, ContactResultGateway
from helis.preview_gateway import ApprovedPreviewGateway, PreviewGateway
from helis.prospect_gateway import ApprovedProspectGateway, ProspectGateway
from helis.resend_gateway import ResendContactGateway, ResendContactResultGateway
from helis.stripe_gateway import StripeCommerceGateway
from helis.vercel_gateway import VercelCliPreviewGateway


@dataclass(frozen=True, slots=True)
class LiveGatewaySelection:
    preview: PreviewGateway | None
    prospect: ProspectGateway | None
    contact: ContactGateway | None
    contact_result: ContactResultGateway | None
    commerce: CommerceGateway | None

    @property
    def names(self) -> dict[str, str | None]:
        return {
            "preview": getattr(self.preview, "name", None),
            "prospect": getattr(self.prospect, "name", None),
            "contact": getattr(self.contact, "name", None),
            "contact_result": getattr(self.contact_result, "name", None),
            "commerce": getattr(self.commerce, "name", None),
        }


def live_gateways_from_env() -> LiveGatewaySelection:
    """Prefer first-party direct adapters, then preserve generic approved gateway fallbacks."""
    preview: PreviewGateway | None = VercelCliPreviewGateway.from_env()
    if preview is None:
        preview = ApprovedPreviewGateway.from_env()

    prospect: ProspectGateway | None = BraveSearchProspectGateway.from_env()
    if prospect is None:
        prospect = ApprovedProspectGateway.from_env()

    contact: ContactGateway | None = ResendContactGateway.from_env()
    if contact is None:
        contact = ApprovedContactGateway.from_env()

    contact_result: ContactResultGateway | None = ResendContactResultGateway.from_env()
    if contact_result is None:
        contact_result = ApprovedContactResultGateway.from_env()

    commerce: CommerceGateway | None = StripeCommerceGateway.from_env()
    if commerce is None:
        commerce = ApprovedCommerceGateway.from_env()

    return LiveGatewaySelection(
        preview=preview,
        prospect=prospect,
        contact=contact,
        contact_result=contact_result,
        commerce=commerce,
    )
