"""Smoke tests for generate.py's thin CLI wiring. No network, no real DB
work beyond what service.upsert_profile_from_yaml itself does against a
test SQLite file — the actual pipeline orchestration (stage sequencing,
--until, event emission, failure handling) is exercised in
tests/test_service.py, not here, since generate.py now just loads/derives
inputs and delegates to podcast.service.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine, select

from podcast import db, generate as generate_module, service
from podcast.models import Host, Interest, Listener, PodcastSettings, Profile, Style


def _write_profile(path: Path) -> None:
    profile = Profile(
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
    profile.to_yaml(path)


def _configure_test_db(monkeypatch, tmp_path: Path) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)


def test_run_upserts_profile_and_delegates_to_service(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    _configure_test_db(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "ensure_interest_queries", lambda profile, path: False)

    calls: list[tuple] = []

    def fake_run_episode(profile, profile_id, episode_id=None, until=None):
        calls.append((profile.name, profile_id, episode_id, until))
        return "the-episode-id"

    monkeypatch.setattr(service, "run_episode", fake_run_episode)

    result = generate_module.run(str(profile_path), episode_id="ep1", until="rank")

    assert result == "the-episode-id"
    assert len(calls) == 1
    name, profile_id, episode_id, until = calls[0]
    assert name == "Test"
    assert episode_id == "ep1"
    assert until == "rank"

    # The profile was upserted into the DB with the id passed through to run_episode.
    with db.session_scope() as session:
        row = session.get(db.ProfileRecord, profile_id)
    assert row is not None
    assert row.name == "Test"


def test_run_caches_generated_search_queries(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    _configure_test_db(monkeypatch, tmp_path)
    monkeypatch.setattr(service, "run_episode", lambda *a, **k: "ep")

    called = {}

    def fake_ensure_interest_queries(profile, path):
        called["path"] = path
        return True

    monkeypatch.setattr(generate_module, "ensure_interest_queries", fake_ensure_interest_queries)

    generate_module.run(str(profile_path))

    assert called["path"] == str(profile_path)


def test_run_lets_a_stage_failure_propagate(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    _configure_test_db(monkeypatch, tmp_path)
    monkeypatch.setattr(generate_module, "ensure_interest_queries", lambda profile, path: False)

    def fake_run_episode(profile, profile_id, episode_id=None, until=None):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(service, "run_episode", fake_run_episode)

    with pytest.raises(RuntimeError, match="stage exploded"):
        generate_module.run(str(profile_path))


def test_import_profile_overwrites_the_db_profile(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    _configure_test_db(monkeypatch, tmp_path)

    with db.session_scope() as session:
        service.upsert_profile(Profile.from_yaml(profile_path).model_copy(update={"name": "Old"}), session)

    monkeypatch.setattr("sys.argv", ["podcast", "import-profile", str(profile_path)])
    generate_module.import_profile()

    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1
    assert rows[0].name == "Test"  # overwritten from the YAML, not "Old"

    out = capsys.readouterr().out
    assert "Test" in out
    assert str(profile_path) in out


def test_main_dispatches_import_profile(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    _configure_test_db(monkeypatch, tmp_path)

    called = {}
    monkeypatch.setattr(generate_module, "import_profile", lambda: called.setdefault("ran", True))
    monkeypatch.setattr("sys.argv", ["podcast", "import-profile", str(profile_path)])

    generate_module.main()

    assert called.get("ran") is True
