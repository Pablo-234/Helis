from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.request import Request, urlopen

from helis.domain import Observation
from helis.sources.base import stable_observation_id

_BASE_URL = "https://hacker-news.firebaseio.com/v0"
_FEEDS = {
    "ask": "askstories",
    "show": "showstories",
    "new": "newstories",
    "top": "topstories",
    "best": "beststories",
}
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", value))).strip()


def parse_story(item: dict) -> Observation | None:
    if item.get("type") != "story" or item.get("deleted") or item.get("dead"):
        return None
    story_id = item.get("id")
    title = str(item.get("title") or "").strip()
    text = _clean(str(item.get("text") or ""))
    if not story_id or not (title or text):
        return None

    discussion_url = f"https://news.ycombinator.com/item?id={story_id}"
    return Observation(
        id=stable_observation_id("hacker_news", str(story_id)),
        text="\n".join(part for part in [title, text] if part),
        source=discussion_url,
        metadata={
            "source_type": "hacker_news",
            "story_id": story_id,
            "author": item.get("by"),
            "score": item.get("score", 0),
            "comments": item.get("descendants", 0),
            "external_url": item.get("url"),
            "time": item.get("time"),
        },
    )


@dataclass(slots=True)
class HackerNewsSource:
    feed: str = "ask"
    limit: int = 30
    timeout_seconds: int = 20
    max_workers: int = 8

    def scan(self) -> list[Observation]:
        endpoint = _FEEDS.get(self.feed)
        if endpoint is None:
            raise ValueError(f"unsupported Hacker News feed: {self.feed}")

        ids = self._get_json(f"{_BASE_URL}/{endpoint}.json")
        if not isinstance(ids, list):
            raise TypeError("Hacker News feed returned an unexpected payload")
        selected = ids[: max(0, self.limit)]

        with ThreadPoolExecutor(max_workers=max(1, min(self.max_workers, 16))) as executor:
            items = executor.map(self._fetch_item, selected)
        return [observation for item in items if (observation := parse_story(item)) is not None]

    def _fetch_item(self, item_id: int) -> dict:
        payload = self._get_json(f"{_BASE_URL}/item/{item_id}.json")
        return payload if isinstance(payload, dict) else {}

    def _get_json(self, url: str) -> object:
        request = Request(url, headers={"User-Agent": "HELIS/0.1 market-research-agent"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
