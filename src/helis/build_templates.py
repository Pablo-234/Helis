from __future__ import annotations

from dataclasses import dataclass

from helis.domain import BuildTemplate


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    key: BuildTemplate
    description: str
    allowed_paths: frozenset[str]
    required_paths: frozenset[str]
    entrypoint: str
    max_files: int
    max_total_bytes: int


_TEMPLATES = {
    BuildTemplate.STATIC_WEB: TemplateDefinition(
        key=BuildTemplate.STATIC_WEB,
        description=(
            "A no-backend, no-external-active-code offer/landing MVP for testing positioning, "
            "copy and customer understanding."
        ),
        allowed_paths=frozenset({"index.html", "styles.css", "README.md", "copy.md"}),
        required_paths=frozenset({"index.html", "README.md"}),
        entrypoint="index.html",
        max_files=4,
        max_total_bytes=80_000,
    ),
    BuildTemplate.CONCIERGE_OPS: TemplateDefinition(
        key=BuildTemplate.CONCIERGE_OPS,
        description=(
            "A concierge/manual-service MVP consisting of an operating procedure, intake, "
            "qualification and follow-up scripts."
        ),
        allowed_paths=frozenset(
            {
                "README.md",
                "SOP.md",
                "intake.md",
                "scripts/qualification.md",
                "scripts/followup.md",
            }
        ),
        required_paths=frozenset({"README.md", "SOP.md", "intake.md"}),
        entrypoint="README.md",
        max_files=5,
        max_total_bytes=80_000,
    ),
}


def get_template(template: BuildTemplate) -> TemplateDefinition:
    return _TEMPLATES[template]


def template_catalog() -> list[dict[str, object]]:
    return [
        {
            "template": definition.key.value,
            "description": definition.description,
            "allowed_paths": sorted(definition.allowed_paths),
            "required_paths": sorted(definition.required_paths),
        }
        for definition in _TEMPLATES.values()
    ]
