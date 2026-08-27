from pathlib import Path

import pytest

from helis.source_registry import HelisConfig, SourceKind, SourceRegistry, SourceSpec


def test_source_spec_requires_kind_specific_fields() -> None:
    with pytest.raises(ValueError):
        SourceSpec(name="broken rss", kind=SourceKind.RSS)
    with pytest.raises(ValueError):
        SourceSpec(name="broken github", kind=SourceKind.GITHUB_ISSUES)


def test_toml_registry_loads_enabled_sources(tmp_path: Path) -> None:
    path = tmp_path / "helis.toml"
    path.write_text(
        """
[[sources]]
name = "Ask HN"
kind = "hacker_news"
feed = "ask"
limit = 12

[[sources]]
name = "Disabled feed"
kind = "rss"
url = "https://example.test/feed"
enabled = false
""".strip(),
        encoding="utf-8",
    )

    registry = SourceRegistry.from_toml(path)

    assert len(registry.config.sources) == 2
    assert registry.config.sources[0].limit == 12
    assert registry.config.sources[1].enabled is False


def test_empty_config_is_valid() -> None:
    assert HelisConfig().sources == []
