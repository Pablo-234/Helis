from __future__ import annotations

import re
from collections.abc import Iterable

from helis.domain import Evidence, Opportunity

_WORD_RE = re.compile(r"\w+", flags=re.UNICODE)
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
    "is",
    "are",
    "business",
    "businesses",
    "customer",
    "customers",
}


def _tokens(values: Iterable[str]) -> set[str]:
    output: set[str] = set()
    for value in values:
        for token in _WORD_RE.findall(value.casefold()):
            if len(token) >= 3 and token not in _STOPWORDS:
                output.add(token)
    return output


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _observation_ids(opportunity: Opportunity) -> set[object]:
    return {
        evidence.observation_id
        for evidence in opportunity.evidence
        if evidence.observation_id is not None
    }


def opportunity_similarity(left: Opportunity, right: Opportunity) -> float:
    """Transparent lexical similarity; semantic embeddings can replace this adapter later."""
    if _observation_ids(left) & _observation_ids(right):
        return 1.0

    left_core = _tokens([left.title, left.problem, left.proposed_value])
    right_core = _tokens([right.title, right.problem, right.proposed_value])
    left_customer = _tokens([left.customer])
    right_customer = _tokens([right.customer])
    core = _jaccard(left_core, right_core)
    customer = _jaccard(left_customer, right_customer)
    return round(core * 0.8 + customer * 0.2, 4)


def find_duplicate(
    candidate: Opportunity,
    existing: list[Opportunity],
    *,
    threshold: float = 0.72,
) -> tuple[Opportunity, float] | None:
    if not existing:
        return None
    scored = [(item, opportunity_similarity(candidate, item)) for item in existing]
    best, score = max(scored, key=lambda item: item[1])
    return (best, score) if score >= threshold else None


def _evidence_key(evidence: Evidence) -> tuple[object, ...]:
    if evidence.observation_id is not None:
        return ("observation", evidence.observation_id)
    return ("claim", evidence.source.casefold(), evidence.claim.casefold())


def merge_opportunities(existing: Opportunity, incoming: Opportunity) -> Opportunity:
    evidence = list(existing.evidence)
    known = {_evidence_key(item) for item in evidence}
    for item in incoming.evidence:
        key = _evidence_key(item)
        if key not in known:
            evidence.append(item)
            known.add(key)

    tags = list(existing.tags)
    known_tags = {tag.casefold() for tag in tags}
    for tag in incoming.tags:
        if tag.casefold() not in known_tags:
            tags.append(tag)
            known_tags.add(tag.casefold())

    return existing.model_copy(update={"evidence": evidence, "tags": tags})
