"""Shared orchestration layer: the one place that sequences pipeline stages,
persists DB state, and emits events. Both generate.py (CLI) and the API
import this module to trigger/resume a run — see docs/decisions.md
("Backend: SQLite + FastAPI + APScheduler") for why this seam exists.

Failure handling: `call_stage` always records a stage failure (episode
status, stage_reached, a `failed` event) before re-raising. Nothing in this
module catches that exception further — `run_episode`/`resume_from_*`
propagate it to their caller. The CLI (generate.py) lets it crash the
process with a traceback, exactly like before this backend existed; the
API's background-task wrapper (podcast/api/routes_episodes.py) is the one
place that catches it, so a failed episode doesn't crash/spam the ASGI
server. The DB row ends up correct either way.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

from sqlmodel import Session, select

from podcast import db
from podcast.artefacts import load_episode_manifest, new_episode_id, tts_script_output
from podcast.models import (
    Article,
    CritiqueOutput,
    Episode,
    FetchOutput,
    OutlineOutput,
    Profile,
    RankOutput,
    ScriptOutput,
    StitchOutput,
    TTSOutput,
)
from podcast.paths import episode_dir
from podcast.stages.critique import critique_stage
from podcast.stages.fetch import fetch_stage
from podcast.stages.outline import outline_stage
from podcast.stages.rank import rank_stage
from podcast.stages.script import script_stage
from podcast.stages.stitch import stitch_stage
from podcast.stages.tts import tts_stage

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Illustrative placeholder — no real ElevenLabs pricing lives anywhere in
# this codebase (see docs/decisions.md). Do not treat as an accurate bill.
COST_PER_1K_CHARS_USD = 0.18


def upsert_profile(profile: Profile, session: Session) -> db.ProfileRecord:
    """Get-or-create the single profiles row (id=1 — single-user app; see
    docs/decisions.md for what changes at scale) and overwrite its
    name/data/updated_at from `profile`."""
    row = session.get(db.ProfileRecord, 1)
    now = datetime.now(timezone.utc)
    if row is None:
        row = db.ProfileRecord(
            id=1, name=profile.name, data=profile.model_dump(mode="json"), created_at=now, updated_at=now
        )
    else:
        row.name = profile.name
        row.data = profile.model_dump(mode="json")
        row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# Same call, kept as a distinct name for readability at the CLI's call
# site (loading a profile from YAML) vs. the API's (an already-in-memory
# Profile from a PUT /profile body) — both just upsert.
upsert_profile_from_yaml = upsert_profile


def get_or_create_episode_record(session: Session, episode_id: str, profile_id: int) -> db.EpisodeRecord:
    row = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()
    if row is None:
        row = db.EpisodeRecord(
            episode_id=episode_id, profile_id=profile_id, created_at=datetime.now(timezone.utc)
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


def emit_event(session: Session, episode_id: str, type_: str, metadata: dict) -> db.EventRecord:
    event = db.EventRecord(episode_id=episode_id, type=type_, ts=datetime.now(timezone.utc), metadata_json=metadata)
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def call_stage(
    session: Session,
    record: db.EpisodeRecord,
    stage_name: str,
    fn: Callable[[], T],
    extra_metadata: Callable[[T], dict] | None = None,
) -> T:
    """Run one stage call (`fn` is a zero-arg callable wrapping the actual
    stage function + its arguments). Updates `record` and emits an event
    either way; re-raises on failure so the caller decides whether that
    crashes (CLI) or is caught (API)."""
    start = time.monotonic()
    try:
        result = fn()
    except Exception as exc:
        record.status = "failed"
        record.stage_reached = stage_name
        session.add(record)
        session.commit()
        emit_event(session, record.episode_id, "failed", {"stage": stage_name, "error": str(exc)})
        logger.exception("episode %s: stage %s failed", record.episode_id, stage_name)
        raise

    elapsed = time.monotonic() - start
    record.stage_reached = stage_name
    if isinstance(result, TTSOutput):
        record.total_characters = result.total_characters
    session.add(record)
    session.commit()

    metadata = {"stage": stage_name, "elapsed_s": round(elapsed, 2)}
    if extra_metadata is not None:
        metadata.update(extra_metadata(result))
    emit_event(session, record.episode_id, "stage_done", metadata)
    return result


def _finish(session: Session, record: db.EpisodeRecord, episode_id: str, start: float, stitch_output: StitchOutput) -> None:
    elapsed = time.monotonic() - start
    record.status = "done"
    record.duration_s = round(elapsed, 2)
    record.cost_estimate_usd = round((record.total_characters or 0) / 1000 * COST_PER_1K_CHARS_USD, 4)
    record.audio_path = str(episode_dir(episode_id) / stitch_output.audio_file)
    session.add(record)
    session.commit()
    emit_event(
        session, episode_id, "completed", {"duration_s": record.duration_s, "audio_file": stitch_output.audio_file}
    )


def _step_rank(
    session: Session, record: db.EpisodeRecord, episode: Episode, fetch_output: FetchOutput, until: str | None
) -> RankOutput | None:
    rank_output = call_stage(
        session,
        record,
        "rank",
        lambda: rank_stage(episode, fetch_output),
        extra_metadata=lambda r: {"selected": len(r.selected), "backfilled": r.backfilled, "total_budget": r.total_budget},
    )
    print(
        f"episode {episode.episode_id}: ranked {len(rank_output.selected)} "
        f"of budget {rank_output.total_budget} articles ({rank_output.backfilled} backfilled)"
    )
    return None if until == "rank" else rank_output


def _step_outline(
    session: Session, record: db.EpisodeRecord, episode: Episode, rank_output: RankOutput, until: str | None
) -> OutlineOutput | None:
    outline_output = call_stage(
        session,
        record,
        "outline",
        lambda: outline_stage(episode, rank_output),
        extra_metadata=lambda r: {"stories": len(r.outline.stories)},
    )
    print(f"episode {episode.episode_id}: outlined {len(outline_output.outline.stories)} stories")
    return None if until == "outline" else outline_output


def _step_script(
    session: Session,
    record: db.EpisodeRecord,
    episode: Episode,
    outline_output: OutlineOutput,
    articles: list[Article],
    until: str | None,
) -> ScriptOutput | None:
    script_output = call_stage(
        session,
        record,
        "script",
        lambda: script_stage(episode, outline_output, articles),
        extra_metadata=lambda r: {"segments": len(r.script.segments)},
    )
    print(f"episode {episode.episode_id}: wrote script with {len(script_output.script.segments)} segments")
    return None if until == "script" else script_output


def _step_critique(
    session: Session,
    record: db.EpisodeRecord,
    episode: Episode,
    script_output: ScriptOutput,
    articles: list[Article],
    outline_output: OutlineOutput,
    until: str | None,
) -> CritiqueOutput | None:
    critique_output = call_stage(
        session,
        record,
        "critique",
        lambda: critique_stage(episode, script_output, articles, outline_output),
        extra_metadata=lambda r: {
            "flags": len(r.critique.flags),
            "total_words": r.total_words,
            "over_budget_segments": len(r.over_budget_segments),
            "terse_hosts": len(r.terse_hosts),
        },
    )
    print(
        f"episode {episode.episode_id}: critique flagged {len(critique_output.critique.flags)} line(s), "
        f"{critique_output.total_words} words total, "
        f"{len(critique_output.over_budget_segments)} segment(s) over budget, "
        f"{len(critique_output.terse_hosts)} host(s) too terse"
    )
    return None if until == "critique" else critique_output


def _run_outline_through_critique(
    session: Session, record: db.EpisodeRecord, episode: Episode, rank_output: RankOutput, until: str | None
) -> CritiqueOutput | None:
    """outline -> script -> critique, stopping early if `until` names one of
    those stages. Shared by every entrypoint that starts at or before
    outline."""
    outline_output = _step_outline(session, record, episode, rank_output, until)
    if outline_output is None:
        return None

    script_output = _step_script(session, record, episode, outline_output, rank_output.selected, until)
    if script_output is None:
        return None

    return _step_critique(session, record, episode, script_output, rank_output.selected, outline_output, until)


def _run_tts_and_stitch(
    session: Session, record: db.EpisodeRecord, episode: Episode, script_output: ScriptOutput, until: str | None
) -> tuple[TTSOutput, StitchOutput] | None:
    tts_output = call_stage(
        session,
        record,
        "tts",
        lambda: tts_stage(episode, script_output),
        extra_metadata=lambda r: {"lines": len(r.lines), "synthesis_mode": r.synthesis_mode, "characters": r.total_characters},
    )
    print(
        f"episode {episode.episode_id}: synthesized {len(tts_output.lines)} lines via "
        f"{tts_output.synthesis_mode} mode ({tts_output.total_characters} characters)"
    )
    if until == "tts":
        return None

    stitch_output = call_stage(
        session,
        record,
        "stitch",
        lambda: stitch_stage(episode, tts_output),
        extra_metadata=lambda r: {"duration_ms": r.duration_ms, "audio_file": r.audio_file},
    )
    print(f"episode {episode.episode_id}: wrote {stitch_output.audio_file} ({stitch_output.duration_ms} ms)")
    return tts_output, stitch_output


def run_episode(profile: Profile, profile_id: int, episode_id: str | None = None, until: str | None = None) -> str:
    """The one function the CLI's run() and POST /episodes both call for a
    full fetch -> ... -> stitch run. Creates the episode.json manifest and
    the DB episode row, emits `generated`, then runs every stage via
    call_stage. `until` stops early, leaving status="running" (the run is
    genuinely incomplete, not a new terminal status)."""
    episode_id = episode_id or new_episode_id()
    with db.session_scope() as session:
        record = get_or_create_episode_record(session, episode_id, profile_id)

        manifest_path = episode_dir(episode_id) / "episode.json"
        if not manifest_path.exists():
            episode = Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)
            manifest_path.write_text(episode.model_dump_json(indent=2), encoding="utf-8")
        else:
            episode = load_episode_manifest(episode_dir(episode_id))

        record.status = "running"
        session.add(record)
        session.commit()
        emit_event(session, episode_id, "generated", {"profile": profile.name})
        start = time.monotonic()

        fetch_output = call_stage(
            session,
            record,
            "fetch",
            lambda: fetch_stage(profile, episode_id),
            extra_metadata=lambda r: {"articles": len(r.articles), "feeds_count": r.feeds_count},
        )
        print(
            f"episode {episode_id}: kept {len(fetch_output.articles)} candidate articles "
            f"from {fetch_output.feeds_count} feeds"
        )
        if until == "fetch":
            return episode_id

        rank_output = _step_rank(session, record, episode, fetch_output, until)
        if rank_output is None:
            return episode_id

        critique_output = _run_outline_through_critique(session, record, episode, rank_output, until)
        if critique_output is None:
            return episode_id

        result = _run_tts_and_stitch(session, record, episode, tts_script_output(critique_output), until)
        if result is None:
            return episode_id
        _, stitch_output = result

        _finish(session, record, episode_id, start, stitch_output)
    return episode_id


def resume_from_fetch(
    session: Session, record: db.EpisodeRecord, episode: Episode, fetch_output: FetchOutput, until: str | None
) -> str:
    """fetch_output already in hand (the CLI's --from-articles): rank ->
    outline -> script -> critique -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    rank_output = _step_rank(session, record, episode, fetch_output, until)
    if rank_output is None:
        return episode.episode_id

    critique_output = _run_outline_through_critique(session, record, episode, rank_output, until)
    if critique_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, tts_script_output(critique_output), until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_rank(
    session: Session, record: db.EpisodeRecord, episode: Episode, rank_output: RankOutput, until: str | None
) -> str:
    """rank_output already in hand: outline -> script -> critique -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    critique_output = _run_outline_through_critique(session, record, episode, rank_output, until)
    if critique_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, tts_script_output(critique_output), until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_outline(
    session: Session,
    record: db.EpisodeRecord,
    episode: Episode,
    outline_output: OutlineOutput,
    rank_output: RankOutput,
    until: str | None,
) -> str:
    """outline_output already in hand: script -> critique -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    script_output = _step_script(session, record, episode, outline_output, rank_output.selected, until)
    if script_output is None:
        return episode.episode_id

    critique_output = _step_critique(session, record, episode, script_output, rank_output.selected, outline_output, until)
    if critique_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, tts_script_output(critique_output), until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_script(
    session: Session,
    record: db.EpisodeRecord,
    episode: Episode,
    script_output: ScriptOutput,
    rank_output: RankOutput,
    outline_output: OutlineOutput,
    until: str | None,
) -> str:
    """script_output already in hand: critique -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    critique_output = _step_critique(session, record, episode, script_output, rank_output.selected, outline_output, until)
    if critique_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, tts_script_output(critique_output), until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_critique(
    session: Session, record: db.EpisodeRecord, episode: Episode, critique_output: CritiqueOutput, until: str | None
) -> str:
    """critique_output already in hand: tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    result = _run_tts_and_stitch(session, record, episode, tts_script_output(critique_output), until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id
