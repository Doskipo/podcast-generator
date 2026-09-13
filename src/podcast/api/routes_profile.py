"""GET/PUT /profile. The DB row (id=1, single-user app) is the API's source
of truth — PUT never writes back to a YAML file. See docs/decisions.md."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from podcast import db, scheduler, service
from podcast.models import Profile

router = APIRouter()


@router.get("/profile", response_model=Profile)
def get_profile(session: Session = Depends(db.get_session)) -> Profile:
    row = session.get(db.ProfileRecord, 1)
    if row is None:
        raise HTTPException(status_code=404, detail="no profile configured yet")
    return Profile.model_validate(row.data)


@router.put("/profile", response_model=Profile)
def put_profile(profile: Profile, request: Request, session: Session = Depends(db.get_session)) -> Profile:
    row = service.upsert_profile(profile, session)
    # Reschedule live so a changed cron expression takes effect immediately,
    # without restarting the app.
    scheduler.schedule_from_profile(request.app.state.scheduler, profile, row.id)
    return Profile.model_validate(row.data)
