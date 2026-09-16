"""Tests for GET /hosts/presets."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

from podcast import db, paths
from podcast.api.app import app
from podcast.host_presets import load_host_presets


def _configure_test_db(monkeypatch, tmp_path: Path) -> None:
    test_engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)
    monkeypatch.setenv("PODCAST_PROFILE_PATH", str(tmp_path / "no-such-profile.yaml"))


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "EPISODES_DIR", tmp_path / "episodes")


def test_host_presets_returns_the_real_presets_directory(tmp_path, monkeypatch):
    _configure_test_db(monkeypatch, tmp_path)
    _patch_episode_dir(monkeypatch, tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/hosts/presets")

    assert resp.status_code == 200
    body = resp.json()
    expected = load_host_presets()
    assert [h["name"] for h in body] == [h.name for h in expected]
    assert body[0]["persona"] == expected[0].persona
