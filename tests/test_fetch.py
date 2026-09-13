"""Smoke tests for the fetch stage. No network: fetch_module._download_feed is
monkeypatched onto local fixtures; feedparser.parse runs for real against the
fixture content. Fetch no longer does full-text extraction (that moved to the
rank stage — see tests/test_rank.py), so there's nothing to patch for it here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

from podcast.models import (
    DEFAULT_CURATED_WINDOW_HOURS,
    DEFAULT_SEARCH_WINDOW_HOURS,
    FetchSettings,
    Host,
    Interest,
    Listener,
    PodcastSettings,
    Profile,
    Style,
)
from podcast.stages import fetch as fetch_module

FIXTURES = Path(__file__).parent / "fixtures"


def _podcast_settings() -> PodcastSettings:
    return PodcastSettings(
        name="Test Podcast",
        duration_minutes=8,
        listener=Listener(name="Eudald"),
        hosts=[
            Host(name="Nova", voice_id="voice-nova", persona="Nova is curious and precise."),
            Host(name="Max", voice_id="voice-max", persona="Max is curious and precise."),
        ],
        style=Style(humour=2, depth=2, tangents=True, banter=True),
        tone="curious",
    )


def _profile(**fetch_overrides) -> Profile:
    return Profile(
        name="Test",
        interests=[
            Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"]),
        ],
        podcast=_podcast_settings(),
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


def test_fetch_stage_filters_and_dedupes(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)
    monkeypatch.setattr(fetch_module, "_download_feed", lambda url: feed_path.read_text(encoding="utf-8"))
    _patch_episode_dir(monkeypatch, tmp_path)

    output = fetch_module.fetch_stage(_profile(), episode_id="ep1")

    # stale article dropped, duplicate (same normalized url/title) collapsed
    titles = sorted(a.title for a in output.articles)
    assert titles == ["Fresh Article One", "Fresh Article Two"]

    # one curated feed, no top-level profile.feeds — feeds_count counts the
    # feed actually queried, not len(profile.feeds) (which would be 0 here)
    assert output.feeds_count == 1

    # persisted, re-parseable, every article carries a grounding source_id, is
    # tagged with the interest it was found for, and has no text yet — full
    # text extraction is the rank stage's job now, only for what it selects.
    articles_path = tmp_path / "episodes" / "ep1" / "articles.json"
    assert articles_path.exists()
    for article in output.articles:
        assert len(article.source_id) == 8
        assert article.text is None
        assert article.interest == "testing"


def test_fetch_stage_caps_entries_per_feed(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)
    monkeypatch.setattr(fetch_module, "_download_feed", lambda url: feed_path.read_text(encoding="utf-8"))
    _patch_episode_dir(monkeypatch, tmp_path)

    output = fetch_module.fetch_stage(_profile(max_entries_per_feed=1), episode_id="ep2")

    # only the single newest in-window entry is kept
    assert len(output.articles) == 1
    assert output.articles[0].title == "Fresh Article One"


def test_fetch_stage_skips_failing_feed(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)

    def fake_download(url: str) -> str:
        if "bad" in url:
            raise RuntimeError("connection refused")
        return feed_path.read_text(encoding="utf-8")

    monkeypatch.setattr(fetch_module, "_download_feed", fake_download)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    profile.feeds.append("https://bad.example.com/feed.xml")

    output = fetch_module.fetch_stage(profile, episode_id="ep3")

    # the good feed's articles still come through despite the bad feed erroring
    assert {a.title for a in output.articles} == {"Fresh Article One", "Fresh Article Two"}

    # both feeds were queried (one failed, but it was still attempted) — this
    # is the case that used to print "from 0 feeds": only 1 of these 2 is a
    # top-level profile.feeds entry, the other comes from the interest
    assert output.feeds_count == 2
    assert len(profile.feeds) == 1  # the old (wrong) count this replaces

    # the bad feed is a top-level profile.feeds extra, not tied to any interest
    by_title = {a.title: a.interest for a in output.articles}
    assert by_title["Fresh Article One"] == "testing"


def test_fetch_stage_builds_search_feed_for_uncurated_interest(tmp_path, monkeypatch):
    feed_path = _write_feed_fixture(tmp_path)

    # A distinct feed (different titles/links than feed_sample.xml) standing
    # in for what a real Bing News search would return, so its article isn't
    # deduped away against the curated feed's identical fixture content.
    search_feed_xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Search Result Alpha</title>
  <link>https://news.example.com/articles/search-alpha</link>
  <pubDate>{pub_date}</pubDate>
  <description>Found via search.</description>
</item>
</channel></rss>""".format(pub_date=format_datetime(datetime.now(timezone.utc) - timedelta(hours=1)))

    requested_urls: list[str] = []

    def fake_download(url: str) -> str:
        requested_urls.append(url)
        return search_feed_xml if "bing.com" in url else feed_path.read_text(encoding="utf-8")

    monkeypatch.setattr(fetch_module, "_download_feed", fake_download)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = Profile(
        name="Test",
        interests=[
            Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"]),
            Interest(topic="space exploration", weight=1.0),
        ],
        podcast=_podcast_settings(),
    )

    output = fetch_module.fetch_stage(profile, episode_id="ep4")

    # the uncurated interest got a generated Bing News search feed for its topic
    assert any("bing.com" in url and "space+exploration" in url for url in requested_urls)

    by_title = {a.title: a.source for a in output.articles}
    assert by_title["Search Result Alpha"] == fetch_module.SOURCE_SEARCH
    assert by_title["Fresh Article One"] == fetch_module.SOURCE_CURATED

    # each article is tagged with the interest that discovered it
    by_title_interest = {a.title: a.interest for a in output.articles}
    assert by_title_interest["Search Result Alpha"] == "space exploration"
    assert by_title_interest["Fresh Article One"] == "testing"


