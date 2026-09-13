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

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from podcast import db, service
from podcast.models import (
    Angle,
    Critique,
    CritiqueOutput,
    FetchOutput,
    Host,
    HostStance,
    Interest,
    Listener,
    Outline,
    OutlineOutput,
    OutlineStory,
    PodcastSettings,
    Profile,
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


def _patch_stages(monkeypatch, calls: list[str]) -> None:
    def fake_fetch_stage(profile, episode_id):
        calls.append("fetch")
        return FetchOutput(episode_id=episode_id, fetched_at=datetime.now(timezone.utc), feeds_count=13, articles=[])

    def fake_rank_stage(episode, fetch_output, client=None):
        calls.append("rank")
        return RankOutput(
            episode_id=episode.episode_id,
            ranked_at=datetime.now(timezone.utc),
            model="test-model",
            total_budget=1,
            backfilled=0,
            scored=[],
            selected=[],
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

    def fake_tts_stage(episode, script_output, client=None):
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


def test_run_episode_without_until_runs_the_full_pipeline(tmp_path, monkeypatch):
    _, calls = _run(monkeypatch, tmp_path, "ep6")
    assert calls == ["fetch", "rank", "outline", "script", "critique", "tts", "stitch"]


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
    assert types == ["generated"] + ["stage_done"] * 7 + ["completed"]


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
    assert len(failed_events) == 1
    assert failed_events[0].metadata_json["stage"] == "outline"
    assert failed_events[0].metadata_json["error"] == "boom"


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
