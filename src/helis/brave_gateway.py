from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import ClassVar
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from helis.gtm_domain import LeadChannel, LeadContactOption, ProspectEvidence, ProspectQuery
from helis.prospect_gateway import ProspectCandidate

_EMAIL_RE = re.compile(r"(?i)(?<![\w.+-])([a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,})(?![\w.-])")
_CONTACT_HINTS = ("contact", "kontakt", "get-in-touch", "reach-us", "about/contact")


class BraveSearchConfigurationError(ValueError):
    pass


def _safe_public_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost"):
        return None
    if parsed.username or parsed.password:
        return None
    return value.strip()


def _organization(title: str, url: str) -> str:
    cleaned = " ".join(title.replace("|", " ").replace("—", " ").replace("–", " ").split())
    if len(cleaned) >= 2:
        return cleaned[:300]
    host = urlsplit(url).hostname or "prospect"
    return host.removeprefix("www.")[:300]


def _email_from_text(*values: str) -> str | None:
    for value in values:
        match = _EMAIL_RE.search(value or "")
        if match:
            return match.group(1).lower()
    return None


def _looks_like_contact_page(url: str) -> bool:
    path = (urlsplit(url).path or "").lower()
    return any(hint in path for hint in _CONTACT_HINTS)


@dataclass(slots=True)
class BraveSearchProspectGateway:
    """Direct read-only prospect discovery using Brave's public Search API.

    The adapter does not scrape result websites or invent contact coordinates. An email/contact
    endpoint is attached only when it is present in the returned public search result itself.
    """

    name: ClassVar[str] = "brave_search_v1"
    api_key: str
    country: str = "US"
    search_lang: str = "en"
    timeout_seconds: int = 20
    endpoint: str = "https://api.search.brave.com/res/v1/web/search"

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise BraveSearchConfigurationError("Brave Search API key is empty")
        if len(self.country) != 2:
            raise BraveSearchConfigurationError("Brave country must be a two-letter code")

    @classmethod
    def from_env(cls) -> BraveSearchProspectGateway | None:
        key = (
            os.getenv("HELIS_BRAVE_SEARCH_API_KEY", "").strip()
            or os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
        )
        if not key:
            return None
        return cls(
            api_key=key,
            country=os.getenv("HELIS_BRAVE_COUNTRY", "US").strip().upper() or "US",
            search_lang=os.getenv("HELIS_BRAVE_SEARCH_LANG", "en").strip() or "en",
            timeout_seconds=int(os.getenv("HELIS_BRAVE_TIMEOUT", "20")),
        )

    @property
    def safe_destination(self) -> str:
        return self.endpoint

    def search(self, query: ProspectQuery) -> list[ProspectCandidate]:
        count = min(20, max(1, query.max_results))
        params = urlencode(
            {
                "q": query.query[:400],
                "count": count,
                "country": self.country,
                "search_lang": self.search_lang,
                "safesearch": "moderate",
            }
        )
        request = Request(
            f"{self.endpoint}?{params}",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
                "User-Agent": "HELIS/0.1 prospect-research",
            },
            method="GET",
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        results = payload.get("web", {}).get("results", [])
        candidates: list[ProspectCandidate] = []
        seen: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            raw_url = str(item.get("url") or "")
            url = _safe_public_url(raw_url)
            if url is None:
                continue
            host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
            if not host or host in seen:
                continue
            seen.add(host)
            title = str(item.get("title") or host)
            description = str(item.get("description") or "")
            email = _email_from_text(title, description, raw_url)
            contact_options: list[LeadContactOption] = []
            primary: str | None = None
            channel = LeadChannel.OTHER
            if email is not None:
                primary = email
                channel = LeadChannel.EMAIL
                contact_options.append(LeadContactOption(channel=LeadChannel.EMAIL, endpoint=email))
            elif _looks_like_contact_page(url):
                primary = url
                channel = LeadChannel.WEBFORM
                contact_options.append(LeadContactOption(channel=LeadChannel.WEBFORM, endpoint=url))
            evidence_text = description.strip() or title.strip()
            reason = (
                f"Brave Search result matched prospect query '{query.query[:180]}': "
                f"{evidence_text[:800]}"
            )
            candidates.append(
                ProspectCandidate(
                    organization=_organization(title, url),
                    website=url,
                    contact_endpoint=primary,
                    channel=channel,
                    contact_options=contact_options,
                    evidence=[
                        ProspectEvidence(
                            source="brave_search_api",
                            reason=reason,
                            source_url=url,
                            confidence=0.65,
                        )
                    ],
                )
            )
            if len(candidates) >= query.max_results:
                break
        return candidates