def test_normalize_url_keeps_redirect_wrapper_query_but_strips_tracking():
    # A redirect wrapper (e.g. Bing's apiclick.aspx) encodes the only thing
    # that makes the link distinct entirely in its query string — collapsing
    # the whole query would falsely dedupe every wrapped link together.
    wrapped_one = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3a%2f%2fa.example.com%2fone"
    wrapped_two = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3a%2f%2fb.example.com%2ftwo"
    assert fetch_module._normalize_url(wrapped_one) != fetch_module._normalize_url(wrapped_two)

    # Known tracking params on an otherwise-identical URL still collapse together.
    plain = "https://example.com/articles/fresh-one"
    tracked = "https://example.com/articles/fresh-one?utm_source=newsletter"
    assert fetch_module._normalize_url(plain) == fetch_module._normalize_url(tracked)


def test_interest_effective_window_hours_defaults():
    curated = Interest(topic="x", weight=1.0, feeds=["https://example.com/feed.xml"])
    search = Interest(topic="y", weight=1.0)
    overridden = Interest(topic="z", weight=1.0, window_hours=24)

    assert curated.effective_window_hours == DEFAULT_CURATED_WINDOW_HOURS
    assert search.effective_window_hours == DEFAULT_SEARCH_WINDOW_HOURS
    assert overridden.effective_window_hours == 24


