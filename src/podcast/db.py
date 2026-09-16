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
    # Set whenever status becomes "failed" — a short, human-readable reason
    # (podcast.errors.humanize_stage_error turns a raised exception into
    # this; podcast.service.mark_interrupted_episodes writes a fixed
    # message for a run orphaned by a server restart). The single source
    # for both the Episodes page and the dashboard's recent-failures table
    # — see docs/decisions.md ("Episode list: mocked, status filter,
    # resilience"). None for every other status, and for a "failed" row
    # persisted before this field existed.
    failure_reason: str | None = None
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
    # True only for rows written by `podcast seed-metrics` (see
    # podcast.seed_metrics) — never set by the real pipeline (run_episode/
    # service.py never touches this field). The dashboard uses it to label
    # demo data and to know a mocked row has no manifest files on disk to
    # read cost/topic data from (see mock_cost_by_stage/mock_topic_counts
    # below and docs/decisions.md, "Dashboard metrics").
    mocked: bool = Field(default=False)
    # Only set (and only meaningful) on a mocked row: a real episode's cost
    # breakdown is always read from its persisted manifests (podcast.metrics.
    # episode_cost_breakdown) — a mocked episode has no manifest files, so
    # its plausible {stage: {provider: cost_usd}} breakdown is stored here
    # directly instead. Shape: {"rank": {"openai": 0.004}, "tts": {"elevenlabs": 0.18}, ...}.
    mock_cost_by_stage: dict | None = Field(default=None, sa_column=Column(JSON))
    # Only set on a mocked row: a plausible {interest_topic: count} airtime
    # distribution, standing in for what podcast.metrics.topic_distribution
    # would otherwise tally from a real episode's ranked.json.
    mock_topic_counts: dict | None = Field(default=None, sa_column=Column(JSON))


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
    # True only for rows written by `podcast seed-metrics` — see
    # EpisodeRecord.mocked above. Real events (service.emit_event, the only
    # other writer of this table) never set this, so "real events are never
    # mocked" holds structurally, not just by convention.
    mocked: bool = Field(default=False)


def init_db() -> None:
    """Create data/ and all tables if they don't exist yet. Safe to call on
    every startup (CLI's main() and the API's lifespan both do)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    SQLModel.metadata.create_all(engine)
    _migrate_add_missing_columns()


# (table, column, SQL type) added after that table's first release — every
# entry here is a column now declared on the SQLModel class above that an
# existing sqlite file from before it existed won't have yet.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("episodes", "no_content_interests", "JSON"),
    ("episodes", "mocked", "BOOLEAN DEFAULT 0"),
    ("episodes", "mock_cost_by_stage", "JSON"),
    ("episodes", "mock_topic_counts", "JSON"),
    ("episodes", "failure_reason", "TEXT"),
    ("events", "mocked", "BOOLEAN DEFAULT 0"),
]


def _migrate_add_missing_columns() -> None:
    """SQLModel.metadata.create_all only creates missing TABLES, never alters
    an existing one — a real data/podcast.db from before one of these
    columns existed would otherwise crash the first time this code reads/
    writes that row. Minimal, additive-only migration: add whichever of
    _ADDITIVE_COLUMNS the actual table doesn't have yet. No down-migration,
    no rename/drop support — reach for a real migration tool (Alembic) if
    this list grows much past a handful of columns."""
    with engine.connect() as conn:
        existing_by_table: dict[str, set[str]] = {}
        for table, column, sql_type in _ADDITIVE_COLUMNS:
            if table not in existing_by_table:
                existing_by_table[table] = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing_by_table[table]:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                existing_by_table[table].add(column)
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
