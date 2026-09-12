"""Smoke tests for the script stage (the writing step: outline + articles ->
dialogue). No network: the OpenAI call is monkeypatched at the script
module's _generate_script seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Angle,
    Article,
    Episode,
    Host,
    Interest,
    Line,
    Listener,
    Outline,
    OutlineOutput,
    OutlineStory,
    PodcastSettings,
    Profile,
    Script,
    Segment,
    Style,
)
from podcast.stages import script as script_module


def _host(name: str) -> Host:
    return Host(name=name, voice_id=f"voice-{name.lower()}", persona=f"{name} is curious and precise.", home_turf=[])


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
        podcast=PodcastSettings(
            duration_minutes=8,
            listener=Listener(name="Eudald"),
            hosts=[_host("Nova"), _host("Max")],
            style=Style(humour=2, depth=2, tangents=True, banter=True),
            tone="curious",
        ),
    )


def _article(source_id: str) -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        source_id=source_id,
        url=f"https://example.com/{source_id}",
        title=f"Article {source_id}",
        feed_url="https://example.com/feed.xml",
        published_at=now,
        fetched_at=now,
        summary=None,
        text="Some article text long enough to ground a claim." * 5,
    )


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _outline_output(episode_id: str, source_id: str) -> OutlineOutput:
    outline = Outline(
        title="Test Episode",
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=[source_id],
                angle=Angle(
                    why_it_matters="it matters",
                    tension_or_surprise="a twist",
                    host_take="Nova cares because...",
                    tangent="reminds Nova of a story",
                ),
            )
        ],
    )
    return OutlineOutput(episode_id=episode_id, generated_at=datetime.now(timezone.utc), model="gpt-4o-mini", outline=outline)


def _fixture_script(source_id: str) -> Script:
    return Script(
        title="Test Episode",
        cold_open=[Line(speaker="Nova", text="Welcome back!")],
        segments=[
            Segment(
                headline="Something happened",
                source_ids=[source_id],
                lines=[
                    Line(speaker="Nova", text="Here's the story."),
                    Line(speaker="Max", text="Tell me more."),
                ],
            )
        ],
        outro=[Line(speaker="Max", text="See you next time.")],
    )


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(script_module, "episode_dir", _episode_dir)


def test_script_stage_persists_and_matches_model(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    fixture_script = _fixture_script("abcd1234")
    monkeypatch.setattr(
        script_module,
        "_generate_script",
        lambda client, model, system_prompt, user_prompt: fixture_script,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = script_module.script_stage(episode, outline_output, articles, client=object())

    # uses the stronger model, not the default cheap one
    assert output.model == profile.llm.script_model
    assert output.model != profile.llm.model

    script_path = tmp_path / "episodes" / episode.episode_id / "script.json"
    assert script_path.exists()
    reparsed = script_module.ScriptOutput.model_validate_json(script_path.read_text(encoding="utf-8"))
    assert reparsed.script.title == "Test Episode"


def test_script_stage_rejects_unknown_source_ids(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    fixture_script = _fixture_script("unknown99")
    monkeypatch.setattr(
        script_module,
        "_generate_script",
        lambda client, model, system_prompt, user_prompt: fixture_script,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        script_module.script_stage(episode, outline_output, articles, client=object())


def test_script_stage_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        script_module.script_stage(episode, outline_output, articles)


def test_flatten_lines_order():
    script = _fixture_script("abcd1234")
    flat = script_module.flatten_lines(script)
    assert [line.text for line in flat] == [
        "Welcome back!",
        "Here's the story.",
        "Tell me more.",
        "See you next time.",
    ]
