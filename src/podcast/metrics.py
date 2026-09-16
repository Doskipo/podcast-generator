"""Pure aggregation logic behind GET /metrics/summary — cost breakdowns
(from persisted manifests, or from a mocked row's own columns — see
db.EpisodeRecord.mocked), per-stage timing, topic distribution, play/
completion/retention math, and the 30-day daily series.

Deliberately independent of FastAPI/pydantic response schemas (api/
routes_metrics.py maps `aggregate_summary`'s plain dataclasses onto the
API's MetricsSummary) so it's unit-testable on its own — see
tests/test_metrics.py. No network calls.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from podcast import db, paths
from podcast.models import (
    CritiqueOutput,
    OutlineOutput,
    PerformOutput,
    QualityOutput,
    RankOutput,
    ScriptOutput,
    StitchOutput,
    TokenUsage,
    TTSOutput,
)
from podcast.service import COST_PER_1K_CHARS_USD
from podcast.stages.perform import flatten_performed_lines

DAILY_SERIES_DAYS = 30
RECENT_FAILURES_LIMIT = 10

# Illustrative placeholder pricing (USD per 1K tokens) — like
# service.COST_PER_1K_CHARS_USD, this is NOT real OpenAI billing, just
# rough-order-of-magnitude list pricing for the two models this profile
# actually uses (llm.model / llm.script_model). See docs/decisions.md.
OPENAI_PRICING_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"prompt": 0.00015, "completion": 0.0006},
    "gpt-4o": {"prompt": 0.0025, "completion": 0.01},
}
# Fallback for a model not in the table above (a profile can set any
# llm.model/script_model string) — rough middle-of-the-road guess rather
# than crashing or silently pricing it at $0.
_DEFAULT_OPENAI_PRICING = {"prompt": 0.002, "completion": 0.008}

# Manifest filename + Output model for each OpenAI-backed pipeline stage
# that persists token usage. Order matches the pipeline's own stage order.
_USAGE_MANIFESTS: list[tuple[str, str, type]] = [
    ("rank", "ranked.json", RankOutput),
    ("outline", "outline.json", OutlineOutput),
    ("script", "script.json", ScriptOutput),
    ("critique", "critique.json", CritiqueOutput),
    ("perform", "performance.json", PerformOutput),
]


def estimate_openai_cost_usd(usage: list[TokenUsage]) -> float:
    total = 0.0
    for call in usage:
        rates = OPENAI_PRICING_PER_1K_TOKENS.get(call.model, _DEFAULT_OPENAI_PRICING)
        total += call.prompt_tokens / 1000 * rates["prompt"] + call.completion_tokens / 1000 * rates["completion"]
    return total


def estimate_elevenlabs_cost_usd(characters: int) -> float:
    return characters / 1000 * COST_PER_1K_CHARS_USD


@dataclass
class WpmMeasurement:
    words_per_minute: float
    episode_count: int
    total_words: int
    total_minutes: float


def measured_words_per_minute(episodes_dir: Path | None = None) -> WpmMeasurement | None:
    """The effective spoken words-per-minute across every completed episode
    on disk — words in performance.json (the text actually sent to
    synthesis) over stitch_manifest.json's measured audio duration. An
    episode only counts if it has BOTH files: a real, fully-synthesized
    episode, not a partial/in-progress run. Aggregated as
    total_words / total_minutes across every qualifying episode (not an
    average of per-episode rates), so longer episodes weigh proportionally
    more. Returns None if no qualifying episode exists yet.

    Not called automatically anywhere in the pipeline — used once, by hand,
    to set LLMSettings.words_per_minute's default; re-run as more real
    episodes accumulate. See docs/decisions.md ("Measured words-per-minute")."""
    base = episodes_dir or paths.EPISODES_DIR
    if not base.is_dir():
        return None

    total_words = 0
    total_minutes = 0.0
    episode_count = 0

    for episode_dir in sorted(base.iterdir()):
        perform_path = episode_dir / "performance.json"
        stitch_path = episode_dir / "stitch_manifest.json"
        if not (perform_path.is_file() and stitch_path.is_file()):
            continue

        perform_output = PerformOutput.model_validate_json(perform_path.read_text(encoding="utf-8"))
        stitch_output = StitchOutput.model_validate_json(stitch_path.read_text(encoding="utf-8"))

        minutes = stitch_output.duration_ms / 1000 / 60
        if minutes <= 0:
            continue

        words = sum(len(line.text.split()) for line in flatten_performed_lines(perform_output.performance))
        total_words += words
        total_minutes += minutes
        episode_count += 1

    if episode_count == 0:
        return None

    return WpmMeasurement(
        words_per_minute=total_words / total_minutes,
        episode_count=episode_count,
        total_words=total_words,
        total_minutes=round(total_minutes, 3),
    )


@dataclass
class StageCost:
    stage: str
    provider: str  # "openai" | "elevenlabs"
    cost_usd: float


@dataclass
class StageDuration:
    stage: str
    avg_elapsed_s: float
    count: int


@dataclass
class EpisodesByStatus:
    status: str
    count: int


@dataclass
class TopicCount:
    interest: str
    count: int


@dataclass
class DailyPoint:
    date: str  # YYYY-MM-DD
    episodes_created: int
    plays: int
    completions: int
    mocked: bool  # true if any row contributing to this day's counts is mocked


@dataclass
class RecentFailure:
    episode_id: str
    status: str  # "failed" | "no_content"
    stage_reached: str | None
    reason: str | None
    created_at: datetime
    mocked: bool


@dataclass
class QualityPoint:
    """One real episode's quality.json — see quality_series() and
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


