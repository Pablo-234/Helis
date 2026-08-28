from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath

from helis.build_templates import get_template
from helis.domain import (
    BuildBundle,
    BuildCheck,
    BuildFile,
    BuildRun,
    BuildSpec,
    BuildTemplate,
)

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{8,}"),
]


class UnsafeBuildArtifact(RuntimeError):
    pass


def _safe_path(path: str) -> bool:
    if "\\" in path:
        return False
    parsed = PurePosixPath(path)
    return bool(path) and not parsed.is_absolute() and ".." not in parsed.parts and "." not in parsed.parts


def _check(name: str, passed: bool, details: str) -> BuildCheck:
    return BuildCheck(name=name, passed=passed, details=details)


class BuildVerifier:
    def verify(self, spec: BuildSpec, bundle: BuildBundle) -> list[BuildCheck]:
        definition = get_template(spec.template)
        paths = [item.path for item in bundle.files]
        total_bytes = sum(len(item.content.encode("utf-8")) for item in bundle.files)
        text = "\n".join(item.content for item in bundle.files)

        checks = [
            _check(
                "file_count",
                0 < len(bundle.files) <= spec.max_files,
                f"files={len(bundle.files)} cap={spec.max_files}",
            ),
            _check(
                "total_bytes",
                total_bytes <= spec.max_total_bytes,
                f"bytes={total_bytes} cap={spec.max_total_bytes}",
            ),
            _check(
                "safe_paths",
                all(_safe_path(path) for path in paths),
                "all paths must stay inside the venture workspace",
            ),
            _check(
                "allowed_paths",
                set(paths) <= definition.allowed_paths,
                f"allowed={sorted(definition.allowed_paths)}",
            ),
            _check(
                "required_files",
                definition.required_paths <= set(paths),
                f"required={sorted(definition.required_paths)}",
            ),
            _check(
                "unique_paths",
                len(paths) == len(set(paths)),
                "duplicate file paths are forbidden",
            ),
            _check(
                "secret_scan",
                not any(pattern.search(text) for pattern in _SECRET_PATTERNS),
                "generated artifacts must not contain credential-like material",
            ),
        ]

        if spec.template == BuildTemplate.STATIC_WEB:
            index = next((item.content.lower() for item in bundle.files if item.path == "index.html"), "")
            active_external = bool(
                re.search(r"<(script|iframe)\b", index)
                or re.search(r"\b(action|src)\s*=\s*['\"]https?://", index)
            )
            checks.extend(
                [
                    _check(
                        "html_document",
                        "<html" in index and "<body" in index,
                        "index.html must contain an HTML document",
                    ),
                    _check(
                        "no_active_external_content",
                        not active_external,
                        "scripts, iframes and remote active form/assets are forbidden",
                    ),
                ]
            )

        if spec.template == BuildTemplate.CONCIERGE_OPS:
            by_path = {item.path: item.content for item in bundle.files}
            substantive = all(
                len(by_path.get(path, "").strip()) >= 80 for path in definition.required_paths
            )
            checks.append(
                _check(
                    "substantive_ops_docs",
                    substantive,
                    "required operating documents must contain actionable content",
                )
            )
        return checks


class BuildSandbox:
    def __init__(self, root: str | Path = ".helis/workspaces") -> None:
        self.root = Path(root).resolve()

    def workspace_for(self, run: BuildRun) -> Path:
        return self.root / str(run.opportunity_id) / str(run.id)

    def write(self, run: BuildRun, bundle: BuildBundle) -> Path:
        workspace = self.workspace_for(run)
        if workspace.exists() and workspace.is_symlink():
            raise UnsafeBuildArtifact("workspace may not be a symlink")
        workspace.mkdir(parents=True, exist_ok=True)
        root = workspace.resolve()
        for item in bundle.files:
            if not _safe_path(item.path):
                raise UnsafeBuildArtifact(f"unsafe generated path: {item.path}")
            destination = (root / item.path).resolve()
            if root != destination and root not in destination.parents:
                raise UnsafeBuildArtifact(f"path escaped workspace: {item.path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and destination.is_symlink():
                raise UnsafeBuildArtifact(f"refusing to overwrite symlink: {item.path}")
            destination.write_text(item.content, encoding="utf-8")
        return root

    def read(self, run: BuildRun) -> BuildBundle:
        if not run.workspace:
            raise UnsafeBuildArtifact("build run has no workspace")
        root = Path(run.workspace).resolve()
        if self.root != root and self.root not in root.parents:
            raise UnsafeBuildArtifact("stored workspace is outside configured sandbox")
        files: list[BuildFile] = []
        for path in run.file_paths:
            if not _safe_path(path):
                raise UnsafeBuildArtifact(f"unsafe stored path: {path}")
            source = (root / path).resolve()
            if root != source and root not in source.parents:
                raise UnsafeBuildArtifact(f"stored path escaped workspace: {path}")
            files.append(BuildFile(path=path, content=source.read_text(encoding="utf-8")))
        return BuildBundle(files=files)


def bundle_hash(bundle: BuildBundle) -> str:
    digest = hashlib.sha256()
    for item in sorted(bundle.files, key=lambda file: file.path):
        digest.update(item.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
