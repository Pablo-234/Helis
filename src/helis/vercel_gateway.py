from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar
from urllib.parse import urlsplit

from helis.domain import BuildBundle, PreviewManifest
from helis.preview_domain import PreviewPublishRun
from helis.preview_gateway import PreviewGatewayAck

_URL_RE = re.compile(r"https://[^\s\]\[()<>\"']+")


class VercelGatewayConfigurationError(ValueError):
    pass


def _safe_bundle_path(path: str) -> bool:
    if "\\" in path:
        return False
    parsed = PurePosixPath(path)
    return bool(path) and not parsed.is_absolute() and ".." not in parsed.parts and "." not in parsed.parts


def _deployment_url(output: str) -> str | None:
    matches = _URL_RE.findall(output)
    for value in reversed(matches):
        candidate = value.rstrip(".,;:")
        try:
            parsed = urlsplit(candidate)
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        if parsed.scheme == "https" and host.endswith((".vercel.app", ".vercel.rocks")):
            return candidate
    return None


@dataclass(slots=True)
class VercelCliPreviewGateway:
    """Publish the exact reviewed bundle through a fixed Vercel CLI invocation."""

    name: ClassVar[str] = "vercel_cli_preview_v1"
    token: str
    org_id: str
    project_id: str
    cli: str = "vercel"
    timeout_seconds: int = 180

    def __post_init__(self) -> None:
        if not self.token.strip() or not self.org_id.strip() or not self.project_id.strip():
            raise VercelGatewayConfigurationError("Vercel token, org id and project id are required")
        if not self.cli.strip():
            raise VercelGatewayConfigurationError("Vercel CLI executable is empty")

    @classmethod
    def from_env(cls) -> VercelCliPreviewGateway | None:
        token = os.getenv("HELIS_VERCEL_TOKEN", "").strip() or os.getenv("VERCEL_TOKEN", "").strip()
        org_id = os.getenv("HELIS_VERCEL_ORG_ID", "").strip() or os.getenv("VERCEL_ORG_ID", "").strip()
        project_id = (
            os.getenv("HELIS_VERCEL_PROJECT_ID", "").strip()
            or os.getenv("VERCEL_PROJECT_ID", "").strip()
        )
        if not token or not org_id or not project_id:
            return None
        return cls(
            token=token,
            org_id=org_id,
            project_id=project_id,
            cli=os.getenv("HELIS_VERCEL_CLI", "vercel").strip() or "vercel",
            timeout_seconds=int(os.getenv("HELIS_VERCEL_TIMEOUT", "180")),
        )

    @property
    def safe_destination(self) -> str:
        return "https://vercel.com"

    def execute(
        self,
        run: PreviewPublishRun,
        preview: PreviewManifest,
        bundle: BuildBundle,
    ) -> PreviewGatewayAck:
        executable = shutil.which(self.cli)
        if executable is None:
            raise RuntimeError(
                f"Vercel CLI '{self.cli}' is not installed or not available on PATH"
            )
        with tempfile.TemporaryDirectory(prefix="helis-vercel-") as temp_dir:
            root = Path(temp_dir).resolve()
            for item in bundle.files:
                if not _safe_bundle_path(item.path):
                    raise RuntimeError(
                        f"unsafe reviewed bundle path at publication boundary: {item.path}"
                    )
                target = (root / item.path).resolve()
                try:
                    target.relative_to(root)
                except ValueError as exc:
                    raise RuntimeError("reviewed bundle escaped Vercel staging root") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item.content, encoding="utf-8")
            metadata_dir = root / ".vercel"
            metadata_dir.mkdir(parents=True, exist_ok=True)
            (metadata_dir / "project.json").write_text(
                json.dumps({"orgId": self.org_id, "projectId": self.project_id}),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["VERCEL_ORG_ID"] = self.org_id
            env["VERCEL_PROJECT_ID"] = self.project_id
            command = [
                executable,
                "deploy",
                "--yes",
                "--target=preview",
                "--token",
                self.token,
            ]
            completed = subprocess.run(
                command,
                cwd=root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            if completed.returncode != 0:
                safe_output = output.replace(self.token, "[REDACTED]")[-1600:]
                raise RuntimeError(f"Vercel preview deployment failed: {safe_output.strip()}")
            url = _deployment_url(output)
            if url is None:
                raise RuntimeError("Vercel deployment succeeded but no public deployment URL was found")
            host = urlsplit(url).hostname or str(run.id)
            return PreviewGatewayAck(
                accepted=True,
                dispatch_id=f"vercel:{host}",
                preview_url=url,
                metadata={
                    "provider": "vercel",
                    "target": "preview",
                    "project_id": self.project_id,
                    "artifact_hash": preview.artifact_hash,
                },
            )
