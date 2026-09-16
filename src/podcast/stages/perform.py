"""Perform stage: rewrite the critique-reviewed script for voice performance
— not content. Punctuation for flow, spoken (not enumerated) lists, one
capitalised emphasis word per line where it matters, a delivery arc plus
inline v3 audio tags, varied sentence contours, and a few lines of cold-open
chit-chat before the existing show/host identification.

Typed input: Episode (+ Profile snapshot), CritiqueOutput, and the ranked
articles (to re-check grounding on the performed text). Typed output:
PerformOutput, persisted as data/episodes/<episode_id>/performance.json.

Fourth of four script-related LLM steps — outline (podcast.stages.outline)
picks the narrative, script (podcast.stages.script) writes the prose,
critique (podcast.stages.critique) reviews it for correctness/naturalness,
and this stage is the last one to touch line text/delivery before tts_stage
sends it straight to synthesis. See docs/decisions.md ("perform stage") for
why performance is split out from critique, and why it's the last
audio-shaping change before synthesis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from openai import OpenAI

from podcast.env import require_env
from podcast.llm_retry import generate_with_retry
from podcast.models import (
    Article,
    CritiqueOutput,
    Episode,
    FactChangeFlag,
    FactCheck,
    Performance,
    PerformedLine,
    PerformOutput,
    Profile,
    Script,
    TokenUsage,
)
from podcast.paths import episode_dir
from podcast.stages.script import flatten_lines

logger = logging.getLogger(__name__)

# Performance rewriting (flow punctuation, spoken lists, cold-open
# chit-chat) may lengthen the script somewhat, but must not turn into
# padding — the total word count (across cold_open + segments + outro) may
# exceed the critique script's own word count by at most this fraction. See
# docs/decisions.md ("Measured words-per-minute").
MAX_WORD_OVERRUN = 0.10


def flatten_performed_lines(performance: Performance) -> list[PerformedLine]:
    """Cold open, then every segment's lines in order, then the outro — the
    same canonical line order as script.py:flatten_lines. Public: imported
    by tts_stage."""
    return [
        *performance.cold_open,
        *(line for segment in performance.segments for line in segment.lines),
        *performance.outro,
    ]


def _render_script_for_performance(script: Script) -> str:
    return "\n".join(f"[{i}] {line.speaker}: {line.text}" for i, line in enumerate(flatten_lines(script)))


def _build_performance_prompts(profile: Profile, script: Script) -> tuple[str, str]:
    hosts = profile.podcast.hosts
    host_names = ", ".join(h.name for h in hosts)
    cold_open_speakers = [line.speaker for line in script.cold_open]
    original_words = sum(len(line.text.split()) for line in flatten_lines(script))
    max_words = int(original_words * (1 + MAX_WORD_OVERRUN))

    system_prompt = (
        "You are a vocal performance director for a two-host podcast. You are handed a "
        "finished, fact-checked script and your only job is to rewrite it for how it should "
        "sound SPOKEN — never change what it says.\n\n"
        f"Hosts: {host_names}.\n\n"
        "Hard rules:\n"
        "1. Punctuation for flow: use commas, ellipses and dashes for a continuing thought; "
        "a full stop only where the speaker genuinely stops. A host should never read like a "
        "series of separate declarations.\n"
        "2. Lists are spoken, not enumerated: use connectors ('and then', 'or', 'also'), "
        "group related items, and land on an emphatic last item — never a flat comma-separated "
        "inventory.\n"
        "3. Emphasis: capitalise the ONE word per line the voice should press, only when it "
        "genuinely matters (not every line needs one). Use an ellipsis right before a reveal "
        "for emphasis.\n"
        "4. Dynamics: set `delivery` to a short description of the line's ARC, not a single "
        "static mood — e.g. 'starts flat, rises into the joke', 'warm, slowing at the end', "
        "'quick, cutting in'. Place any v3 audio tag (e.g. [laughs], [sighs]) INSIDE the "
        "line's own text, at the exact point the emotion turns — not just at the start. "
        "Goodbyes, toasts, and punchlines get rising energy.\n"
        "5. Vary: no host should use the same sentence contour twice in a row — alternate "
        "short and long lines.\n"
        "6. Cold open: add a FEW lines of host chit-chat BEFORE the existing cold-open lines "
        "below — a greeting, then a callback to the show or the listener — and only after "
        "that let the existing identification line(s) follow (rewritten for flow, but keep "
        "them, don't cut them).\n\n"
        "Structural contract — the reviewer will reject anything that breaks this:\n"
        "- Every segment and the outro must end up with the SAME number of lines, in the SAME "
        "speaker order, as the numbered script below — you may only change a line's text, "
        "delivery, and pause_ms, never add, remove, merge, or reorder lines within a segment "
        "or the outro.\n"
        f"- The cold open's existing {len(cold_open_speakers)} line(s) — speakers in order: "
        f"{cold_open_speakers} — must appear, rewritten for flow, as the LAST "
        f"{len(cold_open_speakers)} line(s) of your cold_open. Any chit-chat you add must come "
        "before them, never in place of them.\n"
        f"- Only use these speakers: {host_names}.\n"
        "- Never change a fact, number, name, or claim — this is a performance rewrite, not a "
        "content rewrite. If you wouldn't say it changes what the line means, it's fine.\n"
        f"- The original script below is {original_words} words. Your performed version — "
        f"including any cold-open chit-chat you add — must not exceed {max_words} words "
        f"({int(MAX_WORD_OVERRUN * 100)}% over the original). Rewriting for flow should not mean "
        "padding it out.\n"
    )
    user_prompt = "Numbered script (index: speaker: text):\n\n" + _render_script_for_performance(script)
    return system_prompt, user_prompt


def _generate_performance(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> tuple[Performance, TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch."""
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=Performance,
    )
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    return completion.choices[0].message.parsed, usage


