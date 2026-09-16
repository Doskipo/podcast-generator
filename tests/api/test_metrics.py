"""Tests for GET /metrics/summary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from podcast import db, paths, service
from podcast.api.app import app
from podcast.models import (
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
    monkeypatch.setattr(paths, "EPISODES_DIR", tmp_path / "episodes")


def test_metrics_summary_aggregates_done_and_failed_episodes(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        put_resp = client.put("/api/profile", json=_profile().model_dump(mode="json"))
        profile_id = 1
        assert put_resp.status_code == 200

        # Seeded directly rather than via two POST /episodes calls — episode
        # creation itself is already covered in test_episodes.py; this file
        # only exercises aggregation over rows that already exist.
        with db.session_scope() as session:
            done = service.get_or_create_episode_record(session, "ep-done", profile_id)
            done.status = "done"
            done.total_characters = 1000
            done.cost_estimate_usd = 0.18
            done.duration_s = 10.0
            session.add(done)

            failed = service.get_or_create_episode_record(session, "ep-failed", profile_id)
            failed.status = "failed"
            failed.stage_reached = "outline"
            session.add(failed)

            session.commit()

        resp = client.get("/api/metrics/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_episodes"] == 2
    assert body["done"] == 1
    assert body["failed"] == 1
    assert body["pending_or_running"] == 0
    assert body["total_characters"] == 1000
    assert body["total_cost_estimate_usd"] == 0.18
    assert body["avg_duration_s"] == 10.0

    # extended fields are present and reflect the same two rows
    assert body["no_content"] == 0
    assert {s["status"]: s["count"] for s in body["episodes_by_status"]} == {"done": 1, "failed": 1}
    assert body["has_mocked_data"] is False
    assert body["include_mocked"] is False
    assert len(body["daily_series"]) == 30
    failed_row = next(f for f in body["recent_failures"] if f["episode_id"] == "ep-failed")
    assert failed_row["status"] == "failed"
    assert failed_row["mocked"] is False


def test_metrics_summary_defaults_to_real_episodes_and_toggle_switches_the_whole_response(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        put_resp = client.put("/api/profile", json=_profile().model_dump(mode="json"))
        profile_id = 1
        assert put_resp.status_code == 200

        with db.session_scope() as session:
            real = service.get_or_create_episode_record(session, "ep-real", profile_id)
            real.status = "done"
            real.total_characters = 500
            session.add(real)

            mocked = service.get_or_create_episode_record(session, "ep-mocked", profile_id)
            mocked.status = "done"
            mocked.total_characters = 500
            mocked.mocked = True
            session.add(mocked)

            session.commit()

        default_resp = client.get("/api/metrics/summary")
        opted_in_resp = client.get("/api/metrics/summary?include_mocked=true")

    assert default_resp.status_code == 200
    default_body = default_resp.json()
    assert default_body["total_episodes"] == 1  # ep-mocked excluded
    assert default_body["total_characters"] == 500
    assert default_body["include_mocked"] is False
    assert default_body["has_mocked_data"] is True  # still signals a toggle is worth showing

    assert opted_in_resp.status_code == 200
    opted_in_body = opted_in_resp.json()
    assert opted_in_body["total_episodes"] == 2  # both included
    assert opted_in_body["total_characters"] == 1000
    assert opted_in_body["include_mocked"] is True
    assert opted_in_body["has_mocked_data"] is True


def test_metrics_summary_with_no_episodes(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/metrics/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_episodes"] == 0
    assert body["total_characters"] == 0
    assert body["avg_duration_s"] is None
    assert body["episodes_by_status"] == []
    assert body["cost_by_stage"] == []
    assert body["avg_cost_per_episode_usd"] is None
    assert body["completion_rate"] is None
    assert body["d7_retention"] is None
    assert body["has_mocked_data"] is False
    assert len(body["daily_series"]) == 30
    assert body["quality_series"] == []


def test_metrics_summary_includes_quality_series(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        put_resp = client.put("/api/profile", json=_profile().model_dump(mode="json"))
        assert put_resp.status_code == 200

        with db.session_scope() as session:
            done = service.get_or_create_episode_record(session, "ep-quality", 1)
            done.status = "done"
            session.add(done)
            session.commit()

        quality_output = QualityOutput(
            episode_id="ep-quality",
            generated_at=datetime.now(timezone.utc),
            grounding=GroundingQuality(
                source_count=4,
                evergreen_share=0.25,
                fact_drift_flags=0,
                critique_flags=1,
                critique_rewrite_rate=0.1,
                stage_retries={"outline": False, "script": False, "critique": False, "perform": False},
                total_retries=0,
            ),
            naturalness=NaturalnessQuality(
                audio_tag_density_per_100_words=3.2,
                interjection_or_dash_share=0.4,
                host_balance=0.95,
                catchphrase_count=0,
            ),
            judge=QualityJudge(
                naturalness=JudgeScore(score=5, reason="very natural"),
                stance_clarity=JudgeScore(score=4, reason="clear"),
                model="gpt-4o-mini",
            ),
        )
        (paths.episode_dir("ep-quality") / "quality.json").write_text(
            quality_output.model_dump_json(), encoding="utf-8"
        )

        resp = client.get("/api/metrics/summary")

    body = resp.json()
    assert len(body["quality_series"]) == 1
    point = body["quality_series"][0]
    assert point["episode_id"] == "ep-quality"
    assert point["naturalness_score"] == 5
    assert point["stance_clarity_score"] == 4
    assert point["source_count"] == 4
    assert point["evergreen_share"] == 0.25