def test_fetch_stage_uses_multiple_queries_and_dedupes(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)

    def item(title: str, link: str, hours_ago: int) -> str:
        pub = format_datetime(now - timedelta(hours=hours_ago))
        return f"<item><title>{title}</title><link>{link}</link><pubDate>{pub}</pubDate><description>d</description></item>"

    # Two feeds share "Shared Story" (same link) — dedupe should collapse it,
    # keeping the two feeds' unique stories.
    feed_a = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
{item("Comp Story", "https://news.example.com/comp", 1)}
{item("Shared Story", "https://news.example.com/shared", 1)}
</channel></rss>"""
    feed_b = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
{item("Shared Story", "https://news.example.com/shared", 2)}
{item("Record Story", "https://news.example.com/record", 1)}
</channel></rss>"""

    requested_urls: list[str] = []

    def fake_download(url: str) -> str:
        requested_urls.append(url)
        if "calisthenics+competition" in url:
            return feed_a
        if "calisthenics+world+record" in url:
            return feed_b
        raise AssertionError(f"unexpected feed url: {url}")

    monkeypatch.setattr(fetch_module, "_download_feed", fake_download)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = Profile(
        name="Test",
        interests=[
            Interest(
                topic="calisthenics",
                weight=1.0,
                queries=["calisthenics competition", "calisthenics world record"],
            ),
        ],
        podcast=_podcast_settings(),
    )

    output = fetch_module.fetch_stage(profile, episode_id="ep5")

    assert len(requested_urls) == 2  # one Bing feed fetched per query
    titles = sorted(a.title for a in output.articles)
    assert titles == ["Comp Story", "Record Story", "Shared Story"]
    assert all(a.source == fetch_module.SOURCE_SEARCH for a in output.articles)


def test_ensure_interest_queries_generates_and_caches(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    profile = Profile(
        name="Test",
        interests=[
            Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"]),
            Interest(topic="calisthenics", weight=1.0),
        ],
        podcast=_podcast_settings(),
    )
    profile.to_yaml(profile_path)

    calls: list[str] = []

    def fake_generate(client, model, topic):
        calls.append(topic)
        return [f"{topic} q1", f"{topic} q2", f"{topic} q3"]

    monkeypatch.setattr(fetch_module, "_generate_queries", fake_generate)

    changed = fetch_module.ensure_interest_queries(profile, profile_path, client=object())
    assert changed is True
    assert calls == ["calisthenics"]  # curated interest never needs generation
    assert profile.interests[1].queries == ["calisthenics q1", "calisthenics q2", "calisthenics q3"]

    # cached to disk: reloading sees the queries without another LLM call
    reloaded = Profile.from_yaml(profile_path)
    assert reloaded.interests[1].queries == ["calisthenics q1", "calisthenics q2", "calisthenics q3"]

    calls.clear()
    changed_again = fetch_module.ensure_interest_queries(reloaded, profile_path, client=object())
    assert changed_again is False
    assert calls == []  # already cached — one call per profile, not per run


def test_ensure_interest_queries_skips_when_nothing_to_generate(tmp_path, monkeypatch):
    # No OPENAI_API_KEY needed at all when every interest is either curated or
    # already has cached queries — proves the client is never constructed.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    profile_path = tmp_path / "profile.yaml"
    profile = Profile(
        name="Test",
        interests=[
            Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"]),
            Interest(topic="already cached", weight=1.0, queries=["already cached news"]),
        ],
        podcast=_podcast_settings(),
    )

    changed = fetch_module.ensure_interest_queries(profile, profile_path)

    assert changed is False
    assert not profile_path.exists()  # nothing changed, so nothing was written


def test_suggest_interest_returns_description_and_queries(monkeypatch):
    from podcast.models import InterestSuggestion

    captured = {}

    def fake_generate(client, model, topic, description):
        captured["topic"] = topic
        captured["description"] = description
        captured["model"] = model
        return InterestSuggestion(description="a precise one-liner", queries=["q1", "q2", "q3"])

    monkeypatch.setattr(fetch_module, "_generate_suggestion", fake_generate)

    result = fetch_module.suggest_interest("calisthenics", "sport stuff", "gpt-4o-mini", client=object())

    assert result.description == "a precise one-liner"
    assert result.queries == ["q1", "q2", "q3"]
    assert captured == {"topic": "calisthenics", "description": "sport stuff", "model": "gpt-4o-mini"}


def test_suggest_interest_constructs_client_when_none_given(monkeypatch):
    from podcast.models import InterestSuggestion

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        fetch_module,
        "_generate_suggestion",
        lambda client, model, topic, description: InterestSuggestion(description="d", queries=["a"]),
    )

    result = fetch_module.suggest_interest("topic", None, "gpt-4o-mini")

    assert result.description == "d"
