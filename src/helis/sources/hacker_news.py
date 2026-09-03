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


def parse_comment(
    item: dict,
    *,
    story_id: int,
    story_title: str,
) -> Observation | None:
    if item.get("type") != "comment" or item.get("deleted") or item.get("dead"):
        return None
    comment_id = item.get("id")
    text = _clean(str(item.get("text") or ""))
    if not comment_id or not text:
        return None

    discussion_url = f"https://news.ycombinator.com/item?id={comment_id}"
    context = f'Comment on "{story_title}":' if story_title else "Hacker News comment:"
    return Observation(
        id=stable_observation_id("hacker_news_comment", str(comment_id)),
        text=f"{context}\n{text}",
        source=discussion_url,
        metadata={
            "source_type": "hacker_news_comment",
            "comment_id": comment_id,
            "parent_id": item.get("parent"),
            "story_id": story_id,
            "story_title": story_title,
            "author": item.get("by"),
            "time": item.get("time"),
        },
    )


@dataclass(slots=True)
class HackerNewsSource:
    feed: str = "ask"
    limit: int = 30
    comments_per_story: int = 2
    comment_limit: int = 40
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
            items = list(executor.map(self._safe_fetch_item, selected))
            stories = [
                observation
                for item in items
                if (observation := parse_story(item)) is not None
            ]

            comment_contexts = self._comment_contexts(items)
            comment_items = list(
                executor.map(
                    self._safe_fetch_item,
                    [comment_id for comment_id, _, _ in comment_contexts],
                )
            )

        comments = [
            observation
            for item, (_, story_id, story_title) in zip(
                comment_items,
                comment_contexts,
                strict=True,
            )
            if (
                observation := parse_comment(
                    item,
                    story_id=story_id,
                    story_title=story_title,
                )
            )
            is not None
        ]
        return stories + comments

    def _comment_contexts(self, items: list[dict]) -> list[tuple[int, int, str]]:
        per_story = max(0, self.comments_per_story)
        total_limit = max(0, self.comment_limit)
        if per_story == 0 or total_limit == 0:
            return []

        contexts: list[tuple[int, int, str]] = []
        seen: set[int] = set()
        for item in items:
            story_id = item.get("id")
            if (
                item.get("type") != "story"
                or item.get("deleted")
                or item.get("dead")
                or not isinstance(story_id, int)
            ):
                continue
            story_title = str(item.get("title") or "").strip()
            kids = item.get("kids")
            if not isinstance(kids, list):
                continue
            for comment_id in kids[:per_story]:
                if not isinstance(comment_id, int) or comment_id in seen:
                    continue
                contexts.append((comment_id, story_id, story_title))
                seen.add(comment_id)
                if len(contexts) >= total_limit:
                    return contexts
        return contexts

    def _fetch_item(self, item_id: int) -> dict:
        payload = self._get_json(f"{_BASE_URL}/item/{item_id}.json")
        return payload if isinstance(payload, dict) else {}

    def _safe_fetch_item(self, item_id: int) -> dict:
        try:
            return self._fetch_item(item_id)
        except Exception:  # noqa: BLE001 - one missing HN item must not discard the feed
            return {}

    def _get_json(self, url: str) -> object:
        request = Request(url, headers={"User-Agent": "HELIS/0.1 market-research-agent"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
