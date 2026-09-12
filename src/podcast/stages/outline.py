"""Outline stage: pick the episode's narrative before any dialogue is
written — title, story order, and an angle per story.

Typed input: Episode (+ Profile snapshot) and RankOutput (for the selected,
extracted articles). Typed output: OutlineOutput, persisted as
data/episodes/<episode_id>/outline.json.

First of three script-related LLM steps — see docs/decisions.md ("Script
rebuild") for why planning is split out from prose (podcast.stages.script)
and review (podcast.stages.critique).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, create_model

from podcast.env import require_env
from podcast.models import Angle, Article, Episode, Host, Outline, OutlineOutput, Profile, RankOutput, RecurringBit
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Per-article snippet length for the outline prompt: this step is picking
# order/angles, not writing prose, so it only needs enough to judge a story's
# shape — a short summary/lead, not the full article text (see script.py for
# where the full text gets used).
ARTICLE_BRIEF_CHARS = 300

# Rough words reserved for the cold open + outro, out of duration_minutes *
# 150 total — the rest is split across stories as word_budget, proportional
# to each story's rank score. See docs/decisions.md ("Script quality pass").
COLD_OPEN_OUTRO_RESERVE_WORDS = 120


def _render_article_brief(article: Article) -> str:
    snippet = article.summary or (article.text or "")[:ARTICLE_BRIEF_CHARS] or "(no summary)"
    return f"[{article.source_id}] {article.title} — {snippet}"


def _render_host_brief(host: Host) -> str:
    first_line = next((line.strip() for line in host.persona.splitlines() if line.strip()), "")
    turf = ", ".join(host.home_turf) or "general"
    return f"- {host.name} (home turf: {turf}): {first_line}"


def _build_prompts(profile: Profile, articles: list[Article]) -> tuple[str, str]:
    podcast = profile.podcast
    known_ids = ", ".join(a.source_id for a in articles)
    host_lines = "\n".join(_render_host_brief(host) for host in podcast.hosts)
    bits_block = (
        "\n".join(
            f"- {bit.effective_id}: {bit.description} (use at most {bit.max_per_episode} time(s) this episode)"
            for bit in podcast.recurring_bits
        )
        or "(none configured)"
    )

    system_prompt = (
        "You are the showrunner for a two-host podcast, planning the episode before any "
        "dialogue is written.\n\n"
        f"Hosts:\n{host_lines}\n\n"
        f"Listener: {podcast.listener.name}.\n\n"
        f"Recurring bits available (referenced by id):\n{bits_block}\n\n"
        "For the articles given, decide: an episode title, the order to cover the "
        "stories in, and for each story an angle — why it matters, the tension or "
        "surprise in it, which host should take the lead on it and why, and (optionally) "
        "one possible tangent or analogy from a host's backstory or the listener. If a "
        "tangent references the listener, it may only build on facts actually given "
        "above (currently just their name, unless more is listed) — never invent "
        "hobbies, opinions, or biography for them; if nothing concrete fits, ground the "
        "tangent in a host's own backstory instead, or leave tangent null.\n"
        "If a recurring bit genuinely fits a story, set recurring_bit to its id exactly "
        "as listed above (the response schema only accepts those ids, or null) — never "
        "exceed a bit's stated max uses per episode, and leave it null if nothing fits. "
        "A story that gets a recurring bit must leave tangent null — a segment carries "
        "the bit or a tangent, never both.\n"
        "Turn these articles into stories — normally one story per article, but merge "
        "two into a single story if they're closely related. Don't drop an article "
        "without folding its source_id into another story unless it's genuinely unusable.\n"
        f"Every story's source_ids must be a subset of the known ids: {known_ids}"
    )

    articles_block = "\n\n".join(_render_article_brief(a) for a in articles)
    user_prompt = "Ranked articles available for this episode:\n\n" + articles_block
    return system_prompt, user_prompt


def _response_model(bit_ids: list[str]) -> type[BaseModel]:
    """A structured-output schema shaped like Outline/OutlineStory, except
    `recurring_bit` is constrained to a Literal enum of `bit_ids` (`None`-only
    if there aren't any) instead of a free string — the model can't return an
    id that doesn't exist, so a mismatched/paraphrased bit reference (the bug
    this fixes) becomes a schema-level impossibility, not just a prompt ask.
    _validate_outline still re-checks it afterward as a backstop."""
    recurring_bit_type: type = (Literal[tuple(bit_ids)] | None) if bit_ids else type(None)

    story_model = create_model(
        "OutlineStoryResponse",
        headline=(str, ...),
        source_ids=(list[str], ...),
        angle=(Angle, ...),
        recurring_bit=(recurring_bit_type, None),
    )
    return create_model(
        "OutlineResponse",
        title=(str, ...),
        stories=(list[story_model], ...),
    )


def _generate_outline(
    client: OpenAI, model: str, system_prompt: str, user_prompt: str, bit_ids: list[str]
) -> Outline:
    """Boundary around the OpenAI call — the seam tests monkeypatch. Builds
    the bit_ids-constrained schema (see _response_model), then converts the
    result back to the canonical Outline (same fields, just recurring_bit's
    type differs) so the rest of the stage doesn't need to know about it."""
    response_model = _response_model(bit_ids)
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
    )
    parsed = completion.choices[0].message.parsed
    return Outline.model_validate(parsed.model_dump())


