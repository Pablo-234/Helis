from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import PurePosixPath

from helis.budget import CycleBudget
from helis.build_domain import BuildBundle, BuildRuntime, BuildSpec
from helis.model_provider import ModelProvider


class BuildBundleViolation(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BundleLimits:
    max_files: int = 32
    max_total_bytes: int = 250_000
    max_file_bytes: int = 80_000


_ALLOWED_EXTENSIONS = {
    BuildRuntime.STATIC_WEB: {".html", ".css", ".js", ".json", ".md", ".txt"},
    BuildRuntime.PYTHON_STDLIB: {
        ".py",
        ".html",
        ".css",
        ".js",
        ".json",
        ".md",
        ".txt",
    },
}
_FORBIDDEN_PARTS = {".git", ".github", ".ssh", "__pycache__"}
_FORBIDDEN_NAMES = {".env", "helis-build-manifest.json"}


def validate_bundle(bundle: BuildBundle, spec: BuildSpec, limits: BundleLimits) -> int:
    if bundle.spec_id != spec.id:
        raise BuildBundleViolation("bundle spec_id does not match the build spec")
    if len(bundle.files) > limits.max_files:
        raise BuildBundleViolation("bundle exceeds the file-count limit")

    seen: set[str] = set()
    total = 0
    for file in bundle.files:
        if "\\" in file.path or "\x00" in file.path:
            raise BuildBundleViolation("file path contains forbidden characters")
        path = PurePosixPath(file.path)
        if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
            raise BuildBundleViolation("file path must stay inside the build workspace")
        if any(part in _FORBIDDEN_PARTS for part in path.parts) or path.name in _FORBIDDEN_NAMES:
            raise BuildBundleViolation(f"forbidden build path: {file.path}")
        normalized = path.as_posix()
        if normalized in seen:
            raise BuildBundleViolation(f"duplicate build path: {normalized}")
        seen.add(normalized)
        if path.suffix.lower() not in _ALLOWED_EXTENSIONS[spec.runtime]:
            raise BuildBundleViolation(f"extension not allowed for {spec.runtime}: {file.path}")
        if "\x00" in file.content:
            raise BuildBundleViolation("binary/NUL content is not allowed")
        size = len(file.content.encode("utf-8"))
        if size > limits.max_file_bytes:
            raise BuildBundleViolation(f"file exceeds per-file byte limit: {file.path}")
        total += size

    if total > limits.max_total_bytes:
        raise BuildBundleViolation("bundle exceeds the total byte limit")
    if spec.runtime == BuildRuntime.STATIC_WEB and "index.html" not in seen:
        raise BuildBundleViolation("static_web builds require index.html")
    if spec.runtime == BuildRuntime.PYTHON_STDLIB and not any(
        path.startswith("tests/test_") and path.endswith(".py") for path in seen
    ):
        raise BuildBundleViolation("python_stdlib builds require tests/test_*.py")
    return total


SYSTEM_PROMPT = """You are HELIS bounded MVP code generator.
Generate ONLY the files needed to satisfy the supplied BuildSpec.
Return JSON: {"spec_id":"UUID","files":[{"path":"...","content":"...","role":"..."}]}.
Never output shell commands, Dockerfiles, CI workflows, .env files, secrets, binary data,
absolute paths, parent-directory paths, package manifests requiring downloads, or HELIS core files.
For static_web: use only local HTML/CSS/JS assets; no external URLs or network calls.
For python_stdlib: Python standard library only and include meaningful unittest tests under tests/.
Keep the implementation intentionally small and testable.
"""


class BuildGenerator:
    def __init__(
        self,
        provider: ModelProvider,
        budget: CycleBudget,
        limits: BundleLimits | None = None,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.limits = limits or BundleLimits()

    def generate(self, spec: BuildSpec) -> tuple[BuildBundle, int]:
        self.budget.ensure_call_available()
        response = self.provider.complete(
            system=SYSTEM_PROMPT,
            user=json.dumps(spec.model_dump(mode="json"), ensure_ascii=False),
        )
        self.budget.record(response)
        bundle = BuildBundle.model_validate_json(response.content)
        total = validate_bundle(bundle, spec, self.limits)
        return bundle, total
