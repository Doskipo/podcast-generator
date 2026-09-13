"""API request/response shapes — deliberately separate from podcast.models,
which is the pipeline's own typed input/output shapes. These are what the
HTTP layer speaks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from podcast.models import Script


class EpisodeCreateRequest(BaseModel):
    until: str | None = None  # mirrors the CLI's --until; None runs the full pipeline


class EpisodeCreateResponse(BaseModel):
    episode_id: str
    status: str = "pending"


class EpisodeSummary(BaseModel):
    episode_id: str
    status: str
    stage_reached: str | None
    created_at: datetime
    duration_s: float | None
    total_characters: int | None
    cost_estimate_usd: float | None


class ShowNoteItem(BaseModel):
    title: str
    url: str
    source: str


class EpisodeDetail(EpisodeSummary):
    script: Script | None = None
    show_notes: list[ShowNoteItem] = Field(default_factory=list)


class EventIn(BaseModel):
    type: Literal["played", "completed_playback"]
    metadata: dict = Field(default_factory=dict)


class EventOut(BaseModel):
    id: int
    episode_id: str
    type: str
    ts: datetime
    metadata: dict = Field(default_factory=dict)


class MetricsSummary(BaseModel):
    total_episodes: int
    done: int
    failed: int
    pending_or_running: int
    total_characters: int
    total_cost_estimate_usd: float
    avg_duration_s: float | None


class NextRunOut(BaseModel):
    cron: str
    next_run_at: datetime | None
