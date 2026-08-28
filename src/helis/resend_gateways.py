from __future__ import annotations

import json
import os
from dataclasses import dataclass
from email.utils import parseaddr
from typing import ClassVar
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from helis.contact_gateway import ContactGatewayAck
from helis.gtm_domain import (
    Lead,
    LeadChannel,
    LeadResponse,
    LeadResponseKind,
    OutreachDraft,
    OutreachRun,
)


@dataclass(slots=True)
class ResendContactGateway:
    """Direct Resend sender for already-approved email outreach runs."""

    name: ClassVar[str] = "resend_contact_v1"
    api_key: str
    from_address: str
    receiving_domain: str
    timeout_seconds: int = 30
    api_base: str = "https://api.resend.com"

    @classmethod
    def from_env(cls) -> ResendContactGateway | None:
        api_key = os.getenv("HELIS_RESEND_API_KEY", "").strip()
        from_address = os.getenv("HELIS_RESEND_FROM", "").strip()
        receiving_domain = os.getenv("HELIS_RESEND_RECEIVING_DOMAIN", "").strip().lstrip("@")
        if not api_key or not from_address or not receiving_domain:
            return None
        return cls(
            api_key=api_key,
            from_address=from_address,
            receiving_domain=receiving_domain,
            timeout_seconds=int(os.getenv("HELIS_RESEND_TIMEOUT", "30")),
        )

    @property
    def safe_destination(self) -> str:
        return "https://api.resend.com/emails"

    def send(self, run: OutreachRun, lead: Lead, draft: OutreachDraft) -> ContactGatewayAck:
        if draft.channel != LeadChannel.EMAIL:
            raise RuntimeError("Resend direct adapter only supports email drafts")
        endpoint = (draft.contact_endpoint or lead.contact_endpoint or "").strip()
        _, recipient = parseaddr(endpoint)
        if not recipient or "@" not in recipient:
            raise RuntimeError("approved Resend draft has no valid email endpoint")
        payload = {
            "from": self.from_address,
            "to": [recipient],
            "subject": draft.subject or "Quick question",
            "text": draft.body,
            "reply_to": self.reply_address(run),
            "tags": [
                {"name": "helis_run", "value": str(run.id)},
                {"name": "helis_venture", "value": str(run.opportunity_id)},
            ],
        }
        result = self._request_json(
            "/emails",
            method="POST",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": str(run.id),
            },
        )
        email_id = str(result.get("id", "")).strip()
        if not email_id:
            raise RuntimeError("Resend did not return an email id")
        return ContactGatewayAck(
            accepted=True,
            dispatch_id=email_id,
            channel=LeadChannel.EMAIL.value,
            metadata={
                "provider": "resend",
                "reply_to": self.reply_address(run),
            },
        )

    def reply_address(self, run: OutreachRun) -> str:
        return f"helis-{run.id.hex}@{self.receiving_domain}"

    def _request_json(
        self,
        path: str,
        *,
        method: str,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict:
        request_headers = {"Authorization": f"Bearer {self.api_key}"}
        request_headers.update(headers or {})
        request = Request(
            f"{self.api_base}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Resend returned a non-object response")
        return payload


@dataclass(slots=True)
class ResendContactResultGateway:
    """Read-only Resend Receiving poller. It never infers revenue from reply text."""

    name: ClassVar[str] = "resend_contact_result_v1"
    api_key: str
    receiving_domain: str
    timeout_seconds: int = 30
    api_base: str = "https://api.resend.com"

    @classmethod
    def from_env(cls) -> ResendContactResultGateway | None:
        api_key = os.getenv("HELIS_RESEND_API_KEY", "").strip()
        receiving_domain = os.getenv("HELIS_RESEND_RECEIVING_DOMAIN", "").strip().lstrip("@")
        if not api_key or not receiving_domain:
            return None
        return cls(
            api_key=api_key,
            receiving_domain=receiving_domain,
            timeout_seconds=int(os.getenv("HELIS_RESEND_TIMEOUT", "30")),
        )

    @property
    def safe_destination(self) -> str:
        return "https://api.resend.com/emails/receiving"

    def fetch(self, run: OutreachRun) -> LeadResponse | None:
        expected_to = self.reply_address(run).lower()
        query = urlencode({"limit": 100})
        listing = self._request_json(f"/emails/receiving?{query}", method="GET")
        items = listing.get("data")
        if not isinstance(items, list):
            return None
        match: dict | None = None
        for item in items:
            if not isinstance(item, dict):
                continue
            recipients = item.get("to")
            if not isinstance(recipients, list):
                continue
            if expected_to not in {str(value).lower() for value in recipients}:
                continue
            match = item
            break
        if match is None:
            return None
        email_id = str(match.get("id", "")).strip()
        if not email_id:
            return None
        received = self._request_json(
            f"/emails/receiving/{quote(email_id, safe='')}",
            method="GET",
        )
        text = self._plain_text(received)
        kind = self._kind(text)
        summary = self._summary(text, match)
        return LeadResponse(
            run_id=run.id,
            lead_id=run.lead_id,
            opportunity_id=run.opportunity_id,
            kind=kind,
            summary=summary,
            revenue_cents=0,
        )

    def reply_address(self, run: OutreachRun) -> str:
        return f"helis-{run.id.hex}@{self.receiving_domain}"

    def _request_json(self, path: str, *, method: str) -> dict:
        request = Request(
            f"{self.api_base}{path}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            method=method,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("Resend returned a non-object response")
        return payload

    @staticmethod
    def _plain_text(received: dict) -> str:
        text = received.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
        subject = received.get("subject")
        return str(subject).strip() if subject else "Reply received"

    @staticmethod
    def _kind(text: str) -> LeadResponseKind:
        lowered = " ".join(text.lower().split())
        negative_markers = (
            "not interested",
            "no thanks",
            "remove me",
            "do not contact",
            "don't contact",
            "unsubscribe",
            "stop emailing",
        )
        if any(marker in lowered for marker in negative_markers):
            return LeadResponseKind.NOT_INTERESTED
        return LeadResponseKind.INTERESTED

    @staticmethod
    def _summary(text: str, metadata: dict) -> str:
        compact = " ".join(text.split())
        if len(compact) >= 3:
            return compact[:1200]
        subject = str(metadata.get("subject", "reply received")).strip()
        return subject[:1200] if len(subject) >= 3 else "Reply received"
