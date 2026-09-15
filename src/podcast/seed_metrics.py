"""`podcast seed-metrics` — writes mocked but plausible usage data (episodes,
plays, completions, a decaying-retention audience) into the episodes/events
tables, every row flagged `mocked=True`, so /dashboard has something to show
on a fresh DB. See docs/decisions.md ("Dashboard metrics") for the modeling
choices below.

Real pipeline code never touches this flag: mocked rows are written directly
via `db.EpisodeRecord`/`db.EventRecord` here, never through
`service.run_episode`/`service.emit_event` — "real events are never mocked"
holds structurally, not just by convention.
"""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from sqlmodel import Session, select

from podcast import db, metrics, service
from podcast.models import Profile, TokenUsage

# Pipeline stage order — a local copy, not imported from generate.py, to
# avoid a generate.py <-> seed_metrics.py import cycle (generate.py's own
# main() dispatches to this module's seed()).
STAGES = ["fetch", "rank", "outline", "script", "critique", "perform", "tts", "stitch"]

_LLM_MODELS = {
    "rank": "gpt-4o-mini",
    "outline": "gpt-4o-mini",
    "script": "gpt-4o",
    "critique": "gpt-4o",
    "perform": "gpt-4o",
}

# Plausible (prompt_lo, prompt_hi, completion_lo, completion_hi) token counts
# per LLM stage — order-of-magnitude estimates from docs/decisions.md
# ("Script rebuild" cost table), not measured. "perform" only covers its
# primary performance-writing call — the fact-check call's cheap-model cost
# isn't separately mocked, same convention as rank's unmocked echo-mismatch
# rescoring cost (see docs/decisions.md, "perform stage").
_STAGE_TOKEN_RANGES = {
    "rank": (600, 900, 300, 500),
    "outline": (700, 1100, 400, 700),
    "script": (2500, 3600, 1700, 2500),
    "critique": (1800, 2500, 200, 400),
    "perform": (2200, 3200, 1400, 2200),
}
_STAGE_ELAPSED_RANGES_S = {
    "fetch": (5, 20),
    "rank": (10, 40),
    "outline": (5, 15),
    "script": (15, 45),
    "critique": (10, 30),
    "perform": (10, 30),
    "tts": (30, 90),
    "stitch": (2, 8),
}
_TTS_CHAR_RANGE = (5000, 9000)
_STATUS_WEIGHTS = [("done", 0.80), ("failed", 0.12), ("no_content", 0.08)]
_EPISODE_CHANCE = 0.85
_COMPLETION_RATE_RANGE = (0.65, 0.75)
_FAILURE_MESSAGES = [
    "OpenAI request timed out after 60s",
    "ElevenLabs API returned 429 rate_limited",
    "trafilatura extraction failed for every backfill candidate",
    "OpenAI structured-output validation failed twice",
    "ffmpeg exited with a non-zero status while stitching segments",
]


def _ensure_profile(session: Session) -> db.ProfileRecord:
    row = session.exec(select(db.ProfileRecord)).first()
    if row is not None:
        return row
    row = service.seed_profile_from_yaml_if_empty(session)
    if row is None:
        raise RuntimeError(
            "seed-metrics: no profile configured and none could be seeded from "
            f"{service.DEFAULT_PROFILE_PATH} — configure a profile first "
            "(PUT /profile, or `uv run podcast import-profile <path>`)"
        )
    return row


def _delete_mocked(session: Session) -> None:
    for event in session.exec(select(db.EventRecord).where(db.EventRecord.mocked.is_(True))).all():
        session.delete(event)
    for record in session.exec(select(db.EpisodeRecord).where(db.EpisodeRecord.mocked.is_(True))).all():
        session.delete(record)
    session.commit()


def _weighted_choice(rng: random.Random, weights: list[tuple[str, float]]) -> str:
    total = sum(w for _, w in weights)
    roll = rng.random() * total
    upto = 0.0
    for label, weight in weights:
        upto += weight
        if roll <= upto:
            return label
    return weights[-1][0]


