"""Script stage: turn fetched articles into a grounded two-host podcast script.

Typed input: Episode (+ Profile snapshot) and FetchOutput. Typed output:
ScriptOutput, persisted as data/episodes/<episode_id>/script.json.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from openai import OpenAI

from podcast.env import require_env
from podcast.models import Article, Episode, FetchOutput, Profile, Script, ScriptOutput
from podcast.paths import episode_dir

logger = logging.getLogger(__name__)

# Per-article truncation for the prompt: long enough to ground several claims,
# short enough that a full window of articles stays well within context/cost.
ARTICLE_TEXT_CHARS = 1500


def _render_article(article: Article) -> str:
    text = article.text[:ARTICLE_TEXT_CHARS]
    return f"[{article.source_id}] {article.title} — {text}"


def _build_prompts(profile: Profile, articles: list[Article]) -> tuple[str, str]:
    host_driver, host_asker = profile.podcast.hosts[0], profile.podcast.hosts[1]
    target_words = profile.podcast.duration_minutes * 150
    known_ids = ", ".join(a.source_id for a in articles)

    system_prompt = (
        "You are writing a two-host podcast script for text-to-speech.\n"
        f"Hosts: {host_driver} drives each segment (narrates, introduces the news), "
        f"{host_asker} asks questions and adds context/reactions.\n"
        f"Tone: {profile.podcast.tone}.\n"
        f"Target length: about {target_words} words total.\n"
        "Grounding rules (critical):\n"
        "- Every segment's source_ids must be a subset of the article ids provided below.\n"
        f"- Known source_ids: {known_ids}\n"
        "- Every factual claim must come from the text of the cited articles. "
        "Never invent facts not present in the sources.\n"
        "Writing rules for TTS:\n"
        "- Conversational, short sentences.\n"
        "- No markdown, no bullet lists, no URLs — this is spoken audio.\n"
    )

    articles_block = "\n\n".join(_render_article(a) for a in articles)
    user_prompt = (
        "Write the podcast script from these articles. Each is formatted as "
        "[source_id] title — text.\n\n" + articles_block
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


def _validate_source_ids(script: Script, known_ids: set[str]) -> None:
    used_ids = {sid for segment in script.segments for sid in segment.source_ids}
    unknown = used_ids - known_ids
    if unknown:
        raise ValueError(f"script references unknown source_ids: {sorted(unknown)}")


def script_stage(episode: Episode, fetch_output: FetchOutput, client: OpenAI | None = None) -> ScriptOutput:
    if client is None:
        client = OpenAI(api_key=require_env("OPENAI_API_KEY"))

    profile = episode.profile
    articles = fetch_output.articles
    known_ids = {a.source_id for a in articles}

    system_prompt, user_prompt = _build_prompts(profile, articles)

    script = _generate_script(client, profile.llm.model, system_prompt, user_prompt)

    _validate_source_ids(script, known_ids)

    output = ScriptOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        model=profile.llm.model,
        script=script,
    )

    out_path = episode_dir(episode.episode_id) / "script.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
