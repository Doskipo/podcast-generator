"""Script stage: write the grounded, personas-and-style-aware dialogue for
each outline story.

Typed input: Episode (+ Profile snapshot), OutlineOutput, and the ranked
articles (for full text). Typed output: ScriptOutput, persisted as
data/episodes/<episode_id>/script.json.

This is the second of three script-related LLM steps — outline (podcast.
stages.outline) picks the narrative and angles first; critique (podcast.
stages.critique) reviews and rewrites flagged lines afterward. See
docs/decisions.md ("Script rebuild") for why it's split this way.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from openai import OpenAI

from podcast.env import require_env
from podcast.models import (
    Article,
    Episode,
    Line,
    Outline,
    OutlineOutput,
    OutlineStory,
    Profile,
    RecurringBit,
    Script,
    ScriptOutput,
    Style,
)
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Per-article truncation for the prompt: long enough to ground several claims,
# short enough that a full selection's worth of articles stays well within
# context/cost.
ARTICLE_TEXT_CHARS = 1500


def flatten_lines(script: Script) -> list[Line]:
    """Cold open, then every segment's lines in order, then the outro — the
    canonical line order shared by tts_stage (segment filenames) and
    critique_stage (CritiqueFlag.line_index). Public: imported by both."""
    return [
        *script.cold_open,
        *(line for segment in script.segments for line in segment.lines),
        *script.outro,
    ]


def validate_source_ids(script: Script, known_ids: set[str]) -> None:
    """Every segment's source_ids must be a subset of `known_ids`. Public:
    critique_stage re-checks this on the revised script too."""
    used_ids = {sid for segment in script.segments for sid in segment.source_ids}
    unknown = used_ids - known_ids
    if unknown:
        raise ValueError(f"script references unknown source_ids: {sorted(unknown)}")


def _render_article(article: Article) -> str:
    text = (article.text or "")[:ARTICLE_TEXT_CHARS]
    return f"[{article.source_id}] {article.title} — {text}"


def _style_instructions(style: Style) -> str:
    humour = {
        0: "No jokes or humour — keep it straightforward.",
        1: "Light, occasional humour — a wry aside here and there, nothing forced.",
        2: "Regular humour — banter and light jokes are welcome through the episode.",
        3: "Heavy humour — lean into jokes, teasing, and playful energy throughout.",
    }[style.humour]
    depth = {
        1: "Depth: keep explanations high-level and accessible, skip the technical weeds.",
        2: "Depth: moderate — explain the mechanism/why, but don't over-explain.",
        3: "Depth: go deep — assume an informed listener and get into specifics.",
    }[style.depth]
    tangents = (
        "Tangents are welcome — when a story's angle offers one, let a host follow it "
        "for a sentence or two before returning to the story."
        if style.tangents
        else "Stay on-topic — no tangents, stick to the story at hand."
    )
    banter = (
        "Banter between hosts (reactions, teasing, friendly disagreement) is welcome."
        if style.banter
        else "Keep exchanges focused — minimal banter between hosts."
    )
    return "\n".join([humour, depth, tangents, banter])


def _render_outline_story(story: OutlineStory, recurring_bits_by_id: dict[str, RecurringBit]) -> str:
    angle = story.angle
    bit_line = ""
    if story.recurring_bit:
        bit = recurring_bits_by_id.get(story.recurring_bit)
        # outline_stage's schema constrains recurring_bit to a known id, but
        # this stage doesn't re-derive that guarantee — fall back to the raw
        # id if it's somehow missing rather than crashing script generation.
        bit_line = f"\n  Recurring bit here: {bit.name} — {bit.description}" if bit else f"\n  Recurring bit here: {story.recurring_bit}"
    tangent_line = f"\n  Possible tangent: {angle.tangent}" if angle.tangent else ""
    return (
        f"- {story.headline} (sources: {', '.join(story.source_ids)})\n"
        f"  Word budget: ~{story.word_budget} words\n"
        f"  Why it matters: {angle.why_it_matters}\n"
        f"  Tension/surprise: {angle.tension_or_surprise}\n"
        f"  Host take: {angle.host_take}"
        f"{tangent_line}"
        f"{bit_line}"
    )


def _build_prompts(profile: Profile, outline: Outline, articles: list[Article]) -> tuple[str, str]:
    podcast = profile.podcast
    host_a, host_b = podcast.hosts[0], podcast.hosts[1]
    target_words = podcast.duration_minutes * 150
    known_ids = ", ".join(a.source_id for a in articles)
    recurring_bits_by_id = {bit.effective_id: bit for bit in podcast.recurring_bits}

    persona_block = (
        f"{host_a.name} (home turf: {', '.join(host_a.home_turf) or 'general'}):\n{host_a.persona}\n\n"
        f"{host_b.name} (home turf: {', '.join(host_b.home_turf) or 'general'}):\n{host_b.persona}"
    )

    system_prompt = (
        "You are writing a two-host podcast script for text-to-speech.\n\n"
        f"Hosts:\n{persona_block}\n\n"
        f"Listener: {podcast.listener.name}.\n\n"
        f"Style:\n{_style_instructions(podcast.style)}\n\n"
        f"Tone: {podcast.tone}.\n\n"
        "Hard rules:\n"
        "- The hosts are not an interviewer and an interviewee. Either host can lead a "
        "story, disagree with the other, interrupt with a short reaction, or take a "
        "brief tangent and come back to the story.\n"
        "- Each segment must follow its outline entry's angle below: bring out why it "
        "matters and the tension or surprise, let the host it names take the lead, and "
        "use the tangent naturally if it fits the moment.\n"
        "- Each segment should land close to its stated word budget (within about 20% "
        "either way) — a segment given 80 words is a quick hit, not a deep dive.\n"
        "- At most one backstory tangent per segment, and only when the outline's angle "
        "gives one — don't add extra tangents beyond what's outlined. A segment carrying "
        "the recurring bit never also carries a tangent (the outline already keeps these "
        "separate).\n"
        "- Only state something about the listener if it's explicitly given in the "
        "Listener line above — never invent hobbies, opinions, preferences, or biography "
        "for them. If a tangent references the listener and nothing concrete is given, "
        "ground it in a host's own backstory instead.\n"
        "- No line should run over 35 words, except within the recurring bit's own "
        "segment. Split exposition into a short back-and-forth exchange between the "
        "hosts instead of one long line.\n"
        "- Never say 'the article' or 'the paper' when citing a source — attribute the "
        "claim to the actual actor (the named researcher, company, or organization) or, "
        "if nothing specific fits, call it 'the report'.\n"
        "- Laughter and reactions are short spoken words ('ha', 'oh wow', 'right?') — "
        "never stage directions or bracketed cues like [laughs].\n"
        "- Every factual claim must come from the text of the cited sources. Never "
        "invent facts not present in the sources.\n"
        f"- Every segment's source_ids must be a subset of the known ids: {known_ids}\n"
        "- Write for the ear: short sentences, no lists, no URLs, numbers spoken "
        "naturally (the way a person would say them, not raw digits/symbols).\n"
        f"- Target length: about {target_words} words total, split per-segment as budgeted "
        "below.\n"
    )

    outline_block = "\n\n".join(_render_outline_story(story, recurring_bits_by_id) for story in outline.stories)
    articles_block = "\n\n".join(_render_article(a) for a in articles)
    user_prompt = (
        f"Episode title: {outline.title}\n\n"
        f"Outline — write one segment per story, in this order:\n{outline_block}\n\n"
        "Source articles (each as [source_id] title — text):\n" + articles_block + "\n\n"
        "Write the full script now."
    )
    return system_prompt, user_prompt


def _generate_script(client: OpenAI, model: str, system_prompt: str, user_prompt: str) -> Script:
    """Boundary around the OpenAI call — the seam tests monkeypatch."""
    completion = client.chat.completions.parse(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format=Script,
    )
    return completion.choices[0].message.parsed


def script_stage(
    episode: Episode, outline_output: OutlineOutput, articles: list[Article], client: OpenAI | None = None
) -> ScriptOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    known_ids = {a.source_id for a in articles}

    system_prompt, user_prompt = _build_prompts(profile, outline_output.outline, articles)

    script = _generate_script(client, profile.llm.script_model, system_prompt, user_prompt)

    validate_source_ids(script, known_ids)

    output = ScriptOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        model=profile.llm.script_model,
        script=script,
    )

    out_path = episode_dir(episode.episode_id) / "script.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
