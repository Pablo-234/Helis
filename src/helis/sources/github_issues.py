from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from helis.domain import Observation
from helis.sources.base import stable_observation_id


@dataclass(slots=True)
class GitHubIssuesSource:
    repository: str
    state: str = "open"
    limit: int = 50
    timeout_seconds: int = 30
    token: str = ""

    def scan(self) -> list[Observation]:
        query = urlencode(
            {
                "state": self.state,
                "per_page": min(max(self.limit, 1), 100),
                "sort": "updated",
                "direction": "desc",
            }
        )
        url = f"https://api.github.com/repos/{self.repository}/issues?{query}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "HELIS/0.1 market-research-agent",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self.token or os.getenv("GITHUB_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        return parse_issues(payload, self.repository, self.limit)


def parse_issues(payload: list[dict], repository: str, limit: int = 50) -> list[Observation]:
    observations: list[Observation] = []
    for item in payload:
        if "pull_request" in item:
            continue
        issue_id = str(item.get("id") or item.get("node_id") or item.get("html_url") or "")
        title = str(item.get("title") or "").strip()
        body = str(item.get("body") or "").strip()
        if not issue_id or not (title or body):
            continue
        labels = [label.get("name", "") for label in item.get("labels", []) if isinstance(label, dict)]
        observations.append(
            Observation(
                id=stable_observation_id(f"github:{repository}", issue_id),
                text="\n".join(part for part in [title, body] if part),
                source=str(item.get("html_url") or f"https://github.com/{repository}/issues"),
                metadata={
                    "source_type": "github_issue",
                    "repository": repository,
                    "number": item.get("number"),
                    "labels": labels,
                    "comments": item.get("comments", 0),
                    "updated_at": item.get("updated_at"),
                },
            )
        )
        if len(observations) >= limit:
            break
    return observations
