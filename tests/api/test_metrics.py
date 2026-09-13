"""Tests for GET /metrics/summary."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from podcast import db, paths, service
from podcast.api.app import app
from podcast.models import Host, Interest, Listener, PodcastSettings, Profile, Style


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
        put_resp = client.put("/profile", json=_profile().model_dump(mode="json"))
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

        resp = client.get("/metrics/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_episodes"] == 2
    assert body["done"] == 1
    assert body["failed"] == 1
    assert body["pending_or_running"] == 0
    assert body["total_characters"] == 1000
    assert body["total_cost_estimate_usd"] == 0.18
    assert body["avg_duration_s"] == 10.0


def test_metrics_summary_with_no_episodes(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/metrics/summary")

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_episodes"] == 0
    assert body["total_characters"] == 0
    assert body["avg_duration_s"] is None
