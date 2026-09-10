"""Smoke tests for the script stage. No network: the OpenAI call is monkeypatched
at the script module's _generate_script seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Article,
    Episode,
    FetchOutput,
    Interest,
    Line,
    PodcastSettings,
    Profile,
    Script,
    Segment,
)
from podcast.stages import script as script_module


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
        podcast=PodcastSettings(duration_minutes=8, hosts=["Nova", "Max"], tone="curious"),
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


def _fetch_output(episode_id: str, articles: list[Article]) -> FetchOutput:
    return FetchOutput(
        episode_id=episode_id,
        fetched_at=datetime.now(timezone.utc),
        articles=articles,
    )


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
    fetch_output = _fetch_output(episode.episode_id, articles)

    fixture_script = _fixture_script("abcd1234")
    monkeypatch.setattr(
        script_module,
        "_generate_script",
        lambda client, model, system_prompt, user_prompt: fixture_script,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = script_module.script_stage(episode, fetch_output, client=object())

    assert output.model == profile.llm.model

    script_path = tmp_path / "episodes" / episode.episode_id / "script.json"
    assert script_path.exists()
    reparsed = script_module.ScriptOutput.model_validate_json(script_path.read_text(encoding="utf-8"))
    assert reparsed.script.title == "Test Episode"


def test_script_stage_rejects_unknown_source_ids(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    fetch_output = _fetch_output(episode.episode_id, articles)

    fixture_script = _fixture_script("unknown99")
    monkeypatch.setattr(
        script_module,
        "_generate_script",
        lambda client, model, system_prompt, user_prompt: fixture_script,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        script_module.script_stage(episode, fetch_output, client=object())


def test_script_stage_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    episode = _episode(profile)
    fetch_output = _fetch_output(episode.episode_id, [_article("abcd1234")])

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        script_module.script_stage(episode, fetch_output)
