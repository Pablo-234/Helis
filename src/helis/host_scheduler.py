from __future__ import annotations

import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

WINDOWS_TASK_NAMES = ("HELIS Discovery", "HELIS Scheduler")
SYSTEMD_TIMER_NAMES = ("helis-discovery.timer", "helis-scheduler.timer")

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class HostSchedulerReport:
    platform: str
    scheduler: str
    installed: int
    expected: int
    query_error: str | None = None

    @property
    def complete(self) -> bool:
        return self.installed == self.expected and self.query_error is None

    @property
    def detail(self) -> str:
        summary = f"{self.installed}/{self.expected} {self.scheduler} wake entries installed"
        return f"{summary}; query unavailable: {self.query_error}" if self.query_error else summary


class HostSchedulerInspector:
    """Read-only inspection of the reference host wake schedule."""

    def __init__(
        self,
        *,
        platform_name: str | None = None,
        systemd_user_root: str | Path | None = None,
        runner: CommandRunner = subprocess.run,
    ) -> None:
        self.platform_name = platform_name or platform.system()
        self.systemd_user_root = (
            Path(systemd_user_root).expanduser()
            if systemd_user_root is not None
            else Path.home() / ".config/systemd/user"
        )
        self.runner = runner

    def inspect(self) -> HostSchedulerReport:
        normalized = self.platform_name.strip().lower()
        if normalized == "windows":
            return self._inspect_windows()
        if normalized == "linux":
            return self._inspect_systemd()
        return HostSchedulerReport(
            platform=self.platform_name,
            scheduler=f"unsupported {self.platform_name or 'unknown'} scheduler",
            installed=0,
            expected=len(SYSTEMD_TIMER_NAMES),
        )

    def _inspect_systemd(self) -> HostSchedulerReport:
        installed = sum(
            (self.systemd_user_root / timer_name).is_file()
            for timer_name in SYSTEMD_TIMER_NAMES
        )
        return HostSchedulerReport(
            platform=self.platform_name,
            scheduler="systemd user timer",
            installed=installed,
            expected=len(SYSTEMD_TIMER_NAMES),
        )

    def _inspect_windows(self) -> HostSchedulerReport:
        installed = 0
        query_error: str | None = None
        for task_name in WINDOWS_TASK_NAMES:
            try:
                result = self.runner(
                    ["schtasks.exe", "/Query", "/TN", task_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                query_error = f"{type(exc).__name__}: {exc}"
                break
            installed += result.returncode == 0
        return HostSchedulerReport(
            platform=self.platform_name,
            scheduler="Windows Task Scheduler",
            installed=installed,
            expected=len(WINDOWS_TASK_NAMES),
            query_error=query_error,
        )
