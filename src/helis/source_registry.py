from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from helis.domain import Observation
from helis.policy import ActionKind, ActionRequest, AutonomyPolicy
from helis.sources import GitHubIssuesSource, HackerNewsSource, RSSSource
from helis.sources.base import ObservationSource


class SourceKind(StrEnum):
    HACKER_NEWS = "hacker_news"
    RSS = "rss"
    GITHUB_ISSUES = "github_issues"


class SourceSpec(BaseModel):
    name: str = Field(min_length=1)
    kind: SourceKind
    enabled: bool = True
    limit: int = Field(default=30, ge=1, le=500)
    feed: str = "ask"
    url: str = ""
    repository: str = ""
    state: str = "open"

    @model_validator(mode="after")
    def validate_kind_fields(self) -> SourceSpec:
        if self.kind == SourceKind.RSS and not self.url:
            raise ValueError("rss source requires url")
        if self.kind == SourceKind.GITHUB_ISSUES and not self.repository:
            raise ValueError("github_issues source requires repository")
        return self


class HelisConfig(BaseModel):
    sources: list[SourceSpec] = Field(default_factory=list)


@dataclass(slots=True)
class ScanFailure:
    source_name: str
    error: str


@dataclass(slots=True)
class RegistryScanResult:
    observations: list[Observation] = field(default_factory=list)
    failures: list[ScanFailure] = field(default_factory=list)


class SourceRegistry:
    def __init__(
        self,
        config: HelisConfig,
        policy: AutonomyPolicy | None = None,
    ) -> None:
        self.config = config
        self.policy = policy or AutonomyPolicy()

    @classmethod
    def from_toml(
        cls,
        path: str | Path,
        policy: AutonomyPolicy | None = None,
    ) -> SourceRegistry:
        with Path(path).open("rb") as handle:
            config = HelisConfig.model_validate(tomllib.load(handle))
        return cls(config, policy)

    def scan(self) -> RegistryScanResult:
        output = RegistryScanResult()
        deduplicated: dict[object, Observation] = {}
        for spec in self.config.sources:
            if not spec.enabled:
                continue
            decision = self.policy.evaluate(
                ActionRequest(
                    kind=ActionKind.NETWORK_READ,
                    description=f"scan configured market source: {spec.name}",
                )
            )
            if not decision.allowed:
                output.failures.append(ScanFailure(spec.name, f"policy denied: {decision.reason}"))
                continue
            try:
                for observation in self._build(spec).scan():
                    deduplicated[observation.id] = observation
            # Source adapters are an isolation boundary: a third-party adapter must not kill a scan.
            except Exception as exc:  # noqa: BLE001
                output.failures.append(ScanFailure(spec.name, f"{type(exc).__name__}: {exc}"))
        output.observations = list(deduplicated.values())
        return output

    @staticmethod
    def _build(spec: SourceSpec) -> ObservationSource:
        if spec.kind == SourceKind.HACKER_NEWS:
            return HackerNewsSource(feed=spec.feed, limit=spec.limit)
        if spec.kind == SourceKind.RSS:
            return RSSSource(url=spec.url, limit=spec.limit)
        if spec.kind == SourceKind.GITHUB_ISSUES:
            return GitHubIssuesSource(
                repository=spec.repository,
                state=spec.state,
                limit=spec.limit,
            )
        raise ValueError(f"unsupported source kind: {spec.kind}")
