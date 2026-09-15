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
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TypeVar

import yaml
from pydantic import ValidationError
from sqlmodel import Session, select

from podcast import db
from podcast.artefacts import load_episode_manifest, new_episode_id
from podcast.models import (
    Article,
    CritiqueOutput,
    Episode,
    FetchOutput,
    OutlineOutput,
    PerformOutput,
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
from podcast.stages.perform import perform_stage
from podcast.stages.rank import rank_stage
from podcast.stages.script import script_stage
from podcast.stages.stitch import stitch_stage
from podcast.stages.tts import tts_stage

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Illustrative placeholder — no real ElevenLabs pricing lives anywhere in
# this codebase (see docs/decisions.md). Do not treat as an accurate bill.
COST_PER_1K_CHARS_USD = 0.18

# Env var read at seed time (not import time), so tests/tools can set it
# per-call via monkeypatch.setenv without needing to reimport this module.
PROFILE_PATH_ENV_VAR = "PODCAST_PROFILE_PATH"
DEFAULT_PROFILE_PATH = "profiles/eudald.yaml"


def seed_profile_from_yaml_if_empty(session: Session, path: str | None = None) -> db.ProfileRecord | None:
    """Called once from the API's startup lifespan (api/app.py): if the
    profiles table is empty, load a profile from `path` (or
    $PODCAST_PROFILE_PATH, default profiles/eudald.yaml) and seed it — a
    fresh data/podcast.db comes up already configured instead of GET
    /profile 404-ing until someone PUTs one by hand. Never overwrites an
    existing row; see import_profile_overwrite for that. Missing file or a
    profile that fails validation (e.g. the two-hosts rule) is logged and
    skipped, not raised — a bad default file shouldn't crash the app at
    startup. Returns the seeded row, or None if nothing was seeded."""
    if session.exec(select(db.ProfileRecord)).first() is not None:
        return None

    profile_path = path or os.environ.get(PROFILE_PATH_ENV_VAR, DEFAULT_PROFILE_PATH)
    try:
        profile = Profile.from_yaml(profile_path)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        logger.warning("startup: no profile in DB and could not seed from %r: %s", profile_path, exc)
        return None

    row = upsert_profile(profile, session)
    logger.info("startup: seeded profiles table from %s", profile_path)
    return row


def import_profile_overwrite(session: Session, path: str) -> db.ProfileRecord:
    """`uv run podcast import-profile <path>` — load a profile YAML and
    overwrite the DB's profiles row regardless of what's already there.
    Unlike seed_profile_from_yaml_if_empty, this is an explicit,
    user-triggered action: a missing file or a profile that fails
    validation raises straight through rather than being logged and
    skipped, so the CLI exits with a clear error instead of silently
    no-op'ing."""
    profile = Profile.from_yaml(path)
    return upsert_profile(profile, session)


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
        extra_metadata=lambda r: {
            "selected": len(r.selected),
            "backfilled": r.backfilled,
            "evergreen": r.evergreen_count,
            "total_budget": r.total_budget,
        },
    )
    print(
        f"episode {episode.episode_id}: ranked {len(rank_output.selected)} "
        f"of budget {rank_output.total_budget} articles "
        f"({rank_output.backfilled} backfilled, {rank_output.evergreen_count} evergreen)"
    )
    return None if until == "rank" else rank_output


def _check_grounding(session: Session, record: db.EpisodeRecord, episode: Episode, rank_output: RankOutput) -> bool:
    """After rank succeeds, refuse to continue building an episode from zero
    selected articles — handing outline/script nothing to ground on leaves
    them nothing to write about except invention (this exact failure mode —
    a dead placeholder feed leaving one interest, and the whole run, with
    zero candidates — is what this guard was added for; see
    docs/decisions.md, "Grounding guard"). `outline_stage` itself also
    refuses zero sources as a backstop, but stopping here means the episode
    gets an honest `no_content` status instead of a `failed` one, with the
    empty interests recorded for the UI to show. Returns False (and has
    already updated `record`/emitted the event) when there's nothing to
    build on; True otherwise."""
    if rank_output.selected:
        return True

    scored_topics = {ra.interest for ra in rank_output.scored if ra.interest}
    empty_interests = [i.topic for i in episode.profile.interests if i.topic not in scored_topics]

    record.status = "no_content"
    record.no_content_interests = empty_interests
    session.add(record)
    session.commit()
    emit_event(session, episode.episode_id, "no_content", {"interests": empty_interests})
    logger.warning(
        "episode %s: rank selected zero articles; interests with no candidates: %s",
        episode.episode_id,
        empty_interests,
    )
    return False


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


