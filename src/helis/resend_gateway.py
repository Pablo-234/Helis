from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parseaddr
from html import unescape
from typing import ClassVar
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from helis.contact_gateway import ContactGatewayAck
from helis.contact_result_gateway import ContactResultGateway
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadResponse,
    LeadResponseKind,
    OutreachDraft,
    OutreachRun,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TAG_RE = re.compile(r"<[^>]+>")
_NEGATIVE_MARKERS = (
    "not interested",
    "no thanks",
    "no thank you",
    "remove me",
    "unsubscribe",
    "stop emailing",
    "nie jestem zainteres",
    "nie, dziękuję",
    "nie dziekuje",
    "proszę nie pisać",
    "prosze nie pisac",
)
_MEETING_MARKERS = (
    "schedule a call",
    "book a call",
    "let's talk",
    "lets talk",
    "meeting",
    "calendar",
    "umówmy",
    "umowmy",
    "spotkanie",
    "porozmawiajmy",
)


class ResendGatewayConfigurationError(ValueError):
    pass


def _normalize_email(value: str | None) -> str | None:
    raw = (value or "").strip()
    if raw.lower().startswith("mailto:"):
        raw = raw[7:].split("?", 1)[0]
    _, address = parseaddr(raw)
    address = (address or raw).strip().lower()
    return address if _EMAIL_RE.fullmatch(address) else None


def _clean_domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("@").rstrip(".")
    if not domain or "." not in domain or any(char.isspace() for char in domain):
        raise ResendGatewayConfigurationError("Resend inbound domain is invalid")
    return domain


def _reply_address(run: OutreachRun, inbound_domain: str) -> str:
    return f"helis-{run.id}@{inbound_domain}"