def quality_series(episodes_dir: Path | None = None) -> list[QualityPoint]:
    """One point per real episode with a quality.json, oldest to newest —
    the dashboard's "quality over time" section. Mocked rows never have a
    quality.json (same "no manifest files on disk" rule mocked rows follow
    everywhere else — see docs/decisions.md, "Dashboard metrics"), so
    unlike the rest of this module, no separate mocked-filtering is needed
    here: scanning the directory tree already excludes them. Episode
    directories are named with a sortable timestamp prefix (see
    artefacts.new_episode_id), the same ordering assumption
    measured_words_per_minute() above already relies on."""
    base = episodes_dir or paths.EPISODES_DIR
    if not base.is_dir():
        return []

    points: list[QualityPoint] = []
    for episode_dir_path in sorted(base.iterdir()):
        quality_path = episode_dir_path / "quality.json"
        if not quality_path.is_file():
            continue
        q = QualityOutput.model_validate_json(quality_path.read_text(encoding="utf-8"))
        points.append(
            QualityPoint(
                episode_id=q.episode_id,
                date=q.generated_at.date().isoformat(),
                naturalness_score=q.judge.naturalness.score,
                stance_clarity_score=q.judge.stance_clarity.score,
                source_count=q.grounding.source_count,
                evergreen_share=q.grounding.evergreen_share,
                critique_flags=q.grounding.critique_flags,
                critique_rewrite_rate=q.grounding.critique_rewrite_rate,
                fact_drift_flags=q.grounding.fact_drift_flags,
                total_retries=q.grounding.total_retries,
                audio_tag_density_per_100_words=q.naturalness.audio_tag_density_per_100_words,
                interjection_or_dash_share=q.naturalness.interjection_or_dash_share,
                host_balance=q.naturalness.host_balance,
                catchphrase_count=q.naturalness.catchphrase_count,
            )
        )
    return points


@dataclass
class MetricsSummaryData:
    no_content: int
    episodes_by_status: list[EpisodesByStatus]
    cost_by_stage: list[StageCost]
    avg_cost_per_episode_usd: float | None
    avg_stage_duration_s: list[StageDuration]
    topic_distribution: list[TopicCount]
    plays_total: int
    completions_total: int
    completion_rate: float | None
    d7_retention: float | None
    interests_with_no_content: list[TopicCount]
    daily_series: list[DailyPoint]
    recent_failures: list[RecentFailure]
    quality_series: list[QualityPoint]
    has_mocked_data: bool


def episode_cost_breakdown(record: db.EpisodeRecord) -> list[StageCost]:
    """Every stage's cost for one episode. A **real** episode reads this
    straight from its persisted manifests under data/episodes/<id>/ — a
    stage not yet reached (or a manifest predating the `usage` field) simply
    contributes nothing/$0, same as everywhere else partial episodes are
    handled in this codebase. A **mocked** episode (`record.mocked`) has no
    manifest files at all — its breakdown comes from `mock_cost_by_stage`
    instead (a deliberate simplification: DB-only mock data, not fabricated
    manifests on disk — see docs/decisions.md)."""
    if record.mocked:
        breakdown = record.mock_cost_by_stage or {}
        return [
            StageCost(stage=stage, provider=provider, cost_usd=cost)
            for stage, by_provider in breakdown.items()
            for provider, cost in by_provider.items()
        ]

    dir_path = paths.EPISODES_DIR / record.episode_id
    costs: list[StageCost] = []
    for stage, filename, model in _USAGE_MANIFESTS:
        path = dir_path / filename
        if not path.exists():
            continue
        output = model.model_validate_json(path.read_text(encoding="utf-8"))
        costs.append(StageCost(stage=stage, provider="openai", cost_usd=estimate_openai_cost_usd(output.usage)))

    tts_path = dir_path / "tts_manifest.json"
    if tts_path.exists():
        tts_output = TTSOutput.model_validate_json(tts_path.read_text(encoding="utf-8"))
        costs.append(
            StageCost(stage="tts", provider="elevenlabs", cost_usd=estimate_elevenlabs_cost_usd(tts_output.total_characters))
        )

    return costs


