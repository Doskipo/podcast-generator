"""Smoke tests for the outline stage. No network: the OpenAI call is
monkeypatched at the outline module's _generate_outline seam.
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
    Listener,
    Outline,
    OutlineStory,
    PodcastSettings,
    Profile,
    RankedArticle,
    RankOutput,
    RecurringBit,
    Style,
)
from podcast.stages import outline as outline_module


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


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(outline_module, "episode_dir", _episode_dir)


def test_outline_stage_persists_and_uses_cheap_model(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        stories=[OutlineStory(headline="Something happened", source_ids=["abcd1234"], angle=_angle())],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids: fixture_outline,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    # outline uses the default (cheap) model, not the stronger script_model
    assert output.model == profile.llm.model
    assert output.model != profile.llm.script_model

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
        stories=[OutlineStory(headline="Something happened", source_ids=["unknown99"], angle=_angle())],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids: fixture_outline,
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
        stories=[
            OutlineStory(
                headline="Something happened", source_ids=["abcd1234"], angle=_angle(), recurring_bit="not-a-real-bit"
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids: fixture_outline,
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
        stories=[
            OutlineStory(headline="Story 1", source_ids=["a1"], angle=_angle(tangent=None), recurring_bit="pin-drop"),
            OutlineStory(headline="Story 2", source_ids=["a2"], angle=_angle(tangent=None), recurring_bit="pin-drop"),
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids: fixture_outline,
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

    def fake_generate_outline(client, model, system_prompt, user_prompt, bit_ids):
        captured["bit_ids"] = bit_ids
        return Outline(
            title="Test Episode",
            stories=[
                OutlineStory(
                    headline="Something happened",
                    source_ids=["abcd1234"],
                    angle=_angle(tangent=None),
                    recurring_bit="the-logistical-pin-drop",
                )
            ],
        )

    monkeypatch.setattr(outline_module, "_generate_outline", fake_generate_outline)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = outline_module.outline_stage(episode, rank_output, client=object())

    assert captured["bit_ids"] == ["the-logistical-pin-drop"]
    assert output.outline.stories[0].recurring_bit == "the-logistical-pin-drop"


def test_response_model_constrains_recurring_bit_to_known_ids():
    model = outline_module._response_model(["the-logistical-pin-drop"])

    story = {
        "headline": "h",
        "source_ids": ["a"],
        "angle": {"why_it_matters": "w", "tension_or_surprise": "t", "host_take": "h", "tangent": "a"},
    }

    # a known id validates
    ok = model.model_validate({"title": "t", "stories": [{**story, "recurring_bit": "the-logistical-pin-drop"}]})
    assert ok.stories[0].recurring_bit == "the-logistical-pin-drop"

    # null validates (no bit fits)
    none_ok = model.model_validate({"title": "t", "stories": [story]})
    assert none_ok.stories[0].recurring_bit is None

    # a paraphrased/invented id — the exact bug this fixes — is rejected at
    # the schema level, before it ever reaches _validate_outline
    with pytest.raises(ValueError):
        model.model_validate({"title": "t", "stories": [{**story, "recurring_bit": "logistical pin-drop"}]})


def test_response_model_forces_null_when_no_bits_configured():
    model = outline_module._response_model([])

    story = {
        "headline": "h",
        "source_ids": ["a"],
        "angle": {"why_it_matters": "w", "tension_or_surprise": "t", "host_take": "h", "tangent": "a"},
    }

    with pytest.raises(ValueError):
        model.model_validate({"title": "t", "stories": [{**story, "recurring_bit": "anything"}]})


def test_outline_stage_rejects_a_segment_with_both_bit_and_tangent(tmp_path, monkeypatch):
    profile = _profile(recurring_bits=[RecurringBit(name="pin-drop", description="...", max_per_episode=2)])
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    rank_output = _rank_output(episode.episode_id, articles)

    fixture_outline = Outline(
        title="Test Episode",
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=["abcd1234"],
                angle=_angle(tangent="a real tangent"),  # tangent AND a bit — not allowed together
                recurring_bit="pin-drop",
            )
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids: fixture_outline,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="both recurring_bit"):
        outline_module.outline_stage(episode, rank_output, client=object())


def test_outline_stage_allocates_word_budget_proportional_to_score(tmp_path, monkeypatch):
    profile = _profile()
    profile.podcast.duration_minutes = 10  # total_words = 10*150 - 120 = 1380
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
        stories=[
            OutlineStory(headline="High story", source_ids=["high"], angle=_angle(tangent=None)),
            OutlineStory(headline="Low story", source_ids=["low"], angle=_angle(tangent=None)),
        ],
    )
    monkeypatch.setattr(
        outline_module,
        "_generate_outline",
        lambda client, model, system_prompt, user_prompt, bit_ids: fixture_outline,
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
        OutlineStory(headline="a", source_ids=["a"], angle=_angle(tangent=None)),
        OutlineStory(headline="b", source_ids=["b"], angle=_angle(tangent=None)),
    ]
    budgets = outline_module._allocate_word_budgets(stories, score_by_id={}, total_words=100)
    assert budgets == [50, 50]
