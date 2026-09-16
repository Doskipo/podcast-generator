"""Smoke tests for service.py's orchestration: DB bookkeeping and event
emission wrapped around the pipeline stages. No network: every stage
function service.py calls is monkeypatched to a fake that just records that
it ran (same convention as the old test_generate.py orchestration tests,
which these absorb) — so these tests exercise --until's stop-after-stage
logic, event emission, and failure handling in isolation from the stages
themselves.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import openai
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from podcast import db, service
from podcast.models import (
    Angle,
    Article,
    Critique,
    CritiqueOutput,
    Episode,
    FetchOutput,
    GroundingQuality,
    Host,
    HostStance,
    Interest,
    JudgeScore,
    Listener,
    NaturalnessQuality,
    Outline,
    OutlineOutput,
    OutlineStory,
    Performance,
    PerformOutput,
    PodcastSettings,
    Profile,
    QualityJudge,
    QualityOutput,
    RankedArticle,
    RankOutput,
    Script,
    ScriptOutput,
    StitchOutput,
    Style,
    TTSOutput,
)


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"])],
        podcast=PodcastSettings(
            name="Test Podcast",
            duration_minutes=8,
            listener=Listener(name="Eudald"),
            hosts=[
                Host(name="Nova", voice_id="voice-nova", persona="Nova is curious and precise."),
                Host(name="Max", voice_id="voice-max", persona="Max is curious and precise."),
            ],
            style=Style(humour=2, depth=2, tangents=True, banter=True),
            tone="curious",
        ),
    )


def _configure_test_db(monkeypatch, tmp_path: Path) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)


def _seed_profile(session: Session, profile: Profile) -> int:
    row = service.upsert_profile(profile, session)
    return row.id


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(service, "episode_dir", _episode_dir)


def _article(source_id: str = "abcd1234") -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title="A real article",
        feed_url="https://example.com/feed.xml",
        published_at=now,
        fetched_at=now,
        summary="a summary",
        text="Full text.",
        interest="testing",
    )


def _patch_stages(monkeypatch, calls: list[str]) -> None:
    def fake_fetch_stage(profile, episode_id):
        calls.append("fetch")
        return FetchOutput(episode_id=episode_id, fetched_at=datetime.now(timezone.utc), feeds_count=13, articles=[])

    def fake_rank_stage(episode, fetch_output, client=None):
        calls.append("rank")
        article = _article()
        return RankOutput(
            episode_id=episode.episode_id,
            ranked_at=datetime.now(timezone.utc),
            model="test-model",
            total_budget=1,
            backfilled=0,
            scored=[RankedArticle(article=article, interest="testing", score=0.9, reason="on topic", selected=True)],
            selected=[article],
        )

    def fake_outline_stage(episode, rank_output, client=None):
        calls.append("outline")
        outline = Outline(
            title="t",
            stories=[
                OutlineStory(
                    headline="h",
                    source_ids=[],
                    angle=Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent="a"),
                    stances=[
                        HostStance(host="Nova", attitude="excited", why="her home turf"),
                        HostStance(host="Max", attitude="skeptical", why="wants the numbers"),
                    ],
                )
            ],
        )
        return OutlineOutput(
            episode_id=episode.episode_id, generated_at=datetime.now(timezone.utc), model="test-model", outline=outline
        )

    def fake_script_stage(episode, outline_output, articles, client=None):
        calls.append("script")
        return ScriptOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            model="test-model",
            script=Script(title="t", cold_open=[], segments=[], outro=[]),
        )

    def fake_critique_stage(episode, script_output, articles, outline_output, client=None):
        calls.append("critique")
        return CritiqueOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            model="test-model",
            critique=Critique(flags=[]),
            original_script=script_output.script,
            revised_script=script_output.script,
            total_words=0,
            over_budget_segments=[],
            terse_hosts=[],
        )

    def fake_perform_stage(episode, critique_output, articles, client=None):
        calls.append("perform")
        return PerformOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            model="test-model",
            fact_check_model="test-model-cheap",
            performance=Performance(title="t", cold_open=[], segments=[], outro=[]),
            fact_flags=[],
        )

    def fake_quality_stage(episode, perform_output, client=None):
        calls.append("quality")
        return QualityOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            grounding=GroundingQuality(
                source_count=1,
                evergreen_share=0.0,
                fact_drift_flags=0,
                critique_flags=0,
                critique_rewrite_rate=0.0,
                stage_retries={"outline": False, "script": False, "critique": False, "perform": False},
                total_retries=0,
            ),
            naturalness=NaturalnessQuality(
                audio_tag_density_per_100_words=0.0,
                interjection_or_dash_share=0.0,
                host_balance=1.0,
                catchphrase_count=0,
            ),
            judge=QualityJudge(
                naturalness=JudgeScore(score=4, reason="reads naturally"),
                stance_clarity=JudgeScore(score=4, reason="stances come through"),
                model="test-model",
            ),
        )

    def fake_tts_stage(episode, perform_output, client=None):
        calls.append("tts")
        return TTSOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            lines=[],
            synthesis_mode="dialogue",
            total_characters=1000,
        )

    def fake_stitch_stage(episode, tts_output):
        calls.append("stitch")
        return StitchOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            audio_file="episode.mp3",
            duration_ms=0,
        )

    monkeypatch.setattr(service, "fetch_stage", fake_fetch_stage)
    monkeypatch.setattr(service, "rank_stage", fake_rank_stage)
    monkeypatch.setattr(service, "outline_stage", fake_outline_stage)
    monkeypatch.setattr(service, "script_stage", fake_script_stage)
    monkeypatch.setattr(service, "critique_stage", fake_critique_stage)
    monkeypatch.setattr(service, "perform_stage", fake_perform_stage)
    monkeypatch.setattr(service, "quality_stage", fake_quality_stage)
    monkeypatch.setattr(service, "tts_stage", fake_tts_stage)
    monkeypatch.setattr(service, "stitch_stage", fake_stitch_stage)


def _run(monkeypatch, tmp_path, episode_id, until=None):
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)
    _configure_test_db(monkeypatch, tmp_path)

    with db.session_scope() as session:
        profile_id = _seed_profile(session, _profile())

    result = service.run_episode(_profile(), profile_id, episode_id=episode_id, until=until)
    return result, calls


def test_run_episode_until_rank_stops_before_outline(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep1", until="rank")
    assert calls == ["fetch", "rank"]


def test_run_episode_until_fetch_stops_before_rank(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep2", until="fetch")
    assert calls == ["fetch"]


def test_run_episode_until_outline_stops_before_script(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep3", until="outline")
    assert calls == ["fetch", "rank", "outline"]


def test_run_episode_until_script_stops_before_critique(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep4", until="script")
    assert calls == ["fetch", "rank", "outline", "script"]


def test_run_episode_until_critique_stops_before_tts(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep5", until="critique")
    assert calls == ["fetch", "rank", "outline", "script", "critique"]


def test_run_episode_until_perform_stops_before_tts(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep5b", until="perform")
    assert calls == ["fetch", "rank", "outline", "script", "critique", "perform"]


def test_run_episode_until_quality_stops_before_tts(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep5c", until="quality")
    assert calls == ["fetch", "rank", "outline", "script", "critique", "perform", "quality"]


def test_run_episode_without_until_runs_the_full_pipeline(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep6")
    assert calls == ["fetch", "rank", "outline", "script", "critique", "perform", "quality", "tts", "stitch"]


def test_run_episode_prints_actual_feeds_count(tmp_path, monkeypatch, capsys):
    _run(monkeypatch, tmp_path, "ep7", until="fetch")
    out = capsys.readouterr().out
    assert "from 13 feeds" in out
    assert "from 0 feeds" not in out


def test_run_episode_emits_events_in_order(tmp_path, monkeypatch):
    episode_id, _ = _run(monkeypatch, tmp_path, "ep8")

    with db.session_scope() as session:
        events = session.exec(
            select(db.EventRecord).where(db.EventRecord.episode_id == episode_id).order_by(db.EventRecord.id)
        ).all()

    types = [e.type for e in events]
    assert types == ["generated"] + ["stage_done"] * 9 + ["completed"]


def test_run_episode_records_total_characters_and_cost(tmp_path, monkeypatch):
    episode_id, _ = _run(monkeypatch, tmp_path, "ep9")

    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()

    assert record.status == "done"
    assert record.total_characters == 1000
    assert record.cost_estimate_usd == pytest.approx(0.18)
    assert record.audio_path is not None


def test_run_episode_marks_failed_on_stage_exception_and_reraises(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)
    _configure_test_db(monkeypatch, tmp_path)

    def boom(episode, rank_output, client=None):
        raise ValueError("boom")

    monkeypatch.setattr(service, "outline_stage", boom)

    with db.session_scope() as session:
        profile_id = _seed_profile(session, _profile())

    with pytest.raises(ValueError, match="boom"):
        service.run_episode(_profile(), profile_id, episode_id="ep10")

    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == "ep10")).first()
        failed_events = session.exec(
            select(db.EventRecord).where(db.EventRecord.episode_id == "ep10", db.EventRecord.type == "failed")
        ).all()

    assert record.status == "failed"
    assert record.stage_reached == "outline"
    assert record.failure_reason == "boom"
    assert len(failed_events) == 1
    assert failed_events[0].metadata_json["stage"] == "outline"
    assert failed_events[0].metadata_json["error"] == "boom"


def test_run_episode_marks_failed_when_perform_stage_raises(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)
    _configure_test_db(monkeypatch, tmp_path)

    def boom(episode, critique_output, articles, client=None):
        raise ValueError("perform boom")

    monkeypatch.setattr(service, "perform_stage", boom)

    with db.session_scope() as session:
        profile_id = _seed_profile(session, _profile())

    with pytest.raises(ValueError, match="perform boom"):
        service.run_episode(_profile(), profile_id, episode_id="ep10b")

    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == "ep10b")).first()
        failed_events = session.exec(
            select(db.EventRecord).where(db.EventRecord.episode_id == "ep10b", db.EventRecord.type == "failed")
        ).all()

    assert record.status == "failed"
    assert record.stage_reached == "perform"
    assert record.failure_reason == "perform boom"
    assert len(failed_events) == 1
    assert failed_events[0].metadata_json["stage"] == "perform"
    assert failed_events[0].metadata_json["error"] == "perform boom"


def test_run_episode_stops_at_no_content_when_rank_selects_nothing(tmp_path, monkeypatch):
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)
    _configure_test_db(monkeypatch, tmp_path)

    def fake_rank_stage_empty(episode, fetch_output, client=None):
        calls.append("rank")
        return RankOutput(
            episode_id=episode.episode_id,
            ranked_at=datetime.now(timezone.utc),
            model="test-model",
            total_budget=1,
            backfilled=0,
            scored=[],  # nothing was even scored — every interest came up empty
            selected=[],
        )

    monkeypatch.setattr(service, "rank_stage", fake_rank_stage_empty)

    profile = _profile()
    with db.session_scope() as session:
        profile_id = _seed_profile(session, profile)

    episode_id = service.run_episode(profile, profile_id, episode_id="ep11")

    # Pipeline stopped: outline/script/critique/tts/stitch never ran.
    assert calls == ["fetch", "rank"]

    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()
        events = session.exec(
            select(db.EventRecord).where(db.EventRecord.episode_id == episode_id).order_by(db.EventRecord.id)
        ).all()

    assert record.status == "no_content"
    assert record.no_content_interests == ["testing"]  # the profile's one interest, zero candidates
    event_types = [e.type for e in events]
    assert event_types == ["generated", "stage_done", "stage_done", "no_content"]  # fetch, rank, then no_content
    no_content_event = next(e for e in events if e.type == "no_content")
    assert no_content_event.metadata_json["interests"] == ["testing"]


def test_run_episode_no_content_only_lists_interests_that_truly_had_zero_candidates(tmp_path, monkeypatch):
    """An interest that *did* have scored candidates isn't blamed just
    because none of them ended up selected — only interests absent from
    rank_output.scored entirely count as "no candidates"."""
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)
    _configure_test_db(monkeypatch, tmp_path)

    def fake_rank_stage_scored_but_unselected(episode, fetch_output, client=None):
        calls.append("rank")
        article = _article()
        return RankOutput(
            episode_id=episode.episode_id,
            ranked_at=datetime.now(timezone.utc),
            model="test-model",
            total_budget=1,
            backfilled=0,
            scored=[RankedArticle(article=article, interest="testing", score=0.1, reason="weak", selected=False)],
            selected=[],  # scored, but nothing cleared the bar
        )

    monkeypatch.setattr(service, "rank_stage", fake_rank_stage_scored_but_unselected)

    profile = _profile()
    with db.session_scope() as session:
        profile_id = _seed_profile(session, profile)

    episode_id = service.run_episode(profile, profile_id, episode_id="ep12")

    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()

    assert record.status == "no_content"
    assert record.no_content_interests == []  # "testing" was scored, just not selected


def test_resume_from_rank_also_stops_at_no_content(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)

    profile = _profile()
    with db.session_scope() as session:
        profile_id = _seed_profile(session, profile)

    episode = Episode(episode_id="ep13", created_at=datetime.now(timezone.utc), profile=profile)
    empty_rank_output = RankOutput(
        episode_id="ep13",
        ranked_at=datetime.now(timezone.utc),
        model="test-model",
        total_budget=1,
        backfilled=0,
        scored=[],
        selected=[],
    )

    with db.session_scope() as session:
        record = service.get_or_create_episode_record(session, "ep13", profile_id)
        episode_id = service.resume_from_rank(session, record, episode, empty_rank_output, until=None)

    assert calls == []  # outline_stage (etc.) never got the chance
    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()
    assert record.status == "no_content"
    assert record.no_content_interests == ["testing"]


def test_resume_from_critique_runs_perform_then_tts_then_stitch(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)

    profile = _profile()
    with db.session_scope() as session:
        profile_id = _seed_profile(session, profile)

    episode = Episode(episode_id="ep14", created_at=datetime.now(timezone.utc), profile=profile)
    critique_output = CritiqueOutput(
        episode_id="ep14",
        generated_at=datetime.now(timezone.utc),
        model="test-model",
        critique=Critique(flags=[]),
        original_script=Script(title="t", cold_open=[], segments=[], outro=[]),
        revised_script=Script(title="t", cold_open=[], segments=[], outro=[]),
        total_words=0,
        over_budget_segments=[],
        terse_hosts=[],
    )

    with db.session_scope() as session:
        record = service.get_or_create_episode_record(session, "ep14", profile_id)
        service.resume_from_critique(session, record, episode, critique_output, articles=[], until=None)

    assert calls == ["perform", "quality", "tts", "stitch"]


def test_call_stage_humanizes_provider_errors_into_failure_reason(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    with db.session_scope() as session:
        profile_id = _seed_profile(session, _profile())
        record = service.get_or_create_episode_record(session, "ep-provider-fail", profile_id)

        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        response = httpx.Response(429, request=request, json={"error": {"message": "you exceeded your quota"}})
        exc = openai.RateLimitError("you exceeded your quota", response=response, body=None)

        def boom():
            raise exc

        with pytest.raises(openai.RateLimitError):
            service.call_stage(session, record, "outline", boom)

        # the short, human-readable reason — not the raw SDK message — is
        # what's persisted for the UI/dashboard to read
        assert record.failure_reason == "OpenAI: rate limit or quota exceeded"
        failed_event = session.exec(
            select(db.EventRecord).where(db.EventRecord.episode_id == "ep-provider-fail", db.EventRecord.type == "failed")
        ).one()
        assert failed_event.metadata_json["error"] == "OpenAI: rate limit or quota exceeded"


def test_mark_interrupted_episodes_marks_running_rows_as_failed(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    with db.session_scope() as session:
        profile_id = _seed_profile(session, _profile())
        record = service.get_or_create_episode_record(session, "ep-orphaned", profile_id)
        record.status = "running"
        record.stage_reached = "tts"
        session.add(record)
        session.commit()

        count = service.mark_interrupted_episodes(session)
        assert count == 1

        session.refresh(record)
        assert record.status == "failed"
        assert record.stage_reached == "tts"  # untouched — where it got stuck
        assert record.failure_reason == service.INTERRUPTED_REASON

        failed_event = session.exec(
            select(db.EventRecord).where(db.EventRecord.episode_id == "ep-orphaned", db.EventRecord.type == "failed")
        ).one()
        assert failed_event.metadata_json["error"] == service.INTERRUPTED_REASON
        assert failed_event.metadata_json["stage"] == "tts"


def test_mark_interrupted_episodes_ignores_non_running_and_mocked_rows(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    with db.session_scope() as session:
        profile_id = _seed_profile(session, _profile())
        now = datetime.now(timezone.utc)

        done = db.EpisodeRecord(episode_id="ep-done", profile_id=profile_id, status="done", created_at=now)
        pending = db.EpisodeRecord(episode_id="ep-pending", profile_id=profile_id, status="pending", created_at=now)
        mocked_running = db.EpisodeRecord(
            episode_id="mock-running", profile_id=profile_id, status="running", created_at=now, mocked=True
        )
        session.add(done)
        session.add(pending)
        session.add(mocked_running)
        session.commit()

        count = service.mark_interrupted_episodes(session)
        assert count == 0

        session.refresh(done)
        session.refresh(pending)
        session.refresh(mocked_running)
    assert done.status == "done"
    assert pending.status == "pending"
    assert mocked_running.status == "running"  # mocked demo data is never touched


def test_upsert_profile_is_idempotent(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile = _profile()

    with db.session_scope() as session:
        row1 = service.upsert_profile(profile, session)

    profile.name = "Renamed"
    with db.session_scope() as session:
        row2 = service.upsert_profile(profile, session)

    assert row1.id == row2.id

    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1
    assert rows[0].name == "Renamed"