def _episode_topic_counts(record: db.EpisodeRecord) -> Counter[str]:
    """Which interests actually got airtime in this episode — tallied from
    ranked.json's selected articles (real), or `mock_topic_counts` (mocked).
    A distinct signal from `no_content_interests`, which tracks the
    opposite: interests that got *zero* candidates."""
    if record.mocked:
        return Counter(record.mock_topic_counts or {})

    ranked_path = paths.EPISODES_DIR / record.episode_id / "ranked.json"
    if not ranked_path.exists():
        return Counter()
    rank_output = RankOutput.model_validate_json(ranked_path.read_text(encoding="utf-8"))
    return Counter(article.interest or "_extra" for article in rank_output.selected)


def _d7_retention(played_events: list[db.EventRecord]) -> float | None:
    """Classic D7: of the listeners whose first `played` event was on day D
    (and D is at least 7 days in the past, so there's been time to observe
    it), what fraction also played again exactly on day D+7. Only
    `user_id`-tagged events count — today that's mocked data exclusively
    (see `podcast.seed_metrics`); real playback events carry no listener
    identity yet, so this returns None until either mocked or future real
    per-listener data exists."""
    by_user: dict[str, set[date]] = defaultdict(set)
    for event in played_events:
        user_id = (event.metadata_json or {}).get("user_id")
        if user_id is not None:
            by_user[user_id].add(event.ts.date())

    if not by_user:
        return None

    today = datetime.now(timezone.utc).date()
    cohort_day0 = {user_id: min(dates) for user_id, dates in by_user.items()}
    eligible = [user_id for user_id, day0 in cohort_day0.items() if (today - day0).days >= 7]
    if not eligible:
        return None

    retained = sum(1 for user_id in eligible if (cohort_day0[user_id] + timedelta(days=7)) in by_user[user_id])
    return round(retained / len(eligible), 4)


def _daily_series(
    episodes: list[db.EpisodeRecord], played_events: list[db.EventRecord], completed_events: list[db.EventRecord]
) -> list[DailyPoint]:
    today = datetime.now(timezone.utc).date()
    days = [today - timedelta(days=offset) for offset in range(DAILY_SERIES_DAYS - 1, -1, -1)]

    created_by_day: dict[date, list[db.EpisodeRecord]] = defaultdict(list)
    for record in episodes:
        created_by_day[record.created_at.date()].append(record)
    plays_by_day: dict[date, list[db.EventRecord]] = defaultdict(list)
    for event in played_events:
        plays_by_day[event.ts.date()].append(event)
    completions_by_day: dict[date, list[db.EventRecord]] = defaultdict(list)
    for event in completed_events:
        completions_by_day[event.ts.date()].append(event)

    points: list[DailyPoint] = []
    for day in days:
        day_episodes = created_by_day.get(day, [])
        day_plays = plays_by_day.get(day, [])
        day_completions = completions_by_day.get(day, [])
        mocked = (
            any(r.mocked for r in day_episodes) or any(e.mocked for e in day_plays) or any(e.mocked for e in day_completions)
        )
        points.append(
            DailyPoint(
                date=day.isoformat(),
                episodes_created=len(day_episodes),
                plays=len(day_plays),
                completions=len(day_completions),
                mocked=mocked,
            )
        )
    return points


def _recent_failures(episodes: list[db.EpisodeRecord], events_by_ts: list[db.EventRecord]) -> list[RecentFailure]:
    # events_by_ts must already be sorted ascending by ts, so the last write
    # to this dict per episode_id is genuinely the most recent failure —
    # matters if an episode_id is ever reused (an explicit --episode-id
    # resume) and fails more than once.
    last_error_by_episode: dict[str, str] = {}
    for event in events_by_ts:
        if event.type == "failed":
            error = (event.metadata_json or {}).get("error")
            if error:
                last_error_by_episode[event.episode_id] = error

    failures = sorted(
        (r for r in episodes if r.status in ("failed", "no_content")), key=lambda r: r.created_at, reverse=True
    )[:RECENT_FAILURES_LIMIT]

    results: list[RecentFailure] = []
    for record in failures:
        if record.status == "no_content":
            reason = ", ".join(record.no_content_interests or []) or "no candidates found for any interest"
        else:
            # Prefer the denormalized column (set by call_stage/
            # mark_interrupted_episodes for every failure since it was
            # added) — falls back to the event scan for a row that failed
            # before this field existed, or a mocked row (seed_metrics
            # writes a "failed" event but not this column). See
            # docs/decisions.md ("Episode list: mocked, status filter,
            # resilience").
            reason = record.failure_reason or last_error_by_episode.get(record.episode_id)
        results.append(
            RecentFailure(
                episode_id=record.episode_id,
                status=record.status,
                stage_reached=record.stage_reached,
                reason=reason,
                created_at=record.created_at,
                mocked=record.mocked,
            )
        )
    return results


