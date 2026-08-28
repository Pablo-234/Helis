from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.build_templates import get_template
from helis.builder_generator import BuildGenerationError
from helis.commerce_domain import CommerceBuildContext
from helis.domain import (
    BuildBundle,
    BuildCheck,
    BuildFile,
    BuildReview,
    BuildRun,
    BuildSpec,
    Opportunity,
    ValidationResult,
)
from helis.model_provider import ModelProvider


class RepairBundlePayload(BaseModel):
    files: list[BuildFile] = Field(min_length=1, max_length=12)


SYSTEM_PROMPT = """You are HELIS Bounded Build Repairer.
Repair ONE failed constrained MVP build using the exact verifier/reviewer feedback supplied.
You get one repair attempt. Fix the blocking problems without expanding scope.
Return only files allowed by the original template. Do not output shell commands, dependencies,
remote scripts, credentials, secrets or deployment config.
If approved_commerce is null, do not add payment integrations. If approved_commerce is supplied,
preserve the exact display_price and exact checkout_url using only a normal anchor link. Do not
change the price, currency, billing mode or URL, and do not add alternate external HTTP(S) links,
payment scripts, iframes, widgets or remote form actions.
For python_service_v1, keep the same dependency-free handle(request: dict) -> dict contract and
sandbox restrictions: no network, subprocesses, environment access, application file IO, daemon or
listener. Fix implementation/tests only inside the original three allowed files.
Never invent traction, testimonials, revenue, customers, certifications or validation evidence.
If previous files are supplied, preserve useful parts and change only what is needed.
Return JSON only: {"files":[{"path":"...","content":"..."}]}.
"""


class BuilderRepairer:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def repair(
        self,
        opportunity: Opportunity,
        spec: BuildSpec,
        validation_results: list[ValidationResult],
        failed_run: BuildRun,
        checks: list[BuildCheck],
        review: BuildReview | None,
        previous_bundle: BuildBundle | None,
        commerce: CommerceBuildContext | None = None,
    ) -> BuildBundle:
        definition = get_template(spec.template)
        self.budget.ensure_call_available()
        result = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(
                {
                    "opportunity": opportunity.model_dump(mode="json"),
                    "validation_results": [
                        item.model_dump(mode="json") for item in validation_results
                    ],
                    "build_spec": spec.model_dump(mode="json"),
                    "approved_commerce": (
                        commerce.model_dump(mode="json") if commerce is not None else None
                    ),
                    "failed_run": failed_run.model_dump(mode="json"),
                    "failed_checks": [item.model_dump(mode="json") for item in checks],
                    "review": review.model_dump(mode="json") if review else None,
                    "previous_files": (
                        [item.model_dump(mode="json") for item in previous_bundle.files]
                        if previous_bundle
                        else []
                    ),
                    "allowed_paths": sorted(definition.allowed_paths),
                    "required_paths": sorted(definition.required_paths),
                    "requires_execution": definition.requires_execution,
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = RepairBundlePayload.model_validate_json(result.content)
        invalid = [item.path for item in payload.files if item.path not in definition.allowed_paths]
        if invalid:
            raise BuildGenerationError(f"repair returned forbidden file paths: {invalid}")
        return BuildBundle(files=payload.files)
