"""Critique stage: review the written script for lines that are robotic,
expository, break persona, invent listener detail, misattribute a source, or
run too long — and rewrite (or split) only those.

Typed input: Episode (+ Profile snapshot), ScriptOutput, the ranked articles
(to re-check grounding on the revised script), and OutlineOutput (for each
segment's word_budget and recurring-bit assignment). Typed output:
CritiqueOutput, persisted as data/episodes/<episode_id>/critique.json —
keeps both `original_script` and `revised_script`.

Third of three script-related LLM steps — see docs/decisions.md ("Script
rebuild") for why review is a separate pass from writing (podcast.stages.
script) rather than one combined call, and ("Script quality pass") for the
word-budget/listener-fact/line-length checks added here.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from openai import OpenAI

from podcast.env import require_env
from podcast.llm_retry import generate_with_retry
from podcast.models import (
    Article,
    Critique,
    CritiqueOutput,
    Episode,
    Host,
    HostBrevityFlag,
    Line,
    Outline,
    OutlineOutput,
    Profile,
    Script,
    ScriptOutput,
    SegmentBudgetFlag,
    TokenUsage,
)
from podcast.paths import episode_dir
from podcast.stages.script import MAX_LINE_WORDS, flatten_lines, validate_source_ids

logger = logging.getLogger(__name__)

# A segment counts as over budget once it exceeds its outline word_budget by
# more than this fraction.
BUDGET_OVERRUN_THRESHOLD = 0.20

# A line at or under this many words counts as "short" for the per-host
# brevity check below.
SHORT_LINE_WORDS = 8

# A host is flagged once more than this fraction of their lines are short.
TERSE_HOST_THRESHOLD = 0.60


def _render_script(script: Script) -> str:
    return "\n".join(f"[{i}] {line.speaker}: {line.text}" for i, line in enumerate(flatten_lines(script)))


def _segment_has_bit_flags(outline: Outline, segment_count: int) -> list[bool]:
    """Per-segment "does this segment carry the recurring bit" flags, aligned
    to `script.segments` by position (the script step writes one segment per
    outline story, in order). Falls back to False for any segment beyond
    what the outline described, rather than crashing on a mismatch."""
    flags = [story.recurring_bit is not None for story in outline.stories]
    if len(flags) < segment_count:
        flags += [False] * (segment_count - len(flags))
    return flags


def _over_length_line_indices(script: Script, bit_segment_flags: list[bool]) -> list[int]:
    """Flat indices (flatten_lines order) of lines over MAX_LINE_WORDS words,
    excluding lines inside a segment that carries the recurring bit."""
    indices: list[int] = []
    position = 0
    for line in script.cold_open:
        if len(line.text.split()) > MAX_LINE_WORDS:
            indices.append(position)
        position += 1
    for seg_index, segment in enumerate(script.segments):
        in_bit_segment = seg_index < len(bit_segment_flags) and bit_segment_flags[seg_index]
        for line in segment.lines:
            if not in_bit_segment and len(line.text.split()) > MAX_LINE_WORDS:
                indices.append(position)
            position += 1
    for line in script.outro:
        if len(line.text.split()) > MAX_LINE_WORDS:
            indices.append(position)
        position += 1
    return indices


def _repeated_correct_line_indices(script: Script) -> list[int]:
    """Flat indices (flatten_lines order) of every occurrence of the
    standalone reaction "Correct." past the first — the rule is "at most
    once per episode", so the first is left alone and only repeats get
    flagged."""
    indices: list[int] = []
    seen_once = False
    for i, line in enumerate(flatten_lines(script)):
        if line.text.strip() == "Correct.":
            if seen_once:
                indices.append(i)
            seen_once = True
    return indices


# Matches a `Catchphrase: "..."` line inside a Host.persona free-text block
# — the format profiles/eudald.yaml's two current hosts use (see
# docs/decisions.md, "Persona rigidity"). Not every persona declares one:
# newer, tendency-style personas deliberately don't, and are simply skipped
# by _declared_catchphrases below.
_CATCHPHRASE_RE = re.compile(r'^\s*Catchphrase:\s*"([^"]+)"\s*$', re.MULTILINE)


def _declared_catchphrases(hosts: list[Host]) -> dict[str, str]:
    """host name -> their literal catchphrase, for every host whose persona
    declares one. Personas describe tendencies, not fixed lines to reuse
    (see docs/decisions.md, "Persona rigidity") — but the two current hosts
    were carried over with their existing Catchphrase: line intact, so this
    stage is what actually keeps that fixed line rare rather than rewriting
    the persona text itself."""
    result: dict[str, str] = {}
    for host in hosts:
        match = _CATCHPHRASE_RE.search(host.persona)
        if match:
            result[host.name] = match.group(1)
    return result


def _catchphrase_violations(script: Script, catchphrases: dict[str, str]) -> list[str]:
    """Human-readable descriptions of every catchphrase rule break in
    `script` — at most once per episode, never in the cold open. Matching
    is a case-insensitive substring check against each line's text (the
    catchphrase doesn't need to be the entire line, just present in it).
    Used both to steer the critique prompt and, unconditionally, as
    critique_stage's hard validation backstop."""
    violations: list[str] = []
    for host_name, phrase in catchphrases.items():
        needle = phrase.lower()
        cold_open_hits = sum(1 for line in script.cold_open if line.speaker == host_name and needle in line.text.lower())
        if cold_open_hits:
            violations.append(f"{host_name}'s catchphrase (\"{phrase}\") appears in the cold open — never allowed there")
        total_hits = sum(1 for line in flatten_lines(script) if line.speaker == host_name and needle in line.text.lower())
        if total_hits > 1:
            violations.append(f"{host_name}'s catchphrase (\"{phrase}\") appears {total_hits} times — at most once per episode")
    return violations


