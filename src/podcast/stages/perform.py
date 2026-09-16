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
from pydantic import BaseModel, create_model

from podcast.artefacts import load_outline_output
from podcast.env import require_env
from podcast.llm_retry import generate_with_retry
from podcast.models import (
    Article,
    CritiqueOutput,
    Episode,
    FactChangeFlag,
    FactCheck,
    Host,
    HostMood,
    Performance,
    PerformedLine,
    PerformedSegment,
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
# exceed the critique script's own word count by at most this fraction.
# Tightened from 0.10 to 0.05 after a real episode ran 9'13" against a
# 7-minute target — see docs/decisions.md ("Word budget recalibration").
MAX_WORD_OVERRUN = 0.05


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


def _render_host_persona(host: Host, mood_by_host: dict[str, HostMood]) -> str:
    """Persona plus this episode's sampled mood (outline.host_moods, loaded
    from outline.json — see perform_stage) — the performance director draws
    on both to shape delivery/pacing/energy, the same "persona plus mood"
    reasoning as script.py's own _render_host_persona. Missing an entry
    only for an outline.json persisted before host_moods existed. See
    docs/decisions.md ("Persona rigidity")."""
    mood = mood_by_host.get(host.name)
    mood_line = f"\nMood this episode: {mood.mood} — {mood.reason}" if mood else ""
    return f"{host.name}:\n{host.persona}{mood_line}"


def _build_performance_prompts(profile: Profile, script: Script, host_moods: list[HostMood]) -> tuple[str, str]:
    hosts = profile.podcast.hosts
    host_names = ", ".join(h.name for h in hosts)
    mood_by_host = {m.host: m for m in host_moods}
    persona_block = "\n\n".join(_render_host_persona(host, mood_by_host) for host in hosts)
    cold_open_speakers = [line.speaker for line in script.cold_open]
    original_words = sum(len(line.text.split()) for line in flatten_lines(script))
    max_words = int(original_words * (1 + MAX_WORD_OVERRUN))

    system_prompt = (
        "You are a vocal performance director for a two-host podcast. You are handed a "
        "finished, fact-checked script and your only job is to rewrite it for how it should "
        "sound SPOKEN — never change what it says.\n\n"
        f"Hosts:\n{persona_block}\n\n"
        "Each host's persona describes their tendencies and voice — background, general "
        "speech patterns, what they gravitate to — not a script of fixed lines to reuse. "
        "The same goes for home turf: it's character and taste, not a subject checklist — "
        "don't let delivery manufacture extra excitement or emphasis for a host's pet "
        "subject in a line that doesn't actually engage with it. "
        "Draw on the persona and on the mood given above to shape delivery, pacing, and "
        "energy for that host throughout — never to change what a line says or means.\n\n"
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


def _response_model(segment_count: int) -> type[BaseModel]:
    """A structured-output schema shaped like Performance, except `segments`
    is not a homogeneous `list[PerformedSegment]` (which lets the model
    return any number of segments — the actual cause of a real "performance
    has N segment(s), expected M" failure) but an object with one required
    field per segment index, `segment_0`..`segment_{segment_count-1}` — the
    same "object with a required key per slot" trick outline.py's stances
    and later-story transition use to make a count true by construction
    rather than checked after the fact. See docs/decisions.md ("Structural
    transitions", "Correctness invariants vs quality signals")."""
    segments_model = create_model(
        "PerformanceSegmentsResponse",
        **{f"segment_{i}": (PerformedSegment, ...) for i in range(segment_count)},
    )
    return create_model(
        "PerformanceResponse",
        title=(str, ...),
        cold_open=(list[PerformedLine], ...),
        segments=(segments_model, ...),
        outro=(list[PerformedLine], ...),
    )


def _generate_performance(
    client: OpenAI, model: str, system_prompt: str, user_prompt: str, segment_count: int
) -> tuple[Performance, TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch. Builds
    the segment_count-constrained schema (see _response_model), then
    reassembles the per-index segment fields back into Performance.segments,
    in order."""
    response_model = _response_model(segment_count)
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=response_model,
    )
    parsed = completion.choices[0].message.parsed
    segments = [getattr(parsed.segments, f"segment_{i}") for i in range(segment_count)]
    performance = Performance(title=parsed.title, cold_open=parsed.cold_open, segments=segments, outro=parsed.outro)
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    return performance, usage


def _reconcile_segment_count(performance: Performance, original: Script) -> tuple[Performance, str | None]:
    """Defensive backstop, not the normal path — _response_model's schema
    already fixes the segment count structurally, so the model can't
    actually return the wrong number of segments through the real API. If
    a performance somehow still arrives with too many anyway, keep the
    first len(original.segments) positionally (drop the extras) rather
    than failing the whole run over a count a structurally-required schema
    should never let through in the first place. There's nothing
    equivalent to do for too FEW — no segment to positionally map from —
    so that's left to _validate_segment_structure's count check, which
    still raises: segment structure is a correctness invariant, not a
    quality signal. See docs/decisions.md ("Correctness invariants vs
    quality signals"). Returns the (possibly trimmed) performance plus a
    repair description, or None if nothing needed fixing."""
    if len(performance.segments) > len(original.segments):
        dropped = len(performance.segments) - len(original.segments)
        logger.warning(
            "perform: performance had %d segment(s), expected %d — dropping the extra %d",
            len(performance.segments), len(original.segments), dropped,
        )
        performance = performance.model_copy(update={"segments": performance.segments[: len(original.segments)]})
        return performance, f"performance had {dropped} extra segment(s) — dropped positionally"
    return performance, None


def _validate_segment_structure(performance: Performance, original: Script) -> None:
    """Segment count, and each segment's (and the outro's) line count AND
    speaker sequence, must match the original 1:1 — only text/delivery/
    pause_ms may differ. This is what keeps grounding/meaning safe by
    construction, the same philosophy as critique.py's _apply_critique. A
    correctness invariant, not a quality signal (see docs/decisions.md,
    "Correctness invariants vs quality signals") — still raises and still
    ends the run if generate_with_retry's one retry doesn't fix it.
    _reconcile_segment_count already trims a too-many mismatch before this
    ever runs, so in practice this now only fires for a genuine shortfall
    or a speaker-order break, neither of which can be safely auto-repaired."""
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


def _word_overrun(performance: Performance, original: Script) -> int:
    """How many words `performance`'s total (cold_open + segments + outro,
    including any added chit-chat) exceeds the original's MAX_WORD_OVERRUN
    cap by — 0 if it's within cap. A quality signal, not a correctness
    invariant (see docs/decisions.md, "Correctness invariants vs quality
    signals"): unlike _validate_segment_structure/_validate_cold_open_
    suffix/_validate_speakers, this never raises — perform_stage gives an
    overrun one regeneration attempt, then keeps the performance and
    records whatever overrun remains rather than failing the run over it."""
    original_words = sum(len(line.text.split()) for line in flatten_lines(original))
    performed_words = sum(len(line.text.split()) for line in flatten_performed_lines(performance))
    max_words = int(original_words * (1 + MAX_WORD_OVERRUN))
    return max(0, performed_words - max_words)


def _validate_performance(performance: Performance, original: Script, known_speakers: set[str]) -> None:
    """Correctness invariants only — grounding-adjacent structure that, if
    wrong, would mean the episode says something different from what
    critique reviewed. The word cap is deliberately not checked here: it's
    a quality signal, not a correctness invariant — see _word_overrun and
    docs/decisions.md ("Correctness invariants vs quality signals")."""
    _validate_segment_structure(performance, original)
    _validate_cold_open_suffix(performance, original)
    _validate_speakers(performance, known_speakers)


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

    # outline.json is guaranteed to exist by the time perform runs (perform
    # requires critique, critique requires outline) — read directly rather
    # than threading a new parameter through every service.py call site.
    # Same "read a sibling artefact off disk" convention as quality_stage.
    # See docs/decisions.md ("Persona rigidity").
    outline_output = load_outline_output(episode_dir(episode.episode_id))
    host_moods = outline_output.outline.host_moods

    system_prompt, user_prompt = _build_performance_prompts(profile, original_script, host_moods)
    segment_count = len(original_script.segments)

    # See outline.py's outline_stage for why this is a local accumulator
    # rather than a tuple threaded through generate_with_retry: a rejected
    # first attempt still cost real tokens.
    usage: list[TokenUsage] = []
    # Human-readable descriptions of quality-signal repairs made this run
    # — see PerformOutput.repairs and docs/decisions.md ("Correctness
    # invariants vs quality signals").
    repairs: list[str] = []

    def generate(prompt: str) -> Performance:
        performance, call_usage = _generate_performance(
            client, profile.llm.script_model, system_prompt, prompt, segment_count
        )
        usage.append(call_usage)
        performance, repair = _reconcile_segment_count(performance, original_script)
        if repair:
            repairs.append(repair)
        return performance

    def validate(performance: Performance) -> None:
        _validate_performance(performance, original_script, known_speakers)

    performance, retried = generate_with_retry(generate, validate, user_prompt, stage_name="perform")

    # Word cap: a quality signal, not a correctness invariant (see
    # docs/decisions.md, "Correctness invariants vs quality signals") —
    # gets one regeneration attempt, feedback-driven like
    # generate_with_retry's own retry, but is never allowed to fail the
    # run. If the regeneration doesn't fully resolve it (or itself breaks a
    # correctness invariant), the ORIGINAL performance is kept and its
    # overrun is recorded on PerformOutput.word_overrun for quality_stage
    # to flag — never discarded for a version that's unverified or worse.
    word_overrun = _word_overrun(performance, original_script)
    if word_overrun:
        overrun_before = word_overrun
        logger.warning("perform: performance is %d word(s) over the word cap — regenerating once", word_overrun)
        # Counts as a retry (tokens spent either way — see usage below)
        # regardless of whether the regeneration actually resolves the
        # overrun: unlike generate_with_retry's own `retried`, this is set
        # the moment a second attempt is made, not only on a successful one.
        retried = True
        retry_prompt = (
            f"{user_prompt}\n\n"
            f"Your previous response was invalid: performance is {word_overrun} word(s) over the word cap.\n"
            "Correct this and respond again, following all the same instructions."
        )
        try:
            candidate = generate(retry_prompt)
            validate(candidate)
        except ValueError as exc:
            logger.warning("perform: word-cap regeneration broke a correctness invariant, keeping the original: %s", exc)
            candidate = None
        if candidate is not None and _word_overrun(candidate, original_script) == 0:
            performance, word_overrun = candidate, 0
            repairs.append(f"word cap exceeded by {overrun_before} word(s) — resolved by regenerating")
        else:
            logger.warning(
                "perform: still %d word(s) over the word cap after regenerating — keeping the performance, "
                "flagging the overrun in quality.json instead of failing the run", word_overrun
            )
            repairs.append(f"word cap exceeded by {word_overrun} word(s) after one regeneration attempt — kept as-is")

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
        word_overrun=word_overrun,
        repairs=repairs,
    )

    out_path = episode_dir(episode.episode_id) / "performance.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
