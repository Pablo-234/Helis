from helis.sources.github_issues import parse_issues
from helis.sources.rss import parse_feed


def test_rss_parser_is_deterministic() -> None:
    payload = """<?xml version='1.0'?><rss><channel><item><title>Manual work hurts</title><link>https://example.test/1</link><guid>abc</guid><description>Users copy data by hand.</description></item></channel></rss>"""
    first = parse_feed(payload, "https://example.test/feed")
    second = parse_feed(payload, "https://example.test/feed")
    assert first[0].id == second[0].id
    assert "copy data" in first[0].text


def test_github_issue_parser_ignores_pull_requests() -> None:
    payload = [
        {
            "id": 1,
            "number": 1,
            "title": "Slow workflow",
            "body": "This repetitive step takes hours.",
            "html_url": "https://github.com/acme/tool/issues/1",
            "labels": [{"name": "pain"}],
            "comments": 4,
        },
        {
            "id": 2,
            "title": "A PR",
            "body": "not evidence",
            "pull_request": {},
        },
    ]
    observations = parse_issues(payload, "acme/tool")
    assert len(observations) == 1
    assert observations[0].metadata["comments"] == 4