def _build_prompts(profile: Profile, script: Script, outline: Outline) -> tuple[str, str]:
    persona_block = "\n\n".join(f"{host.name}:\n{host.persona}" for host in profile.podcast.hosts)
    listener = profile.podcast.listener

    bit_segment_flags = _segment_has_bit_flags(outline, len(script.segments))
    over_length = _over_length_line_indices(script, bit_segment_flags)
    over_length_line = (
        f"These line indices are over {MAX_LINE_WORDS} words and must be flagged with issue "
        f"\"too_long\", split into a short back-and-forth exchange (2+ shorter lines, "
        f"alternating the two hosts, via rewritten_lines) instead of one long line: "
        f"{', '.join(str(i) for i in over_length)}.\n"
        if over_length
        else "No line is currently over the length limit.\n"
    )

    repeated_correct = _repeated_correct_line_indices(script)
    repeated_correct_line = (
        f"These line indices repeat the standalone reaction \"Correct.\" (only the first "
        f"use in the episode is allowed) and must be flagged with issue \"repeated_correct\" "
        f"and rewritten with a different, emotionally-varied way of agreeing: "
        f"{', '.join(str(i) for i in repeated_correct)}.\n"
        if repeated_correct
        else ""
    )

    catchphrases = _declared_catchphrases(profile.podcast.hosts)
    catchphrase_declared_line = (
        "Declared catchphrases (each allowed at most once per episode, never in the cold "
        "open — flag a violation with issue \"repeated_catchphrase\" and rewrite it into "
        "something new in the host's voice, not the fixed phrase):\n"
        + "\n".join(f'- {name}: "{phrase}"' for name, phrase in catchphrases.items())
        + "\n"
        if catchphrases
        else ""
    )
    catchphrase_violations = _catchphrase_violations(script, catchphrases)
    catchphrase_violation_line = (
        f"These catchphrase rule violations exist right now and must be fixed: "
        f"{'; '.join(catchphrase_violations)}.\n"
        if catchphrase_violations
        else ""
    )

    system_prompt = (
        "You are a script editor for a two-host podcast, reviewing a draft for how it "
        "will sound spoken out loud.\n\n"
        f"Host personas:\n{persona_block}\n\n"
        f"Listener: {listener.name} — this is everything actually known about them.\n\n"
        "Flag any line that is:\n"
        "- robotic: sounds like a report being read, not a person talking\n"
        "- expository: over-explains something a real person would just say plainly\n"
        "- breaks_persona: inconsistent with the host's persona above\n"
        "- invented_listener_detail: states something about the listener (a hobby, "
        "opinion, preference, biography) that isn't in the Listener line above\n"
        "- the_article_phrasing: says 'the article' or 'the paper' instead of naming the "
        "actual actor (researcher/company/organization) or calling it 'the report'\n"
        "- written_not_spoken: reads like prose on a page — no natural connectors or "
        "disfluencies ('I mean', 'no?', 'okay so', 'look', 'wait'), no self-corrections, "
        "no mid-line tone shift, too clean and complete to be something a person just said\n"
        "- too_long: see below\n"
        "- repeated_correct: see below\n"
        "- repeated_catchphrase: see below\n\n"
        f"{over_length_line}"
        f"{repeated_correct_line}"
        f"{catchphrase_declared_line}"
        f"{catchphrase_violation_line}\n"
        "For each flagged line, give its index (as shown in the numbered script below), "
        "an issue label, and rewritten_lines — normally a single rewritten line that fixes "
        "it while keeping the same meaning, speaker, and any facts it cites, but for a "
        "too_long split, two or more shorter lines that together cover the same content as "
        "a natural exchange (the first one should usually keep the original speaker). Only "
        "flag lines that genuinely need a fix — most lines should be left alone."
    )
    user_prompt = "Numbered script (index: speaker: text):\n\n" + _render_script(script)
    return system_prompt, user_prompt


