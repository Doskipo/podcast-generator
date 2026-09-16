"""GET /metrics/summary — aggregate counts/costs over the episodes table,
extended with the richer dashboard breakdown from podcast.metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from podcast import db, metrics
from podcast.api.schemas import (
    DailyPointOut,
    EpisodesByStatusOut,
    MetricsSummary,
    RecentFailureOut,
    StageCostOut,
    StageDurationOut,
    TopicCountOut,
)

router = APIRouter()


@router.get("/metrics/summary", response_model=MetricsSummary)
def metrics_summary(include_mocked: bool = False, session: Session = Depends(db.get_session)) -> MetricsSummary:
    # Original counts/avg-duration queries — same include_mocked filter as
    # podcast.metrics.aggregate_summary below, so every number on the
    # response reflects one consistent mode. See docs/decisions.md
    # ("Re-measured words-per-minute, dashboard mocked-data toggle").
    mocked_filter = () if include_mocked else (db.EpisodeRecord.mocked.is_(False),)

    total = session.exec(select(func.count()).select_from(db.EpisodeRecord).where(*mocked_filter)).one()
    done = session.exec(
        select(func.count()).select_from(db.EpisodeRecord).where(db.EpisodeRecord.status == "done", *mocked_filter)
    ).one()
    failed = session.exec(
        select(func.count()).select_from(db.EpisodeRecord).where(db.EpisodeRecord.status == "failed", *mocked_filter)
    ).one()
    total_characters = session.exec(
        select(func.coalesce(func.sum(db.EpisodeRecord.total_characters), 0)).where(*mocked_filter)
    ).one()
    total_cost = session.exec(
        select(func.coalesce(func.sum(db.EpisodeRecord.cost_estimate_usd), 0.0)).where(*mocked_filter)
    ).one()
    avg_duration = session.exec(
        select(func.avg(db.EpisodeRecord.duration_s)).where(db.EpisodeRecord.status == "done", *mocked_filter)
    ).one()

    extended = metrics.aggregate_summary(session, include_mocked=include_mocked)

    return MetricsSummary(
        total_episodes=total,
        done=done,
        failed=failed,
        pending_or_running=total - done - failed,
        total_characters=total_characters or 0,
        total_cost_estimate_usd=round(total_cost or 0.0, 4),
        avg_duration_s=round(avg_duration, 2) if avg_duration is not None else None,
        no_content=extended.no_content,
        episodes_by_status=[EpisodesByStatusOut(status=s.status, count=s.count) for s in extended.episodes_by_status],
        cost_by_stage=[
            StageCostOut(stage=c.stage, provider=c.provider, cost_usd=c.cost_usd) for c in extended.cost_by_stage
        ],
        avg_cost_per_episode_usd=extended.avg_cost_per_episode_usd,
        avg_stage_duration_s=[
            StageDurationOut(stage=d.stage, avg_elapsed_s=d.avg_elapsed_s, count=d.count)
            for d in extended.avg_stage_duration_s
        ],
        topic_distribution=[TopicCountOut(interest=t.interest, count=t.count) for t in extended.topic_distribution],
        plays_total=extended.plays_total,
        completions_total=extended.completions_total,
        completion_rate=extended.completion_rate,
        d7_retention=extended.d7_retention,
        interests_with_no_content=[
            TopicCountOut(interest=t.interest, count=t.count) for t in extended.interests_with_no_content
        ],
        daily_series=[
            DailyPointOut(
                date=d.date, episodes_created=d.episodes_created, plays=d.plays, completions=d.completions, mocked=d.mocked
            )
            for d in extended.daily_series
        ],
        recent_failures=[
            RecentFailureOut(
                episode_id=f.episode_id,
                status=f.status,
                stage_reached=f.stage_reached,
                reason=f.reason,
                created_at=f.created_at,
                mocked=f.mocked,
            )
            for f in extended.recent_failures
        ],
        has_mocked_data=extended.has_mocked_data,
        include_mocked=include_mocked,
    )
