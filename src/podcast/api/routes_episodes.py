"""POST /episodes (triggers a background generation run), GET /episodes,
GET /episodes/{id} (status + script + show notes), GET /episodes/{id}/audio,
POST /episodes/{id}/events (dashboard playback events)."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session, select

from podcast import db, service
from podcast.api.schemas import (
    EpisodeCreateRequest,
    EpisodeCreateResponse,
    EpisodeDetail,
    EpisodeSummary,
    EventIn,
    EventOut,
    ShowNoteItem,
)
from podcast.artefacts import new_episode_id
from podcast.models import CritiqueOutput, Profile, RankOutput, Script, ScriptOutput
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_summary(record: db.EpisodeRecord) -> EpisodeSummary:
    return EpisodeSummary(
        episode_id=record.episode_id,
        status=record.status,
        stage_reached=record.stage_reached,
        created_at=record.created_at,
        duration_s=record.duration_s,
        total_characters=record.total_characters,
        cost_estimate_usd=record.cost_estimate_usd,
        no_content_interests=record.no_content_interests,
    )


def _load_script_and_notes(episode_id: str) -> tuple[Script | None, list[ShowNoteItem]]:
    """Reads whatever artefacts the episode has reached so far — None/[] for
    a stage that hasn't run yet, not an error."""
    dir_path = episode_dir(episode_id)

    script: Script | None = None
    critique_path = dir_path / "critique.json"
    script_path = dir_path / "script.json"
    if critique_path.exists():
        script = CritiqueOutput.model_validate_json(critique_path.read_text(encoding="utf-8")).revised_script
    elif script_path.exists():
        script = ScriptOutput.model_validate_json(script_path.read_text(encoding="utf-8")).script

    show_notes: list[ShowNoteItem] = []
    ranked_path = dir_path / "ranked.json"
    if ranked_path.exists():
        rank_output = RankOutput.model_validate_json(ranked_path.read_text(encoding="utf-8"))
        show_notes = [ShowNoteItem(title=a.title, url=str(a.url), source=a.source) for a in rank_output.selected]

    return script, show_notes


def _run_episode_task(profile: Profile, profile_id: int, episode_id: str, until: str | None) -> None:
    """Background-task wrapper. call_stage() has already recorded a stage
    failure (status/stage_reached/`failed` event) before re-raising, so the
    DB is correct either way — this just stops the exception from
    propagating into the ASGI server's own unhandled-exception handling.
    See docs/decisions.md ("Backend...") for why the CLI, unlike this
    wrapper, lets the same exception crash the process."""
    try:
        service.run_episode(profile, profile_id, episode_id, until)
    except Exception:
        logger.exception("episode %s: background run failed", episode_id)


@router.post("/episodes", response_model=EpisodeCreateResponse, status_code=202)
def create_episode(
    body: EpisodeCreateRequest,
    background_tasks: BackgroundTasks,
    session: Session = Depends(db.get_session),
) -> EpisodeCreateResponse:
    profile_row = session.get(db.ProfileRecord, 1)
    if profile_row is None:
        raise HTTPException(status_code=404, detail="no profile configured yet — PUT /profile first")

    profile = Profile.model_validate(profile_row.data)
    episode_id = new_episode_id()
    # Created synchronously so it's immediately visible to GET /episodes,
    # before the background task has done anything.
    service.get_or_create_episode_record(session, episode_id, profile_row.id)

    background_tasks.add_task(_run_episode_task, profile, profile_row.id, episode_id, body.until)
    return EpisodeCreateResponse(episode_id=episode_id, status="pending")


@router.get("/episodes", response_model=list[EpisodeSummary])
def list_episodes(session: Session = Depends(db.get_session)) -> list[EpisodeSummary]:
    rows = session.exec(select(db.EpisodeRecord).order_by(db.EpisodeRecord.created_at.desc())).all()
    return [_to_summary(r) for r in rows]


@router.get("/episodes/{episode_id}", response_model=EpisodeDetail)
def get_episode(episode_id: str, session: Session = Depends(db.get_session)) -> EpisodeDetail:
    record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()
    if record is None:
        raise HTTPException(status_code=404, detail="unknown episode")
    script, show_notes = _load_script_and_notes(episode_id)
    return EpisodeDetail(**_to_summary(record).model_dump(), script=script, show_notes=show_notes)


@router.get("/episodes/{episode_id}/audio")
def get_episode_audio(episode_id: str, session: Session = Depends(db.get_session)) -> FileResponse:
    record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()
    if record is None or not record.audio_path:
        raise HTTPException(status_code=404, detail="no audio for this episode yet")
    audio_path = Path(record.audio_path)
    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="audio file missing on disk")
    return FileResponse(audio_path, media_type="audio/mpeg")


@router.post("/episodes/{episode_id}/events", response_model=EventOut, status_code=201)
def post_episode_event(episode_id: str, body: EventIn, session: Session = Depends(db.get_session)) -> EventOut:
    record = session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.episode_id == episode_id)).first()
    if record is None:
        raise HTTPException(status_code=404, detail="unknown episode")
    event = service.emit_event(session, episode_id, body.type, body.metadata)
    return EventOut(
        id=event.id, episode_id=event.episode_id, type=event.type, ts=event.ts, metadata=event.metadata_json or {}
    )