def _generate_critique(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> tuple[Critique, TokenUsage]:
    """Boundary around the OpenAI call — the seam tests monkeypatch."""
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=Critique,
    )
    usage = TokenUsage(
        model=model, prompt_tokens=completion.usage.prompt_tokens, completion_tokens=completion.usage.completion_tokens
    )
    return completion.choices[0].message.parsed, usage


def _apply_critique(script: Script, critique: Critique) -> Script:
    """Deep-copies `script` and, walking cold_open / each segment's lines /
    outro in flatten_lines order, replaces each flagged position's original
    line with its rewritten_lines (usually 1 line, 2+ for a too_long split) —
    nothing else (segments, source_ids, unflagged lines) changes, so
    grounding stays intact by construction."""
    revised = script.model_copy(deep=True)
    total_lines = len(flatten_lines(script))

    replacements: dict[int, list[Line]] = {}
    for flag in critique.flags:
        if not (0 <= flag.line_index < total_lines):
            logger.warning("critique flagged out-of-range line_index %d, ignoring", flag.line_index)
            continue
        if not flag.rewritten_lines:
            logger.warning("critique flag at line_index %d had no rewritten_lines, ignoring", flag.line_index)
            continue
        replacements[flag.line_index] = flag.rewritten_lines

    position = 0

    def _rebuild(lines: list[Line]) -> list[Line]:
        nonlocal position
        rebuilt: list[Line] = []
        for original_line in lines:
            rebuilt.extend(replacements.get(position, [original_line]))
            position += 1
        return rebuilt

    revised.cold_open = _rebuild(revised.cold_open)
    for segment in revised.segments:
        segment.lines = _rebuild(segment.lines)
    revised.outro = _rebuild(revised.outro)

    return revised


def _segment_word_count(lines: list[Line]) -> int:
    return sum(len(line.text.split()) for line in lines)


