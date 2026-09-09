"""Smoke tests for the fetch stage. No network: feedparser.parse and
trafilatura.fetch_url are monkeypatched onto local fixtures; trafilatura.extract
runs for real against the fixture HTML.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import feedparser

from podcast.models import FetchSettings, Interest, PodcastSettings, Profile
from podcast.stages import fetch as fetch_module

FIXTURES = Path(__file__).parent / "fixtures"


def _profile(**fetch_overrides) -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
        podcast=PodcastSettings(duration_minutes=8, hosts=["Nova", "Max"], tone="curious"),
        fetch=FetchSettings(**fetch_overrides) if fetch_overrides else FetchSettings(),
    )


def _write_feed_fixture(tmp_path: Path) -> Path:
    """Render feed_sample.xml with pubDates relative to now, so the window
    filter test doesn't depend on when the suite runs."""
    now = datetime.now(timezone.utc)
    template = (FIXTURES / "feed_sample.xml").read_text(encoding="utf-8")
    rendered = template.format(
        fresh_1=format_datetime(now - timedelta(hours=2)),
        fresh_2=format_datetime(now - timedelta(hours=5)),
        stale=format_datetime(now - timedelta(hours=72)),
    )
    feed_path = tmp_path / "feed.xml"
    feed_path.write_text(rendered, encoding="utf-8")
    return feed_path


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(fetch_module, "episode_dir", _episode_dir)


def _patch_extraction(monkeypatch) -> None:
    html = (FIXTURES / "article_sample.html").read_text(encoding="utf-8")
    monkeypatch.setattr(fetch_module.trafilatura, "fetch_url", lambda url: html)


def test_fetch_stage_filters_dedupes_and_extracts(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)
    real_parse = feedparser.parse
    monkeypatch.setattr(fetch_module.feedparser, "parse", lambda url: real_parse(str(feed_path)))
    _patch_extraction(monkeypatch)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = fetch_module.fetch_stage(_profile(), episode_id="ep1")

    # stale article dropped, duplicate (same normalized url/title) collapsed
    titles = sorted(a.title for a in output.articles)
    assert titles == ["Fresh Article One", "Fresh Article Two"]

    # persisted, re-parseable, and every article carries a grounding source_id + text
    articles_path = tmp_path / "episodes" / "ep1" / "articles.json"
    assert articles_path.exists()
    for article in output.articles:
        assert len(article.source_id) == 8
        assert len(article.text) >= fetch_module.MIN_EXTRACTED_CHARS


def test_fetch_stage_caps_entries_per_feed(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)
    real_parse = feedparser.parse
    monkeypatch.setattr(fetch_module.feedparser, "parse", lambda url: real_parse(str(feed_path)))
    _patch_extraction(monkeypatch)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = fetch_module.fetch_stage(_profile(max_entries_per_feed=1), episode_id="ep2")

    # only the single newest in-window entry is kept
    assert len(output.articles) == 1
    assert output.articles[0].title == "Fresh Article One"


def test_fetch_stage_skips_failing_feed(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)
    real_parse = feedparser.parse

    def fake_parse(url: str):
        if "bad" in url:
            raise RuntimeError("connection refused")
        return real_parse(str(feed_path))

    monkeypatch.setattr(fetch_module.feedparser, "parse", fake_parse)
    _patch_extraction(monkeypatch)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    profile.feeds.append("https://bad.example.com/feed.xml")

    output = fetch_module.fetch_stage(profile, episode_id="ep3")

    # the good feed's articles still come through despite the bad feed erroring
    assert {a.title for a in output.articles} == {"Fresh Article One", "Fresh Article Two"}
