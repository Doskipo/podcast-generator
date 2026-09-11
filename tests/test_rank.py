"""Smoke tests for the rank stage. No network: the OpenAI scoring call is
monkeypatched at rank_module._score_batch, and the httpx redirect-resolution
call is monkeypatched at rank_module._resolve_and_download — the real
trafilatura.extract still runs against fixture/fake html, same as fetch.py's
tests run real feedparser against fixture feed content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Article,
    ArticleScore,
    Episode,
    FetchOutput,
    Interest,
    PodcastSettings,
    Profile,
    RankOutput,
    ScoreBatch,
)
from podcast.stages import rank as rank_module

FIXTURES = Path(__file__).parent / "fixtures"
GOOD_HTML = (FIXTURES / "article_sample.html").read_text(encoding="utf-8")


def _profile(interests: list[Interest], duration_minutes: int = 8) -> Profile:
    return Profile(
        name="Test",
        interests=interests,
        podcast=PodcastSettings(duration_minutes=duration_minutes, hosts=["Nova", "Max"], tone="curious"),
    )


def _article(source_id: str, interest: str | None, title: str | None = None) -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=title or f"Article {source_id}",
        feed_url="https://example.com/feed.xml",
        published_at=now,
        fetched_at=now,
        summary="a summary",
        interest=interest,
    )


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _fetch_output(episode_id: str, articles: list[Article]) -> FetchOutput:
    return FetchOutput(episode_id=episode_id, fetched_at=datetime.now(timezone.utc), articles=articles)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(rank_module, "episode_dir", _episode_dir)


def _patch_resolve_succeeds(monkeypatch) -> None:
    """Every URL resolves to itself with fixture html that extracts cleanly."""
    monkeypatch.setattr(rank_module, "_resolve_and_download", lambda url: (url, GOOD_HTML))


def _patch_resolve_fails(monkeypatch) -> None:
    """Every URL fails to resolve/download outright (network error)."""
    monkeypatch.setattr(rank_module, "_resolve_and_download", lambda url: (None, None))


def _scorer(scores_by_id: dict[str, float]) -> callable:
    """A fake _score_batch that scores each article from a fixed mapping,
    defaulting to 0.5 for anything not listed, and correctly echoes the
    interest label it was given (so these tests aren't exercising the
    echo-mismatch path incidentally)."""

    def fake_score_batch(client, model, interest_topic, description, batch):
        label = interest_topic or "general"
        return ScoreBatch(
            scores=[
                ArticleScore(
                    source_id=a.source_id,
                    interest=label,
                    score=scores_by_id.get(a.source_id, 0.5),
                    reason="fixture",
                )
                for a in batch
            ]
        )

    return fake_score_batch


def test_rank_stage_selects_per_interest_proportional_to_weight(tmp_path, monkeypatch):
    # duration_minutes=6 -> total_budget = round(6/1.5) = 4. Weights 3:1 ->
    # heavy gets 3, light gets 1 (largest-remainder allocation).
    profile = _profile(
        interests=[
            Interest(topic="heavy", weight=0.75, feeds=["https://example.com/heavy.xml"]),
            Interest(topic="light", weight=0.25, feeds=["https://example.com/light.xml"]),
        ],
        duration_minutes=6,
    )
    episode = _episode(profile)

    heavy_articles = [_article(f"heavy{i}", "heavy") for i in range(5)]
    light_articles = [_article(f"light{i}", "light") for i in range(5)]
    fetch_output = _fetch_output(episode.episode_id, heavy_articles + light_articles)

    scores = {f"heavy{i}": 0.9 - i * 0.01 for i in range(5)}
    scores.update({f"light{i}": 0.8 - i * 0.01 for i in range(5)})
    monkeypatch.setattr(rank_module, "_score_batch", _scorer(scores))
    _patch_resolve_succeeds(monkeypatch)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    assert output.total_budget == 4
    assert output.backfilled == 0  # nothing failed, so nothing needed backfilling
    selected_by_interest = {"heavy": 0, "light": 0}
    for article in output.selected:
        selected_by_interest[article.interest] += 1
    assert selected_by_interest == {"heavy": 3, "light": 1}

    # each interest's selection is its own highest-scored candidates
    selected_heavy_ids = {a.source_id for a in output.selected if a.interest == "heavy"}
    assert selected_heavy_ids == {"heavy0", "heavy1", "heavy2"}


def test_rank_stage_orders_selection_globally_by_score_times_weight(tmp_path, monkeypatch):
    profile = _profile(
        interests=[
            Interest(topic="a", weight=0.9, feeds=["https://example.com/a.xml"]),
            Interest(topic="b", weight=0.5, feeds=["https://example.com/b.xml"]),
        ],
        duration_minutes=3,  # total_budget = 2, one per interest (each has one candidate)
    )
    episode = _episode(profile)

    articles = [_article("a1", "a"), _article("b1", "b")]
    fetch_output = _fetch_output(episode.episode_id, articles)

    # b1 scores higher on its own, but a's higher weight should still win the
    # global ordering: 0.6*0.9 = 0.54 > 0.9*0.5 = 0.45
    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"a1": 0.6, "b1": 0.9}))
    _patch_resolve_succeeds(monkeypatch)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    assert [a.source_id for a in output.selected] == ["a1", "b1"]


def test_rank_stage_extracts_text_only_for_selected(tmp_path, monkeypatch):
    profile = _profile(
        interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])],
        duration_minutes=1,  # total_budget = round(1/1.5) = 1
    )
    episode = _episode(profile)

    articles = [_article("high", "only"), _article("low", "only")]
    fetch_output = _fetch_output(episode.episode_id, articles)

    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"high": 0.9, "low": 0.1}))
    _patch_resolve_succeeds(monkeypatch)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    assert [a.source_id for a in output.selected] == ["high"]
    assert output.selected[0].text is not None

    scored_by_id = {ra.article.source_id: ra for ra in output.scored}
    assert scored_by_id["high"].selected is True
    assert scored_by_id["low"].selected is False
    assert scored_by_id["low"].article.text is None  # never extracted — wasn't a candidate

    # persisted and round-trips
    ranked_path = tmp_path / "episodes" / episode.episode_id / "ranked.json"
    assert ranked_path.exists()
    reparsed = RankOutput.model_validate_json(ranked_path.read_text(encoding="utf-8"))
    assert reparsed.total_budget == 1


def test_rank_stage_drops_when_no_candidate_left_to_backfill(tmp_path, monkeypatch):
    profile = _profile(
        interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])],
        duration_minutes=1,  # total_budget = round(1/1.5) = 1
    )
    episode = _episode(profile)

    fetch_output = _fetch_output(episode.episode_id, [_article("a1", "only")])

    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"a1": 0.9}))
    _patch_resolve_fails(monkeypatch)  # resolution fails for everything
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    # picked as a candidate, but resolution failing drops it — no backfill
    # possible since there's no other candidate for this interest
    assert output.selected == []
    assert output.scored[0].selected is False
    assert output.backfilled == 0  # nothing was actually backfilled, just dropped


def test_rank_stage_backfills_after_extraction_failure(tmp_path, monkeypatch):
    profile = _profile(
        interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])],
        duration_minutes=1,  # total_budget = 1
    )
    episode = _episode(profile)
    fetch_output = _fetch_output(episode.episode_id, [_article("best", "only"), _article("second", "only")])

    def fake_resolve(url: str):
        if "best" in url:
            return url, "<html><body>too short to pass MIN_EXTRACTED_CHARS</body></html>"
        return url, GOOD_HTML

    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"best": 0.9, "second": 0.5}))
    monkeypatch.setattr(rank_module, "_resolve_and_download", fake_resolve)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    # "best" scored higher and was tried first, but its extraction came back
    # too thin; "second" backfilled in to fill the one slot
    assert [a.source_id for a in output.selected] == ["second"]
    assert output.backfilled == 1

    scored_by_id = {ra.article.source_id: ra for ra in output.scored}
    assert scored_by_id["best"].selected is False
    assert scored_by_id["best"].article.final_url == "https://example.com/best"  # resolved ok, extraction failed
    assert scored_by_id["best"].article.text is None


def test_rank_stage_backfills_when_redirect_resolution_fails(tmp_path, monkeypatch):
    profile = _profile(
        interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])],
        duration_minutes=1,
    )
    episode = _episode(profile)
    fetch_output = _fetch_output(episode.episode_id, [_article("best", "only"), _article("second", "only")])

    def fake_resolve(url: str):
        if "best" in url:
            return None, None  # network error resolving the redirect
        return url, GOOD_HTML

    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"best": 0.9, "second": 0.5}))
    monkeypatch.setattr(rank_module, "_resolve_and_download", fake_resolve)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    assert [a.source_id for a in output.selected] == ["second"]
    assert output.backfilled == 1

    scored_by_id = {ra.article.source_id: ra for ra in output.scored}
    assert scored_by_id["best"].selected is False
    assert scored_by_id["best"].article.final_url is None  # never resolved


def test_rank_stage_treats_msn_final_url_as_unextractable_and_backfills(tmp_path, monkeypatch):
    profile = _profile(
        interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])],
        duration_minutes=1,
    )
    episode = _episode(profile)
    fetch_output = _fetch_output(episode.episode_id, [_article("best", "only"), _article("second", "only")])

    def fake_resolve(url: str):
        if "best" in url:
            # a Bing redirect that landed on an MSN aggregator page — html
            # would otherwise extract fine, but msn.com is never trusted
            return "https://www.msn.com/en-us/news/article-xyz", GOOD_HTML
        return url, GOOD_HTML

    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"best": 0.9, "second": 0.5}))
    monkeypatch.setattr(rank_module, "_resolve_and_download", fake_resolve)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    assert [a.source_id for a in output.selected] == ["second"]
    assert output.backfilled == 1

    scored_by_id = {ra.article.source_id: ra for ra in output.scored}
    assert scored_by_id["best"].selected is False
    assert scored_by_id["best"].article.final_url == "https://www.msn.com/en-us/news/article-xyz"


def test_rank_stage_persists_resolved_final_url(tmp_path, monkeypatch):
    profile = _profile(
        interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])],
        duration_minutes=1,
    )
    episode = _episode(profile)
    article = _article("a1", "only")
    fetch_output = _fetch_output(episode.episode_id, [article])

    resolved_url = "https://real-publisher.example.com/the-actual-article"
    monkeypatch.setattr(rank_module, "_score_batch", _scorer({"a1": 0.9}))
    monkeypatch.setattr(rank_module, "_resolve_and_download", lambda url: (resolved_url, GOOD_HTML))
    _patch_episode_dir(monkeypatch, tmp_path)

    output = rank_module.rank_stage(episode, fetch_output, client=object())

    # the redirect wrapper's original URL differs from where it actually landed
    assert str(article.url) != resolved_url
    assert output.selected[0].final_url == resolved_url


def test_score_articles_includes_interest_description(monkeypatch):
    articles = [_article("a1", "space")]
    descriptions = {"space": "rocket launches and orbital missions"}
    captured = {}

    def fake_score_batch(client, model, interest_topic, description, batch):
        captured["interest_topic"] = interest_topic
        captured["description"] = description
        return ScoreBatch(
            scores=[ArticleScore(source_id="a1", interest=interest_topic, score=0.9, reason="mentions a rocket launch")]
        )

    monkeypatch.setattr(rank_module, "_score_batch", fake_score_batch)

    rank_module._score_articles(client=object(), model="m", articles=articles, descriptions=descriptions)

    assert captured["interest_topic"] == "space"
    assert captured["description"] == "rocket launches and orbital missions"


def test_score_articles_never_mixes_interests_in_one_batch(monkeypatch):
    articles = [_article(f"a{i}", "alpha") for i in range(3)] + [_article(f"b{i}", "beta") for i in range(3)]
    descriptions = {"alpha": None, "beta": None}
    seen_batches: list[tuple[str | None, set[str]]] = []

    def fake_score_batch(client, model, interest_topic, description, batch):
        seen_batches.append((interest_topic, {a.source_id for a in batch}))
        return ScoreBatch(
            scores=[
                ArticleScore(source_id=a.source_id, interest=interest_topic, score=0.5, reason="fixture")
                for a in batch
            ]
        )

    monkeypatch.setattr(rank_module, "_score_batch", fake_score_batch)

    rank_module._score_articles(client=object(), model="m", articles=articles, descriptions=descriptions)

    # every call's batch is entirely one interest's articles — never a mix
    for _interest_topic, ids in seen_batches:
        assert ids <= {f"a{i}" for i in range(3)} or ids <= {f"b{i}" for i in range(3)}
    assert {topic for topic, _ids in seen_batches} == {"alpha", "beta"}


def test_score_articles_rejects_and_rescores_mismatched_echo(monkeypatch):
    articles = [_article("a1", "alpha")]
    descriptions = {"alpha": None}
    calls: list[int] = []

    def fake_score_batch(client, model, interest_topic, description, batch):
        calls.append(len(batch))
        if len(calls) == 1:
            # first attempt: echoes the wrong interest — must be rejected
            return ScoreBatch(scores=[ArticleScore(source_id="a1", interest="beta", score=0.9, reason="wrong")])
        # retry: echoes correctly this time
        return ScoreBatch(scores=[ArticleScore(source_id="a1", interest="alpha", score=0.8, reason="right")])

    monkeypatch.setattr(rank_module, "_score_batch", fake_score_batch)

    scores = rank_module._score_articles(client=object(), model="m", articles=articles, descriptions=descriptions)

    assert len(calls) == 2  # rejected once, re-scored once
    assert scores["a1"].score == 0.8
    assert scores["a1"].reason == "right"


def test_score_articles_falls_back_after_persistent_echo_mismatch(monkeypatch):
    articles = [_article("a1", "alpha")]
    descriptions = {"alpha": None}

    def fake_score_batch(client, model, interest_topic, description, batch):
        # always echoes the wrong interest, even on retry
        return ScoreBatch(scores=[ArticleScore(source_id="a1", interest="beta", score=0.9, reason="wrong")])

    monkeypatch.setattr(rank_module, "_score_batch", fake_score_batch)

    scores = rank_module._score_articles(client=object(), model="m", articles=articles, descriptions=descriptions)

    # gives up after MAX_SCORE_ATTEMPTS rather than trusting a score that
    # never proved it was scored against the right interest
    assert scores["a1"].score == 0.0
    assert scores["a1"].reason == "interest echo mismatch"


def test_total_budget_derived_from_duration():
    assert rank_module._total_budget(8) == 5  # round(8/1.5) == 5
    assert rank_module._total_budget(1) == 1  # never zero


def test_rank_stage_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile(interests=[Interest(topic="only", weight=1.0, feeds=["https://example.com/only.xml"])])
    episode = _episode(profile)
    fetch_output = _fetch_output(episode.episode_id, [_article("a1", "only")])

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        rank_module.rank_stage(episode, fetch_output)