def _sample_usage(rng: random.Random, stage: str) -> TokenUsage:
    model = _LLM_MODELS[stage]
    prompt_lo, prompt_hi, completion_lo, completion_hi = _STAGE_TOKEN_RANGES[stage]
    return TokenUsage(model=model, prompt_tokens=rng.randint(prompt_lo, prompt_hi), completion_tokens=rng.randint(completion_lo, completion_hi))


def _episode_progression(rng: random.Random, status: str) -> tuple[list[str], str | None]:
    """Returns (stages that ran to completion, the stage that failed — None
    for `done`/`no_content`). `no_content` always stops right after `rank`,
    mirroring the real grounding guard (service._check_grounding): rank
    itself succeeds, it's just that it selected nothing."""
    if status == "done":
        return STAGES, None
    if status == "no_content":
        rank_idx = STAGES.index("rank")
        return STAGES[: rank_idx + 1], None
    idx = rng.randint(0, len(STAGES) - 1)
    return STAGES[:idx], STAGES[idx]


def _build_episode(
    rng: random.Random, episode_id: str, created_at: datetime, profile_id: int, interests: list[str], status: str
) -> tuple[db.EpisodeRecord, list[tuple[str, float, dict]]]:
    """One mocked episode row plus its (event_type, seconds_after_created_at,
    metadata) event specs."""
    succeeded, failed_stage = _episode_progression(rng, status)
    stage_reached = succeeded[-1] if succeeded else failed_stage

    cost_by_stage: dict[str, dict[str, float]] = {}
    total_characters: int | None = None
    cumulative_s = 0.0
    events: list[tuple[str, float, dict]] = []

    for stage in succeeded:
        elapsed = round(rng.uniform(*_STAGE_ELAPSED_RANGES_S[stage]), 2)
        cumulative_s += elapsed
        events.append(("stage_done", cumulative_s, {"stage": stage, "elapsed_s": elapsed}))
        if stage in _LLM_MODELS:
            usage = _sample_usage(rng, stage)
            cost_by_stage[stage] = {"openai": round(metrics.estimate_openai_cost_usd([usage]), 5)}
        elif stage == "tts":
            total_characters = rng.randint(*_TTS_CHAR_RANGE)
            cost_by_stage["tts"] = {"elevenlabs": round(metrics.estimate_elevenlabs_cost_usd(total_characters), 5)}

    if failed_stage is not None:
        elapsed = round(rng.uniform(*_STAGE_ELAPSED_RANGES_S[failed_stage]), 2)
        cumulative_s += elapsed
        events.append(("failed", cumulative_s, {"stage": failed_stage, "error": rng.choice(_FAILURE_MESSAGES)}))
    elif status == "no_content":
        events.append(("no_content", cumulative_s, {"interests": interests}))
    else:
        events.append(("completed", cumulative_s, {"duration_s": round(cumulative_s, 2)}))

    topic_counts: dict[str, int] = {}
    if status != "no_content" and interests:
        k = min(len(interests), rng.randint(2, min(4, len(interests))))
        for topic in rng.sample(interests, k=k):
            topic_counts[topic] = rng.randint(1, 3)

    cost_estimate_usd = (
        round(metrics.estimate_elevenlabs_cost_usd(total_characters), 4) if status == "done" and total_characters else None
    )

    record = db.EpisodeRecord(
        episode_id=episode_id,
        profile_id=profile_id,
        status=status,
        stage_reached=stage_reached,
        created_at=created_at,
        duration_s=round(cumulative_s, 2) if status == "done" else None,
        total_characters=total_characters,
        cost_estimate_usd=cost_estimate_usd,
        no_content_interests=list(interests) if status == "no_content" else None,
        mocked=True,
        mock_cost_by_stage=cost_by_stage or None,
        mock_topic_counts=topic_counts or None,
    )
    return record, events


def _join_day_by_user(rng: random.Random, user_ids: list[str], window_days: list[date]) -> dict[str, date]:
    """Staggers listener join days across roughly the first 70% of the
    window, so every cohort still has at least some runway left for a D7
    retention check by the end of the window."""
    last_joinable_index = max(1, int(len(window_days) * 0.7))
    return {uid: window_days[rng.randint(0, last_joinable_index)] for uid in user_ids}