def _host_brevity_flags(script: Script) -> list[HostBrevityFlag]:
    """Hosts whose lines are more than TERSE_HOST_THRESHOLD (60%) under
    SHORT_LINE_WORDS (8) words — computed in code (no LLM judgment needed),
    across every line in the script, not just one segment. Informational:
    flags a distribution problem, doesn't rewrite anything itself."""
    lines_by_host: dict[str, list[Line]] = {}
    for line in flatten_lines(script):
        lines_by_host.setdefault(line.speaker, []).append(line)

    flags: list[HostBrevityFlag] = []
    for host, lines in lines_by_host.items():
        short_count = sum(1 for line in lines if len(line.text.split()) <= SHORT_LINE_WORDS)
        fraction = short_count / len(lines)
        if fraction > TERSE_HOST_THRESHOLD:
            flags.append(HostBrevityFlag(host=host, short_line_fraction=round(fraction, 2), line_count=len(lines)))
    return flags


def _budget_flags(outline: Outline, script: Script) -> list[SegmentBudgetFlag]:
    """Segments (by position, matching outline.stories to script.segments)
    whose word count exceeds their outline word_budget by more than
    BUDGET_OVERRUN_THRESHOLD. Informational — reported, not auto-shortened."""
    flags: list[SegmentBudgetFlag] = []
    for index, (story, segment) in enumerate(zip(outline.stories, script.segments)):
        budget = story.word_budget
        actual = _segment_word_count(segment.lines)
        if budget <= 0:
            continue
        over_by = (actual - budget) / budget
        if over_by > BUDGET_OVERRUN_THRESHOLD:
            flags.append(
                SegmentBudgetFlag(
                    segment_index=index,
                    headline=segment.headline,
                    word_budget=budget,
                    actual_words=actual,
                    over_by_percent=round(over_by * 100, 1),
                )
            )
    return flags


def critique_stage(
    episode: Episode,
    script_output: ScriptOutput,
    articles: list[Article],
    outline_output: OutlineOutput,
    client: OpenAI | None = None,
) -> CritiqueOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    model = profile.llm.script_model  # same stronger model as writing — see docs/decisions.md
    original_script = script_output.script
    outline = outline_output.outline

    system_prompt, user_prompt = _build_prompts(profile, original_script, outline)
    known_ids = {a.source_id for a in articles}
    catchphrases = _declared_catchphrases(profile.podcast.hosts)

    # See outline.py's outline_stage for why this is a local accumulator
    # rather than a tuple threaded through generate_with_retry: a rejected
    # first attempt still cost real tokens.
    usage: list[TokenUsage] = []

    def generate(prompt: str) -> Critique:
        critique, call_usage = _generate_critique(client, model, system_prompt, prompt)
        usage.append(call_usage)
        return critique

    def validate(critique: Critique) -> None:
        # Grounding can only be re-checked on the script the critique would
        # actually produce, so validation here applies it first (pure,
        # deterministic, cheap to redo below with the validated critique).
        revised = _apply_critique(original_script, critique)
        validate_source_ids(revised, known_ids)
        # Hard backstop, not just a prompt instruction — see
        # docs/decisions.md ("Persona rigidity"): a catchphrase used twice,
        # or at all in the cold open, forces the one retry-with-feedback
        # rather than shipping as-is.
        violations = _catchphrase_violations(revised, catchphrases)
        if violations:
            raise ValueError(f"catchphrase rule violated: {'; '.join(violations)}")

    critique, retried = generate_with_retry(generate, validate, user_prompt, stage_name="critique")
    revised_script = _apply_critique(original_script, critique)

    for host_name, phrase in catchphrases.items():
        count = sum(1 for line in flatten_lines(revised_script) if line.speaker == host_name and phrase.lower() in line.text.lower())
        logger.info("critique: %s catchphrase %r used %d time(s) this episode", host_name, phrase, count)

    total_words = sum(len(line.text.split()) for line in flatten_lines(revised_script))
    over_budget_segments = _budget_flags(outline, revised_script)
    terse_hosts = _host_brevity_flags(revised_script)

    output = CritiqueOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        model=model,
        critique=critique,
        original_script=original_script,
        revised_script=revised_script,
        total_words=total_words,
        over_budget_segments=over_budget_segments,
        terse_hosts=terse_hosts,
        usage=usage,
        retried=retried,
    )

    out_path = episode_dir(episode.episode_id) / "critique.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
