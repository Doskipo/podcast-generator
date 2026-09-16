"""Tests for GET/PUT /profile."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, select

from podcast import db, service
from podcast.api.app import app
from podcast.models import Host, Interest, Listener, PodcastSettings, Profile, Style


def _profile(name: str = "Test") -> Profile:
    return Profile(
        name=name,
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


def test_get_profile_404_before_any_put(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/profile")
    assert resp.status_code == 404


def test_put_then_get_profile_round_trips(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    payload = _profile("Roundtrip").model_dump(mode="json")

    with TestClient(app) as client:
        put_resp = client.put("/api/profile", json=payload)
        assert put_resp.status_code == 200
        assert put_resp.json()["name"] == "Roundtrip"

        get_resp = client.get("/api/profile")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Roundtrip"


def test_put_profile_twice_updates_the_same_row(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    with TestClient(app) as client:
        client.put("/api/profile", json=_profile("First").model_dump(mode="json"))
        client.put("/api/profile", json=_profile("Second").model_dump(mode="json"))

        get_resp = client.get("/api/profile")
        assert get_resp.json()["name"] == "Second"

    # Sanity: exactly one row in the table regardless of how many PUTs happened.
    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1


def test_put_profile_with_wrong_host_count_is_422(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    payload = _profile().model_dump(mode="json")
    payload["podcast"]["hosts"] = payload["podcast"]["hosts"][:1]

    with TestClient(app) as client:
        resp = client.put("/api/profile", json=payload)

    assert resp.status_code == 422
    assert "exactly two hosts" in resp.text

    # Rejected — nothing was saved.
    with TestClient(app) as client:
        assert client.get("/api/profile").status_code == 404


def test_put_profile_with_blank_voice_id_is_422(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    payload = _profile().model_dump(mode="json")
    payload["podcast"]["hosts"][0]["voice_id"] = "  "

    with TestClient(app) as client:
        resp = client.put("/api/profile", json=payload)

    assert resp.status_code == 422
    assert "voice_id" in resp.text


def test_app_startup_seeds_profile_from_podcast_profile_path_env_var(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile_path = tmp_path / "seed.yaml"
    _profile("Seeded On Startup").to_yaml(profile_path)
    monkeypatch.setenv("PODCAST_PROFILE_PATH", str(profile_path))

    with TestClient(app) as client:
        resp = client.get("/api/profile")

    assert resp.status_code == 200
    assert resp.json()["name"] == "Seeded On Startup"


def test_app_startup_does_not_reseed_over_an_existing_profile(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile_path = tmp_path / "seed.yaml"
    _profile("Would Overwrite").to_yaml(profile_path)
    monkeypatch.setenv("PODCAST_PROFILE_PATH", str(profile_path))

    with db.session_scope() as session:
        service.upsert_profile(_profile("Already saved"), session)

    with TestClient(app) as client:
        resp = client.get("/api/profile")

    assert resp.json()["name"] == "Already saved"