def _step_perform(
    session: Session,
    record: db.EpisodeRecord,
    episode: Episode,
    critique_output: CritiqueOutput,
    articles: list[Article],
    until: str | None,
) -> PerformOutput | None:
    perform_output = call_stage(
        session,
        record,
        "perform",
        lambda: perform_stage(episode, critique_output, articles),
        extra_metadata=lambda r: {
            "fact_flags": len(r.fact_flags),
            "cold_open_lines": len(r.performance.cold_open),
            "segments": len(r.performance.segments),
        },
    )
    print(
        f"episode {episode.episode_id}: performed script with "
        f"{len(perform_output.performance.cold_open)} cold-open line(s), "
        f"{len(perform_output.fact_flags)} fact-change flag(s)"
    )
    return None if until == "perform" else perform_output


def _run_outline_through_perform(
    session: Session, record: db.EpisodeRecord, episode: Episode, rank_output: RankOutput, until: str | None
) -> PerformOutput | None:
    """outline -> script -> critique -> perform, stopping early if `until`
    names one of those stages. Shared by every entrypoint that starts at or
    before outline."""
    outline_output = _step_outline(session, record, episode, rank_output, until)
    if outline_output is None:
        return None

    script_output = _step_script(session, record, episode, outline_output, rank_output.selected, until)
    if script_output is None:
        return None

    critique_output = _step_critique(session, record, episode, script_output, rank_output.selected, outline_output, until)
    if critique_output is None:
        return None

    return _step_perform(session, record, episode, critique_output, rank_output.selected, until)


def _run_tts_and_stitch(
    session: Session, record: db.EpisodeRecord, episode: Episode, perform_output: PerformOutput, until: str | None
) -> tuple[TTSOutput, StitchOutput] | None:
    tts_output = call_stage(
        session,
        record,
        "tts",
        lambda: tts_stage(episode, perform_output),
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
        if not _check_grounding(session, record, episode, rank_output):
            return episode_id

        perform_output = _run_outline_through_perform(session, record, episode, rank_output, until)
        if perform_output is None:
            return episode_id

        result = _run_tts_and_stitch(session, record, episode, perform_output, until)
        if result is None:
            return episode_id
        _, stitch_output = result

        _finish(session, record, episode_id, start, stitch_output)
    return episode_id


def resume_from_fetch(
    session: Session, record: db.EpisodeRecord, episode: Episode, fetch_output: FetchOutput, until: str | None
) -> str:
    """fetch_output already in hand (the CLI's --from-articles): rank ->
    outline -> script -> critique -> perform -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    rank_output = _step_rank(session, record, episode, fetch_output, until)
    if rank_output is None:
        return episode.episode_id
    if not _check_grounding(session, record, episode, rank_output):
        return episode.episode_id

    perform_output = _run_outline_through_perform(session, record, episode, rank_output, until)
    if perform_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, perform_output, until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_rank(
    session: Session, record: db.EpisodeRecord, episode: Episode, rank_output: RankOutput, until: str | None
) -> str:
    """rank_output already in hand: outline -> script -> critique -> perform -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    if not _check_grounding(session, record, episode, rank_output):
        return episode.episode_id

    perform_output = _run_outline_through_perform(session, record, episode, rank_output, until)
    if perform_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, perform_output, until)
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
    """outline_output already in hand: script -> critique -> perform -> tts -> stitch."""
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

    perform_output = _step_perform(session, record, episode, critique_output, rank_output.selected, until)
    if perform_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, perform_output, until)
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
    """script_output already in hand: critique -> perform -> tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    critique_output = _step_critique(session, record, episode, script_output, rank_output.selected, outline_output, until)
    if critique_output is None:
        return episode.episode_id

    perform_output = _step_perform(session, record, episode, critique_output, rank_output.selected, until)
    if perform_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, perform_output, until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_critique(
    session: Session,
    record: db.EpisodeRecord,
    episode: Episode,
    critique_output: CritiqueOutput,
    articles: list[Article],
    until: str | None,
) -> str:
    """critique_output already in hand: perform -> tts -> stitch. `articles`
    (the episode's selected/ranked articles) is needed by perform_stage to
    re-check grounding on the performed text."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    perform_output = _step_perform(session, record, episode, critique_output, articles, until)
    if perform_output is None:
        return episode.episode_id

    result = _run_tts_and_stitch(session, record, episode, perform_output, until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id


def resume_from_performance(
    session: Session, record: db.EpisodeRecord, episode: Episode, perform_output: PerformOutput, until: str | None
) -> str:
    """perform_output already in hand: tts -> stitch."""
    record.status = "running"
    session.add(record)
    session.commit()
    start = time.monotonic()

    result = _run_tts_and_stitch(session, record, episode, perform_output, until)
    if result is None:
        return episode.episode_id
    _, stitch_output = result

    _finish(session, record, episode.episode_id, start, stitch_output)
    return episode.episode_id
