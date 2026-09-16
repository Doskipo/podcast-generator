"""Unit tests for podcast.metrics (the aggregation behind GET /metrics/
summary) — real episodes' cost/topic data comes from fixture manifests
written under a tmp data/episodes/ dir (same pattern tests/api/test_metrics.py
uses); mocked episodes' comes from their own DB columns. No network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import SQLModel, create_engine

from podcast import db, metrics, paths
from podcast.models import (
    Article,
    Performance,
    PerformedLine,
    PerformedSegment,
    PerformOutput,
    RankedArticle,
    RankOutput,
    StitchOutput,
    TokenUsage,
)


def _configure_test_db(monkeypatch, tmp_path: Path) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "EPISODES_DIR", tmp_path / "episodes")


def _article(source_id: str, interest: str | None) -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=f"Article {source_id}",
        feed_url="https://example.com/feed.xml",
        published_at=now,
        fetched_at=now,
        interest=interest,
        text="grounded text",
    )


def _write_ranked_json(episodes_dir: Path, episode_id: str, selected_interests: list[str | None], usage: list[TokenUsage]) -> None:
    d = episodes_dir / episode_id
    d.mkdir(parents=True, exist_ok=True)
    selected = [_article(f"{episode_id}-{i}", interest) for i, interest in enumerate(selected_interests)]
    output = RankOutput(
        episode_id=episode_id,
        ranked_at=datetime.now(timezone.utc),
        model="gpt-4o-mini",
        total_budget=len(selected),
        backfilled=0,
        scored=[RankedArticle(article=a, interest=a.interest, score=0.8, reason="r", selected=True) for a in selected],
        selected=selected,
        usage=usage,
    )
    (d / "ranked.json").write_text(output.model_dump_json(indent=2), encoding="utf-8")


def _write_performance_and_stitch(episodes_dir: Path, episode_id: str, word_texts: list[str], duration_ms: int) -> None:
    """Writes just enough of performance.json + stitch_manifest.json for
    measured_words_per_minute to read — one segment carrying all of
    `word_texts` as separate lines, one line per text."""
    d = episodes_dir / episode_id
    d.mkdir(parents=True, exist_ok=True)
    performance = Performance(
        title="t",
        cold_open=[],
        segments=[
            PerformedSegment(
                headline="h",
                source_ids=[],
                lines=[PerformedLine(speaker="Nova", text=text) for text in word_texts],
            )
        ],
        outro=[],
    )
    perform_output = PerformOutput(
        episode_id=episode_id,
        generated_at=datetime.now(timezone.utc),
        model="test-model",
        fact_check_model="test-model-cheap",
        performance=performance,
        fact_flags=[],
    )
    (d / "performance.json").write_text(perform_output.model_dump_json(indent=2), encoding="utf-8")

    stitch_output = StitchOutput(
        episode_id=episode_id, generated_at=datetime.now(timezone.utc), audio_file="episode.mp3", duration_ms=duration_ms
    )
    (d / "stitch_manifest.json").write_text(stitch_output.model_dump_json(indent=2), encoding="utf-8")


def test_measured_words_per_minute_aggregates_across_episodes(tmp_path, monkeypatch):
    _patch_episode_dir(monkeypatch, tmp_path)
    # ep1: 120 words in 60s (1 min) -> 120 wpm alone
    _write_performance_and_stitch(paths.EPISODES_DIR, "ep1", ["word"] * 120, duration_ms=60_000)
    # ep2: 60 words in 60s (1 min) -> 60 wpm alone
    _write_performance_and_stitch(paths.EPISODES_DIR, "ep2", ["word"] * 60, duration_ms=60_000)

    result = metrics.measured_words_per_minute()

    assert result.episode_count == 2
    assert result.total_words == 180
    assert result.total_minutes == 2.0
    # aggregate (weighted by duration), not a simple average of 120 and 60
    assert result.words_per_minute == 90.0


def test_measured_words_per_minute_ignores_episodes_missing_either_file(tmp_path, monkeypatch):
    _patch_episode_dir(monkeypatch, tmp_path)
    _write_performance_and_stitch(paths.EPISODES_DIR, "complete", ["word"] * 100, duration_ms=60_000)

    # performance.json only, no stitch_manifest.json — episode never finished synthesis
    perform_only_dir = paths.EPISODES_DIR / "perform-only"
    perform_only_dir.mkdir(parents=True, exist_ok=True)
    performance = Performance(title="t", cold_open=[], segments=[], outro=[])
    perform_output = PerformOutput(
        episode_id="perform-only",
        generated_at=datetime.now(timezone.utc),
        model="m",
        fact_check_model="m",
        performance=performance,
        fact_flags=[],
    )
    (perform_only_dir / "performance.json").write_text(perform_output.model_dump_json(indent=2), encoding="utf-8")

    # stitch_manifest.json only, no performance.json — predates the perform stage
    stitch_only_dir = paths.EPISODES_DIR / "stitch-only"
    stitch_only_dir.mkdir(parents=True, exist_ok=True)
    stitch_output = StitchOutput(
        episode_id="stitch-only", generated_at=datetime.now(timezone.utc), audio_file="episode.mp3", duration_ms=60_000
    )
    (stitch_only_dir / "stitch_manifest.json").write_text(stitch_output.model_dump_json(indent=2), encoding="utf-8")

    result = metrics.measured_words_per_minute()

    assert result.episode_count == 1  # only "complete" qualifies
    assert result.total_words == 100


def test_measured_words_per_minute_returns_none_when_no_qualifying_episode(tmp_path, monkeypatch):
    _patch_episode_dir(monkeypatch, tmp_path)
    assert metrics.measured_words_per_minute() is None


def test_estimate_openai_cost_uses_known_model_pricing():
    usage = [TokenUsage(model="gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000)]
    cost = metrics.estimate_openai_cost_usd(usage)
    assert cost == metrics.OPENAI_PRICING_PER_1K_TOKENS["gpt-4o-mini"]["prompt"] + metrics.OPENAI_PRICING_PER_1K_TOKENS["gpt-4o-mini"]["completion"]


def test_estimate_openai_cost_falls_back_for_unknown_model():
    usage = [TokenUsage(model="some-future-model", prompt_tokens=1000, completion_tokens=0)]
    assert metrics.estimate_openai_cost_usd(usage) == metrics._DEFAULT_OPENAI_PRICING["prompt"]


def test_episode_cost_breakdown_reads_real_manifest(tmp_path, monkeypatch):
    _patch_episode_dir(monkeypatch, tmp_path)
    usage = [TokenUsage(model="gpt-4o-mini", prompt_tokens=800, completion_tokens=400)]
    _write_ranked_json(paths.EPISODES_DIR, "ep1", ["alpha"], usage)

    record = db.EpisodeRecord(episode_id="ep1", profile_id=1, status="done", created_at=datetime.now(timezone.utc))
    breakdown = metrics.episode_cost_breakdown(record)

    assert len(breakdown) == 1
    assert breakdown[0].stage == "rank"
    assert breakdown[0].provider == "openai"
    assert breakdown[0].cost_usd == metrics.estimate_openai_cost_usd(usage)


def test_episode_cost_breakdown_reads_mocked_row_not_manifests(tmp_path, monkeypatch):
    _patch_episode_dir(monkeypatch, tmp_path)  # no manifest files written at all
    record = db.EpisodeRecord(
        episode_id="mock-1",
        profile_id=1,
        status="done",
        created_at=datetime.now(timezone.utc),
        mocked=True,
        mock_cost_by_stage={"rank": {"openai": 0.01}, "tts": {"elevenlabs": 0.5}},
    )
    breakdown = {(c.stage, c.provider): c.cost_usd for c in metrics.episode_cost_breakdown(record)}
    assert breakdown == {("rank", "openai"): 0.01, ("tts", "elevenlabs"): 0.5}


def test_aggregate_summary_defaults_to_real_episodes_only(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    now = datetime.now(timezone.utc)
    usage = [TokenUsage(model="gpt-4o-mini", prompt_tokens=800, completion_tokens=400)]
    _write_ranked_json(paths.EPISODES_DIR, "real-1", ["alpha"], usage)

    with db.session_scope() as session:
        session.add(db.ProfileRecord(id=1, name="Test", data={}, created_at=now, updated_at=now))
        real = db.EpisodeRecord(episode_id="real-1", profile_id=1, status="done", created_at=now, duration_s=100.0)
        mocked = db.EpisodeRecord(
            episode_id="mock-1",
            profile_id=1,
            status="done",
            created_at=now,
            duration_s=120.0,
            mocked=True,
            mock_cost_by_stage={"rank": {"openai": 0.01}},
            mock_topic_counts={"alpha": 2},
        )
        session.add(real)
        session.add(mocked)
        session.commit()

        session.add(db.EventRecord(episode_id="real-1", type="stage_done", ts=now, metadata_json={"stage": "rank", "elapsed_s": 12.0}))
        session.add(db.EventRecord(episode_id="mock-1", type="stage_done", ts=now, metadata_json={"stage": "rank", "elapsed_s": 8.0}, mocked=True))
        session.commit()

        default_summary = metrics.aggregate_summary(session)
        opted_in_summary = metrics.aggregate_summary(session, include_mocked=True)

    # has_mocked_data reflects the DB regardless of the current filter — the
    # signal a caller uses to decide whether a toggle is worth showing.
    assert default_summary.has_mocked_data is True
    assert opted_in_summary.has_mocked_data is True

    # default (include_mocked=False): only the real row counts
    status_counts = {s.status: s.count for s in default_summary.episodes_by_status}
    assert status_counts == {"done": 1}
    topics = {t.interest: t.count for t in default_summary.topic_distribution}
    assert topics == {"alpha": 1}  # only the real manifest's count, not the mocked row's
    stage_durations = {d.stage: d for d in default_summary.avg_stage_duration_s}
    assert stage_durations["rank"].count == 1
    assert stage_durations["rank"].avg_elapsed_s == 12.0

    # include_mocked=True: both rows count, same as the combined-view test below
    status_counts_all = {s.status: s.count for s in opted_in_summary.episodes_by_status}
    assert status_counts_all == {"done": 2}
    topics_all = {t.interest: t.count for t in opted_in_summary.topic_distribution}
    assert topics_all["alpha"] == 1 + 2


def test_aggregate_summary_combines_real_and_mocked_episodes(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    now = datetime.now(timezone.utc)
    usage = [TokenUsage(model="gpt-4o-mini", prompt_tokens=800, completion_tokens=400)]
    _write_ranked_json(paths.EPISODES_DIR, "real-1", ["alpha", "beta"], usage)

    with db.session_scope() as session:
        session.add(db.ProfileRecord(id=1, name="Test", data={}, created_at=now, updated_at=now))
        real = db.EpisodeRecord(episode_id="real-1", profile_id=1, status="done", created_at=now, duration_s=100.0)
        mocked = db.EpisodeRecord(
            episode_id="mock-1",
            profile_id=1,
            status="done",
            created_at=now - timedelta(days=1),
            duration_s=120.0,
            mocked=True,
            mock_cost_by_stage={"rank": {"openai": 0.01}},
            mock_topic_counts={"alpha": 2},
        )
        no_content = db.EpisodeRecord(
            episode_id="real-2", profile_id=1, status="no_content", created_at=now, no_content_interests=["beta"]
        )
        session.add(real)
        session.add(mocked)
        session.add(no_content)
        session.commit()

        session.add(db.EventRecord(episode_id="real-1", type="stage_done", ts=now, metadata_json={"stage": "rank", "elapsed_s": 12.0}))
        session.add(db.EventRecord(episode_id="mock-1", type="stage_done", ts=now, metadata_json={"stage": "rank", "elapsed_s": 8.0}, mocked=True))
        session.add(db.EventRecord(episode_id="real-2", type="failed", ts=now, metadata_json={"stage": "outline", "error": "boom"}))
        session.commit()

        # explicit include_mocked=True: this test is about the combined view,
        # not the (now real-only-by-default) default — see
        # test_aggregate_summary_defaults_to_real_episodes_only
        summary = metrics.aggregate_summary(session, include_mocked=True)

    assert summary.has_mocked_data is True
    status_counts = {s.status: s.count for s in summary.episodes_by_status}
    assert status_counts == {"done": 2, "no_content": 1}
    assert summary.no_content == 1

    cost_by_key = {(c.stage, c.provider): c.cost_usd for c in summary.cost_by_stage}
    assert cost_by_key[("rank", "openai")] == round(
        metrics.estimate_openai_cost_usd(usage) + 0.01, 4
    )

    assert summary.avg_cost_per_episode_usd is not None  # averaged over the two "done" episodes

    topics = {t.interest: t.count for t in summary.topic_distribution}
    assert topics["alpha"] == 1 + 2  # 1 from the real manifest, 2 from the mocked row
    assert topics["beta"] == 1

    stage_durations = {d.stage: d for d in summary.avg_stage_duration_s}
    assert stage_durations["rank"].count == 2
    assert stage_durations["rank"].avg_elapsed_s == round((12.0 + 8.0) / 2, 2)

    assert summary.interests_with_no_content == [metrics.TopicCount(interest="beta", count=1)]

    failures = {f.episode_id: f for f in summary.recent_failures}
    assert failures["real-2"].status == "no_content"
    assert failures["real-2"].reason == "beta"


def test_recent_failures_prefers_the_failure_reason_column_over_event_scan(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        session.add(db.ProfileRecord(id=1, name="Test", data={}, created_at=now, updated_at=now))
        # has the denormalized column set (every real failure since it was
        # added) — the event's own "error" is deliberately different, to
        # prove the column wins, not the event scan.
        with_column = db.EpisodeRecord(
            episode_id="ep-with-column",
            profile_id=1,
            status="failed",
            created_at=now,
            failure_reason="OpenAI: rate limit or quota exceeded",
        )
        # column never set (a row that failed before failure_reason existed,
        # or a mocked row — seed_metrics writes a "failed" event but not
        # this column) — must fall back to the event's own error.
        without_column = db.EpisodeRecord(episode_id="ep-without-column", profile_id=1, status="failed", created_at=now)
        session.add(with_column)
        session.add(without_column)
        session.commit()

        session.add(
            db.EventRecord(
                episode_id="ep-with-column", type="failed", ts=now, metadata_json={"stage": "outline", "error": "stale event text"}
            )
        )
        session.add(
            db.EventRecord(
                episode_id="ep-without-column", type="failed", ts=now, metadata_json={"stage": "tts", "error": "from the event"}
            )
        )
        session.commit()

        summary = metrics.aggregate_summary(session)

    failures = {f.episode_id: f for f in summary.recent_failures}
    assert failures["ep-with-column"].reason == "OpenAI: rate limit or quota exceeded"
    assert failures["ep-without-column"].reason == "from the event"

    # today's real/mocked rows both land on a daily_series point
    today_point = next(p for p in summary.daily_series if p.date == now.date().isoformat())
    assert today_point.episodes_created == 2  # real-1 and real-2, both created "now"
    assert today_point.mocked is False  # only real rows landed on this day


def test_d7_retention_needs_user_tagged_play_events(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    now = datetime.now(timezone.utc)
    with db.session_scope() as session:
        session.add(db.ProfileRecord(id=1, name="Test", data={}, created_at=now, updated_at=now))
        session.add(db.EpisodeRecord(episode_id="ep1", profile_id=1, status="done", created_at=now - timedelta(days=10)))
        session.commit()

        # a real play with no user_id — doesn't contribute to retention
        session.add(db.EventRecord(episode_id="ep1", type="played", ts=now - timedelta(days=10), metadata_json={}))
        session.commit()

        summary = metrics.aggregate_summary(session)
    assert summary.d7_retention is None
    assert summary.plays_total == 1  # still counted toward the raw play total

    with db.session_scope() as session:
        # a user who played on day -10 and again on day -3 (exactly +7)
        session.add(
            db.EventRecord(
                episode_id="ep1", type="played", ts=now - timedelta(days=10), metadata_json={"user_id": "u1"}, mocked=True
            )
        )
        session.add(
            db.EventRecord(
                episode_id="ep1", type="played", ts=now - timedelta(days=3), metadata_json={"user_id": "u1"}, mocked=True
            )
        )
        session.commit()

        # these play events are mocked (per-listener identity is mocked-only
        # today, per _d7_retention's docstring) — need include_mocked=True to
        # see them at all under the new real-only default
        summary = metrics.aggregate_summary(session, include_mocked=True)
    assert summary.d7_retention == 1.0
