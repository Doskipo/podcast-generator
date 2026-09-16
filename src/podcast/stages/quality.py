"""Quality stage: proxy metrics for how well-grounded and how natural an
episode is, plus one cheap-model judge call — computed after perform,
before tts (grading the text that will actually be synthesized before
spending money synthesizing it).

Typed input: Episode (+ Profile snapshot) and PerformOutput — outline.json,
script.json, and critique.json are read directly from `episode_dir` rather
than threaded through every caller, since all three are guaranteed to
exist by the time perform has succeeded (perform requires critique,
critique requires outline/script). This is what lets every entrypoint that
reaches a PerformOutput call this stage identically, including
resume_from_performance, which has no CritiqueOutput in scope at all.
Typed output: QualityOutput, persisted as
data/episodes/<episode_id>/quality.json.

Every number here is a PROXY, not a verdict — see docs/decisions.md
("Quality metrics") for why each metric was chosen and what it can't
measure. The one new LLM call (the judge) uses profile.llm.model (the
cheap model already used for scoring/query-generation elsewhere), not
profile.llm.script_model, and is never retried — see that same decisions
entry for why exactly one call.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from openai import OpenAI
from pydantic import BaseModel

from podcast.artefacts import load_critique_output, load_outline_output, load_script_output
from podcast.env import require_env
from podcast.models import (
    CritiqueOutput,
    Episode,
    GroundingQuality,
    JudgeScore,
    NaturalnessQuality,
    Outline,
    OutlineOutput,
    PerformedLine,
    Performance,
    PerformOutput,
    Profile,
    QualityJudge,
    QualityOutput,
    TokenUsage,
)
from podcast.paths import episode_dir
from podcast.stages.perform import flatten_performed_lines
from podcast.stages.script import flatten_lines

# ---- grounding -------------------------------------------------------


def _grounding_quality(
    outline_output: OutlineOutput,
    critique_output: CritiqueOutput,
    perform_output: PerformOutput,
    stage_retries: dict[str, bool],
) -> GroundingQuality:
    stories = outline_output.outline.stories
    # Distinct sources actually grounded into the narrative — not the size
    # of the candidate pool (RankOutput.selected), which measures what was
    # *available*, not what the episode actually used. See
    # docs/decisions.md ("Quality metrics").
    source_count = len({sid for story in stories for sid in story.source_ids})
    evergreen_share = (sum(1 for s in stories if s.is_primer) / len(stories)) if stories else 0.0

    fact_drift_flags = len(perform_output.fact_flags)
    critique_flags = len(critique_output.critique.flags)
    original_line_count = len(flatten_lines(critique_output.original_script))
    critique_rewrite_rate = (critique_flags / original_line_count) if original_line_count else 0.0

    return GroundingQuality(
        source_count=source_count,
        evergreen_share=round(evergreen_share, 4),
        fact_drift_flags=fact_drift_flags,
        critique_flags=critique_flags,
        critique_rewrite_rate=round(critique_rewrite_rate, 4),
        stage_retries=stage_retries,
        total_retries=sum(stage_retries.values()),
    )


# ---- naturalness proxies ----------------------------------------------

_AUDIO_TAG_RE = re.compile(r"\[[a-zA-Z][a-zA-Z ]*\]")

# The exact connector/interjection vocabulary script.py's own prompt asks
# the model to use (its system_prompt: interjections like "Wait—", "Hold
# on,", "Right, but—", and disfluencies "I mean", "no?", "okay so", "look",
# "wait") — reused here so "does it sound natural" is measured against the
# vocabulary the pipeline was actually told to produce, not an arbitrary
# external list. Deliberately narrow: a real natural-sounding line that
# doesn't use one of these words scores as "no interjection" here — this
# undercounts naturalness achieved some other way (see docs/decisions.md).
_INTERJECTION_OPENERS = ("wait", "hold on", "right,", "right—", "i mean", "no?", "okay so", "look,", "look—")

_CATCHPHRASE_N = 4  # word-length of the recurring-phrase window — see _catchphrase_count
_WORD_RE = re.compile(r"[a-z0-9']+")


def _starts_with_interjection(text: str) -> bool:
    return text.strip().lower().startswith(_INTERJECTION_OPENERS)


def _ends_with_dash(text: str) -> bool:
    return text.strip().endswith(("—", "--"))


def _audio_tag_density_per_100_words(lines: list[PerformedLine]) -> float:
    total_words = sum(len(line.text.split()) for line in lines)
    tag_count = sum(len(_AUDIO_TAG_RE.findall(line.text)) for line in lines)
    return round(tag_count / total_words * 100, 4) if total_words else 0.0


def _interjection_or_dash_share(lines: list[PerformedLine]) -> float:
    if not lines:
        return 0.0
    matches = sum(1 for line in lines if _starts_with_interjection(line.text) or _ends_with_dash(line.text))
    return round(matches / len(lines), 4)


def _host_balance(lines: list[PerformedLine]) -> float:
    """min/max of the two hosts' mean words-per-line — 1.0 is perfectly
    even, closer to 0 is lopsided. Can't tell a deliberately economical
    persona from an actually-neglected host (see docs/decisions.md)."""
    by_host: dict[str, list[int]] = defaultdict(list)
    for line in lines:
        by_host[line.speaker].append(len(line.text.split()))
    means = [sum(counts) / len(counts) for counts in by_host.values() if counts]
    if len(means) < 2:
        return 1.0  # fewer than two speakers with lines — nothing to compare
    lo, hi = min(means), max(means)
    return round(lo / hi, 4) if hi > 0 else 1.0


def _catchphrase_count(lines: list[PerformedLine]) -> int:
    """Per host, the count of distinct 4-word phrases that recur verbatim
    (2+ times) across the episode — an emergent-repetition proxy, not a
    configured catchphrase list (hosts have no such field today). 4 words
    balances false positives (common short phrases) against false
    negatives (missing a real repeated line); doesn't distinguish a
    deliberate running bit from unintentional repetitiveness — see
    docs/decisions.md."""
    by_host: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        by_host[line.speaker].extend(_WORD_RE.findall(line.text.lower()))

    total = 0
    for words in by_host.values():
        phrase_counts = Counter(tuple(words[i : i + _CATCHPHRASE_N]) for i in range(len(words) - _CATCHPHRASE_N + 1))
        total += sum(1 for count in phrase_counts.values() if count >= 2)
    return total


def _naturalness_quality(perform_output: PerformOutput) -> NaturalnessQuality:
    lines = flatten_performed_lines(perform_output.performance)
    return NaturalnessQuality(
        audio_tag_density_per_100_words=_audio_tag_density_per_100_words(lines),
        interjection_or_dash_share=_interjection_or_dash_share(lines),
        host_balance=_host_balance(lines),
        catchphrase_count=_catchphrase_count(lines),
    )


# ---- judge -------------------------------------------------------------


class _JudgeResponse(BaseModel):
    """Structured-output shape for the one judge call — mirrors QualityJudge
    minus `model`, which is code-set, not asked of the model."""

    naturalness: JudgeScore
    stance_clarity: JudgeScore


def _render_performed_script(performance: Performance) -> str:
    return "\n".join(f"{line.speaker}: {line.text}" for line in flatten_performed_lines(performance))


def _render_stances(outline: Outline) -> str:
    blocks = []
    for story in outline.stories:
        stance_bits = "; ".join(f"{s.host} should come across {s.attitude} ({s.why})" for s in story.stances)
        blocks.append(f"- {story.headline}: {stance_bits}")
    return "\n".join(blocks)


def _build_judge_prompts(profile: Profile, performance: Performance, outline: Outline) -> tuple[str, str]:
    host_names = ", ".join(h.name for h in profile.podcast.hosts)
    system_prompt = (
        "You are grading a two-host podcast script for exactly two things, each a 1-5 score with "
        "a one-line reason.\n\n"
        f"Hosts: {host_names}.\n\n"
        "- naturalness: does this read like two people actually talking, not written prose being "
        "read aloud? Consider rhythm, interruption, reaction, and whether any line sounds like it "
        "was written to be read rather than said.\n"
        "- stance_clarity: for each story below, does each host's intended attitude actually come "
        "through in their lines — distinctly from the other host, not just narrated?\n\n"
        f"Intended stances per story:\n{_render_stances(outline)}\n\n"
        "Score honestly: most real scripts land in the 2-4 range. Reserve 5 for genuinely "
        "excellent and 1 for genuinely broken. Each reason must be one specific line, not generic "
        "praise or criticism."
    )
    user_prompt = "Performed script (speaker: text):\n\n" + _render_performed_script(performance)
    return system_prompt, user_prompt


def _generate_judge(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> tuple[QualityJudge, TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch. No
    generate_with_retry here: this is the one new LLM call this feature is
    allowed, and a retry would make it two."""
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=_JudgeResponse,
    )
    parsed = completion.choices[0].message.parsed
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    judge = QualityJudge(naturalness=parsed.naturalness, stance_clarity=parsed.stance_clarity, model=model)
    return judge, usage


def quality_stage(episode: Episode, perform_output: PerformOutput, client: OpenAI | None = None) -> QualityOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    dir_path = episode_dir(episode.episode_id)
    outline_output = load_outline_output(dir_path)
    script_output = load_script_output(dir_path)
    critique_output = load_critique_output(dir_path)

    stage_retries = {
        "outline": outline_output.retried,
        "script": script_output.retried,
        "critique": critique_output.retried,
        "perform": perform_output.retried,
    }
    grounding = _grounding_quality(outline_output, critique_output, perform_output, stage_retries)
    naturalness = _naturalness_quality(perform_output)

    system_prompt, user_prompt = _build_judge_prompts(profile, perform_output.performance, outline_output.outline)
    judge, usage = _generate_judge(client, profile.llm.model, system_prompt, user_prompt)

    output = QualityOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        grounding=grounding,
        naturalness=naturalness,
        judge=judge,
        usage=[usage],
    )

    out_path = dir_path / "quality.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
