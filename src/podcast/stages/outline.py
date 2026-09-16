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
from podcast.llm_retry import generate_with_retry
from podcast.models import (
    Angle,
    Article,
    Episode,
    Host,
    HostStance,
    Outline,
    OutlineOutput,
    OutlineStory,
    Profile,
    RankOutput,
    RecurringBit,
    TokenUsage,
    Transition,
)
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Per-article snippet length for the outline prompt: this step is picking
# order/angles, not writing prose, so it only needs enough to judge a story's
# shape — a short summary/lead, not the full article text (see script.py for
# where the full text gets used).
ARTICLE_BRIEF_CHARS = 300

# Rough words reserved for the cold open + outro, out of duration_minutes *
# profile.llm.words_per_minute total — the rest is split across stories as
# word_budget, proportional to each story's rank score. See
# docs/decisions.md ("Script quality pass").
COLD_OPEN_OUTRO_RESERVE_WORDS = 120


def _render_article_brief(article: Article) -> str:
    snippet = article.summary or (article.text or "")[:ARTICLE_BRIEF_CHARS] or "(no summary)"
    marker = "[EVERGREEN PRIMER] " if article.source == "evergreen" else ""
    return f"{marker}[{article.source_id}] {article.title} — {snippet}"


_EVERGREEN_INSTRUCTION = (
    "Some articles are marked [EVERGREEN PRIMER] — these aren't news, they're background/"
    "reference material for an interest that had nothing fresh this episode (see "
    "docs/decisions.md, \"Evergreen fallback\"). Give each one its own story, never merged "
    "with a news article. Its angle should introduce or deepen the topic for a listener who "
    "already likes it — no urgency, no \"breaking\" framing, don't pretend it's new. "
    "host_take and tension_or_surprise should still say something genuine (a compelling "
    "detail or angle worth dwelling on), just never framed as a recent development.\n"
)


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

    host_names = ", ".join(f'"{host.name}"' for host in podcast.hosts)
    evergreen_line = _EVERGREEN_INSTRUCTION if any(a.source == "evergreen" for a in articles) else ""

    system_prompt = (
        "You are the showrunner for a two-host podcast, planning the episode before any "
        "dialogue is written.\n\n"
        f"Hosts:\n{host_lines}\n\n"
        f"Listener: {podcast.listener.name}.\n\n"
        f"Recurring bits available (referenced by id):\n{bits_block}\n\n"
        f"{evergreen_line}"
        "For the articles given, decide: an episode title, the order to cover the "
        "stories in, and for each story an angle — why it matters, the tension or "
        "surprise in it, which host should take the lead on it and why, and (optionally) "
        "one possible tangent or analogy from a host's backstory or the listener. If a "
        "tangent references the listener, it may only build on facts actually given "
        "above (currently just their name, unless more is listed) — never invent "
        "hobbies, opinions, or biography for them; if nothing concrete fits, ground the "
        "tangent in a host's own backstory instead, or leave tangent null.\n"
        "For each story, also give each host a stance: an attitude toward that specific "
        "story (e.g. excited, skeptical, moved, amused, bored, annoyed, protective — or "
        "another word that actually fits), one sentence on why, consistent with that "
        "host's persona and home turf above, and an arc — how the stance shifts by the "
        "end of the segment, if it does at all (null if it stays constant throughout). "
        f"Every story needs exactly one stance per host: {host_names}.\n"
        "For every story after the first, decide how it bridges from the story immediately "
        "before it: transition.kind is either \"link\" — only when there's a genuine "
        "connection to the previous story (a shared mechanism, the same person or "
        "organization, the same underlying tension) — or \"clean_transition\" — a short "
        "handoff with no claimed connection. Default to clean_transition when you're not "
        "sure a link is real; never invent a connection just to justify one. "
        "transition.text_hint is a short phrase (not full dialogue) describing the "
        "connection (for link) or the handoff framing (for clean_transition) for the "
        "script step to build from. The first story has no story before it — leave its "
        "transition null.\n"
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


def _response_model(bit_ids: list[str], host_names: list[str]) -> type[BaseModel]:
    """A structured-output schema shaped like Outline/OutlineStory, except:
    - `recurring_bit` is constrained to a Literal enum of `bit_ids`
      (`None`-only if there aren't any) — the model can't return an id that
      doesn't exist.
    - `stances` is not a list at all, but an object with one *required*
      field per host name, each a {attitude, why, arc} stance — not
      list[stance-with-a-host-field]. A list-of-discriminated-items schema
      lets a model return two stances for one host and none for another
      (seen in practice — see docs/decisions.md, "Structural schemas over
      post-hoc validation"); an object with a fixed, required key per host
      makes "exactly one stance per host" true by construction, the same
      way a Literal enum makes an invalid bit id unrepresentable.
    _validate_outline still re-checks both afterward as a backstop."""
    recurring_bit_type: type = (Literal[tuple(bit_ids)] | None) if bit_ids else type(None)

    stance_model = create_model(
        "StanceResponse",
        attitude=(str, ...),
        why=(str, ...),
        arc=(str | None, None),
    )
    # **{...} rather than literal kwargs: host names are runtime data, not
    # known field names at code-writing time — create_model accepts any
    # string key here even if it isn't a valid Python identifier.
    stances_model = create_model("StancesResponse", **{name: (stance_model, ...) for name in host_names})
    story_model = create_model(
        "OutlineStoryResponse",
        headline=(str, ...),
        source_ids=(list[str], ...),
        angle=(Angle, ...),
        recurring_bit=(recurring_bit_type, None),
        stances=(stances_model, ...),
        transition=(Transition | None, None),
    )
    return create_model(
        "OutlineResponse",
        title=(str, ...),
        stories=(list[story_model], ...),
    )


def _convert_story(story_response: BaseModel, host_names: list[str]) -> OutlineStory:
    """One response story -> canonical OutlineStory. Every field but
    `stances` round-trips via dump/revalidate (same shape); `stances` needs
    its own conversion since the response has it as an object keyed by host
    name (see _response_model), not the canonical list[HostStance]."""
    stances_response = story_response.stances
    stances = [
        HostStance(host=name, **getattr(stances_response, name).model_dump()) for name in host_names
    ]
    data = story_response.model_dump(exclude={"stances"})
    return OutlineStory.model_validate({**data, "stances": [s.model_dump() for s in stances]})


def _generate_outline(
    client: OpenAI, model: str, system_prompt: str, user_prompt: str, bit_ids: list[str], host_names: list[str]
) -> tuple[Outline, TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch. Builds
    the bit_ids/host_names-constrained schema (see _response_model), then
    converts the result back to the canonical Outline via _convert_story."""
    response_model = _response_model(bit_ids, host_names)
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
    )
    parsed = completion.choices[0].message.parsed
    stories = [_convert_story(story, host_names) for story in parsed.stories]
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    return Outline(title=parsed.title, stories=stories), usage


def _validate_outline(
    outline: Outline,
    known_ids: set[str],
    recurring_bits: list[RecurringBit],
    host_names: list[str],
    evergreen_ids: set[str] = frozenset(),
) -> None:
    known_bit_ids = {bit.effective_id for bit in recurring_bits}
    max_per_bit = {bit.effective_id: bit.max_per_episode for bit in recurring_bits}
    known_host_names = set(host_names)

    used_ids: set[str] = set()
    bit_counts: dict[str, int] = {}
    for i, story in enumerate(outline.stories):
        if i == 0:
            if story.transition is not None:
                raise ValueError(f"story {story.headline!r} is the first story and must not have a transition")
        elif story.transition is None:
            raise ValueError(
                f"story {story.headline!r} at position {i} must have a transition (link or clean_transition)"
            )

        used_ids.update(story.source_ids)
        story_ids = set(story.source_ids)
        if story_ids & evergreen_ids and story_ids - evergreen_ids:
            raise ValueError(
                f"story {story.headline!r} mixes an evergreen primer source with a real news source "
                f"({sorted(story_ids)}) — a primer must be its own story, never merged with news"
            )
        if story.recurring_bit is not None:
            if story.recurring_bit not in known_bit_ids:
                raise ValueError(f"outline references unknown recurring bit id: {story.recurring_bit!r}")
            bit_counts[story.recurring_bit] = bit_counts.get(story.recurring_bit, 0) + 1
            if story.angle.tangent:
                raise ValueError(
                    f"story {story.headline!r} has both recurring_bit {story.recurring_bit!r} and a "
                    "tangent — a segment may not carry both"
                )

        stance_hosts = [stance.host for stance in story.stances]
        if set(stance_hosts) != known_host_names or len(stance_hosts) != len(known_host_names):
            raise ValueError(
                f"story {story.headline!r} must have exactly one stance per host {sorted(known_host_names)}, "
                f"got {stance_hosts}"
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
    # Defensive backstop: podcast.service already stops the pipeline earlier
    # (episode status "no_content") whenever rank selects zero articles, so
    # this should never actually fire in the normal service-orchestrated
    # flow — but outline_stage must refuse on its own too, for any direct/
    # bypassed call, rather than ever let the script-writing step improvise
    # an episode grounded in nothing. See docs/decisions.md ("Grounding
    # guard").
    if not rank_output.selected:
        raise ValueError("outline_stage: rank_output.selected is empty — refusing to outline zero sources")

    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    articles = rank_output.selected
    known_ids = {a.source_id for a in articles}
    evergreen_ids = {a.source_id for a in articles if a.source == "evergreen"}
    bit_ids = [bit.effective_id for bit in profile.podcast.recurring_bits]
    host_names = [host.name for host in profile.podcast.hosts]

    system_prompt, user_prompt = _build_prompts(profile, articles)

    # A local accumulator, not a tuple threaded through generate_with_retry:
    # a first attempt that fails *validation* still made a real, billed API
    # call, so its usage must be counted too — not just the attempt that
    # ultimately succeeds. Keeps generate_with_retry itself generic/untouched.
    usage: list[TokenUsage] = []

    def generate(prompt: str) -> Outline:
        outline, call_usage = _generate_outline(client, profile.llm.model, system_prompt, prompt, bit_ids, host_names)
        usage.append(call_usage)
        return outline

    def validate(outline: Outline) -> None:
        _validate_outline(outline, known_ids, profile.podcast.recurring_bits, host_names, evergreen_ids)

    outline = generate_with_retry(generate, validate, user_prompt, stage_name="outline")

    for i, story in enumerate(outline.stories):
        if story.transition is not None:
            logger.info(
                "outline: story %d %r transition=%s hint=%r",
                i, story.headline, story.transition.kind, story.transition.text_hint,
            )

    # word_budget is code-computed, not asked of the model — proportional to
    # each story's rank score, summing to duration_minutes*words_per_minute
    # minus a reserve for the cold open/outro (see docs/decisions.md).
    score_by_id = {ra.article.source_id: ra.score for ra in rank_output.scored}
    target_words = round(profile.podcast.duration_minutes * profile.llm.words_per_minute)
    total_words = max(0, target_words - COLD_OPEN_OUTRO_RESERVE_WORDS)
    budgets = _allocate_word_budgets(outline.stories, score_by_id, total_words)
    for story, budget in zip(outline.stories, budgets):
        story.word_budget = budget

    # is_primer is code-derived, not asked of the model — same reasoning as
    # word_budget above. See docs/decisions.md ("Evergreen fallback").
    for story in outline.stories:
        story.is_primer = bool(set(story.source_ids) & evergreen_ids)

    output = OutlineOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        model=profile.llm.model,
        outline=outline,
        usage=usage,
    )

    out_path = episode_dir(episode.episode_id) / "outline.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
