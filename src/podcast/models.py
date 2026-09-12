"""Pydantic models shared across pipeline stages."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, HttpUrl


def _slugify(text: str) -> str:
    """Lowercase, hyphenated slug — used as RecurringBit's default id when
    none is set explicitly."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "bit"


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


class HostVoiceSettings(BaseModel):
    """ElevenLabs per-voice knobs (0.0-1.0). Only applied in tts_stage's
    per-line fallback path — the text_to_dialogue endpoint's DialogueInput
    has no per-turn settings field, so a host's voice_settings has no effect
    when dialogue mode succeeds. See docs/decisions.md ("Voice and dynamics
    pass"). Any field left unset falls back to ElevenLabs' own default for
    that voice."""

    stability: float | None = Field(default=None, ge=0.0, le=1.0)
    similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    style: float | None = Field(default=None, ge=0.0, le=1.0)


class Host(BaseModel):
    """One podcast host. `persona` is injected verbatim into the script/
    critique prompts — write it the way you'd brief a voice actor."""

    name: str
    voice_id: str  # ElevenLabs voice id — tts_stage reads this directly, no separate voice map
    persona: str
    home_turf: list[str] = Field(default_factory=list)  # topics/angles this host naturally gravitates to
    voice_settings: HostVoiceSettings | None = None


class Listener(BaseModel):
    name: str


class RecurringBit(BaseModel):
    """A recurring segment/bit the outline can slot into an episode, at most
    `max_per_episode` times. The outline references `effective_id`, never
    `name` — a free-text name is prone to the model paraphrasing it back
    slightly differently (seen in practice); an id is stable and, in the
    outline prompt, constrained to a fixed enum so the model can't invent
    one. See docs/decisions.md ("Recurring bit id fix")."""

    name: str
    id: str | None = None  # slug; derived from name if not given
    description: str
    max_per_episode: int

    @property
    def effective_id(self) -> str:
        return self.id or _slugify(self.name)


class Style(BaseModel):
    humour: int = Field(ge=0, le=3)
    depth: int = Field(ge=1, le=3)
    tangents: bool
    banter: bool


class PodcastSettings(BaseModel):
    name: str  # the show's name — spoken in the cold open's host identification
    duration_minutes: int
    listener: Listener
    hosts: list[Host]
    recurring_bits: list[RecurringBit] = Field(default_factory=list)
    style: Style
    tone: str


class FetchSettings(BaseModel):
    """Knobs for the fetch stage. Overridable per profile, sane defaults otherwise."""

    max_entries_per_feed: int = 30


class LLMSettings(BaseModel):
    """Knobs for LLM-backed stages. Overridable per profile."""

    model: str = "gpt-4o-mini"  # cheap default — scoring, query generation, outline, critique
    # Stronger model for the script-writing step, where persona/voice quality
    # matters most and the per-episode call count is low (one call). See
    # docs/decisions.md ("Script rebuild") for the cost trade-off.
    script_model: str = "gpt-4o"


class TTSSettings(BaseModel):
    """Knobs for the tts stage. Overridable per profile. Voice ids live on
    each Host now (profile.podcast.hosts[].voice_id), not here — see
    docs/decisions.md ("PodcastSettings migration").

    Both model ids default to a v3-class model, needed for audio-tag support
    (Line.delivery) in either synthesis path — see docs/decisions.md ("Voice
    and dynamics pass")."""

    model_id: str = "eleven_v3"  # per-line fallback synthesis model
    dialogue_model_id: str = "eleven_v3"  # text_to_dialogue (primary) model


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
    # Feed URLs actually queried this run (one per interest's curated feed or
    # generated/cached query, plus top-level profile.feeds extras) — NOT
    # len(profile.feeds), which only counts the top-level extras and is 0 for
    # a profile whose feeds all come from interests. See docs/decisions.md.
    feeds_count: int = 0
    articles: list[Article]


class QueryList(BaseModel):
    """Structured-output shape for the cheap LLM call that turns an interest's
    topic into event-shaped search queries (see fetch.py:_generate_queries)."""

    queries: list[str]


class ArticleScore(BaseModel):
    """One article's relevance score, from the rank stage's batch scoring
    call (see rank.py:_score_batch). Every batch scores exactly one interest,
    so `interest` is the model's echo of the label it was told to score
    against — a consistency check, not new information. rank.py rejects and
    re-scores any item whose echo doesn't match what it was actually given."""

    source_id: str
    interest: str  # echoed back from the prompt; verified against the expected label
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


class Angle(BaseModel):
    """The narrative take on one outline story — what the script step should
    build the segment's dialogue around."""

    why_it_matters: str
    tension_or_surprise: str
    host_take: str  # which host should lead this story, and why
    # A possible tangent or analogy from a host's backstory or the listener's
    # interests. None when nothing genuinely fits, or when this story carries
    # the recurring bit instead — a segment never gets both (see
    # outline.py:_validate_outline and docs/decisions.md).
    tangent: str | None = None


