"""Filesystem layout for episode artefacts."""

from __future__ import annotations

from pathlib import Path

DATA_DIR = Path("data")
EPISODES_DIR = DATA_DIR / "episodes"
# Cross-episode, cross-profile-run state (unlike everything else under
# data/, which is per-episode) — the evergreen fallback's anti-repeat
# memory. See podcast.evergreen and docs/decisions.md ("Evergreen
# fallback").
EVERGREEN_CACHE_PATH = DATA_DIR / "evergreen_cache.json"


def episode_dir(episode_id: str) -> Path:
    """Return data/episodes/<episode_id>/, creating it if needed."""
    d = EPISODES_DIR / episode_id
    d.mkdir(parents=True, exist_ok=True)
    return d
