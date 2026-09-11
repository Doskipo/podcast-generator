"""Pydantic models shared across pipeline stages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl


# Per-interest freshness window defaults: curated feeds (arXiv, a chosen blog's
# RSS, ...) publish daily, so a short window keeps things current. Interests
# discovered via news search skew toward older explainer/reference content for
# anything but high-volume topics, so they get a longer window by default —
# see docs/decisions.md ("Per-interest freshness window") for the reasoning.
DEFAULT_CURATED_WINDOW_HOURS = 48
DEFAULT_SEARCH_WINDOW_HOURS = 168


class Interest(BaseModel):
    topic: str
    weight: float = Field(ge=0.0, le=1.0)
    # Free-text elaboration on what this interest actually means to the user
    # (scope, angle, what counts as relevant) — passed to the rank stage's
    # scorer alongside the topic so it can judge relevance against more than
    # a bare keyword. None is fine; the topic alone still scores.
    description: str | None = None
    feeds: list[HttpUrl] = Field(default_factory=list)  # curated feeds; empty means "discover via search"
    # Search queries for this interest, used only when `feeds` is empty. None
    # means "not generated yet" — the fetch stage generates
    # NUM_GENERATED_QUERIES event-shaped queries with one LLM call and caches
    # them here (persisted back to the profile's YAML file), so it's one call
    # per profile, not per run.
    queries: list[str] | None = None
    window_hours: int | None = None  # override; default depends on curated vs. search, see below

    @property
    def is_curated(self) -> bool:
        return bool(self.feeds)

    @property
    def effective_window_hours(self) -> int:
        if self.window_hours is not None:
            return self.window_hours
        return DEFAULT_CURATED_WINDOW_HOURS if self.is_curated else DEFAULT_SEARCH_WINDOW_HOURS


class PodcastSettings(BaseModel):
    duration_minutes: int
    hosts: list[str]
    tone: str


class FetchSettings(BaseModel):
    """Knobs for the fetch stage. Overridable per profile, sane defaults otherwise."""

    max_entries_per_feed: int = 30


class LLMSettings(BaseModel):
    """Knobs for LLM-backed stages (script, ...). Overridable per profile."""

    model: str = "gpt-4o-mini"  # cheap default


# Placeholder ElevenLabs premade voice ids, used as defaults so the two demo
# hosts (Nova, Max) work out of the box. Override per profile with real voices.
_DEFAULT_VOICES = {
    "Nova": "21m00Tcm4TlvDq8ikWAM",  # "Rachel"
    "Max": "pNInz6obpgDQGcFmaJgB",  # "Adam"
}


class TTSSettings(BaseModel):
    """Knobs for the tts stage. Overridable per profile."""

    voices: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_VOICES))
    model_id: str = "eleven_turbo_v2_5"  # cheap/fast default


class Profile(BaseModel):
    name: str
    interests: list[Interest]
    feeds: list[HttpUrl] = Field(default_factory=list)  # optional extras, layered on top of interests' feeds
    podcast: PodcastSettings
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Profile:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)

    def to_yaml(self, path: str | Path) -> None:
        """Serialize back to YAML — used to cache generated interest queries
        into the profile's source file. Round-trips through the model, so
        hand-written comments/formatting in the original file are not
        preserved."""
        data = self.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
        Path(path).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class Article(BaseModel):
    source_id: str  # stable short id (hash of the URL) — script segments cite this
    url: HttpUrl
    title: str
    feed_url: HttpUrl
    published_at: datetime
    fetched_at: datetime
    summary: str | None = None
    # Full text, extracted by trafilatura in the rank stage — only for
    # candidates selected there. None until then (or if extraction failed and
    # the candidate was dropped from the selection). See docs/decisions.md
    # ("Rank stage") for why extraction moved out of fetch.
    text: str | None = None
    # The URL trafilatura actually fetched, after following redirects (e.g.
    # Bing's apiclick.aspx wrapper) — set by the rank stage for every
    # candidate it attempts extraction on, whether or not that attempt
    # succeeds. None for candidates extraction was never attempted for.
    final_url: str | None = None
    # "curated" (an explicit feed URL, interest-level or top-level extra) or
    # "<provider>_search" (a feed generated from an interest's query). Defaults
    # to "curated" so pre-existing persisted articles.json files (from before
    # this field existed) still validate via --from-articles.
    source: str = "curated"
    # The Interest.topic this candidate was discovered for; None for
    # candidates from top-level profile.feeds extras, which aren't tied to
    # any single interest. Lets the rank stage group/budget per interest.
    interest: str | None = None


class Episode(BaseModel):
    """Per-episode manifest, written once when an episode is created."""

    episode_id: str
    created_at: datetime
    profile: Profile


class FetchOutput(BaseModel):
    """Typed output of the fetch stage, persisted as articles.json."""

    episode_id: str
    fetched_at: datetime
    articles: list[Article]


class QueryList(BaseModel):
    """Structured-output shape for the cheap LLM call that turns an interest's
    topic into event-shaped search queries (see fetch.py:_generate_queries)."""

    queries: list[str]


class ArticleScore(BaseModel):
    """One article's relevance score, from the rank stage's batch scoring
    call (see rank.py:_score_batch)."""

    source_id: str
    score: float = Field(ge=0.0, le=1.0)
    reason: str  # one line


class ScoreBatch(BaseModel):
    """Structured-output shape for one batch scoring call — up to
    rank.BATCH_SIZE articles' worth of scores at a time."""

    scores: list[ArticleScore]


class RankedArticle(BaseModel):
    """One scored candidate, whether or not it ended up selected. `article`
    carries extracted `text` only when `selected` is True — extraction only
    runs for the chosen subset."""

    article: Article
    interest: str | None  # the Interest.topic (or None) this candidate was scored against
    score: float
    reason: str
    selected: bool


class RankOutput(BaseModel):
    """Typed output of the rank stage, persisted as ranked.json."""

    episode_id: str
    ranked_at: datetime
    model: str
    total_budget: int
    backfilled: int  # selected candidates that weren't in the original per-interest top-k
    scored: list[RankedArticle]  # every candidate that was scored, for audit
    selected: list[Article]  # the chosen subset, text extracted, in global order


class Line(BaseModel):
    speaker: str
    text: str


class Segment(BaseModel):
    headline: str
    source_ids: list[str]  # must be a subset of the fetched articles' source_ids
    lines: list[Line]


class Script(BaseModel):
    title: str
    cold_open: list[Line]
    segments: list[Segment]
    outro: list[Line]


class ScriptOutput(BaseModel):
    """Typed output of the script stage, persisted as script.json."""

    episode_id: str
    generated_at: datetime
    model: str
    script: Script


class TTSLine(BaseModel):
    """One synthesized line, in script order."""

    index: int
    speaker: str
    file: str  # path relative to the episode dir, e.g. "segments/000_Nova.mp3"
    characters: int  # length of the synthesized text, for cost tracking


class TTSOutput(BaseModel):
    """Typed output of the tts stage, persisted as tts_manifest.json."""

    episode_id: str
    generated_at: datetime
    lines: list[TTSLine]


class StitchOutput(BaseModel):
    """Typed output of the stitch stage, persisted as stitch_manifest.json."""

    episode_id: str
    generated_at: datetime
    audio_file: str  # path relative to the episode dir, e.g. "episode.mp3"
    duration_ms: int
