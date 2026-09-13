"""Tests for service.seed_profile_from_yaml_if_empty and
service.import_profile_overwrite — the startup-seed and
`uv run podcast import-profile` paths from docs/decisions.md ("Profile
seeding")."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine, select

from podcast import db, service
from podcast.models import Host, Interest, Listener, PodcastSettings, Profile, Style


def _profile(name: str = "Seeded") -> Profile:
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


def test_seed_from_yaml_if_empty_seeds_when_table_is_empty(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile_path = tmp_path / "profile.yaml"
    _profile("From YAML").to_yaml(profile_path)

    with db.session_scope() as session:
        row = service.seed_profile_from_yaml_if_empty(session, path=str(profile_path))

    assert row is not None
    assert row.name == "From YAML"

    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1
    assert rows[0].name == "From YAML"


def test_seed_from_yaml_if_empty_reads_env_var_when_no_path_given(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile_path = tmp_path / "profile.yaml"
    _profile("From Env Var").to_yaml(profile_path)
    monkeypatch.setenv("PODCAST_PROFILE_PATH", str(profile_path))

    with db.session_scope() as session:
        row = service.seed_profile_from_yaml_if_empty(session)

    assert row is not None
    assert row.name == "From Env Var"


def test_seed_from_yaml_if_empty_is_a_noop_when_a_profile_already_exists(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile_path = tmp_path / "profile.yaml"
    _profile("Would-be seed").to_yaml(profile_path)

    with db.session_scope() as session:
        service.upsert_profile(_profile("Already here"), session)
        row = service.seed_profile_from_yaml_if_empty(session, path=str(profile_path))

    assert row is None  # skipped — table wasn't empty
    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1
    assert rows[0].name == "Already here"  # untouched


def test_seed_from_yaml_if_empty_logs_and_skips_a_missing_file(tmp_path, monkeypatch, caplog):
    _configure_test_db(monkeypatch, tmp_path)

    with db.session_scope() as session:
        row = service.seed_profile_from_yaml_if_empty(session, path=str(tmp_path / "does-not-exist.yaml"))

    assert row is None
    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert rows == []  # nothing seeded, nothing raised


def test_seed_from_yaml_if_empty_logs_and_skips_an_invalid_profile(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    bad_path = tmp_path / "bad.yaml"
    bad_path.write_text("name: only a name, missing everything else required\n", encoding="utf-8")

    with db.session_scope() as session:
        row = service.seed_profile_from_yaml_if_empty(session, path=str(bad_path))

    assert row is None
    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert rows == []


def test_import_profile_overwrite_replaces_an_existing_profile(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    profile_path = tmp_path / "profile.yaml"
    _profile("Imported").to_yaml(profile_path)

    with db.session_scope() as session:
        service.upsert_profile(_profile("Old"), session)
        row = service.import_profile_overwrite(session, str(profile_path))

    assert row.name == "Imported"
    with db.session_scope() as session:
        rows = session.exec(select(db.ProfileRecord)).all()
    assert len(rows) == 1
    assert rows[0].name == "Imported"


def test_import_profile_overwrite_raises_on_a_missing_file(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)

    with db.session_scope() as session, pytest.raises(FileNotFoundError):
        service.import_profile_overwrite(session, str(tmp_path / "nope.yaml"))