class OutlineStory(BaseModel):
    headline: str
    source_ids: list[str]  # subset of the ranked articles' source_ids this story is grounded in
    angle: Angle
    # RecurringBit.effective_id of a profile.podcast.recurring_bits entry, if
    # one fits here — never the free-text name (see RecurringBit).
    recurring_bit: str | None = None
    # Target word count for this story's segment, proportional to the
    # story's rank score. Computed in code by outline_stage after the LLM
    # call (not asked of the model) — defaults to 0 until then.
    word_budget: int = 0


class Outline(BaseModel):
    title: str
    stories: list[OutlineStory]  # already in the intended narrative order


class OutlineOutput(BaseModel):
    """Typed output of the outline stage, persisted as outline.json."""

    episode_id: str
    generated_at: datetime
    model: str
    outline: Outline


class Line(BaseModel):
    speaker: str
    text: str
    # Optional pause AFTER this line, in ms. The script writer sets this
    # deliberately: ~100-200 for a quick back-and-forth exchange, ~600-900
    # for a beat — reserve the long end for right before the recurring bit
    # or a reveal, not routinely. tts_stage's per-line fallback ignores this
    # (ElevenLabs has no "pause after" concept); stitch_stage applies it as
    # the gap between this line's clip and the next, defaulting to 200ms
    # when unset, but only outside dialogue mode — see docs/decisions.md
    # ("Voice and dynamics pass").
    pause_ms: int | None = None
    # A short audio-tag descriptor ("laughs", "sighs", "deadpan", "amused",
    # "whispers", ...), mapped by tts_stage to a bracketed tag prefix
    # ("[laughs] ...") when the configured model supports them (a v3-class
    # model — see podcast.stages.script:supports_audio_tags). None for an
    # ordinary line.
    delivery: str | None = None


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


class CritiqueFlag(BaseModel):
    """One line the critique pass flagged, and its fix. `line_index` is the
    flagged line's position in the ORIGINAL script's flattened line order —
    cold_open, then every segment's lines, then outro (see
    script.py:flatten_lines) — the same order tts_stage synthesizes in.
    `rewritten_lines` replaces that one original line with one or more lines:
    usually a single rewritten line, but more than one when the fix is to
    split an over-long or over-expository line into a short back-and-forth
    exchange between the hosts."""

    line_index: int
    # e.g. "robotic", "expository", "breaks_persona", "invented_listener_detail",
    # "too_long", "the_article_phrasing", "repeated_correct"
    issue: str
    rewritten_lines: list[Line]


class Critique(BaseModel):
    flags: list[CritiqueFlag]


class SegmentBudgetFlag(BaseModel):
    """A segment whose word count landed more than 20% over its outline
    word_budget — computed in code (word counting needs no LLM judgment),
    not part of the LLM-facing Critique schema. Informational: reported, not
    auto-shortened."""

    segment_index: int
    headline: str
    word_budget: int
    actual_words: int
    over_by_percent: float


class CritiqueOutput(BaseModel):
    """Typed output of the critique stage, persisted as critique.json. Keeps
    both the pre-critique and rewritten script — critique.py only replaces
    flagged lines (via CritiqueFlag), never touches segments/source_ids, so
    `revised_script` stays grounded exactly like `original_script`."""

    episode_id: str
    generated_at: datetime
    model: str
    critique: Critique
    original_script: Script
    revised_script: Script
    total_words: int  # word count of revised_script (cold_open + segments + outro)
    over_budget_segments: list[SegmentBudgetFlag]


class TTSLine(BaseModel):
    """One synthesized line, in script order."""

    index: int
    speaker: str
    file: str  # path relative to the episode dir, e.g. "segments/000_Nova.mp3"
    characters: int  # length of the synthesized text, for cost tracking
    # Copied from the source Line — stitch_stage reads this (outside dialogue
    # mode only; see TTSOutput.synthesis_mode) to size the gap after this line.
    pause_ms: int | None = None


class TTSOutput(BaseModel):
    """Typed output of the tts stage, persisted as tts_manifest.json."""

    episode_id: str
    generated_at: datetime
    lines: list[TTSLine]
    # "dialogue": ElevenLabs' text_to_dialogue endpoint synthesized the whole
    # script (whole or per-chunk) as one continuous conversation, then each
    # line's own clip was sliced out via the endpoint's voice_segments
    # timestamps — natural inter-turn pacing is already embedded in the
    # clips. "per_line": the endpoint wasn't available/failed, and every
    # line was synthesized independently instead — stitch_stage must add its
    # own pause_ms-based gap between lines. See docs/decisions.md ("Voice
    # and dynamics pass").
    synthesis_mode: str
    total_characters: int  # sum of TTSLine.characters, for cost tracking


class StitchOutput(BaseModel):
    """Typed output of the stitch stage, persisted as stitch_manifest.json."""

    episode_id: str
    generated_at: datetime
    audio_file: str  # path relative to the episode dir, e.g. "episode.mp3"
    duration_ms: int
