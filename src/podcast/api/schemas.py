"""API request/response shapes — deliberately separate from podcast.models,
which is the pipeline's own typed input/output shapes. These are what the
HTTP layer speaks."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from podcast.models import QualityOutput, Script


class EpisodeCreateRequest(BaseModel):
    until: str | None = None  # mirrors the CLI's --until; None runs the full pipeline


class EpisodeCreateResponse(BaseModel):
    episode_id: str
    status: str = "pending"


class EpisodeSummary(BaseModel):
    episode_id: str
    # The script's own title (revised_script if critique has run, else
    # script's) — None until the script stage has produced one (e.g. still
    # pending, or failed before scripting). The UI falls back to the
    # episode's date when this is None (see docs/decisions.md, "Episode
    # list: mocked, status filter, resilience").
    title: str | None = None
    status: str  # pending | running | done | failed | no_content
    stage_reached: str | None
    created_at: datetime
    duration_s: float | None
    total_characters: int | None
    cost_estimate_usd: float | None
    # Set only when status == "no_content": the interests that had zero
    # fetched candidates this run. On the list itself (not just detail) so
    # the Episodes UI can show it without an extra fetch. See
    # docs/decisions.md ("Grounding guard").
    no_content_interests: list[str] | None = None
    # Set only when status == "failed": a short, human-readable reason
    # (podcast.errors.humanize_stage_error, or a fixed message for a run
    # interrupted by a server restart — see
    # service.mark_interrupted_episodes). On the list itself for the same
    # reason no_content_interests is.
    failure_reason: str | None = None


class ShowNoteItem(BaseModel):
    title: str
    url: str
    source: str


class EpisodeDetail(EpisodeSummary):
    script: Script | None = None
    show_notes: list[ShowNoteItem] = Field(default_factory=list)
    # None until the quality stage has run (still earlier in the pipeline,
    # failed before reaching it, or predates this feature). Every number in
    # here is a PROXY — see docs/decisions.md ("Quality metrics") — the UI
    # must label them as such, never as a pass/fail verdict.
    quality: QualityOutput | None = None


class EventIn(BaseModel):
    type: Literal["played", "completed_playback"]
    metadata: dict = Field(default_factory=dict)


class EventOut(BaseModel):
    id: int
    episode_id: str
    type: str
    ts: datetime
    metadata: dict = Field(default_factory=dict)


class EpisodesByStatusOut(BaseModel):
    status: str
    count: int


class StageCostOut(BaseModel):
    stage: str  # rank | outline | script | critique | tts
    provider: str  # openai | elevenlabs
    cost_usd: float


class StageDurationOut(BaseModel):
    stage: str
    avg_elapsed_s: float
    count: int


class TopicCountOut(BaseModel):
    interest: str
    count: int


class DailyPointOut(BaseModel):
    date: str  # YYYY-MM-DD
    episodes_created: int
    plays: int
    completions: int
    mocked: bool  # true if any row behind this day's counts is mocked demo data


class RecentFailureOut(BaseModel):
    episode_id: str
    status: str  # failed | no_content
    stage_reached: str | None
    reason: str | None
    created_at: datetime
    mocked: bool


class QualityPointOut(BaseModel):
    """One real (non-mocked) episode's quality.json, for the dashboard's
    "over time" section — see podcast.metrics.quality_series and
    docs/decisions.md ("Quality metrics"). Every number is a PROXY, not a
    verdict."""

    episode_id: str
    date: str  # YYYY-MM-DD, from quality.json's generated_at
    naturalness_score: int
    stance_clarity_score: int
    source_count: int
    evergreen_share: float
    critique_flags: int
    critique_rewrite_rate: float
    fact_drift_flags: int
    total_retries: int
    audio_tag_density_per_100_words: float
    interjection_or_dash_share: float
    host_balance: float
    catchphrase_count: int


class MetricsSummary(BaseModel):
    total_episodes: int
    done: int
    failed: int
    pending_or_running: int
    total_characters: int
    total_cost_estimate_usd: float
    avg_duration_s: float | None

    # Extended fields — see podcast.metrics.aggregate_summary for how these
    # are computed and docs/decisions.md ("Dashboard metrics") for why each
    # was chosen.
    no_content: int
    episodes_by_status: list[EpisodesByStatusOut]
    cost_by_stage: list[StageCostOut]
    avg_cost_per_episode_usd: float | None
    avg_stage_duration_s: list[StageDurationOut]
    topic_distribution: list[TopicCountOut]
    plays_total: int
    completions_total: int
    completion_rate: float | None
    d7_retention: float | None
    interests_with_no_content: list[TopicCountOut]
    daily_series: list[DailyPointOut]
    recent_failures: list[RecentFailureOut]
    # Oldest-to-newest, one point per real episode with a quality.json — see
    # podcast.metrics.quality_series and docs/decisions.md ("Quality
    # metrics"). Unaffected by include_mocked below (mocked rows never have
    # a quality.json — quality metrics are never mocked).
    quality_series: list[QualityPointOut]
    has_mocked_data: bool
    # Echoes the request's `include_mocked` query param — the single source
    # of truth for which mode produced these numbers, so the frontend never
    # has to track it separately from what it asked for. See
    # docs/decisions.md ("Re-measured words-per-minute, dashboard
    # mocked-data toggle").
    include_mocked: bool


class NextRunOut(BaseModel):
    cron: str
    next_run_at: datetime | None