def _validate_segment_structure(performance: Performance, original: Script) -> None:
    """Segment count, and each segment's (and the outro's) line count AND
    speaker sequence, must match the original 1:1 — only text/delivery/
    pause_ms may differ. This is what keeps grounding/meaning safe by
    construction, the same philosophy as critique.py's _apply_critique."""
    if len(performance.segments) != len(original.segments):
        raise ValueError(
            f"performance has {len(performance.segments)} segment(s), expected "
            f"{len(original.segments)} matching the original script"
        )
    for i, (perf_seg, orig_seg) in enumerate(zip(performance.segments, original.segments)):
        perf_speakers = [line.speaker for line in perf_seg.lines]
        orig_speakers = [line.speaker for line in orig_seg.lines]
        if perf_speakers != orig_speakers:
            raise ValueError(
                f"performance segment {i} has speakers {perf_speakers}, expected {orig_speakers} "
                "(same line count and speaker order as the original — only text/delivery/pause_ms may change)"
            )

    perf_outro_speakers = [line.speaker for line in performance.outro]
    orig_outro_speakers = [line.speaker for line in original.outro]
    if perf_outro_speakers != orig_outro_speakers:
        raise ValueError(
            f"performance outro has speakers {perf_outro_speakers}, expected {orig_outro_speakers} "
            "(same line count and speaker order as the original)"
        )


def _validate_cold_open_suffix(performance: Performance, original: Script) -> None:
    """The performed cold_open's LAST N entries' speakers must match the
    original cold_open's N speakers, in order (N=0 passes trivially) — new
    chit-chat lines (rule 6) may only be prepended, never replace the
    existing identification line(s)."""
    n = len(original.cold_open)
    if n == 0:
        return
    orig_speakers = [line.speaker for line in original.cold_open]
    actual_tail = performance.cold_open[-n:] if len(performance.cold_open) >= n else performance.cold_open
    actual_speakers = [line.speaker for line in actual_tail]
    if actual_speakers != orig_speakers:
        raise ValueError(
            f"performance cold_open must end with the original {n} speaker(s) {orig_speakers} in "
            f"order (new chit-chat lines may only be prepended before them), got {actual_speakers}"
        )


def _validate_speakers(performance: Performance, known_speakers: set[str]) -> None:
    used = {line.speaker for line in flatten_performed_lines(performance)}
    unknown = used - known_speakers
    if unknown:
        raise ValueError(f"performance uses unknown speaker(s) {sorted(unknown)}; known hosts are {sorted(known_speakers)}")


def _validate_word_cap(performance: Performance, original: Script) -> None:
    """The performed script's total word count (cold_open + segments +
    outro, including any added chit-chat) may not exceed the original's by
    more than MAX_WORD_OVERRUN — rewriting for flow is not license to pad."""
    original_words = sum(len(line.text.split()) for line in flatten_lines(original))
    performed_words = sum(len(line.text.split()) for line in flatten_performed_lines(performance))
    max_words = int(original_words * (1 + MAX_WORD_OVERRUN))
    if performed_words > max_words:
        raise ValueError(
            f"performance is {performed_words} words, over the {max_words}-word cap "
            f"({int(MAX_WORD_OVERRUN * 100)}% over the original script's {original_words} words) — trim it"
        )


def _validate_performance(performance: Performance, original: Script, known_speakers: set[str]) -> None:
    _validate_segment_structure(performance, original)
    _validate_cold_open_suffix(performance, original)
    _validate_speakers(performance, known_speakers)
    _validate_word_cap(performance, original)