def _plain_text(text: str | None, html: str | None) -> str:
    value = (text or "").strip()
    if not value and html:
        value = _TAG_RE.sub(" ", unescape(html))
    return " ".join(value.split())


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(slots=True)
class ResendContactGateway:
    """Send one already-approved HELIS outreach draft through Resend."""

    name: ClassVar[str] = "resend_email_v1"
    api_key: str
    from_email: str
    inbound_domain: str | None = None
    timeout_seconds: int = 30
    api_base: str = "https://api.resend.com"

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ResendGatewayConfigurationError("Resend API key is empty")
        if _normalize_email(self.from_email) is None:
            raise ResendGatewayConfigurationError("HELIS_RESEND_FROM must contain a valid email")
        if self.inbound_domain is not None:
            self.inbound_domain = _clean_domain(self.inbound_domain)

    @classmethod
    def from_env(cls) -> ResendContactGateway | None:
        key = (
            os.getenv("HELIS_RESEND_API_KEY", "").strip()
            or os.getenv("RESEND_API_KEY", "").strip()
        )
        sender = os.getenv("HELIS_RESEND_FROM", "").strip()
        if not key or not sender:
            return None
        inbound = os.getenv("HELIS_RESEND_INBOUND_DOMAIN", "").strip() or None
        return cls(
            api_key=key,
            from_email=sender,
            inbound_domain=inbound,
            timeout_seconds=int(os.getenv("HELIS_RESEND_TIMEOUT", "30")),
        )

    @property
    def safe_destination(self) -> str:
        return f"{self.api_base}/emails"

    def send(self, run: OutreachRun, lead: Lead, draft: OutreachDraft) -> ContactGatewayAck:
        endpoint = draft.contact_endpoint or lead.contact_endpoint
        recipient = _normalize_email(endpoint)
        if draft.channel != LeadChannel.EMAIL or recipient is None:
            raise RuntimeError("Resend adapter only dispatches drafts bound to a valid email endpoint")
        payload: dict[str, object] = {
            "from": self.from_email,
            "to": [recipient],
            "subject": draft.subject or "Quick question",
            "text": draft.body,
        }
        reply_to: str | None = None
        if self.inbound_domain is not None:
            reply_to = _reply_address(run, self.inbound_domain)
            payload["reply_to"] = reply_to
        request = Request(
            f"{self.api_base}/emails",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Idempotency-Key": str(run.id),
                "User-Agent": "HELIS/0.1 outreach",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            data = json.loads(response.read().decode("utf-8"))
        email_id = str(data.get("id") or "").strip()
        if not email_id:
            raise RuntimeError("Resend did not return an email id")
        return ContactGatewayAck(
            accepted=True,
            dispatch_id=email_id,
            channel=LeadChannel.EMAIL.value,
            metadata={"provider": "resend", "reply_to": reply_to or ""},
        )


@dataclass(slots=True)
class ResendContactResultGateway(ContactResultGateway):
    """Read observed replies from Resend using one per-run Reply-To address.

    This adapter never infers a SALE or revenue from email text. Actual payment remains a separate
    observed commerce boundary.
    """

    name: ClassVar[str] = "resend_inbound_results_v1"
    api_key: str
    inbound_domain: str
    timeout_seconds: int = 30
    api_base: str = "https://api.resend.com"
    list_limit: int = 100

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ResendGatewayConfigurationError("Resend API key is empty")
        self.inbound_domain = _clean_domain(self.inbound_domain)
        self.list_limit = max(1, min(100, self.list_limit))

    @classmethod
    def from_env(cls) -> ResendContactResultGateway | None:
        key = (
            os.getenv("HELIS_RESEND_API_KEY", "").strip()
            or os.getenv("RESEND_API_KEY", "").strip()
        )
        inbound = os.getenv("HELIS_RESEND_INBOUND_DOMAIN", "").strip()
        if not key or not inbound:
            return None
        return cls(
            api_key=key,
            inbound_domain=inbound,
            timeout_seconds=int(os.getenv("HELIS_RESEND_TIMEOUT", "30")),
        )

    @property
    def safe_destination(self) -> str:
        return f"{self.api_base}/emails/receiving"

    def fetch(self, run: OutreachRun) -> LeadResponse | None:
        target = _reply_address(run, self.inbound_domain).lower()
        params = urlencode({"limit": self.list_limit})
        listing = self._get_json(f"/emails/receiving?{params}")
        items = listing.get("data", [])
        if not isinstance(items, list):
            raise TypeError("Resend inbound list response has invalid data shape")
        matches: list[tuple[datetime, str]] = []
        dispatched = run.dispatched_at.astimezone(UTC) if run.dispatched_at is not None else None
        for item in items:
            if not isinstance(item, dict):
                continue
            recipients = item.get("to", [])
            if isinstance(recipients, str):
                recipients = [recipients]
            if not isinstance(recipients, list):
                continue
            if target not in {str(value).strip().lower() for value in recipients}:
                continue
            received_at = _parse_time(item.get("created_at")) or datetime.now(UTC)
            if dispatched is not None and received_at < dispatched:
                continue
            email_id = str(item.get("id") or "").strip()
            if email_id:
                matches.append((received_at, email_id))
        if not matches:
            return None
        _, email_id = min(matches, key=lambda pair: pair[0])
        email = self._get_json(f"/emails/receiving/{email_id}")
        text = _plain_text(
            str(email.get("text")) if email.get("text") is not None else None,
            str(email.get("html")) if email.get("html") is not None else None,
        )
        subject = " ".join(str(email.get("subject") or "Reply received").split())
        summary_source = text or subject
        summary = summary_source[:1800] if len(summary_source) >= 3 else "Observed email reply"
        lowered = f"{subject}\n{text}".lower()
        if any(marker in lowered for marker in _NEGATIVE_MARKERS):
            kind = LeadResponseKind.NOT_INTERESTED
        elif any(marker in lowered for marker in _MEETING_MARKERS):
            kind = LeadResponseKind.MEETING
        else:
            kind = LeadResponseKind.INTERESTED
        return LeadResponse(
            run_id=run.id,
            lead_id=run.lead_id,
            opportunity_id=run.opportunity_id,
            kind=kind,
            summary=summary,
            revenue_cents=0,
            currency="PLN",
        )

    def _get_json(self, path: str) -> dict[str, object]:
        request = Request(
            f"{self.api_base}{path}",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": "HELIS/0.1 outreach-results",
            },
            method="GET",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("Resend returned a non-object JSON response")
        return payload
