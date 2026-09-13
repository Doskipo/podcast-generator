"""GET /schedule/next — the scheduler's own next fire time for the
configured cron expression."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session

from podcast import db, scheduler
from podcast.api.schemas import NextRunOut

router = APIRouter()


@router.get("/schedule/next", response_model=NextRunOut)
def schedule_next(request: Request, session: Session = Depends(db.get_session)) -> NextRunOut:
    row = session.get(db.ProfileRecord, 1)
    if row is None:
        raise HTTPException(status_code=404, detail="no profile configured yet")
    cron = row.data.get("schedule", "0 7 * * *")
    job = request.app.state.scheduler.get_job(scheduler.JOB_ID)
    next_run_at = job.next_run_time if job else None
    return NextRunOut(cron=cron, next_run_at=next_run_at)