def _apply_grounding(performance: Performance, original: Script) -> Performance:
    """Deep-copies `performance` and overwrites each segment's
    headline/source_ids from the matching original segment by index —
    grounding is structural, not re-derived from a rewritten segment.
    Mirrors critique.py:_apply_critique's "safe by construction"
    philosophy. Only valid to call after _validate_segment_structure has
    confirmed segment counts match."""
    grounded = performance.model_copy(deep=True)
    for perf_seg, orig_seg in zip(grounded.segments, original.segments):
        perf_seg.headline = orig_seg.headline
        perf_seg.source_ids = list(orig_seg.source_ids)
    return grounded


def validate_performed_source_ids(performance: Performance, known_ids: set[str]) -> None:
    """Every segment's source_ids must be a subset of `known_ids` — local
    equivalent of script.py:validate_source_ids (PerformedSegment is a
    distinct type from Segment). Belt-and-braces: _apply_grounding already
    makes this true by construction, but critique.py re-validates its own
    safe-by-construction splice too, for the same reason."""
    used_ids = {sid for segment in performance.segments for sid in segment.source_ids}
    unknown = used_ids - known_ids
    if unknown:
        raise ValueError(f"performance references unknown source_ids: {sorted(unknown)}")


def _render_fact_check_pairs(performance: Performance, original: Script) -> str:
    """1:1-aligned (original, performed) line-text pairs for every segment
    and outro line — new cold-open chit-chat lines are excluded, since they
    have no original counterpart to check facts against. Only valid to call
    after _validate_segment_structure/_validate_cold_open_suffix have
    confirmed 1:1 alignment holds."""
    n = len(original.cold_open)
    cold_open_pairs = zip(original.cold_open, performance.cold_open[-n:] if n else [])

    blocks: list[str] = []
    for orig_line, perf_line in cold_open_pairs:
        blocks.append(f'[cold_open] {orig_line.speaker}: ORIGINAL: "{orig_line.text}" PERFORMED: "{perf_line.text}"')
    for seg_index, (orig_seg, perf_seg) in enumerate(zip(original.segments, performance.segments)):
        for orig_line, perf_line in zip(orig_seg.lines, perf_seg.lines):
            blocks.append(
                f'[segment {seg_index}] {orig_line.speaker}: ORIGINAL: "{orig_line.text}" PERFORMED: "{perf_line.text}"'
            )
    for orig_line, perf_line in zip(original.outro, performance.outro):
        blocks.append(f'[outro] {orig_line.speaker}: ORIGINAL: "{orig_line.text}" PERFORMED: "{perf_line.text}"')
    return "\n".join(blocks)


def _build_fact_check_prompts(pairs_block: str) -> tuple[str, str]:
    system_prompt = (
        "You are a fact-checker comparing a podcast script's original lines against a "
        "performance rewrite of the same lines. Flag ONLY a pair where a fact, number, name, "
        "or claim actually changed — never for phrasing, punctuation, emphasis, or delivery-style "
        "changes, which are expected and fine. Most pairs should not be flagged. For each "
        "flagged pair, give the segment label (as shown), the speaker, both texts, and a "
        "one-line reason naming exactly what changed."
    )
    user_prompt = "Original/performed line pairs:\n\n" + pairs_block
    return system_prompt, user_prompt


def _generate_fact_check(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> tuple[list[FactChangeFlag], TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch."""
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=FactCheck,
    )
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    return completion.choices[0].message.parsed.flags, usage


def perform_stage(
    episode: Episode, critique_output: CritiqueOutput, articles: list[Article], client: OpenAI | None = None
) -> PerformOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    original_script = critique_output.revised_script
    known_speakers = {host.name for host in profile.podcast.hosts}
    known_ids = {a.source_id for a in articles}

    system_prompt, user_prompt = _build_performance_prompts(profile, original_script)

    # See outline.py's outline_stage for why this is a local accumulator
    # rather than a tuple threaded through generate_with_retry: a rejected
    # first attempt still cost real tokens.
    usage: list[TokenUsage] = []

    def generate(prompt: str) -> Performance:
        performance, call_usage = _generate_performance(client, profile.llm.script_model, system_prompt, prompt)
        usage.append(call_usage)
        return performance

    def validate(performance: Performance) -> None:
        _validate_performance(performance, original_script, known_speakers)

    performance, retried = generate_with_retry(generate, validate, user_prompt, stage_name="perform")
    performance = _apply_grounding(performance, original_script)
    validate_performed_source_ids(performance, known_ids)

    fact_check_system, fact_check_user = _build_fact_check_prompts(_render_fact_check_pairs(performance, original_script))
    fact_flags, fact_check_usage = _generate_fact_check(client, profile.llm.model, fact_check_system, fact_check_user)
    usage.append(fact_check_usage)

    output = PerformOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        model=profile.llm.script_model,
        fact_check_model=profile.llm.model,
        performance=performance,
        fact_flags=fact_flags,
        usage=usage,
        retried=retried,
    )

    out_path = episode_dir(episode.episode_id) / "performance.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