def _validate_outline(outline: Outline, known_ids: set[str], recurring_bits: list[RecurringBit]) -> None:
    known_bit_ids = {bit.effective_id for bit in recurring_bits}
    max_per_bit = {bit.effective_id: bit.max_per_episode for bit in recurring_bits}

    used_ids: set[str] = set()
    bit_counts: dict[str, int] = {}
    for story in outline.stories:
        used_ids.update(story.source_ids)
        if story.recurring_bit is not None:
            if story.recurring_bit not in known_bit_ids:
                raise ValueError(f"outline references unknown recurring bit id: {story.recurring_bit!r}")
            bit_counts[story.recurring_bit] = bit_counts.get(story.recurring_bit, 0) + 1
            if story.angle.tangent:
                raise ValueError(
                    f"story {story.headline!r} has both recurring_bit {story.recurring_bit!r} and a "
                    "tangent — a segment may not carry both"
                )

    unknown = used_ids - known_ids
    if unknown:
        raise ValueError(f"outline references unknown source_ids: {sorted(unknown)}")

    for bit_id, count in bit_counts.items():
        limit = max_per_bit[bit_id]
        if count > limit:
            raise ValueError(f"recurring bit {bit_id!r} used {count} times, exceeds max_per_episode={limit}")


def _story_score(story: OutlineStory, score_by_id: dict[str, float]) -> float:
    scores = [score_by_id.get(sid, 0.0) for sid in story.source_ids]
    return (sum(scores) / len(scores)) if scores else 0.0


def _allocate_word_budgets(stories: list[OutlineStory], score_by_id: dict[str, float], total_words: int) -> list[int]:
    """Proportional-to-score word budgets summing exactly to total_words, via
    the same largest-remainder method rank.py uses for per-interest article
    budgets (floor each story's share, hand the leftover words to the
    largest fractional remainders). Falls back to an equal split if every
    story scores 0 (nothing to be proportional to)."""
    if not stories:
        return []

    scores = [_story_score(story, score_by_id) for story in stories]
    total_score = sum(scores)
    if total_score <= 0:
        scores = [1.0] * len(stories)
        total_score = float(len(stories))

    raw = [total_words * score / total_score for score in scores]
    floors = [int(share) for share in raw]
    remainder = total_words - sum(floors)

    by_fraction = sorted(range(len(stories)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in by_fraction[:remainder]:
        floors[i] += 1
    return floors


def outline_stage(episode: Episode, rank_output: RankOutput, client: OpenAI | None = None) -> OutlineOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    articles = rank_output.selected
    known_ids = {a.source_id for a in articles}
    bit_ids = [bit.effective_id for bit in profile.podcast.recurring_bits]

    system_prompt, user_prompt = _build_prompts(profile, articles)
    outline = _generate_outline(client, profile.llm.model, system_prompt, user_prompt, bit_ids)

    _validate_outline(outline, known_ids, profile.podcast.recurring_bits)

    # word_budget is code-computed, not asked of the model — proportional to
    # each story's rank score, summing to duration_minutes*150 minus a
    # reserve for the cold open/outro (see docs/decisions.md).
    score_by_id = {ra.article.source_id: ra.score for ra in rank_output.scored}
    total_words = max(0, profile.podcast.duration_minutes * 150 - COLD_OPEN_OUTRO_RESERVE_WORDS)
    budgets = _allocate_word_budgets(outline.stories, score_by_id, total_words)
    for story, budget in zip(outline.stories, budgets):
        story.word_budget = budget

    output = OutlineOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        model=profile.llm.model,
        outline=outline,
    )

    out_path = episode_dir(episode.episode_id) / "outline.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
