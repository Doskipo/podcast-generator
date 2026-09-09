"""Pydantic models shared across pipeline stages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl


class Interest(BaseModel):
    topic: str
    weight: float = Field(ge=0.0, le=1.0)


class PodcastSettings(BaseModel):
    duration_minutes: int
    hosts: list[str]
    tone: str


class FetchSettings(BaseModel):
    """Knobs for the fetch stage. Overridable per profile, sane defaults otherwise."""

    window_hours: int = 48
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
    feeds: list[HttpUrl]
    podcast: PodcastSettings
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    tts: TTSSettings = Field(default_factory=TTSSettings)

    @classmethod
    def from_yaml(cls, path: str | Path) -> Profile:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)


class Article(BaseModel):
    source_id: str  # stable short id (hash of the URL) — script segments cite this
    url: HttpUrl
    title: str
    feed_url: HttpUrl
    published_at: datetime
    fetched_at: datetime
    summary: str | None = None
    text: str  # full text extracted by trafilatura; article is dropped upstream if this is unavailable


class Episode(BaseModel):
    """Per-episode manifest, written once when an episode is created."""

    episode_id: str
    created_at: datetime
    profile: Profile


class FetchOutput(BaseModel):
    """Typed output of the fetch stage, persisted as articles.json."""

    episode_id: str
    fetched_at: datetime
    window_hours: int
    articles: list[Article]


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
