"""SQLite persistence: engine + the three tables (profiles, episodes,
events). See docs/decisions.md ("Backend: SQLite + FastAPI + APScheduler")
for why SQLite and what the events table is for.

Nothing outside this module should import `engine` directly by name into a
long-lived binding — always go through `db.get_session()`, or read
`db.engine` via `from podcast import db` right before use. That's what lets
tests swap the engine with `monkeypatch.setattr(db, "engine", test_engine)`
and have every caller pick it up immediately.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine

DB_PATH = Path("data") / "podcast.db"

# check_same_thread=False: FastAPI's BackgroundTasks run the pipeline in a
# threadpool thread different from the request-handling thread that opened
# the app; SQLite's default same-thread check would reject that.
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


class ProfileRecord(SQLModel, table=True):
    """Single-user app: in practice there is exactly one row (id=1,
    get-or-create). See docs/decisions.md for what changes at scale (a real
    per-user row keyed by auth identity)."""

    __tablename__ = "profiles"

    id: int | None = Field(default=None, primary_key=True)
    name: str  # denormalized podcast.models.Profile.name, display convenience only
    data: dict = Field(sa_column=Column(JSON))  # Profile.model_dump(mode="json")
    created_at: datetime
    updated_at: datetime


class EpisodeRecord(SQLModel, table=True):
    """DB-side current-state row for one episode. Deliberately named
    `EpisodeRecord`, not `Episode` — `podcast.models.Episode` is the
    existing pure per-run manifest persisted as episode.json; the two are
    unrelated shapes and must never be aliased to the same name."""

    __tablename__ = "episodes"

    id: int | None = Field(default=None, primary_key=True)
    episode_id: str = Field(index=True, unique=True)  # matches data/episodes/<episode_id>/
    profile_id: int = Field(foreign_key="profiles.id")
    status: str = Field(default="pending")  # pending | running | done | failed | no_content
    stage_reached: str | None = None  # fetch|rank|outline|script|critique|tts|stitch
    created_at: datetime
    duration_s: float | None = None
    total_characters: int | None = None
    cost_estimate_usd: float | None = None
    audio_path: str | None = None  # set once stitch succeeds
    # Set only when status == "no_content": the profile.interests[].topic
    # values that had zero fetched candidates this run — the grounding guard
    # (podcast.service._check_grounding) refuses to build an episode from
    # zero selected articles and stops the pipeline here instead. See
    # docs/decisions.md ("Grounding guard").
    no_content_interests: list[str] | None = Field(default=None, sa_column=Column(JSON))


class EventRecord(SQLModel, table=True):
    """Append-only audit trail — dashboard timeline / debugging. Distinct
    from `episodes`, which is a mutable current-state row: only `events`
    lets you reconstruct what happened and when, including things the
    `episodes` row later overwrote (e.g. an earlier stage_done once a later
    stage has since updated stage_reached)."""

    __tablename__ = "events"

    id: int | None = Field(default=None, primary_key=True)
    episode_id: str = Field(foreign_key="episodes.episode_id", index=True)
    # generated | stage_done | completed | failed | played | completed_playback
    type: str
    ts: datetime
    # Named metadata_json, not metadata — `metadata` is reserved on
    # SQLAlchemy's declarative base. The API exposes this under the JSON key
    # "metadata" (see podcast.api.schemas.EventOut).
    metadata_json: dict | None = Field(default=None, sa_column=Column(JSON))


def init_db() -> None:
    """Create data/ and all tables if they don't exist yet. Safe to call on
    every startup (CLI's main() and the API's lifespan both do)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _migrate_add_missing_columns()


def _migrate_add_missing_columns() -> None:
    """SQLModel.metadata.create_all only creates missing TABLES, never alters
    an existing one — a real data/podcast.db from before `no_content_interests`
    existed would otherwise crash the first time this code reads/writes an
    EpisodeRecord. Minimal, additive-only migration: add any column declared
    on EpisodeRecord that the actual table doesn't have yet. No down-migration,
    no rename/drop support, no other tables — this app has exactly one
    additive column so far; reach for a real migration tool (Alembic) if that
    changes."""
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(episodes)").fetchall()}
        if "no_content_interests" not in existing:
            conn.exec_driver_sql("ALTER TABLE episodes ADD COLUMN no_content_interests JSON")
            conn.commit()


def get_session() -> Iterator[Session]:
    """FastAPI dependency shape: `Depends(get_session)`. Reads the
    module-global `engine` at call time (not at import time), so a test's
    `monkeypatch.setattr(db, "engine", test_engine)` takes effect on the
    very next call."""
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    """Same as get_session, but usable as a plain `with` block for code
    outside FastAPI's dependency-injection system (CLI, service.py,
    scheduler.py, tests)."""
    with Session(engine) as session:
        yield session
