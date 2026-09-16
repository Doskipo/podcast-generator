"""Tests for POST/GET /episodes, GET /episodes/{id}/audio, and
POST /episodes/{id}/events. `service.run_episode` — the entire pipeline — is
replaced with a fake that writes the same handful of artefacts a real run
would (a dummy mp3, a minimal ranked.json/critique.json) and updates the
EpisodeRecord row directly, standing in for what call_stage would have done.
TestClient(app) is used as a context manager so the lifespan runs and
BackgroundTasks complete before client.post(...) returns.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, select

from podcast import db, paths, service
from podcast.api.app import app
from podcast.models import (
    Article,
    Critique,
    CritiqueOutput,
    GroundingQuality,
    Host,
    Interest,
    JudgeScore,
    Listener,
    NaturalnessQuality,
    PodcastSettings,
    Profile,
    QualityJudge,
    QualityOutput,
    RankOutput,
    Script,
    Style,
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
    # Prevent the app's startup seeding (service.seed_profile_from_yaml_if_empty)
    # from picking up the real profiles/eudald.yaml — these tests want a
    # genuinely empty profiles table unless they seed one themselves.
    monkeypatch.setenv("PODCAST_PROFILE_PATH", str(tmp_path / "no-such-profile.yaml"))


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    # podcast.paths.episode_dir() reads this module-global at call time, so
    # patching it here affects every caller (service.py, routes_episodes.py,
    # this file's own fake) uniformly.
    monkeypatch.setattr(paths, "EPISODES_DIR", tmp_path / "episodes")


def _seed_profile(client: TestClient) -> None:
    resp = client.put("/api/profile", json=_profile().model_dump(mode="json"))
    assert resp.status_code == 200


def _fake_run_episode_success(profile, profile_id, episode_id=None, until=None) -> str:
    d = paths.episode_dir(episode_id)
    (d / "episode.mp3").write_bytes(b"fake mp3 bytes")

    now = datetime.now(timezone.utc)
    article = Article(
        source_id="a1",
        url="https://example.com/a1",
        title="A Great Article",
        feed_url="https://example.com/feed.xml",
        published_at=now,
        fetched_at=now,
        source="curated",
    )
    rank_output = RankOutput(
        episode_id=episode_id,
        ranked_at=now,
        model="test-model",
        total_budget=1,
        backfilled=0,
        scored=[],
        selected=[article],
    )
    (d / "ranked.json").write_text(rank_output.model_dump_json(), encoding="utf-8")

    script = Script(title="t", cold_open=[], segments=[], outro=[])
    critique_output = CritiqueOutput(
        episode_id=episode_id,
        generated_at=now,
        model="test-model",
        critique=Critique(flags=[]),
        original_script=script,
        revised_script=script,
        total_words=0,
        over_budget_segments=[],
        terse_hosts=[],
    )
    (d / "critique.json").write_text(critique_output.model_dump_json(), encoding="utf-8")

    with db.session_scope() as session:
        record = service.get_or_create_episode_record(session, episode_id, profile_id)
        record.status = "done"
        record.audio_path = str(d / "episode.mp3")
        record.total_characters = 500
        record.cost_estimate_usd = 0.09
        record.duration_s = 1.23
        session.add(record)
        session.commit()
    return episode_id


def test_post_episodes_returns_pending_immediately(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_success)

    with TestClient(app) as client:
        _seed_profile(client)
        resp = client.post("/api/episodes", json={})

    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending"
    assert body["episode_id"]


def test_post_episodes_without_a_profile_is_404(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.post("/api/episodes", json={})
    assert resp.status_code == 404


def test_get_episodes_lists_created_episode(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_success)

    with TestClient(app) as client:
        _seed_profile(client)
        create_resp = client.post("/api/episodes", json={})
        episode_id = create_resp.json()["episode_id"]

        list_resp = client.get("/api/episodes")

    assert list_resp.status_code == 200
    rows = list_resp.json()
    ids = [e["episode_id"] for e in rows]
    assert episode_id in ids
    # title comes from the script (critique.json's revised_script here), not
    # the bare episode_id — see routes_episodes._load_title
    listed = next(e for e in rows if e["episode_id"] == episode_id)
    assert listed["title"] == "t"


def test_get_episodes_title_is_none_before_a_script_exists(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        _seed_profile(client)
        # Seeded directly (no background pipeline run) — a pending episode
        # with no script.json/critique.json on disk yet.
        with db.session_scope() as session:
            record = service.get_or_create_episode_record(session, "ep-pending", profile_id=1)
            session.add(record)
            session.commit()

        list_resp = client.get("/api/episodes")

    listed = next(e for e in list_resp.json() if e["episode_id"] == "ep-pending")
    assert listed["title"] is None


def test_get_episode_detail_has_script_and_show_notes_once_done(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_success)

    with TestClient(app) as client:
        _seed_profile(client)
        episode_id = client.post("/api/episodes", json={}).json()["episode_id"]

        detail_resp = client.get(f"/api/episodes/{episode_id}")

    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "done"
    assert detail["script"]["title"] == "t"
    assert detail["show_notes"] == [{"title": "A Great Article", "url": "https://example.com/a1", "source": "curated"}]
    assert detail["quality"] is None  # the fake pipeline never wrote quality.json


def test_get_episode_detail_includes_quality_when_quality_json_exists(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_success)

    with TestClient(app) as client:
        _seed_profile(client)
        episode_id = client.post("/api/episodes", json={}).json()["episode_id"]

        quality_output = QualityOutput(
            episode_id=episode_id,
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
                audio_tag_density_per_100_words=1.5,
                interjection_or_dash_share=0.2,
                host_balance=0.8,
                catchphrase_count=1,
            ),
            judge=QualityJudge(
                naturalness=JudgeScore(score=4, reason="reads naturally"),
                stance_clarity=JudgeScore(score=3, reason="a bit flat for Max"),
                model="gpt-4o-mini",
            ),
        )
        (paths.episode_dir(episode_id) / "quality.json").write_text(
            quality_output.model_dump_json(), encoding="utf-8"
        )

        detail_resp = client.get(f"/api/episodes/{episode_id}")

    detail = detail_resp.json()
    assert detail["quality"]["judge"]["naturalness"]["score"] == 4
    assert detail["quality"]["judge"]["stance_clarity"]["reason"] == "a bit flat for Max"
    assert detail["quality"]["naturalness"]["host_balance"] == 0.8


def test_get_episode_detail_unknown_id_is_404(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/episodes/does-not-exist")
    assert resp.status_code == 404


def test_get_episode_audio_serves_the_file(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_success)

    with TestClient(app) as client:
        _seed_profile(client)
        episode_id = client.post("/api/episodes", json={}).json()["episode_id"]

        audio_resp = client.get(f"/api/episodes/{episode_id}/audio")

    assert audio_resp.status_code == 200
    assert audio_resp.headers["content-type"] == "audio/mpeg"
    assert audio_resp.content == b"fake mp3 bytes"


def test_get_episode_audio_404_before_done(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    def fake_pending(profile, profile_id, episode_id=None, until=None) -> str:
        return episode_id  # never marks done, no audio_path set

    monkeypatch.setattr(service, "run_episode", fake_pending)

    with TestClient(app) as client:
        _seed_profile(client)
        episode_id = client.post("/api/episodes", json={}).json()["episode_id"]

        audio_resp = client.get(f"/api/episodes/{episode_id}/audio")

    assert audio_resp.status_code == 404


def test_post_episode_event_creates_an_event_record(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_success)

    with TestClient(app) as client:
        _seed_profile(client)
        episode_id = client.post("/api/episodes", json={}).json()["episode_id"]

        event_resp = client.post(f"/api/episodes/{episode_id}/events", json={"type": "played", "metadata": {"pos_s": 0}})

    assert event_resp.status_code == 201
    body = event_resp.json()
    assert body["type"] == "played"
    assert body["metadata"] == {"pos_s": 0}

    with db.session_scope() as session:
        rows = session.exec(select(db.EventRecord).where(db.EventRecord.episode_id == episode_id)).all()
    assert any(r.type == "played" for r in rows)


def test_post_episode_event_unknown_episode_is_404(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.post("/api/episodes/does-not-exist/events", json={"type": "played"})
    assert resp.status_code == 404


def _fake_run_episode_no_content(profile, profile_id, episode_id=None, until=None) -> str:
    """Stands in for service.run_episode hitting the grounding guard: rank
    selected zero articles, so the (fake) pipeline stops there — same shape
    as service._check_grounding actually leaves the DB in."""
    with db.session_scope() as session:
        record = service.get_or_create_episode_record(session, episode_id, profile_id)
        record.status = "no_content"
        record.no_content_interests = ["testing"]
        session.add(record)
        session.commit()
    return episode_id


def test_no_content_episode_is_a_non_error_state_with_the_empty_interests_listed(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", _fake_run_episode_no_content)

    with TestClient(app) as client:
        _seed_profile(client)
        episode_id = client.post("/api/episodes", json={}).json()["episode_id"]

        list_resp = client.get("/api/episodes")
        detail_resp = client.get(f"/api/episodes/{episode_id}")

    episode_summary = next(e for e in list_resp.json() if e["episode_id"] == episode_id)
    assert episode_summary["status"] == "no_content"
    assert episode_summary["no_content_interests"] == ["testing"]

    # Detail still resolves cleanly (no script/audio yet, not an error).
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "no_content"
    assert detail["no_content_interests"] == ["testing"]
    assert detail["script"] is None

    # No audio to serve for a no_content episode — 404, not a crash.
    with TestClient(app) as client:
        audio_resp = client.get(f"/api/episodes/{episode_id}/audio")
    assert audio_resp.status_code == 404


def test_get_episodes_never_lists_mocked_rows(tmp_path, monkeypatch):
    """Mocked rows (podcast seed-metrics) exist only for the dashboard —
    see docs/decisions.md ("Episode list: mocked, status filter,
    resilience")."""
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        _seed_profile(client)
        with db.session_scope() as session:
            real = service.get_or_create_episode_record(session, "ep-real", profile_id=1)
            real.status = "done"
            session.add(real)
            mocked = service.get_or_create_episode_record(session, "mock-1", profile_id=1)
            mocked.status = "done"
            mocked.mocked = True
            session.add(mocked)
            session.commit()

        list_resp = client.get("/api/episodes")

    ids = [e["episode_id"] for e in list_resp.json()]
    assert "ep-real" in ids
    assert "mock-1" not in ids


def test_get_episodes_surfaces_a_human_readable_failure_reason(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        _seed_profile(client)
        with db.session_scope() as session:
            record = service.get_or_create_episode_record(session, "ep-failed", profile_id=1)
            record.status = "failed"
            record.stage_reached = "tts"
            record.failure_reason = "ElevenLabs: rate limit or quota exceeded"
            session.add(record)
            session.commit()

        list_resp = client.get("/api/episodes")

    listed = next(e for e in list_resp.json() if e["episode_id"] == "ep-failed")
    assert listed["failure_reason"] == "ElevenLabs: rate limit or quota exceeded"


def test_startup_marks_a_leftover_running_episode_as_failed(tmp_path, monkeypatch):
    """The API's lifespan calls service.mark_interrupted_episodes before
    anything else — a "running" row left over from a killed/restarted
    process is orphaned (nothing will ever resume it via the UI), so a
    fresh app startup must resolve it to "failed" instead of leaving it
    stuck "running" forever. See docs/decisions.md ("Episode list: mocked,
    status filter, resilience")."""
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    # Seed the "orphaned" row directly in the DB, as if a previous process
    # had been killed mid-episode — no TestClient/lifespan involved yet.
    with db.session_scope() as session:
        profile_row = service.upsert_profile(_profile(), session)
        record = service.get_or_create_episode_record(session, "ep-orphaned", profile_row.id)
        record.status = "running"
        record.stage_reached = "critique"
        session.add(record)
        session.commit()

    # Entering TestClient(app) as a context manager runs the lifespan.
    with TestClient(app):
        pass

    with db.session_scope() as session:
        record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == "ep-orphaned")).first()
    assert record.status == "failed"
    assert record.stage_reached == "critique"
    assert record.failure_reason == service.INTERRUPTED_REASON
