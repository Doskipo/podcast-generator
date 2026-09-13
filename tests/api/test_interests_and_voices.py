"""Tests for POST /interests/suggest and GET /voices."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from podcast import db, paths
from podcast.api import routes_interests
from podcast.api.app import app
from podcast.api.voices import VOICE_CATALOG
from podcast.models import Host, Interest, InterestSuggestion, Listener, PodcastSettings, Profile, Style


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


def _fake_suggest_interest(topic, description, model):
    return InterestSuggestion(description=f"about {topic}", queries=[f"{topic} news", f"{topic} update"])


def test_voices_returns_the_fixed_catalog(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/voices")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(VOICE_CATALOG)
    assert body[0]["voice_id"] == VOICE_CATALOG[0].voice_id
    assert body[0]["label"] == VOICE_CATALOG[0].label


def test_suggest_interest_without_a_profile_uses_default_model(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    captured = {}

    def fake(topic, description, model):
        captured["model"] = model
        return _fake_suggest_interest(topic, description, model)

    monkeypatch.setattr(routes_interests, "suggest_interest", fake)

    with TestClient(app) as client:
        resp = client.post("/interests/suggest", json={"topic": "calisthenics"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "about calisthenics"
    assert body["queries"] == ["calisthenics news", "calisthenics update"]
    assert captured["model"] == "gpt-4o-mini"  # LLMSettings() default, no profile row yet


def test_suggest_interest_uses_the_saved_profile_llm_model(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    captured = {}

    def fake(topic, description, model):
        captured["model"] = model
        captured["description_in"] = description
        return _fake_suggest_interest(topic, description, model)

    monkeypatch.setattr(routes_interests, "suggest_interest", fake)

    with TestClient(app) as client:
        client.put("/profile", json=_profile().model_dump(mode="json"))
        resp = client.post("/interests/suggest", json={"topic": "music", "description": "rough draft"})

    assert resp.status_code == 200
    assert captured["model"] == "gpt-4o-mini"  # matches this profile's llm.model default too, but via the row now
    assert captured["description_in"] == "rough draft"