def _active_days_by_user(
    rng: random.Random, user_ids: list[str], window_days: list[date], join_day_by_user: dict[str, date]
) -> dict[str, set[date]]:
    """A simple decaying-return-probability model per listener: ~90% chance
    to listen the day they join, decaying toward a ~10% floor — tuned so the
    aggregate D7 retention (see podcast.metrics._d7_retention) lands in a
    plausible ~30-45% range, a stand-in for "some people become regulars,
    most drift off."""
    activity: dict[str, set[date]] = {}
    for uid in user_ids:
        join_day = join_day_by_user[uid]
        active: set[date] = set()
        for day in window_days:
            if day < join_day:
                continue
            days_since_join = (day - join_day).days
            probability = 0.90 if days_since_join == 0 else max(0.10, 0.90 * (0.85**days_since_join))
            if rng.random() < probability:
                active.add(day)
        activity[uid] = active
    return activity


def seed(session: Session, users: int = 40, days: int = 90, random_seed: int = 42, force: bool = False) -> dict:
    """Deterministic for a given (users, days, random_seed) — reseeds are
    reproducible, which matters for demos and for tests/test_seed_metrics.py.
    Window is strictly `[today - days, today - 1]`, so mocked data never
    lands on the same calendar day as real data (see docs/decisions.md)."""
    rng = random.Random(random_seed)
    profile_row = _ensure_profile(session)
    interests = [interest.topic for interest in Profile.model_validate(profile_row.data).interests] or ["general"]

    if force:
        _delete_mocked(session)

    today = datetime.now(timezone.utc).date()
    window_days = [today - timedelta(days=offset) for offset in range(days, 0, -1)]

    episodes_seeded = 0
    events_seeded = 0
    published: list[tuple[date, str]] = []  # (day, episode_id) for status == "done", ascending

    for day in window_days:
        if rng.random() > _EPISODE_CHANCE:
            continue
        episode_id = f"mock-{day.strftime('%Y%m%d')}"
        created_at = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
            hours=7, minutes=rng.randint(0, 20)
        )
        status = _weighted_choice(rng, _STATUS_WEIGHTS)
        record, event_specs = _build_episode(rng, episode_id, created_at, profile_row.id, interests, status)

        session.add(record)
        session.add(db.EventRecord(episode_id=episode_id, type="generated", ts=created_at, metadata_json={"profile": profile_row.name}, mocked=True))
        events_seeded += 1
        for event_type, offset_s, metadata in event_specs:
            session.add(
                db.EventRecord(
                    episode_id=episode_id, type=event_type, ts=created_at + timedelta(seconds=offset_s), metadata_json=metadata, mocked=True
                )
            )
            events_seeded += 1

        episodes_seeded += 1
        if status == "done":
            published.append((day, episode_id))

    session.commit()

    user_ids = [f"mock-user-{i:02d}" for i in range(1, users + 1)]
    join_day_by_user = _join_day_by_user(rng, user_ids, window_days)
    activity = _active_days_by_user(rng, user_ids, window_days, join_day_by_user)

    plays_seeded = 0
    completions_seeded = 0
    for uid, active_days in activity.items():
        for day in sorted(active_days):
            # "catch up" on the latest episode published on or before this
            # active day — a listener doesn't only ever play the day-of.
            candidates = [ep for d, ep in published if d <= day]
            if not candidates:
                continue
            episode_id = candidates[-1]
            played_at = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
                hours=rng.randint(7, 22), minutes=rng.randint(0, 59)
            )
            session.add(db.EventRecord(episode_id=episode_id, type="played", ts=played_at, metadata_json={"user_id": uid}, mocked=True))
            events_seeded += 1
            plays_seeded += 1
            if rng.random() < rng.uniform(*_COMPLETION_RATE_RANGE):
                completed_at = played_at + timedelta(minutes=rng.randint(5, 12))
                session.add(
                    db.EventRecord(episode_id=episode_id, type="completed_playback", ts=completed_at, metadata_json={"user_id": uid}, mocked=True)
                )
                events_seeded += 1
                completions_seeded += 1

    session.commit()

    return {
        "episodes": episodes_seeded,
        "events": events_seeded,
        "plays": plays_seeded,
        "completions": completions_seeded,
        "users": users,
        "days": days,
        "window_start": window_days[0].isoformat(),
        "window_end": window_days[-1].isoformat(),
    }