def aggregate_summary(session: Session, include_mocked: bool = False) -> MetricsSummaryData:
    """`include_mocked=False` (the dashboard default) computes every KPI
    below from real, non-mocked rows only — `podcast seed-metrics`' rows
    exist to give a fresh install something to render, not to be quietly
    blended into the numbers that judge the pipeline's actual behavior. See
    docs/decisions.md ("Re-measured words-per-minute, dashboard
    mocked-data toggle"). `has_mocked_data` is always computed over every
    row regardless of `include_mocked`, so the caller can tell whether
    opting in would change anything."""
    all_episodes = list(session.exec(select(db.EpisodeRecord)).all())
    all_events = sorted(session.exec(select(db.EventRecord)).all(), key=lambda e: e.ts)

    has_mocked_data = any(r.mocked for r in all_episodes) or any(e.mocked for e in all_events)

    episodes = all_episodes if include_mocked else [r for r in all_episodes if not r.mocked]
    events = all_events if include_mocked else [e for e in all_events if not e.mocked]

    status_counts = Counter(r.status for r in episodes)
    episodes_by_status = [EpisodesByStatus(status=status, count=count) for status, count in sorted(status_counts.items())]
    no_content = status_counts.get("no_content", 0)

    cost_totals: dict[tuple[str, str], float] = defaultdict(float)
    episode_total_cost: dict[str, float] = {}
    for record in episodes:
        breakdown = episode_cost_breakdown(record)
        episode_total_cost[record.episode_id] = sum(c.cost_usd for c in breakdown)
        for cost in breakdown:
            cost_totals[(cost.stage, cost.provider)] += cost.cost_usd
    cost_by_stage = [
        StageCost(stage=stage, provider=provider, cost_usd=round(cost, 4))
        for (stage, provider), cost in sorted(cost_totals.items())
    ]

    done_costs = [episode_total_cost[r.episode_id] for r in episodes if r.status == "done"]
    avg_cost_per_episode_usd = round(sum(done_costs) / len(done_costs), 4) if done_costs else None

    topic_counts: Counter[str] = Counter()
    for record in episodes:
        topic_counts.update(_episode_topic_counts(record))
    topic_distribution = [TopicCount(interest=topic, count=count) for topic, count in topic_counts.most_common()]

    duration_by_stage: dict[str, list[float]] = defaultdict(list)
    for event in events:
        if event.type != "stage_done":
            continue
        meta = event.metadata_json or {}
        stage, elapsed = meta.get("stage"), meta.get("elapsed_s")
        if stage is not None and elapsed is not None:
            duration_by_stage[stage].append(elapsed)
    avg_stage_duration_s = [
        StageDuration(stage=stage, avg_elapsed_s=round(sum(values) / len(values), 2), count=len(values))
        for stage, values in sorted(duration_by_stage.items())
    ]

    played_events = [e for e in events if e.type == "played"]
    completed_events = [e for e in events if e.type == "completed_playback"]
    plays_total = len(played_events)
    completions_total = len(completed_events)
    completion_rate = round(completions_total / plays_total, 4) if plays_total else None

    d7_retention = _d7_retention(played_events)

    no_content_counts: Counter[str] = Counter()
    for record in episodes:
        no_content_counts.update(record.no_content_interests or [])
    interests_with_no_content = [TopicCount(interest=topic, count=count) for topic, count in no_content_counts.most_common()]

    daily_series = _daily_series(episodes, played_events, completed_events)
    recent_failures = _recent_failures(episodes, events)

    return MetricsSummaryData(
        no_content=no_content,
        episodes_by_status=episodes_by_status,
        cost_by_stage=cost_by_stage,
        avg_cost_per_episode_usd=avg_cost_per_episode_usd,
        avg_stage_duration_s=avg_stage_duration_s,
        topic_distribution=topic_distribution,
        plays_total=plays_total,
        completions_total=completions_total,
        completion_rate=completion_rate,
        d7_retention=d7_retention,
        interests_with_no_content=interests_with_no_content,
        daily_series=daily_series,
        recent_failures=recent_failures,
        quality_series=quality_series(),
        has_mocked_data=has_mocked_data,
    )
