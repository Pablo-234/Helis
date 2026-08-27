from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from helis.build_domain import BuildRuntime, SandboxReport, SandboxStatus


class StaticWebVerifier:
    name = "static_web_verifier_v1"

    def verify(self, workspace: Path) -> SandboxReport:
        start = time.monotonic()
        problems: list[str] = []
        index = workspace / "index.html"
        if not index.is_file():
            problems.append("index.html is missing")
        else:
            html = index.read_text(encoding="utf-8", errors="replace").lower()
            if "<html" not in html and "<!doctype" not in html:
                problems.append("index.html does not look like an HTML document")

        network_pattern = re.compile(r"https?://|(?<!:)//[a-z0-9]", re.IGNORECASE)
        network_calls = ("fetch(", "xmlhttprequest", "websocket(", "sendbeacon(")
        for path in workspace.rglob("*"):
            if not path.is_file() or path.name == "helis-build-manifest.json":
                continue
            if path.suffix.lower() not in {".html", ".css", ".js"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lowered = text.lower()
            if network_pattern.search(text) or any(call in lowered for call in network_calls):
                problems.append(f"external network reference/call found in {path.name}")

        elapsed = time.monotonic() - start
        if problems:
            return SandboxReport(
                status=SandboxStatus.FAILED,
                stderr="\n".join(problems),
                duration_seconds=elapsed,
                verifier=self.name,
            )
        return SandboxReport(
            status=SandboxStatus.PASSED,
            stdout="static bundle passed offline verification",
            duration_seconds=elapsed,
            verifier=self.name,
        )


class DockerPythonSandbox:
    name = "docker_python_stdlib_v1"
    _IMAGE_PATTERN = re.compile(r"^[a-zA-Z0-9._:/@-]+$")

    def __init__(self, image: str = "python:3.12-alpine", timeout_seconds: int = 120) -> None:
        if not self._IMAGE_PATTERN.fullmatch(image):
            raise ValueError("invalid Docker image reference")
        self.image = image
        self.timeout_seconds = timeout_seconds

    def verify(self, workspace: Path) -> SandboxReport:
        start = time.monotonic()
        docker = shutil.which("docker")
        if docker is None:
            return SandboxReport(
                status=SandboxStatus.BLOCKED,
                stderr="Docker is not installed or not on PATH; host execution is forbidden",
                duration_seconds=time.monotonic() - start,
                verifier=self.name,
            )

        try:
            inspect = subprocess.run(
                [docker, "image", "inspect", self.image],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return SandboxReport(
                status=SandboxStatus.BLOCKED,
                stderr=f"Docker image inspection unavailable: {exc}",
                duration_seconds=time.monotonic() - start,
                verifier=self.name,
            )
        if inspect.returncode != 0:
            return SandboxReport(
                status=SandboxStatus.BLOCKED,
                stderr=(
                    f"sandbox image {self.image!r} is not present locally; "
                    "HELIS will not auto-pull images during a build"
                ),
                duration_seconds=time.monotonic() - start,
                verifier=self.name,
            )

        command = [
            docker,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "256m",
            "--cpus",
            "1",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--user",
            "65534:65534",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-v",
            f"{workspace.resolve()}:/workspace:ro",
            "-w",
            "/workspace",
            self.image,
            "python",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests",
            "-p",
            "test_*.py",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxReport(
                status=SandboxStatus.FAILED,
                stderr=f"sandbox timeout after {self.timeout_seconds}s: {exc}",
                duration_seconds=time.monotonic() - start,
                verifier=self.name,
            )
        except OSError as exc:
            return SandboxReport(
                status=SandboxStatus.BLOCKED,
                stderr=f"Docker execution unavailable: {exc}",
                duration_seconds=time.monotonic() - start,
                verifier=self.name,
            )
        return SandboxReport(
            status=(SandboxStatus.PASSED if completed.returncode == 0 else SandboxStatus.FAILED),
            exit_code=completed.returncode,
            stdout=completed.stdout[-12_000:],
            stderr=completed.stderr[-12_000:],
            duration_seconds=time.monotonic() - start,
            verifier=self.name,
        )


def verifier_for(runtime: BuildRuntime):
    if runtime == BuildRuntime.STATIC_WEB:
        return StaticWebVerifier()
    if runtime == BuildRuntime.PYTHON_STDLIB:
        return DockerPythonSandbox()
    raise ValueError(f"unsupported build runtime: {runtime}")
