"""Pydantic models shared across pipeline stages."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, HttpUrl, model_validator


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
    # Concrete, user-authored facts about the listener (hobbies, current
    # projects, what they're grinding at) — e.g. "plays piano; likes jazzy /
    # alternative-pop progressions". Replaces the old bare-name-only hook:
    # script_stage renders these into the prompt so a tangent or aside can
    # reference something real about the listener instead of the model
    # having nothing to work with but their name. Still nothing to invent
    # beyond what's listed here — see script.py's "never invent hobbies,
    # opinions..." hard rule.
    facts: list[str] = Field(default_factory=list)


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

    @model_validator(mode="after")
    def _validate_hosts(self) -> "PodcastSettings":
        # The pipeline's own design assumes exactly two hosts (script.py
        # assigns roles positionally: hosts[0] drives, hosts[1] asks — see
        # docs/decisions.md, "Script stage"), and tts_stage raises on any
        # script line whose speaker has no matching host/voice — so this is
        # enforced here, once, rather than as a hopeful runtime assumption.
        # Enforced on every Profile validation (PUT /profile, profile
        # YAML loads, DB seeding), so a bad profile is rejected at the
        # boundary (422 for the API) instead of failing deep in the
        # pipeline. See docs/decisions.md ("Profile validation").
        if len(self.hosts) != 2:
            raise ValueError(f"exactly two hosts are required, got {len(self.hosts)}")
        for host in self.hosts:
            if not host.voice_id or not host.voice_id.strip():
                raise ValueError(f"host {host.name!r} must have a non-empty voice_id")
        return self


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
    # Drives outline_stage's and script_stage's word budgets (duration_minutes
    # * words_per_minute). Default is a real measurement — see
    # podcast.metrics.measured_words_per_minute and docs/decisions.md
    # ("Measured words-per-minute", "Word budget recalibration") — of actual
    # spoken pace from performed, synthesized dialogue-mode audio: below the
    # ~150 wpm this replaced, but above this project's own ~100-110 prior
    # guess. Re-measured (139.6, essentially unchanged from 140.0) across 4
    # real episodes — re-run measured_words_per_minute() again as more
    # accumulate. This number alone didn't explain a 9'13"-against-7-minute
    # overrun; see perform.MAX_WORD_OVERRUN, tightened alongside this.
    words_per_minute: float = 139.6


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
    # Cron expression (min hour dom month dow) for the scheduler to create an
    # episode on. Default: daily at 07:00 UTC. Parsed/validated by
    # apscheduler.triggers.cron.CronTrigger.from_crontab at schedule-registration
    # time (podcast/scheduler.py) — not validated here, to avoid a second,
    # possibly-diverging cron parser in the codebase.
    schedule: str = "0 7 * * *"

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


class InterestSuggestion(BaseModel):
    """Structured-output shape for the combined description+queries call
    behind the settings UI's "suggest" button (see
    fetch.py:suggest_interest, api/routes_interests.py). One LLM call draws
    both a one-line description draft and NUM_GENERATED_QUERIES event-shaped
    search queries — same cost as generating queries alone."""

    description: str  # one line, e.g. "mechanistic interpretability of neural networks: circuits, features, probes"
    queries: list[str]


class TokenUsage(BaseModel):
    """One OpenAI `chat.completions.parse` call's token cost, captured at
    the call boundary (`_score_batch`/`_generate_outline`/`_generate_script`/
    `_generate_critique`) and persisted onto the stage's own output — this
    is what `podcast.metrics` reads back to price an episode "from the
    persisted manifests" (see docs/decisions.md). A stage's `usage` field is
    a *list*, one entry per actual API call (echo-mismatch rescoring in
    rank.py, or a one-retry-with-feedback attempt in llm_retry.py both cost
    real money and must both be counted, not just the first attempt)."""

    model: str
    prompt_tokens: int
    completion_tokens: int


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
    # Interests that had zero real (fetched) selected candidates and got a
    # Wikipedia primer instead (podcast.evergreen) — a different source,
    # not a within-pool reorder, so distinct from `backfilled`. See
    # docs/decisions.md ("Evergreen fallback"). Defaults to 0 so a manifest
    # persisted before this field existed still validates.
    evergreen_count: int = 0
    scored: list[RankedArticle]  # every candidate that was scored, for audit
    selected: list[Article]  # the chosen subset, text extracted, in global order
    # Every scoring call's token usage — see TokenUsage. Defaults to [] so a
    # manifest persisted before this field existed still validates.
    usage: list[TokenUsage] = Field(default_factory=list)


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


class HostStance(BaseModel):
    """One host's emotional posture toward a story — what the script step
    should write that host's lines from. `host` must match a
    profile.podcast.hosts[] name exactly (outline.py's response schema
    constrains it to a Literal enum of known names, the same technique used
    for RecurringBit ids)."""

    host: str
    attitude: str  # e.g. "excited", "skeptical", "moved", "amused", "bored", "annoyed", "protective" — free text, not an enum
    why: str  # one sentence, consistent with the host's persona/home_turf
    arc: str | None = None  # how the stance shifts by the end of the segment; None = stays constant throughout


class Transition(BaseModel):
    """How the outline bridges from the story immediately before this one
    into this one. `link` is only for a genuine connection (shared
    mechanism, same person/organization, same underlying tension) — never
    invented to force two unrelated stories together; `clean_transition` is
    a short, neutral handoff with no claimed connection. Default to
    `clean_transition` when unsure. See docs/decisions.md ("Outline
    transitions")."""

    kind: Literal["link", "clean_transition"]
    # A short phrase, not full dialogue, for the script step to build the
    # connecting/handoff lines from — the actual connection (for "link") or
    # the framing of the handoff (for "clean_transition").
    text_hint: str = Field(min_length=1)


MOOD_OPTIONS: tuple[str, ...] = ("energetic", "tired-but-sharp", "playful", "contrarian", "tender")


class HostMood(BaseModel):
    """One host's sampled mood for this whole episode — episode-level, not
    per-story (contrast OutlineStory.stances, which vary story to story).
    `mood` is code-sampled (podcast.stages.outline._sample_moods, seeded
    from the episode_id so a re-run reproduces the same moods, and
    excluding that host's mood in the immediately preceding episode so two
    episodes running don't feel identical) — the same "code computes what
    code can compute" reasoning as OutlineStory.word_budget/is_primer, not
    asked of the model. `reason` is the one thing that genuinely needs the
    model: a one-line justification tied to today's actual stories, not a
    generic explanation that could apply to any episode. See
    docs/decisions.md ("Persona rigidity")."""

    host: str
    mood: Literal[MOOD_OPTIONS]
    reason: str  # one line, tied to today's stories


class OutlineStory(BaseModel):
    headline: str
    source_ids: list[str]  # subset of the ranked articles' source_ids this story is grounded in
    angle: Angle
    # RecurringBit.effective_id of a profile.podcast.recurring_bits entry, if
    # one fits here — never the free-text name (see RecurringBit).
    recurring_bit: str | None = None
    # How this story bridges from the one before it — None only for the
    # first story (nothing precedes it); every later story must have one.
    # See docs/decisions.md ("Outline transitions").
    transition: Transition | None = None
    # Target word count for this story's segment, proportional to the
    # story's rank score. Computed in code by outline_stage after the LLM
    # call (not asked of the model) — defaults to 0 until then.
    word_budget: int = 0
    # True when any of this story's source_ids is a podcast.evergreen
    # primer (Article.source == "evergreen") — computed in code by
    # outline_stage after the LLM call, the same "derivable, not asked of
    # the model" reasoning as word_budget, not part of the structured-
    # output response schema. script.py reads this to write the segment as
    # background/reference rather than news. See docs/decisions.md
    # ("Evergreen fallback").
    is_primer: bool = False
    stances: list[HostStance]  # one per host in profile.podcast.hosts — validated in outline.py


class Outline(BaseModel):
    title: str
    stories: list[OutlineStory]  # already in the intended narrative order
    # One per host, episode-wide — see HostMood. Defaults to [] so an
    # outline.json persisted before this field existed still validates
    # (podcast.stages.outline._previous_episode_moods relies on exactly
    # this default when looking at an old episode).
    host_moods: list[HostMood] = Field(default_factory=list)


class OutlineOutput(BaseModel):
    """Typed output of the outline stage, persisted as outline.json."""

    episode_id: str
    generated_at: datetime
    model: str
    outline: Outline
    usage: list[TokenUsage] = Field(default_factory=list)
    # True iff generate_with_retry's one retry-with-feedback was needed to
    # get a valid outline — recorded explicitly by the stage (not inferred
    # from len(usage) downstream) since a stage can append more than one
    # usage entry for reasons other than a retry (see PerformOutput.retried).
    # A quality signal in its own right: a story worth grading, but not by
    # this field alone. See docs/decisions.md ("Quality metrics").
    retried: bool = False
    # Human-readable descriptions of quality-signal issues _repair_outline
    # fixed in code (a bit+tangent conflict, a bit over max_per_episode, a
    # missing transition) — never a correctness invariant, never a reason
    # this stage failed. See docs/decisions.md ("Correctness invariants vs
    # quality signals").
    repairs: list[str] = Field(default_factory=list)


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
    usage: list[TokenUsage] = Field(default_factory=list)
    retried: bool = False  # see OutlineOutput.retried


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
    # "too_long", "the_article_phrasing", "repeated_correct", "written_not_spoken"
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


class HostBrevityFlag(BaseModel):
    """A host whose lines are more than TERSE_HOST_THRESHOLD (60%) under
    SHORT_LINE_WORDS (8) words — computed in code (line-length is countable,
    no LLM judgment needed), not part of the LLM-facing Critique schema.
    Informational: flags a distribution problem across all of that host's
    lines, not any one line to rewrite."""

    host: str
    short_line_fraction: float  # fraction of this host's lines under SHORT_LINE_WORDS words
    line_count: int  # total lines from this host, for context


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
    terse_hosts: list[HostBrevityFlag]
    usage: list[TokenUsage] = Field(default_factory=list)
    retried: bool = False  # see OutlineOutput.retried
    # Human-readable descriptions of caps that were still over after one
    # regeneration attempt (catchphrase, listener-name) — style rules
    # degrade, they don't fail the run. See OutlineOutput.repairs and
    # docs/decisions.md ("Correctness invariants vs quality signals").
    repairs: list[str] = Field(default_factory=list)


class PerformedLine(BaseModel):
    """One line rewritten for voice performance — same content as its source
    Line, restructured for how it should sound spoken. `delivery` here is a
    free-text description of the line's ARC ("starts flat, rises into the
    joke", "warm, slowing at the end", "quick, cutting in") — NOT a bracket-
    tag keyword like Line.delivery. Any v3 audio tag ("[laughs]", "[sighs]")
    belongs inline inside `text` at the point the emotion actually shifts
    (see docs/decisions.md, "perform stage") — tts_stage sends `text` as-is,
    with no bracket-prefixing step."""

    speaker: str
    text: str
    pause_ms: int | None = None
    delivery: str | None = None  # the delivery arc, e.g. "warm, slowing at the end"


class PerformedSegment(BaseModel):
    """Mirrors Segment, but headline/source_ids are never trusted from the
    performance-writing model — perform_stage overwrites both from the
    matching original segment by index after generation, so grounding stays
    structural rather than re-derived from a rewritten segment."""

    headline: str
    source_ids: list[str]
    lines: list[PerformedLine]


class Performance(BaseModel):
    title: str
    cold_open: list[PerformedLine]
    segments: list[PerformedSegment]
    outro: list[PerformedLine]


class FactChangeFlag(BaseModel):
    """One line the perform stage's fact-check pass judged to have changed
    meaning/facts during performance rewriting, not just phrasing.
    `segment_index` is the segment's position in the original revised_script
    (None means the outro). Informational, like CritiqueOutput's
    over_budget_segments/terse_hosts — reported, not auto-corrected."""

    segment_index: int | None
    speaker: str
    original_text: str
    performed_text: str
    reason: str  # one line: what changed and why it matters


class FactCheck(BaseModel):
    """Structured-output wrapper for the fact-check call — same convention
    as Critique/ScoreBatch wrapping a list for chat.completions.parse."""

    flags: list[FactChangeFlag]


class PerformOutput(BaseModel):
    """Typed output of the perform stage, persisted as performance.json.
    tts_stage reads this instead of script.json/critique.json from here on
    — see docs/decisions.md ("perform stage")."""

    episode_id: str
    generated_at: datetime
    model: str  # the performance-writing call's model (profile.llm.script_model)
    fact_check_model: str  # the fact-check call's model (profile.llm.model)
    performance: Performance
    fact_flags: list[FactChangeFlag]
    usage: list[TokenUsage] = Field(default_factory=list)
    # Whether the performance-writing call (not the fact-check call, which
    # is never retried) needed generate_with_retry's one retry — see
    # OutlineOutput.retried. Explicit, not inferred from len(usage): this
    # stage appends a second usage entry (the fact-check call) on every
    # normal run, which would make a len(usage)>1 heuristic wrong here.
    retried: bool = False
    # Words the final performance exceeds perform.MAX_WORD_OVERRUN's cap by;
    # 0 = within cap. A quality signal, not a correctness invariant — an
    # overrun gets one regeneration attempt (see perform_stage) but never
    # fails the run; quality_stage surfaces whatever remains as a flag
    # instead. See docs/decisions.md ("Correctness invariants vs quality
    # signals").
    word_overrun: int = 0
    # Human-readable descriptions of quality-signal repairs this stage made
    # — a segment count trimmed back to the original's (see
    # _reconcile_segment_count), and/or the word-cap outcome (resolved by
    # regenerating, or still over after the one attempt). See
    # OutlineOutput.repairs and docs/decisions.md ("Correctness invariants
    # vs quality signals").
    repairs: list[str] = Field(default_factory=list)


class GroundingQuality(BaseModel):
    """Proxy signals for how well-grounded the episode is — computed from
    existing artefacts, no new LLM call. See docs/decisions.md ("Quality
    metrics") for why each was chosen and what it can't measure (the short
    version: none of these can tell a *correctly* grounded claim from a
    fluently-worded one; they only measure the pipeline's own bookkeeping
    around sourcing and self-correction)."""

    source_count: int  # distinct source_ids actually cited across the outline's stories
    evergreen_share: float  # fraction of stories that are an evergreen primer, not fresh news
    fact_drift_flags: int  # PerformOutput.fact_flags count — perform's own fact-check pass
    critique_flags: int  # CritiqueOutput.critique.flags count
    critique_rewrite_rate: float  # critique_flags / original script's line count
    # Per-stage: did that stage need generate_with_retry's one retry? From
    # each Output's own `retried` field (OutlineOutput.retried etc.), not
    # inferred from usage counts — see docs/decisions.md ("Quality
    # metrics").
    stage_retries: dict[str, bool]
    total_retries: int  # sum of stage_retries.values(), for a single at-a-glance number
    # PerformOutput.word_overrun, copied straight through — a quality
    # signal, not a correctness invariant, so perform_stage never fails the
    # run over it; surfaced here instead. See docs/decisions.md
    # ("Correctness invariants vs quality signals").
    word_overrun: int = 0
    # OutlineOutput.repairs + CritiqueOutput.repairs + PerformOutput.repairs,
    # concatenated and prefixed by stage ("outline: ...", "critique: ...",
    # "perform: ..."), so the dashboard has one place to show everything a
    # quality signal caused code to auto-correct this episode. See
    # docs/decisions.md ("Correctness invariants vs quality signals").
    repairs: list[str] = Field(default_factory=list)


class NaturalnessQuality(BaseModel):
    """Proxies for how much the performed script reads like speech rather
    than prose read aloud — computed from performance.json's text, no new
    LLM call. See docs/decisions.md ("Quality metrics")."""

    audio_tag_density_per_100_words: float  # "[laughs]"-style inline tags per 100 words
    interjection_or_dash_share: float  # fraction of lines opening with a disfluency or ending with an em dash
    host_balance: float  # 0-1; 1.0 = the two hosts' mean words-per-line are equal
    catchphrase_count: int  # recurring (2+) verbatim 4-word phrases per host, summed


class JudgeScore(BaseModel):
    """One axis of the quality judge's verdict."""

    score: int = Field(ge=1, le=5)
    reason: str  # one line


class QualityJudge(BaseModel):
    """The one new LLM call this feature adds — see docs/decisions.md
    ("Quality metrics") for why exactly one call, on the cheap model, and
    why these two axes specifically."""

    naturalness: JudgeScore
    stance_clarity: JudgeScore
    model: str


class QualityOutput(BaseModel):
    """Typed output of the quality stage, persisted as quality.json. Runs
    after perform, before tts — grading the text that will actually be
    synthesized, before spending money synthesizing it. Every number here
    is a PROXY, not a verdict — see docs/decisions.md ("Quality metrics")
    for what each one can and can't measure; the UI must label them as
    such, never as a pass/fail quality gate."""

    episode_id: str
    generated_at: datetime
    grounding: GroundingQuality
    naturalness: NaturalnessQuality
    judge: QualityJudge
    # Copied straight from outline.json's own host_moods — not recomputed —
    # so the dashboard/episode card can show mood varying episode to
    # episode without a second read. See docs/decisions.md ("Persona
    # rigidity").
    host_moods: list[HostMood] = Field(default_factory=list)
    usage: list[TokenUsage] = Field(default_factory=list)  # the one judge call


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
