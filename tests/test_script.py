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
    HostStance,
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
    TokenUsage,
)
from podcast.stages import script as script_module

_FIXTURE_USAGE = TokenUsage(model="m", prompt_tokens=10, completion_tokens=5)


def _host(name: str) -> Host:
    return Host(name=name, voice_id=f"voice-{name.lower()}", persona=f"{name} is curious and precise.", home_turf=[])


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
        podcast=PodcastSettings(
            name="Test Podcast",
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
                stances=[
                    HostStance(host="Nova", attitude="excited", why="it's her home turf"),
                    HostStance(host="Max", attitude="skeptical", why="wants the numbers"),
                ],
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
        lambda client, model, system_prompt, user_prompt: (fixture_script, _FIXTURE_USAGE),
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
        lambda client, model, system_prompt, user_prompt: (fixture_script, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        script_module.script_stage(episode, outline_output, articles, client=object())


def test_script_stage_retries_once_then_succeeds_after_bad_source_ids(tmp_path, monkeypatch):
    """See docs/decisions.md ("One-retry-with-feedback")."""
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    bad_script = _fixture_script("unknown99")
    good_script = _fixture_script("abcd1234")
    prompts: list[str] = []

    def fake_generate_script(client, model, system_prompt, user_prompt):
        prompts.append(user_prompt)
        script = bad_script if len(prompts) == 1 else good_script
        return script, _FIXTURE_USAGE

    monkeypatch.setattr(script_module, "_generate_script", fake_generate_script)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = script_module.script_stage(episode, outline_output, articles, client=object())

    assert output.script.segments[0].source_ids == ["abcd1234"]
    assert len(prompts) == 2
    assert "unknown99" in prompts[1]  # the validation error, fed back verbatim
    # both the rejected first attempt and the retry are billed calls
    assert output.usage == [_FIXTURE_USAGE, _FIXTURE_USAGE]


def test_script_stage_raises_after_a_second_failed_validation(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    always_bad = _fixture_script("unknown99")
    prompts: list[str] = []

    def fake_generate_script(client, model, system_prompt, user_prompt):
        prompts.append(user_prompt)
        return always_bad, _FIXTURE_USAGE

    monkeypatch.setattr(script_module, "_generate_script", fake_generate_script)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError):
        script_module.script_stage(episode, outline_output, articles, client=object())

    assert len(prompts) == 2  # exactly one retry, no more


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


def test_supports_audio_tags():
    assert script_module.supports_audio_tags("eleven_v3") is True
    assert script_module.supports_audio_tags("eleven_turbo_v2_5") is False


def test_render_outline_story_includes_primer_instruction_only_when_is_primer():
    story = OutlineStory(
        headline="A primer",
        source_ids=["abcd1234"],
        angle=Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent=None),
        stances=[
            HostStance(host="Nova", attitude="curious", why="w"),
            HostStance(host="Max", attitude="curious", why="w"),
        ],
        is_primer=True,
    )
    rendered = script_module._render_outline_story(story, recurring_bits_by_id={})
    assert "not news" in rendered
    assert "already likes it" in rendered

    news_story = story.model_copy(update={"is_primer": False})
    rendered_news = script_module._render_outline_story(news_story, recurring_bits_by_id={})
    assert "not news" not in rendered_news


def test_render_listener_includes_facts_when_present():
    with_facts = script_module._render_listener(Listener(name="Eudald", facts=["plays piano", "trains calisthenics"]))
    assert "Eudald" in with_facts
    assert "plays piano" in with_facts
    assert "trains calisthenics" in with_facts

    no_facts = script_module._render_listener(Listener(name="Eudald"))
    assert no_facts == "Listener: Eudald."


def test_build_prompts_includes_listener_facts(tmp_path):
    profile = _profile()
    profile.podcast.listener = Listener(name="Eudald", facts=["plays piano; likes jazzy progressions"])
    outline_output = _outline_output("ep1", "abcd1234")
    articles = [_article("abcd1234")]

    system_prompt, _user_prompt = script_module._build_prompts(profile, outline_output.outline, articles)

    assert "plays piano; likes jazzy progressions" in system_prompt


def test_build_prompts_states_cold_open_identification_rule(tmp_path):
    profile = _profile()
    outline_output = _outline_output("ep1", "abcd1234")
    articles = [_article("abcd1234")]

    system_prompt, _user_prompt = script_module._build_prompts(profile, outline_output.outline, articles)

    assert profile.podcast.name in system_prompt
    assert "one breath" in system_prompt
    assert profile.podcast.hosts[0].name in system_prompt
    assert profile.podcast.hosts[1].name in system_prompt


def test_build_prompts_laughter_instruction_depends_on_tag_support():
    profile_v3 = _profile()  # default hosts use tts.model_id="eleven_v3" from Profile defaults
    outline_output = _outline_output("ep1", "abcd1234")
    articles = [_article("abcd1234")]

    system_prompt, _ = script_module._build_prompts(profile_v3, outline_output.outline, articles)
    assert "[laughs]" in system_prompt

    profile_no_tags = _profile()
    profile_no_tags.tts.model_id = "eleven_turbo_v2_5"
    profile_no_tags.tts.dialogue_model_id = "eleven_turbo_v2_5"
    system_prompt_no_tags, _ = script_module._build_prompts(profile_no_tags, outline_output.outline, articles)
    assert "[laughs]" not in system_prompt_no_tags
    assert "spoken reaction word" in system_prompt_no_tags
