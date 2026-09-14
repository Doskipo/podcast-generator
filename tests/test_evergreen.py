"""Smoke tests for the evergreen (Wikipedia primer) fallback. No network:
the two Wikipedia calls are monkeypatched at evergreen_module._wikipedia_
search / _wikipedia_extract — the same "monkeypatch the boundary" seam as
every other network call in this codebase (fetch.py:_download_feed,
rank.py:_score_batch, ...).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from podcast import evergreen as evergreen_module
from podcast import paths
from podcast.models import Interest


def _interest(topic: str = "calisthenics", description: str | None = None) -> Interest:
    return Interest(topic=topic, weight=1.0, description=description)


def _patch_cache_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "EVERGREEN_CACHE_PATH", tmp_path / "evergreen_cache.json")


def test_fetch_primer_happy_path(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics", "Radio calisthenics"])
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: f"Extract for {title}." * 10)

    article = evergreen_module.fetch_primer(_interest())

    assert article is not None
    assert article.source == "evergreen"
    assert article.interest == "calisthenics"
    assert article.title == "Calisthenics"
    assert "Extract for Calisthenics" in article.text
    assert str(article.url) == "https://en.wikipedia.org/wiki/Calisthenics"


def test_fetch_primer_uses_description_over_topic_for_the_search_query(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    captured = {}

    def fake_search(query):
        captured["query"] = query
        return ["Some Page"]

    monkeypatch.setattr(evergreen_module, "_wikipedia_search", fake_search)
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: "text " * 50)

    evergreen_module.fetch_primer(_interest(description="bodyweight strength training"))

    assert captured["query"] == "bodyweight strength training"


def test_fetch_primer_returns_none_when_search_finds_nothing(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: [])

    assert evergreen_module.fetch_primer(_interest()) is None


def test_fetch_primer_returns_none_when_search_raises(tmp_path, monkeypatch):
    import httpx

    _patch_cache_path(monkeypatch, tmp_path)

    def raising_search(query):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(evergreen_module, "_wikipedia_search", raising_search)

    assert evergreen_module.fetch_primer(_interest()) is None


def test_fetch_primer_returns_none_when_extract_is_empty(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics"])
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: None)

    assert evergreen_module.fetch_primer(_interest()) is None


def test_fetch_primer_avoids_the_page_used_within_the_last_7_days(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    recent = datetime.now(timezone.utc) - timedelta(days=2)
    paths.EVERGREEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.EVERGREEN_CACHE_PATH.write_text(
        json.dumps({"calisthenics": {"page_title": "Calisthenics", "used_at": recent.isoformat()}}), encoding="utf-8"
    )

    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics", "Radio calisthenics"])
    captured_titles = []

    def fake_extract(title):
        captured_titles.append(title)
        return "text " * 50

    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", fake_extract)

    article = evergreen_module.fetch_primer(_interest())

    assert article.title == "Radio calisthenics"  # skipped the recently-used top candidate
    assert captured_titles == ["Radio calisthenics"]


def test_fetch_primer_reuses_the_page_once_the_cache_entry_is_stale(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    stale = datetime.now(timezone.utc) - timedelta(days=8)
    paths.EVERGREEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.EVERGREEN_CACHE_PATH.write_text(
        json.dumps({"calisthenics": {"page_title": "Calisthenics", "used_at": stale.isoformat()}}), encoding="utf-8"
    )

    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics", "Radio calisthenics"])
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: "text " * 50)

    article = evergreen_module.fetch_primer(_interest())

    assert article.title == "Calisthenics"  # fine to reuse — the cache entry is past its 7-day window


def test_fetch_primer_reuses_the_only_candidate_rather_than_give_up(tmp_path, monkeypatch):
    """A repeated primer still beats no primer at all — no_content is the
    worse outcome (see docs/decisions.md, "Evergreen fallback")."""
    _patch_cache_path(monkeypatch, tmp_path)
    recent = datetime.now(timezone.utc) - timedelta(days=1)
    paths.EVERGREEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.EVERGREEN_CACHE_PATH.write_text(
        json.dumps({"calisthenics": {"page_title": "Calisthenics", "used_at": recent.isoformat()}}), encoding="utf-8"
    )

    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics"])  # only one candidate
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: "text " * 50)

    article = evergreen_module.fetch_primer(_interest())

    assert article.title == "Calisthenics"


def test_fetch_primer_writes_the_cache_entry_on_success(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics"])
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: "text " * 50)

    evergreen_module.fetch_primer(_interest())

    cache = json.loads(paths.EVERGREEN_CACHE_PATH.read_text(encoding="utf-8"))
    assert cache["calisthenics"]["page_title"] == "Calisthenics"


def test_fetch_primer_survives_a_corrupt_cache_file(tmp_path, monkeypatch):
    _patch_cache_path(monkeypatch, tmp_path)
    paths.EVERGREEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    paths.EVERGREEN_CACHE_PATH.write_text("not json", encoding="utf-8")

    monkeypatch.setattr(evergreen_module, "_wikipedia_search", lambda query: ["Calisthenics"])
    monkeypatch.setattr(evergreen_module, "_wikipedia_extract", lambda title: "text " * 50)

    article = evergreen_module.fetch_primer(_interest())

    assert article is not None  # degrades to an empty cache rather than crashing
