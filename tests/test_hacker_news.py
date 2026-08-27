from helis.sources.hacker_news import parse_story


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
