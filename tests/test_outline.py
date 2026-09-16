"""Smoke tests for the outline stage. No network: the OpenAI call is
monkeypatched at the outline module's _generate_outline seam.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Angle,
    Article,
    Episode,
    Host,
    HostMood,
    HostStance,
    Interest,
    Listener,
    Outline,
    OutlineStory,
    PodcastSettings,
    Profile,
    RankedArticle,
    RankOutput,
    RecurringBit,
    Style,
    TokenUsage,
    Transition,
)
from podcast.stages import outline as outline_module

_FIXTURE_USAGE = TokenUsage(model="m", prompt_tokens=10, completion_tokens=5)


def _host(name: str) -> Host:
    return Host(name=name, voice_id=f"voice-{name.lower()}", persona=f"{name} is curious and precise.", home_turf=[])


def _profile(recurring_bits: list[RecurringBit] | None = None) -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        podcast=PodcastSettings(
            name="Test Podcast",
            duration_minutes=8,
            listener=Listener(name="Eudald"),
            hosts=[_host("Nova"), _host("Max")],
            recurring_bits=recurring_bits or [],
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
        summary="a summary",
        text="Full text." * 20,
        interest="testing",
    )


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _rank_output(episode_id: str, articles: list[Article]) -> RankOutput:
    return RankOutput(
        episode_id=episode_id,
        ranked_at=datetime.now(timezone.utc),
        model="gpt-4o-mini",
        total_budget=len(articles),
        backfilled=0,
        scored=[],
        selected=articles,
    )


def _angle(tangent: str | None = "a tangent") -> Angle:
    return Angle(why_it_matters="it matters", tension_or_surprise="a twist", host_take="Nova cares", tangent=tangent)


def _transition(kind: str = "clean_transition", text_hint: str = "pivot to the next story") -> Transition:
    return Transition(kind=kind, text_hint=text_hint)


def _stances() -> list[HostStance]:
    """One stance per default test host (Nova, Max) — most tests don't care
    about stance content, just that every story has a valid one per host."""
    return [
        HostStance(host="Nova", attitude="excited", why="it's her home turf"),
        HostStance(host="Max", attitude="skeptical", why="wants the numbers"),
    ]


def _host_moods() -> list[HostMood]:
    """One mood per default test host (Nova, Max) — most tests don't care
    about mood content, just that the outline has exactly one per host
    (see _validate_outline's mood_hosts check)."""
    return [
        HostMood(host="Nova", mood="playful", reason="today's stories lean silly"),
        HostMood(host="Max", mood="tired-but-sharp", reason="up late double-checking a stat"),
    ]


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    episodes_dir = tmp_path / "episodes"

    def _episode_dir(episode_id: str) -> Path:
        d = episodes_dir / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(outline_module, "episode_dir", _episode_dir)
    # _previous_episode_moods scans paths.EPISODES_DIR directly (it has no
    # episode_dir() call of its own to patch) — without this, tests would
    # scan the *real* data/episodes/ on disk instead of the tmp_path used
    # everywhere else in these tests.
    monkeypatch.setattr(outline_module.paths, "EPISODES_DIR", episodes_dir)


def test_outline_stage_persists_and_uses_cheap_model(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[OutlineStory(headline="Something happened", source_ids=["abcd1234"], angle=_angle(), stances=_stances())],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    # outline uses the default (cheap) model, not the stronger script_model
    assert output.model == profile.llm.model
    assert output.model != profile.llm.script_model
    assert output.retried is False

    outline_path = tmp_path / "episodes" / episode.episode_id / "outline.json"
    assert outline_path.exists()
    reparsed = outline_module.OutlineOutput.model_validate_json(outline_path.read_text(encoding="utf-8"))
    assert reparsed.outline.title == "Test Episode"


def test_outline_stage_rejects_unknown_source_ids(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[OutlineStory(headline="Something happened", source_ids=["unknown99"], angle=_angle(), stances=_stances())],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="unknown source_ids"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_rejects_unknown_recurring_bit(tmp_path, monkeypatch):
    profile = _profile(recurring_bits=[RecurringBit(name="pin-drop", description="...", max_per_episode=2)])
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(),
                recurring_bit="not-a-real-bit",
                stances=_stances(),
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="unknown recurring bit"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_rejects_recurring_bit_over_its_max(tmp_path, monkeypatch):
    profile = _profile(recurring_bits=[RecurringBit(name="pin-drop", description="...", max_per_episode=1)])
    episode = _episode(profile)
    articles = [_article("a1"), _article("a2")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(headline="Story 1", source_ids=["a1"], angle=_angle(tangent=None), recurring_bit="pin-drop", stances=_stances()),
            OutlineStory(
                headline="Story 2",
                source_ids=["a2"],
                angle=_angle(tangent=None),
                recurring_bit="pin-drop",
                stances=_stances(),
                transition=_transition(),
            ),
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="exceeds max_per_episode"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        outline_module.outline_stage(episode, rank_output)


def test_recurring_bit_effective_id_defaults_to_slug_of_name():
    # this is the exact bug from the log: a free-text name like "The
    # logistical pin-drop" is what the model used to have to paraphrase back
    # verbatim; effective_id gives it a stable, slugged identifier instead.
    slugged = RecurringBit(name="The logistical pin-drop", description="...", max_per_episode=2)
    assert slugged.effective_id == "the-logistical-pin-drop"

    explicit = RecurringBit(name="The logistical pin-drop", id="pin-drop", description="...", max_per_episode=2)
    assert explicit.effective_id == "pin-drop"  # explicit id wins over the derived slug


def test_outline_stage_passes_effective_ids_to_generate_outline(tmp_path, monkeypatch):
    profile = _profile(
        recurring_bits=[RecurringBit(name="The logistical pin-drop", description="...", max_per_episode=2)]
    )
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    captured = {}

    def fake_generate_outline(client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods):
        captured["bit_ids"] = bit_ids
        return (
            Outline(
                title="Test Episode",
                host_moods=_host_moods(),
                stories=[
                    OutlineStory(
                        headline="Something happened",
                        source_ids=["abcd1234"],
                        angle=_angle(tangent=None),
                        recurring_bit="the-logistical-pin-drop",
                        stances=_stances(),
                    )
                ],
            ),
            _FIXTURE_USAGE,
        )

    monkeypatch.setattr(outline_module, "_generate_outline", fake_generate_outline)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    assert captured["bit_ids"] == ["the-logistical-pin-drop"]
    assert output.outline.stories[0].recurring_bit == "the-logistical-pin-drop"


_STANCE_OBJECT = {
    "Nova": {"attitude": "excited", "why": "her home turf", "arc": None},
    "Max": {"attitude": "skeptical", "why": "wants the numbers", "arc": None},
}

_MOOD_REASONS_OBJECT = {"Nova": "today's stories lean silly", "Max": "up late double-checking a stat"}


def test_response_model_constrains_recurring_bit_to_known_ids():
    model = outline_module._response_model(["the-logistical-pin-drop"], ["Nova", "Max"])

    story = {
        "headline": "h",
        "source_ids": ["a"],
        "angle": {"why_it_matters": "w", "tension_or_surprise": "t", "host_take": "h", "tangent": "a"},
        "stances": _STANCE_OBJECT,
    }

    # a known id validates
    ok = model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "recurring_bit": "the-logistical-pin-drop"}]})
    assert ok.stories[0].recurring_bit == "the-logistical-pin-drop"

    # null validates (no bit fits)
    none_ok = model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [story]})
    assert none_ok.stories[0].recurring_bit is None

    # a paraphrased/invented id — the exact bug this fixes — is rejected at
    # the schema level, before it ever reaches _validate_outline
    with pytest.raises(ValueError):
        model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "recurring_bit": "logistical pin-drop"}]})


