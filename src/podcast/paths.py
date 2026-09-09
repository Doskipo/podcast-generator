"""Filesystem layout for episode artefacts."""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path("data")
EPISODES_DIR = DATA_DIR / "episodes"


def episode_dir(episode_id: str) -> Path:
    """Return data/episodes/<episode_id>/, creating it if needed."""
    d = EPISODES_DIR / episode_id
    d.mkdir(parents=True, exist_ok=True)
    return d
