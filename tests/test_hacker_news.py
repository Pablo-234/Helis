from helis.sources.hacker_news import HackerNewsSource, parse_comment, parse_story


def test_hacker_news_story_becomes_traceable_observation() -> None:
    observation = parse_story(
        {
            "id": 123,
            "type": "story",
            "by": "alice",
            "title": "Ask HN: How do you handle repetitive quoting?",
            "text": "We spend <b>hours</b> copying requests by hand.",
            "score": 42,
            "descendants": 15,
            "time": 1_700_000_000,
        }
    )

    assert observation is not None
    assert "hours copying requests" in observation.text
    assert observation.source == "https://news.ycombinator.com/item?id=123"
    assert observation.metadata["comments"] == 15


def test_hacker_news_deleted_story_is_ignored() -> None:
    assert parse_story({"id": 123, "type": "story", "deleted": True, "title": "gone"}) is None


def test_hacker_news_comment_becomes_contextual_traceable_observation() -> None:
    observation = parse_comment(
        {
            "id": 456,
            "type": "comment",
            "parent": 123,
            "by": "bob",
            "text": "We still do this <i>manually</i> every Friday &amp; lose two hours.",
            "time": 1_700_000_001,
        },
        story_id=123,
        story_title="Ask HN: Repetitive reporting workflows",
    )

    assert observation is not None
    assert observation.text.startswith('Comment on "Ask HN: Repetitive reporting workflows":')
    assert "manually every Friday & lose two hours" in observation.text
    assert observation.source == "https://news.ycombinator.com/item?id=456"
    assert observation.metadata["source_type"] == "hacker_news_comment"
    assert observation.metadata["story_id"] == 123
    assert observation.metadata["parent_id"] == 123


def test_hacker_news_deleted_or_empty_comment_is_ignored() -> None:
    assert (
        parse_comment(
            {"id": 456, "type": "comment", "deleted": True, "text": "gone"},
            story_id=123,
            story_title="A story",
        )
        is None
    )
    assert (
        parse_comment(
            {"id": 457, "type": "comment", "text": "<p></p>"},
            story_id=123,
            story_title="A story",
        )
        is None
    )


def test_hacker_news_scan_adds_bounded_top_level_discussion_signals(monkeypatch) -> None:
    payloads: dict[str, object] = {
        "askstories.json": [1, 2],
        "item/1.json": {
            "id": 1,
            "type": "story",
            "title": "First workflow",
            "kids": [11, 12],
        },
        "item/2.json": {
            "id": 2,
            "type": "story",
            "title": "Second workflow",
            "kids": [21],
        },
        "item/11.json": {"id": 11, "type": "comment", "parent": 1, "text": "First pain"},
        "item/12.json": {"id": 12, "type": "comment", "parent": 1, "text": "Second pain"},
        "item/21.json": {"id": 21, "type": "comment", "parent": 2, "text": "Third pain"},
    }
    requested: list[str] = []

    def fake_get_json(self, url: str) -> object:
        key = url.rsplit("/v0/", maxsplit=1)[1]
        requested.append(key)
        return payloads[key]

    monkeypatch.setattr(HackerNewsSource, "_get_json", fake_get_json)

    observations = HackerNewsSource(
        feed="ask",
        limit=2,
        comments_per_story=2,
        comment_limit=2,
        max_workers=1,
    ).scan()

    assert [item.metadata["source_type"] for item in observations] == [
        "hacker_news",
        "hacker_news",
        "hacker_news_comment",
        "hacker_news_comment",
    ]
    assert [item.metadata["comment_id"] for item in observations[2:]] == [11, 12]
    assert "item/21.json" not in requested


def test_hacker_news_scan_isolates_one_missing_comment(monkeypatch) -> None:
    def fake_get_json(self, url: str) -> object:
        if url.endswith("askstories.json"):
            return [1]
        if url.endswith("item/1.json"):
            return {"id": 1, "type": "story", "title": "Workflow", "kids": [11, 12]}
        if url.endswith("item/11.json"):
            raise TimeoutError("one item timed out")
        return {"id": 12, "type": "comment", "parent": 1, "text": "Visible pain"}

    monkeypatch.setattr(HackerNewsSource, "_get_json", fake_get_json)

    observations = HackerNewsSource(max_workers=1).scan()

    assert len(observations) == 2
    assert observations[0].metadata["story_id"] == 1
    assert observations[1].metadata["comment_id"] == 12
