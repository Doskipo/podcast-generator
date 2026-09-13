"""FastAPI app entrypoint. Run with:
    uv run uvicorn podcast.api.app:app --reload --port 8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from sqlmodel import select

from podcast import db, scheduler
from podcast.api import routes_episodes, routes_metrics, routes_profile, routes_schedule
from podcast.models import Profile


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    db.init_db()

    sched = scheduler.create_scheduler()
    with db.session_scope() as session:
        row = session.exec(select(db.ProfileRecord)).first()
    if row is not None:
        scheduler.schedule_from_profile(sched, Profile.model_validate(row.data), row.id)
    sched.start()
    app.state.scheduler = sched

    yield

    sched.shutdown()


app = FastAPI(title="podcast-generator API", lifespan=lifespan)
app.include_router(routes_profile.router)
app.include_router(routes_episodes.router)
app.include_router(routes_metrics.router)
app.include_router(routes_schedule.router)