def test_response_model_forces_null_when_no_bits_configured():
    model = outline_module._response_model([], ["Nova", "Max"])

    story = {
        "headline": "h",
        "source_ids": ["a"],
        "angle": {"why_it_matters": "w", "tension_or_surprise": "t", "host_take": "h", "tangent": "a"},
        "stances": _STANCE_OBJECT,
    }

    with pytest.raises(ValueError):
        model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "recurring_bit": "anything"}]})


def test_response_model_stances_is_an_object_with_one_required_field_per_host():
    """The structural fix: stances isn't list[{host, ...}] (a shape a model
    can duplicate/omit a host in) — it's an object with a required field
    named after each host, so "exactly one stance per host" is true by
    construction, not just checked afterward."""
    model = outline_module._response_model([], ["Nova", "Max"])

    story = {
        "headline": "h",
        "source_ids": ["a"],
        "angle": {"why_it_matters": "w", "tension_or_surprise": "t", "host_take": "h", "tangent": "a"},
    }

    ok = model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "stances": _STANCE_OBJECT}]})
    assert ok.stories[0].stances.Nova.attitude == "excited"
    assert ok.stories[0].stances.Max.attitude == "skeptical"

    # missing a required host's stance — schema-level rejection, the same
    # way an invalid recurring bit id is, not left to _validate_outline alone
    missing_max = {"Nova": _STANCE_OBJECT["Nova"]}
    with pytest.raises(ValueError):
        model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "stances": missing_max}]})


