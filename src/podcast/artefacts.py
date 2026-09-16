"""Pure helpers for loading/building persisted pipeline artefacts. Split out
of generate.py so both generate.py (CLI) and service.py (shared
orchestration) can use them without generate.py -> service.py ->
generate.py becoming a circular import.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from podcast.models import CritiqueOutput, Episode, OutlineOutput, PerformOutput, RankOutput, ScriptOutput


def new_episode_id(now: datetime | None = None) -> str:
    """Second-resolution timestamp (sortable, human-readable) plus a short
    random suffix. The suffix wasn't needed for the original CLI-only usage
    (one run per invocation), but POST /episodes can fire faster than one a
    second — without it, two requests in the same second would collide on
    the same episode_id and silently become one episode."""
    timestamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(3)}"


def load_episode_manifest(episode_dir_path: Path) -> Episode:
    manifest_path = episode_dir_path / "episode.json"
    return Episode.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def load_rank_output(dir_path: Path) -> RankOutput:
    ranked_path = dir_path / "ranked.json"
    return RankOutput.model_validate_json(ranked_path.read_text(encoding="utf-8"))


def load_outline_output(dir_path: Path) -> OutlineOutput:
    outline_path = dir_path / "outline.json"
    return OutlineOutput.model_validate_json(outline_path.read_text(encoding="utf-8"))


def load_script_output(dir_path: Path) -> ScriptOutput:
    script_path = dir_path / "script.json"
    return ScriptOutput.model_validate_json(script_path.read_text(encoding="utf-8"))


def load_critique_output(dir_path: Path) -> CritiqueOutput:
    critique_path = dir_path / "critique.json"
    return CritiqueOutput.model_validate_json(critique_path.read_text(encoding="utf-8"))


def load_perform_output(dir_path: Path) -> PerformOutput:
    perform_path = dir_path / "performance.json"
    return PerformOutput.model_validate_json(perform_path.read_text(encoding="utf-8"))
