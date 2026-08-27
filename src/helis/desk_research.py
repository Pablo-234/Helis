from __future__ import annotations

import json
import re
from collections.abc import Iterable

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.domain import (
    Experiment,
    ExperimentRun,
    Observation,
    Opportunity,
    ValidationOutcome,
    ValidationResult,
)
from helis.model_provider import ModelProvider
from helis.store import HelisStore


class DeskResearchEvidenceError(RuntimeError):
    pass


class DeskResearchPayload(BaseModel):
    outcome: ValidationOutcome
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=3)
    supporting_observation_ids: list[str] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    pivot_signal: str | None = None


SYSTEM_PROMPT = """You are HELIS Desk Research Validator.
Your job is to test ONE venture experiment using ONLY the supplied real observations.
Do not invent market facts. A positive or negative conclusion must cite observation IDs supplied
in the prompt. If the corpus cannot test the hypothesis, return inconclusive.
Return JSON only:
{"outcome":"positive|negative|inconclusive","confidence":0.0,"summary":"...",
"supporting_observation_ids":[],"metrics":{},"pivot_signal":null}
"""


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-Z0-9_]{3,}", text.lower())}


def _relevant_observations(
    opportunity: Opportunity,
    experiment: Experiment,
    observations: Iterable[Observation],
    *,
    limit: int,
) -> list[Observation]:
    query = _tokens(
        " ".join(
            [
                opportunity.title,
                opportunity.problem,
                opportunity.customer,
                opportunity.proposed_value,
                experiment.hypothesis,
                experiment.success_metric,
            ]
        )
    )
    ranked = sorted(
        observations,
        key=lambda item: len(query & _tokens(item.text)),
        reverse=True,
    )
    return ranked[:limit]


class DeskResearchExecutor:
    name = "desk_research_corpus_v1"

    def __init__(
        self,
        provider: ModelProvider,
        model_budget: CycleBudget,
        store: HelisStore,
        *,
        observation_limit: int = 30,
    ) -> None:
        self.provider = provider
        self.model_budget = model_budget
        self.store = store
        self.observation_limit = observation_limit

    def execute(
        self,
        experiment: Experiment,
        opportunity: Opportunity,
        run: ExperimentRun,
    ) -> ValidationResult:
        selected = _relevant_observations(
            opportunity,
            experiment,
            self.store.list_observations(limit=500),
            limit=self.observation_limit,
        )
        if not selected:
            return ValidationResult(
                run_id=run.id,
                experiment_id=experiment.id,
                opportunity_id=opportunity.id,
                outcome=ValidationOutcome.INCONCLUSIVE,
                confidence=0.15,
                summary="No external observations are available to test this hypothesis yet.",
                source=self.name,
            )

        corpus = [
            {
                "id": str(item.id),
                "source": item.source,
                "captured_at": item.captured_at.isoformat(),
                "text": item.text,
            }
            for item in selected
        ]
        self.model_budget.ensure_call_available()
        response = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "experiment": experiment.model_dump(mode="json"),
                    "observations": corpus,
                },
                ensure_ascii=False,
            ),
        )
        self.model_budget.record(response)
        payload = DeskResearchPayload.model_validate_json(response.content)

        known = {str(item.id): item.id for item in selected}
        invalid = [item for item in payload.supporting_observation_ids if item not in known]
        if invalid:
            raise DeskResearchEvidenceError(
                "model cited observation IDs that were not present in the research corpus"
            )
        bound_ids = [known[item] for item in payload.supporting_observation_ids]
        if payload.outcome != ValidationOutcome.INCONCLUSIVE and not bound_ids:
            raise DeskResearchEvidenceError(
                "positive/negative desk research conclusion must cite at least one observation"
            )

        return ValidationResult(
            run_id=run.id,
            experiment_id=experiment.id,
            opportunity_id=opportunity.id,
            outcome=payload.outcome,
            confidence=payload.confidence,
            summary=payload.summary,
            supporting_observation_ids=bound_ids,
            metrics=payload.metrics,
            pivot_signal=payload.pivot_signal,
            source=self.name,
            actual_cost_cents=response.estimated_cost_cents,
        )