def test_outline_stage_rejects_a_segment_with_both_bit_and_tangent(tmp_path, monkeypatch):
    profile = _profile(recurring_bits=[RecurringBit(name="pin-drop", description="...", max_per_episode=2)])
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent="a real tangent"),  # tangent AND a bit — not allowed together
                recurring_bit="pin-drop",
                stances=_stances(),
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="both recurring_bit"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_allocates_word_budget_proportional_to_score(tmp_path, monkeypatch):
    profile = _profile()
    profile.podcast.duration_minutes = 10
    profile.llm.words_per_minute = 150  # round number for this test's arithmetic; total_words = 10*150 - 120 = 1380
    episode = _episode(profile)
    articles = [_article("high"), _article("low")]
    rank_output = _rank_output(episode.episode_id, articles)
    # give "high" 3x the score of "low" -> budgets should split roughly 3:1
    rank_output = rank_output.model_copy(
        update={
            "scored": [
                RankedArticle(article=articles[0], interest=None, score=0.9, reason="r", selected=True),
                RankedArticle(article=articles[1], interest=None, score=0.3, reason="r", selected=True),
            ]
        }
    )

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(headline="High story", source_ids=["high"], angle=_angle(tangent=None), stances=_stances()),
            OutlineStory(
                headline="Low story",
                source_ids=["low"],
                angle=_angle(tangent=None),
                stances=_stances(),
                transition=_transition(),
            ),
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    budgets = {s.headline: s.word_budget for s in output.outline.stories}
    assert sum(budgets.values()) == 1380  # exact sum, largest-remainder method
    assert budgets["High story"] > budgets["Low story"]
    # roughly 3:1 (0.9 : 0.3) -> ~1035 : ~345
    assert abs(budgets["High story"] - 1035) <= 1
    assert abs(budgets["Low story"] - 345) <= 1


def test_allocate_word_budgets_falls_back_to_equal_split_when_all_scores_zero():
    stories = [
        OutlineStory(headline="a", source_ids=["a"], angle=_angle(tangent=None), stances=_stances()),
        OutlineStory(headline="b", source_ids=["b"], angle=_angle(tangent=None), stances=_stances()),
    ]
    budgets = outline_module._allocate_word_budgets(stories, score_by_id={}, total_words=100)
    assert budgets == [50, 50]


