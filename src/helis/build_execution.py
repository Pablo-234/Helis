from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


class BuildExecutionError(RuntimeError):
    pass


class BuildExecutionConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class BuildExecutionResult:
    passed: bool
    return_code: int | None
    details: str


class BuildExecutionBackend(Protocol):
    def execute(self, workspace: str | Path) -> BuildExecutionResult: ...


Runner = Callable[..., subprocess.CompletedProcess[str]]


_IMAGE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,200}$")


@dataclass(slots=True)
class DockerBuildExecutionBackend:
    """Runs one fixed Python unittest command in a heavily restricted Docker container."""

    docker_binary: str = "docker"
    image: str = "python:3.12-alpine"
    timeout_seconds: int = 15
    memory_mb: int = 128
    cpus: float = 0.5
    pids_limit: int = 64
    runner: Runner = subprocess.run

    def __post_init__(self) -> None:
        if not _IMAGE_PATTERN.fullmatch(self.image):
            raise BuildExecutionConfigurationError("invalid executable sandbox image")
        if not 1 <= self.timeout_seconds <= 30:
            raise BuildExecutionConfigurationError("sandbox timeout must be between 1 and 30 seconds")
        if not 64 <= self.memory_mb <= 256:
            raise BuildExecutionConfigurationError("sandbox memory must be between 64 and 256 MB")
        if not 0.1 <= self.cpus <= 1.0:
            raise BuildExecutionConfigurationError("sandbox CPUs must be between 0.1 and 1.0")
        if not 16 <= self.pids_limit <= 128:
            raise BuildExecutionConfigurationError("sandbox PID limit must be between 16 and 128")

    @classmethod
    def from_env(cls) -> DockerBuildExecutionBackend | None:
        mode = os.getenv("HELIS_EXECUTABLE_SANDBOX", "").strip().lower()
        if not mode:
            return None
        if mode != "docker":
            raise BuildExecutionConfigurationError(
                "HELIS_EXECUTABLE_SANDBOX must be empty or 'docker'"
            )
        binary = shutil.which("docker")
        if binary is None:
            raise BuildExecutionConfigurationError(
                "HELIS_EXECUTABLE_SANDBOX=docker but docker is not available on PATH"
            )
        return cls(
            docker_binary=binary,
            image=os.getenv("HELIS_EXECUTABLE_SANDBOX_IMAGE", "python:3.12-alpine").strip(),
            timeout_seconds=int(os.getenv("HELIS_EXECUTABLE_SANDBOX_TIMEOUT", "15")),
            memory_mb=int(os.getenv("HELIS_EXECUTABLE_SANDBOX_MEMORY_MB", "128")),
            cpus=float(os.getenv("HELIS_EXECUTABLE_SANDBOX_CPUS", "0.5")),
            pids_limit=int(os.getenv("HELIS_EXECUTABLE_SANDBOX_PIDS", "64")),
        )

    def command_for(self, workspace: str | Path) -> list[str]:
        root = Path(workspace).resolve()
        mount = f"type=bind,src={root},dst=/workspace,readonly"
        memory = f"{self.memory_mb}m"
        return [
            self.docker_binary,
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--memory",
            memory,
            "--memory-swap",
            memory,
            "--cpus",
            str(self.cpus),
            "--pids-limit",
            str(self.pids_limit),
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount",
            mount,
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONHASHSEED=0",
            self.image,
            "python",
            "-I",
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            "/workspace",
            "-p",
            "test_*.py",
        ]

    def execute(self, workspace: str | Path) -> BuildExecutionResult:
        command = self.command_for(workspace)
        try:
            completed = self.runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return BuildExecutionResult(
                passed=False,
                return_code=None,
                details=f"sandbox timed out after {self.timeout_seconds}s: {self._clip(exc.stderr or '')}",
            )
        except OSError as exc:
            raise BuildExecutionError(f"sandbox runtime failed: {type(exc).__name__}: {exc}") from exc

        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        return BuildExecutionResult(
            passed=completed.returncode == 0,
            return_code=completed.returncode,
            details=(
                f"docker unittest exit={completed.returncode}; "
                + (self._clip(output) if output else "no test output")
            ),
        )

    @staticmethod
    def _clip(value: str, limit: int = 1700) -> str:
        normalized = value.replace("\x00", "").strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[:limit] + "…"
