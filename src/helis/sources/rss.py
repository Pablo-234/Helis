from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

from helis.domain import Observation
from helis.sources.base import stable_observation_id

_TAG_RE = re.compile(r"<[^>]+>")


def _text(node: ET.Element | None) -> str:
    return "" if node is None or node.text is None else node.text.strip()


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", value))).strip()


@dataclass(slots=True)
class RSSSource:
    url: str
    limit: int = 50
    timeout_seconds: int = 30

    def scan(self) -> list[Observation]:
        request = Request(self.url, headers={"User-Agent": "HELIS/0.1 market-research-agent"})
        with urlopen(request, timeout=self.timeout_seconds) as response:
            payload = response.read()
        return parse_feed(payload, self.url, self.limit)


def parse_feed(payload: bytes | str, feed_url: str, limit: int = 50) -> list[Observation]:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    root = ET.fromstring(payload)
    observations: list[Observation] = []

    rss_items = root.findall(".//item")
    if rss_items:
        for item in rss_items[:limit]:
            title = _text(item.find("title"))
            link = _text(item.find("link"))
            description = _clean(_text(item.find("description")))
            guid = _text(item.find("guid")) or link or title
            if not guid or not (title or description):
                continue
            observations.append(
                Observation(
                    id=stable_observation_id(feed_url, guid),
                    text="\n".join(part for part in [title, description] if part),
                    source=link or feed_url,
                    metadata={
                        "source_type": "rss",
                        "feed_url": feed_url,
                        "published": _text(item.find("pubDate")),
                    },
                )
            )
        return observations

    entries = root.findall(".//{*}entry")
    for entry in entries[:limit]:
        title = _text(entry.find("{*}title"))
        summary = _clean(_text(entry.find("{*}summary")) or _text(entry.find("{*}content")))
        external_id = _text(entry.find("{*}id"))
        link_node = entry.find("{*}link")
        link = "" if link_node is None else link_node.attrib.get("href", "")
        external_id = external_id or link or title
        if not external_id or not (title or summary):
            continue
        observations.append(
            Observation(
                id=stable_observation_id(feed_url, external_id),
                text="\n".join(part for part in [title, summary] if part),
                source=link or feed_url,
                metadata={
                    "source_type": "atom",
                    "feed_url": feed_url,
                    "published": _text(entry.find("{*}published"))
                    or _text(entry.find("{*}updated")),
                },
            )
        )
    return observations
