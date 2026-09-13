"""GET /metrics/summary — aggregate counts/costs over the episodes table."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from podcast import db
from podcast.api.schemas import MetricsSummary

router = APIRouter()


@router.get("/metrics/summary", response_model=MetricsSummary)
def metrics_summary(session: Session = Depends(db.get_session)) -> MetricsSummary:
    total = session.exec(select(func.count()).select_from(db.EpisodeRecord)).one()
    done = session.exec(
        select(func.count()).select_from(db.EpisodeRecord).where(db.EpisodeRecord.status == "done")
    ).one()
    failed = session.exec(
        select(func.count()).select_from(db.EpisodeRecord).where(db.EpisodeRecord.status == "failed")
    ).one()
    total_characters = session.exec(select(func.coalesce(func.sum(db.EpisodeRecord.total_characters), 0))).one()
    total_cost = session.exec(select(func.coalesce(func.sum(db.EpisodeRecord.cost_estimate_usd), 0.0))).one()
    avg_duration = session.exec(
        select(func.avg(db.EpisodeRecord.duration_s)).where(db.EpisodeRecord.status == "done")
    ).one()

    return MetricsSummary(
        total_episodes=total,
        done=done,
        failed=failed,
        pending_or_running=total - done - failed,
        total_characters=total_characters or 0,
        total_cost_estimate_usd=round(total_cost or 0.0, 4),
        avg_duration_s=round(avg_duration, 2) if avg_duration is not None else None,
    )