def test_outline_stage_rejects_missing_stance_for_a_host(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent=None),
                # only Nova has a stance — Max is missing
                stances=[HostStance(host="Nova", attitude="excited", why="her home turf")],
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="exactly one stance per host"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_retries_once_then_succeeds_after_a_bad_outline(tmp_path, monkeypatch):
    """See docs/decisions.md ("One-retry-with-feedback"): a validation
    failure gets exactly one retry, with the error fed back into the
    prompt, before the stage gives up."""
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    invalid_outline = Outline(
        title="Bad",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent=None),
                stances=[HostStance(host="Nova", attitude="excited", why="her home turf")],  # Max missing
            )
        ],
    )
    valid_outline = Outline(
        title="Good",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent=None),
                stances=_stances(),
            )
        ],
    )

    prompts: list[str] = []

    def fake_generate_outline(client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods):
        prompts.append(user_prompt)
        outline = invalid_outline if len(prompts) == 1 else valid_outline
        return outline, _FIXTURE_USAGE

    monkeypatch.setattr(outline_module, "_generate_outline", fake_generate_outline)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    assert output.outline.title == "Good"
    assert len(prompts) == 2
    assert prompts[0] == prompts[1].split("\n\nYour previous response was invalid:")[0]  # original kept intact
    assert "exactly one stance per host" in prompts[1]  # the validation error, fed back verbatim
    # both attempts are billed API calls — both counted, not just the one that
    # ultimately passed validation
    assert output.usage == [_FIXTURE_USAGE, _FIXTURE_USAGE]
    assert output.retried is True


def test_outline_stage_raises_after_a_second_failed_validation(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    always_invalid = Outline(
        title="Bad",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent=None),
                stances=[HostStance(host="Nova", attitude="excited", why="her home turf")],
            )
        ],
    )

    prompts: list[str] = []

    def fake_generate_outline(client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods):
        prompts.append(user_prompt)
        return always_invalid, _FIXTURE_USAGE

    monkeypatch.setattr(outline_module, "_generate_outline", fake_generate_outline)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="exactly one stance per host"):
        outline_module.outline_stage(episode, rank_output, client=object())

    assert len(prompts) == 2  # exactly one retry, no more


def _evergreen_article(source_id: str) -> Article:
    now = datetime.now(timezone.utc)
    return Article(
        source_id=source_id,
        url=f"https://en.wikipedia.org/wiki/{source_id}",
        title=f"Wiki {source_id}",
        feed_url=f"https://en.wikipedia.org/wiki/{source_id}",
        published_at=now,
        fetched_at=now,
        summary="a primer",
        text="primer text " * 20,
        source="evergreen",
        interest="testing",
    )


