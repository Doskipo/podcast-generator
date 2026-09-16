"""FastAPI app entrypoint. Run with:
    uv run podcast serve
or directly:
    uv run uvicorn podcast.api.app:app --reload --port 8000

Serves the built React SPA (web/dist/, built separately via `npm run build`
— this app never builds it) as static files when present; API-only
otherwise. See docs/ui.md for the dev-vs-prod serving split.

On startup, if the profiles table is empty, seeds it from
$PODCAST_PROFILE_PATH (default profiles/eudald.yaml) — see
service.seed_profile_from_yaml_if_empty and docs/decisions.md ("Profile
seeding"). `uv run podcast import-profile <path>` overwrites the DB's
profile unconditionally, seeding or not.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from podcast import db, scheduler, service
from podcast.api import (
    routes_episodes,
    routes_hosts,
    routes_interests,
    routes_metrics,
    routes_profile,
    routes_schedule,
    routes_voices,
)
from podcast.models import Profile

logger = logging.getLogger(__name__)

# Vite's build output (npm run build, run from web/) — not built by this app.
WEB_DIST = Path("web") / "dist"

# Permissive: single-user take-home, and only the Vite dev server (a
# different origin, :5173) ever needs this — production serves the SPA and
# the API from the same origin (this app), where CORS doesn't apply at all.
_DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    db.init_db()

    sched = scheduler.create_scheduler()
    with db.session_scope() as session:
        interrupted = service.mark_interrupted_episodes(session)
        if interrupted:
            logger.warning("startup: marked %d interrupted episode(s) as failed", interrupted)
        service.seed_profile_from_yaml_if_empty(session)  # no-op if a profile already exists
        row = session.exec(select(db.ProfileRecord)).first()
    if row is not None:
        scheduler.schedule_from_profile(sched, Profile.model_validate(row.data), row.id)
    sched.start()
    app.state.scheduler = sched

    yield

    sched.shutdown()


app = FastAPI(title="podcast-generator API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=_DEV_ORIGINS, allow_methods=["*"], allow_headers=["*"], allow_credentials=True
)
# Every API route lives under /api — the SPA owns the bare path namespace
# (/episodes, /settings, /dashboard, ...) for React Router, and without this
# prefix a client-side route can collide with a same-named API route (e.g.
# GET /episodes was both "list episodes" and the Episodes page): a hard
# refresh/direct navigation on the SPA route would hit the API handler
# instead of index.html. See docs/decisions.md ("API routes under /api").
app.include_router(routes_profile.router, prefix="/api")
app.include_router(routes_episodes.router, prefix="/api")
app.include_router(routes_metrics.router, prefix="/api")
app.include_router(routes_schedule.router, prefix="/api")
app.include_router(routes_interests.router, prefix="/api")
app.include_router(routes_voices.router, prefix="/api")
app.include_router(routes_hosts.router, prefix="/api")

# Static SPA, registered LAST so it never shadows an API route above: FastAPI
# matches routes in registration order, and the catch-all path parameter
# below would otherwise swallow every request. StaticFiles(html=True) alone
# doesn't do SPA fallback for a client-side route like /settings (it only
# serves index.html for "/" and real directories, 404 otherwise — verified
# against Starlette's own StaticFiles.get_response) so the fallback route
# does that explicitly: serve the real file if Vite emitted one at that
# path (a favicon, a hashed asset), else index.html for React Router to
# take over client-side.
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        candidate = WEB_DIST / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
