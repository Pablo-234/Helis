from __future__ import annotations

import json

from pydantic import BaseModel, Field

from helis.budget import CycleBudget
from helis.build_templates import get_template
from helis.domain import BuildBundle, BuildFile, BuildSpec, Opportunity, ValidationResult
from helis.model_provider import ModelProvider


class BuildGenerationError(RuntimeError):
    pass


class BuildBundlePayload(BaseModel):
    files: list[BuildFile] = Field(min_length=1, max_length=12)


SYSTEM_PROMPT = """You are HELIS Constrained MVP Builder.
Generate only the text files allowed by the supplied template. Do not output shell commands.
Do not add dependencies, package manifests, executable server code, external scripts, iframes,
tracking pixels, remote form actions, API keys, credentials or secrets. Do not fabricate customer
counts, revenue, testimonials, endorsements, certifications or research results.
For static_web_v1, build a clear honest offer page suitable for local preview.
For concierge_ops_v1, build a practical manual operating kit that can deliver the value before
software exists.
Return JSON only: {"files":[{"path":"...","content":"..."}]}.
"""


class BuilderGenerator:
    def __init__(self, provider: ModelProvider, budget: CycleBudget) -> None:
        self.provider = provider
        self.budget = budget

    def generate(
        self,
        opportunity: Opportunity,
        spec: BuildSpec,
        validation_results: list[ValidationResult],
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
                    "allowed_paths": sorted(definition.allowed_paths),
                    "required_paths": sorted(definition.required_paths),
                },
                ensure_ascii=False,
            ),
        )
        self.budget.record(result)
        payload = BuildBundlePayload.model_validate_json(result.content)
        invalid = [item.path for item in payload.files if item.path not in definition.allowed_paths]
        if invalid:
            raise BuildGenerationError(f"builder returned forbidden file paths: {invalid}")
        return BuildBundle(files=payload.files)