def test_outline_stage_marks_a_primer_story_is_primer(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_evergreen_article("wiki1")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[OutlineStory(headline="A primer", source_ids=["wiki1"], angle=_angle(tangent=None), stances=_stances())],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    assert output.outline.stories[0].is_primer is True


def test_outline_stage_leaves_a_news_story_is_primer_false(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[OutlineStory(headline="News", source_ids=["abcd1234"], angle=_angle(tangent=None), stances=_stances())],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    assert output.outline.stories[0].is_primer is False


def test_outline_stage_rejects_a_story_mixing_evergreen_and_news_sources(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("news1"), _evergreen_article("wiki1")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Mixed", source_ids=["news1", "wiki1"], angle=_angle(tangent=None), stances=_stances()
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="mixes an evergreen primer"):
        outline_module.outline_stage(episode, rank_output, client=object())


_SAMPLED_MOODS = {"Nova": "playful", "Max": "tired-but-sharp"}


def test_build_prompts_includes_evergreen_marker_and_instruction_only_when_present():
    profile = _profile()

    system_prompt, user_prompt = outline_module._build_prompts(profile, [_evergreen_article("wiki1")], _SAMPLED_MOODS)
    assert "[EVERGREEN PRIMER]" in user_prompt
    assert "introduce or deepen the topic" in system_prompt

    system_prompt_news_only, user_prompt_news_only = outline_module._build_prompts(
        profile, [_article("news1")], _SAMPLED_MOODS
    )
    assert "[EVERGREEN PRIMER]" not in user_prompt_news_only
    assert "introduce or deepen the topic" not in system_prompt_news_only


def test_build_prompts_includes_moods_and_tendency_framing():
    profile = _profile()

    system_prompt, _ = outline_module._build_prompts(profile, [_article("news1")], _SAMPLED_MOODS)

    assert "Nova: playful" in system_prompt
    assert "Max: tired-but-sharp" in system_prompt
    assert "not a script of fixed lines to reuse" in system_prompt


def test_outline_stage_refuses_zero_selected_articles(monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    rank_output = _rank_output(episode.episode_id, [])

    # No _generate_outline patch needed — the stage must reject before ever
    # calling the model.
    with pytest.raises(ValueError, match="zero sources"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_rejects_a_transition_on_the_first_story(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent=None),
                stances=_stances(),
                transition=_transition(),  # nothing precedes the first story — this must be null
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="first story and must not have a transition"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_rejects_a_missing_transition_on_a_later_story(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("a1"), _article("a2")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(headline="Story 1", source_ids=["a1"], angle=_angle(tangent=None), stances=_stances()),
            # Story 2 is missing a transition
            OutlineStory(headline="Story 2", source_ids=["a2"], angle=_angle(tangent=None), stances=_stances()),
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="must have a transition"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_accepts_link_and_clean_transition_and_logs_them(tmp_path, monkeypatch, caplog):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("a1"), _article("a2"), _article("a3")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        host_moods=_host_moods(),
        stories=[
            OutlineStory(headline="Story 1", source_ids=["a1"], angle=_angle(tangent=None), stances=_stances()),
            OutlineStory(
                headline="Story 2",
                source_ids=["a2"],
                angle=_angle(tangent=None),
                stances=_stances(),
                transition=_transition(kind="link", text_hint="same company as Story 1"),
            ),
            OutlineStory(
                headline="Story 3",
                source_ids=["a3"],
                angle=_angle(tangent=None),
                stances=_stances(),
                transition=_transition(kind="clean_transition", text_hint="pivot, nothing connects them"),
            ),
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods: (fixture_outline, _FIXTURE_USAGE),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with caplog.at_level(logging.INFO, logger="podcast.stages.outline"):
        output = outline_module.outline_stage(episode, rank_output, client=object())

    assert output.outline.stories[0].transition is None
    assert output.outline.stories[1].transition.kind == "link"
    assert output.outline.stories[2].transition.kind == "clean_transition"

    messages = [r.getMessage() for r in caplog.records]
    assert any("link" in m and "same company as Story 1" in m for m in messages)
    assert any("clean_transition" in m and "pivot, nothing connects them" in m for m in messages)


def test_response_model_transition_is_nullable_and_constrains_kind():
    model = outline_module._response_model([], ["Nova", "Max"])

    story = {
        "headline": "h",
        "source_ids": ["a"],
        "angle": {"why_it_matters": "w", "tension_or_surprise": "t", "host_take": "h", "tangent": None},
        "stances": _STANCE_OBJECT,
    }

    # null transition validates (e.g. the first story)
    none_ok = model.model_validate({"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [story]})
    assert none_ok.stories[0].transition is None

    # a valid transition validates
    ok = model.model_validate(
        {"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "transition": {"kind": "link", "text_hint": "same person"}}]}
    )
    assert ok.stories[0].transition.kind == "link"

    # an invalid kind is rejected at the schema level
    with pytest.raises(ValueError):
        model.model_validate(
            {"title": "t", "mood_reasons": _MOOD_REASONS_OBJECT, "stories": [{**story, "transition": {"kind": "related", "text_hint": "x"}}]}
        )


def _write_outline_json(episodes_dir: Path, episode_id: str, host_moods: list[HostMood], *, corrupt: bool = False) -> None:
    """Persist a minimal outline.json for `episode_id` directly under
    `episodes_dir`, bypassing outline_stage — used to set up a "previous
    episode" for _previous_episode_moods to find."""
    d = episodes_dir / episode_id
    d.mkdir(parents=True, exist_ok=True)
    outline_path = d / "outline.json"
    if corrupt:
        outline_path.write_text("{not valid json", encoding="utf-8")
        return

    outline = Outline(
        title="Previous episode",
        host_moods=host_moods,
        stories=[OutlineStory(headline="s", source_ids=["a"], angle=_angle(tangent=None), stances=_stances())],
    )
    output = outline_module.OutlineOutput(
        episode_id=episode_id,
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o-mini",
        outline=outline,
        usage=[],
        retried=False,
    )
    outline_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")


def test_previous_episode_moods_reads_nearest_earlier_episode_with_moods(tmp_path, monkeypatch):
    episodes_dir = tmp_path / "episodes"
    monkeypatch.setattr(outline_module.paths, "EPISODES_DIR", episodes_dir)

    _write_outline_json(
        episodes_dir,
        "ep1",
        [HostMood(host="Nova", mood="energetic", reason="r"), HostMood(host="Max", mood="tender", reason="r")],
    )
    _write_outline_json(
        episodes_dir,
        "ep2",
        [HostMood(host="Nova", mood="playful", reason="r"), HostMood(host="Max", mood="tired-but-sharp", reason="r")],
    )

    result = outline_module._previous_episode_moods("ep3")

    # the nearest earlier episode (ep2), not an older one (ep1)
    assert result == {"Nova": "playful", "Max": "tired-but-sharp"}


def test_previous_episode_moods_skips_episodes_without_usable_moods(tmp_path, monkeypatch):
    """A nearest-earlier episode that has no outline.json at all (failed
    before outline), one whose outline.json is corrupt, and one whose
    outline.json predates the host_moods field (defaults to []) must all be
    skipped in favour of the next episode back that actually has moods."""
    episodes_dir = tmp_path / "episodes"
    monkeypatch.setattr(outline_module.paths, "EPISODES_DIR", episodes_dir)

    _write_outline_json(
        episodes_dir,
        "ep1",
        [HostMood(host="Nova", mood="contrarian", reason="r"), HostMood(host="Max", mood="playful", reason="r")],
    )
    _write_outline_json(episodes_dir, "ep2", [])  # pre-existing outline.json, no moods field populated
    _write_outline_json(episodes_dir, "ep3", [], corrupt=True)
    (episodes_dir / "ep4").mkdir(parents=True)  # reached fetch/rank but never outline

    result = outline_module._previous_episode_moods("ep5")

    assert result == {"Nova": "contrarian", "Max": "playful"}


def test_previous_episode_moods_returns_empty_when_none_found(tmp_path, monkeypatch):
    episodes_dir = tmp_path / "episodes"
    monkeypatch.setattr(outline_module.paths, "EPISODES_DIR", episodes_dir)

    # no episodes directory at all yet
    assert outline_module._previous_episode_moods("ep1") == {}

    episodes_dir.mkdir()
    # an episodes dir that exists but is empty
    assert outline_module._previous_episode_moods("ep1") == {}

    # only *later* episodes exist — nothing earlier to read
    _write_outline_json(episodes_dir, "ep9", [HostMood(host="Nova", mood="tender", reason="r")])
    assert outline_module._previous_episode_moods("ep1") == {}


def test_sample_moods_is_reproducible_for_the_same_seed():
    host_names = ["Nova", "Max"]
    first = outline_module._sample_moods(outline_module.random.Random("episode-123"), host_names, {})
    second = outline_module._sample_moods(outline_module.random.Random("episode-123"), host_names, {})
    assert first == second


def test_sample_moods_excludes_the_previous_episodes_mood():
    rng = outline_module.random.Random("episode-123")
    result = outline_module._sample_moods(rng, ["Nova", "Max"], {"Nova": "playful", "Max": "tender"})
    assert result["Nova"] != "playful"
    assert result["Max"] != "tender"


def test_sample_moods_falls_back_to_full_range_for_a_host_with_no_previous_mood():
    # Max has no entry in previous_moods (new host, or first episode ever)
    # — every option must still be reachable, not just the 4 that would
    # remain after excluding a value that isn't actually there.
    seen = set()
    for seed in range(30):
        result = outline_module._sample_moods(outline_module.random.Random(seed), ["Max"], {})
        seen.add(result["Max"])
    assert seen == set(outline_module.MOOD_OPTIONS)


def test_outline_stage_seeds_moods_from_episode_id_reproducibly(tmp_path, monkeypatch):
    """Re-running outline_stage for the same episode_id must sample the
    same moods both times — see docs/decisions.md ("Persona rigidity")."""
    profile = _profile()
    articles = [_article("abcd1234")]

    captured_moods: list[dict[str, str]] = []

    def fake_generate_outline(client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods):
        captured_moods.append(sampled_moods)
        return (
            Outline(
                title="Test Episode",
                host_moods=[HostMood(host=n, mood=sampled_moods[n], reason="r") for n in host_names],
                stories=[OutlineStory(headline="h", source_ids=["abcd1234"], angle=_angle(tangent=None), stances=_stances())],
            ),
            _FIXTURE_USAGE,
        )

    monkeypatch.setattr(outline_module, "_generate_outline", fake_generate_outline)
    _patch_episode_dir(monkeypatch, tmp_path)

    episode = _episode(profile, episode_id="repeatable-ep")
    rank_output = _rank_output(episode.episode_id, articles)

    outline_module.outline_stage(episode, rank_output, client=object())
    outline_module.outline_stage(episode, rank_output, client=object())

    assert len(captured_moods) == 2
    assert captured_moods[0] == captured_moods[1]


def test_outline_stage_excludes_previous_episodes_mood_and_logs_moods(tmp_path, monkeypatch, caplog):
    profile = _profile()
    articles = [_article("abcd1234")]
    _patch_episode_dir(monkeypatch, tmp_path)

    episodes_dir = tmp_path / "episodes"
    _write_outline_json(
        episodes_dir,
        "ep1-previous",
        [HostMood(host="Nova", mood="playful", reason="r"), HostMood(host="Max", mood="tender", reason="r")],
    )

    captured_moods: dict[str, str] = {}

    def fake_generate_outline(client, model, system_prompt, user_prompt, bit_ids, host_names, sampled_moods):
        captured_moods.update(sampled_moods)
        return (
            Outline(
                title="Test Episode",
                host_moods=[HostMood(host=n, mood=sampled_moods[n], reason=f"{n} reason") for n in host_names],
                stories=[OutlineStory(headline="h", source_ids=["abcd1234"], angle=_angle(tangent=None), stances=_stances())],
            ),
            _FIXTURE_USAGE,
        )

    monkeypatch.setattr(outline_module, "_generate_outline", fake_generate_outline)

    episode = _episode(profile, episode_id="ep2-current")
    rank_output = _rank_output(episode.episode_id, articles)

    with caplog.at_level(logging.INFO, logger="podcast.stages.outline"):
        output = outline_module.outline_stage(episode, rank_output, client=object())

    assert captured_moods["Nova"] != "playful"
    assert captured_moods["Max"] != "tender"

    messages = [r.getMessage() for r in caplog.records]
    for mood in output.outline.host_moods:
        assert any(mood.host in m and f"mood={mood.mood}" in m and mood.reason in m for m in messages)
