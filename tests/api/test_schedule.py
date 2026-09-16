"""Tests for GET /schedule/next."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from podcast import db, paths
from podcast.api.app import app
from podcast.models import Host, Interest, Listener, PodcastSettings, Profile, Style


def _profile(schedule: str = "0 7 * * *") -> Profile:
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
        schedule=schedule,
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


def test_schedule_next_404_before_any_profile(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/schedule/next")
    assert resp.status_code == 404


def test_schedule_next_reflects_default_daily_07_00(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        client.put("/api/profile", json=_profile().model_dump(mode="json"))
        resp = client.get("/api/schedule/next")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cron"] == "0 7 * * *"
    next_run_at = body["next_run_at"]
    assert next_run_at is not None
    assert next_run_at.endswith("07:00:00Z") or "07:00:00" in next_run_at


def test_schedule_next_updates_after_put_profile_changes_cron(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        client.put("/api/profile", json=_profile("0 7 * * *").model_dump(mode="json"))
        first = client.get("/api/schedule/next").json()

        client.put("/api/profile", json=_profile("30 9 * * *").model_dump(mode="json"))
        second = client.get("/api/schedule/next").json()

    assert first["cron"] == "0 7 * * *"
    assert second["cron"] == "30 9 * * *"
    assert "09:30:00" in second["next_run_at"]
