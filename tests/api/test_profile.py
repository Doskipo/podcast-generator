"""Tests for GET/PUT /profile."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine, select

from podcast import db
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


def test_get_profile_404_before_any_put(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/profile")
    assert resp.status_code == 404


def test_put_then_get_profile_round_trips(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    payload = _profile("Roundtrip").model_dump(mode="json")

    with TestClient(app) as client:
        put_resp = client.put("/profile", json=payload)
        assert put_resp.status_code == 200
        assert put_resp.json()["name"] == "Roundtrip"

        get_resp = client.get("/profile")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == "Roundtrip"


def test_put_profile_twice_updates_the_same_row(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    with TestClient(app) as client:
        client.put("/profile", json=_profile("First").model_dump(mode="json"))
        client.put("/profile", json=_profile("Second").model_dump(mode="json"))

        get_resp = client.get("/profile")
        assert get_resp.json()["name"] == "Second"

    # Sanity: exactly one row in the table regardless of how many PUTs happened.
    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1
