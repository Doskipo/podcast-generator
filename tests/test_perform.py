"""Smoke tests for the perform stage. No network: the OpenAI calls are
monkeypatched at the perform module's _generate_performance and
_generate_fact_check seams.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Angle,
    Article,
    Critique,
    CritiqueOutput,
    Episode,
    FactChangeFlag,
    Host,
    HostMood,
    HostStance,
    Interest,
    Line,
    Listener,
    Outline,
    OutlineOutput,
    OutlineStory,
    Performance,
    PerformedLine,
    PerformedSegment,
    PodcastSettings,
    Profile,
    Script,
    Segment,
    Style,
    TokenUsage,
)
from podcast.stages import perform as perform_module

_FIXTURE_USAGE = TokenUsage(model="m", prompt_tokens=10, completion_tokens=5)


def _host(name: str) -> Host:
    return Host(name=name, voice_id=f"voice-{name.lower()}", persona=f"{name} is curious and precise.", home_turf=[])


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
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
        text="Full text." * 20,
    )


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _original_script(source_id: str) -> Script:
    return Script(
        title="Test Episode",
        cold_open=[Line(speaker="Nova", text="This is Test Podcast, with Nova and Max.")],
        segments=[
            Segment(
                headline="Something happened",
                source_ids=[source_id],
                lines=[
                    Line(speaker="Nova", text="Here is the story."),
                    Line(speaker="Max", text="Tell me more."),
                ],
            )
        ],
        # Long enough (not just "See you next time.") that the mandatory
        # cold-open chit-chat line _valid_performance adds still fits inside
        # the 5% word cap — a short fixture and a percentage cap don't mix
        # well at these tiny word counts, so this outro carries the headroom
        # instead of the more heavily-asserted-on cold_open/segment lines.
        outro=[Line(speaker="Max", text="See you next time, thanks so much for tuning in today, it means a lot.")],
    )


def _critique_output(episode_id: str, source_id: str) -> CritiqueOutput:
    script = _original_script(source_id)
    return CritiqueOutput(
        episode_id=episode_id,
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o",
        critique=Critique(flags=[]),
        original_script=script,
        revised_script=script,
        total_words=sum(len(line.text.split()) for line in perform_module.flatten_lines(script)),
        over_budget_segments=[],
        terse_hosts=[],
    )


def _valid_performance(extra_cold_open: bool = True) -> Performance:
    """A performance that satisfies the structural contract against
    `_original_script`: same segment/outro speaker sequences, cold_open
    suffix-aligned with the original's single identification line."""
    cold_open = []
    if extra_cold_open:
        cold_open.append(PerformedLine(speaker="Nova", text="Hey.", delivery="warm"))
    cold_open.append(PerformedLine(speaker="Nova", text="This is Test Podcast... with Nova and Max.", delivery="settles in"))
    return Performance(
        title="Test Episode",
        cold_open=cold_open,
        segments=[
            PerformedSegment(
                headline="wrong headline",
                source_ids=["wrong-id"],
                lines=[
                    PerformedLine(speaker="Nova", text="So get THIS —", delivery="rises"),
                    PerformedLine(speaker="Max", text="Tell me more.", delivery="flat, curious"),
                ],
            )
        ],
        outro=[PerformedLine(speaker="Max", text="See you next time...", delivery="warm, slowing")],
    )


def _host_moods() -> list[HostMood]:
    return [
        HostMood(host="Nova", mood="playful", reason="today's stories lean silly"),
        HostMood(host="Max", mood="tired-but-sharp", reason="up late double-checking a stat"),
    ]


def _write_outline_json(dir_path: Path, episode_id: str, host_moods: list[HostMood] | None = None) -> None:
    """perform_stage now reads outline.json off disk (for host_moods) the
    same way quality_stage does — every perform test needs one present."""
    outline = Outline(
        title="t",
        host_moods=_host_moods() if host_moods is None else host_moods,
        stories=[
            OutlineStory(
                headline="s",
                source_ids=["abcd1234"],
                angle=Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent=None),
                stances=[
                    HostStance(host="Nova", attitude="excited", why="her home turf"),
                    HostStance(host="Max", attitude="skeptical", why="wants the numbers"),
                ],
            )
        ],
    )
    output = OutlineOutput(episode_id=episode_id, generated_at=datetime.now(timezone.utc), model="gpt-4o-mini", outline=outline)
    (dir_path / "outline.json").write_text(output.model_dump_json(), encoding="utf-8")


def _patch_episode_dir(monkeypatch, tmp_path: Path, episode_id: str = "ep1") -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(perform_module, "episode_dir", _episode_dir)
    _write_outline_json(_episode_dir(episode_id), episode_id)


def _patch_generation(monkeypatch, performance: Performance, fact_flags: list[FactChangeFlag] | None = None):
    prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt):
        prompts.append(user_prompt)
        return performance, _FIXTURE_USAGE

    def fake_generate_fact_check(client, model, system_prompt, user_prompt):
        return fact_flags or [], _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    monkeypatch.setattr(perform_module, "_generate_fact_check", fake_generate_fact_check)
    return prompts


def test_perform_stage_persists_performance_json(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    _patch_generation(monkeypatch, _valid_performance())
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    performance_path = tmp_path / "episodes" / episode.episode_id / "performance.json"
    assert performance_path.exists()
    reparsed = perform_module.PerformOutput.model_validate_json(performance_path.read_text(encoding="utf-8"))
    assert reparsed.performance.segments[0].lines[0].text == "So get THIS —"
    assert output.fact_flags == []
    assert output.retried is False


def test_perform_stage_overwrites_headline_and_source_ids_from_original(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    _patch_generation(monkeypatch, _valid_performance())
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    segment = output.performance.segments[0]
    assert segment.headline == "Something happened"
    assert segment.source_ids == ["abcd1234"]


def test_perform_stage_accepts_extra_cold_open_chitchat(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    _patch_generation(monkeypatch, _valid_performance(extra_cold_open=True))
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert len(output.performance.cold_open) == 2
    assert output.performance.cold_open[-1].speaker == "Nova"


def test_perform_stage_rejects_cold_open_suffix_mismatch(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance()
    bad_performance.cold_open[-1] = PerformedLine(speaker="Max", text="Wrong speaker at the end.")

    _patch_generation(monkeypatch, bad_performance)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="cold_open must end with"):
        perform_module.perform_stage(episode, critique_output, articles, client=object())


def test_perform_stage_rejects_segment_line_count_mismatch(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance()
    bad_performance.segments[0].lines = bad_performance.segments[0].lines[:1]  # dropped a line

    _patch_generation(monkeypatch, bad_performance)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="segment 0"):
        perform_module.perform_stage(episode, critique_output, articles, client=object())


def test_perform_stage_rejects_segment_speaker_sequence_mismatch(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance()
    lines = bad_performance.segments[0].lines
    bad_performance.segments[0].lines = [lines[1], lines[0]]  # swapped order

    _patch_generation(monkeypatch, bad_performance)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="segment 0"):
        perform_module.perform_stage(episode, critique_output, articles, client=object())


def test_perform_stage_rejects_unknown_speaker(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance()
    bad_performance.outro = [PerformedLine(speaker="Carol", text="See you next time...")]

    _patch_generation(monkeypatch, bad_performance)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="Carol"):
        perform_module.perform_stage(episode, critique_output, articles, client=object())


def test_perform_stage_rejects_performance_over_word_cap(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance(extra_cold_open=False)
    # pad one line's text far past the +5% word cap, structure untouched
    bad_performance.segments[0].lines[0] = PerformedLine(speaker="Nova", text="So get THIS — " + "padding " * 20)

    _patch_generation(monkeypatch, bad_performance)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="word cap"):
        perform_module.perform_stage(episode, critique_output, articles, client=object())


def test_perform_stage_retries_when_over_word_cap_then_succeeds(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bloated_performance = _valid_performance(extra_cold_open=False)
    bloated_performance.segments[0].lines[0] = PerformedLine(speaker="Nova", text="So get THIS — " + "padding " * 20)
    trimmed_performance = _valid_performance(extra_cold_open=False)

    attempts = {"n": 0}
    prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt):
        prompts.append(user_prompt)
        attempts["n"] += 1
        return (bloated_performance if attempts["n"] == 1 else trimmed_performance), _FIXTURE_USAGE

    def fake_generate_fact_check(client, model, system_prompt, user_prompt):
        return [], _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    monkeypatch.setattr(perform_module, "_generate_fact_check", fake_generate_fact_check)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert len(prompts) == 2
    assert "word cap" in prompts[1]  # the validation error, fed back verbatim
    assert output.performance.segments[0].lines[0].text == "So get THIS —"
    assert output.retried is True


def test_validate_word_cap_rejects_over_5_percent_overrun():
    original = _original_script("abcd1234")
    performance = _valid_performance(extra_cold_open=False)
    performance.segments[0].lines[0] = PerformedLine(speaker="Nova", text="So get THIS — " + "padding " * 20)

    with pytest.raises(ValueError, match="word cap"):
        perform_module._validate_word_cap(performance, original)


def test_validate_word_cap_accepts_within_5_percent():
    original = _original_script("abcd1234")
    performance = _valid_performance(extra_cold_open=False)

    perform_module._validate_word_cap(performance, original)  # does not raise


def test_perform_stage_retries_once_then_succeeds(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance()
    bad_performance.outro = [PerformedLine(speaker="Carol", text="oops")]
    good_performance = _valid_performance()

    attempts = {"n": 0}
    prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt):
        prompts.append(user_prompt)
        attempts["n"] += 1
        return (bad_performance if attempts["n"] == 1 else good_performance), _FIXTURE_USAGE

    def fake_generate_fact_check(client, model, system_prompt, user_prompt):
        return [], _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    monkeypatch.setattr(perform_module, "_generate_fact_check", fake_generate_fact_check)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert len(prompts) == 2
    assert "Carol" in prompts[1]  # the validation error, fed back verbatim
    # both the rejected first attempt, the retry, and the fact-check call are billed
    assert output.usage == [_FIXTURE_USAGE, _FIXTURE_USAGE, _FIXTURE_USAGE]
    assert output.retried is True


def test_perform_stage_raises_after_second_failed_validation(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bad_performance = _valid_performance()
    bad_performance.outro = [PerformedLine(speaker="Carol", text="oops")]

    prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt):
        prompts.append(user_prompt)
        return bad_performance, _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    _patch_episode_dir(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="Carol"):
        perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert len(prompts) == 2  # exactly one retry, no more


def test_perform_stage_uses_script_model_and_cheap_fact_check_model(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    _patch_generation(monkeypatch, _valid_performance())
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert output.model == profile.llm.script_model
    assert output.fact_check_model == profile.llm.model
    assert output.model != output.fact_check_model


def test_perform_stage_records_fact_check_flags(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    flag = FactChangeFlag(
        segment_index=0, speaker="Nova", original_text="Here is the story.", performed_text="So get THIS —", reason="dropped a number"
    )
    _patch_generation(monkeypatch, _valid_performance(), fact_flags=[flag])
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert output.fact_flags == [flag]


def test_perform_stage_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        perform_module.perform_stage(episode, critique_output, articles)


def test_flatten_performed_lines_order():
    performance = _valid_performance(extra_cold_open=True)
    flat = perform_module.flatten_performed_lines(performance)
    assert [line.text for line in flat] == [
        "Hey.",
        "This is Test Podcast... with Nova and Max.",
        "So get THIS —",
        "Tell me more.",
        "See you next time...",
    ]


def test_render_fact_check_pairs_excludes_new_cold_open_lines():
    original = _original_script("abcd1234")
    performance = _valid_performance(extra_cold_open=True)
    pairs_block = perform_module._render_fact_check_pairs(performance, original)

    assert "Hey." not in pairs_block  # the new chit-chat line has no original counterpart
    assert "This is Test Podcast... with Nova and Max." in pairs_block
    assert "Here is the story." in pairs_block


def test_build_performance_prompts_includes_all_six_rules():
    profile = _profile()
    original = _original_script("abcd1234")

    system_prompt, _user_prompt = perform_module._build_performance_prompts(profile, original, _host_moods())

    assert "continuing thought" in system_prompt  # rule 1
    assert "connectors" in system_prompt  # rule 2
    assert "capitalise" in system_prompt  # rule 3
    assert "ARC" in system_prompt  # rule 4
    assert "same sentence contour" in system_prompt  # rule 5
    assert "chit-chat" in system_prompt  # rule 6


def test_build_performance_prompts_includes_moods_and_tendency_framing():
    profile = _profile()
    original = _original_script("abcd1234")

    system_prompt, _user_prompt = perform_module._build_performance_prompts(profile, original, _host_moods())

    assert "Mood this episode: playful — today's stories lean silly" in system_prompt
    assert "Mood this episode: tired-but-sharp — up late double-checking a stat" in system_prompt
    assert "not a script of fixed lines to reuse" in system_prompt


def test_build_performance_prompts_includes_home_turf_not_a_quota_framing():
    profile = _profile()
    original = _original_script("abcd1234")

    system_prompt, _user_prompt = perform_module._build_performance_prompts(profile, original, _host_moods())

    assert "not a subject checklist" in system_prompt
    assert "pet subject" in system_prompt


def test_build_performance_prompts_omits_mood_line_when_no_host_moods():
    profile = _profile()
    original = _original_script("abcd1234")

    system_prompt, _user_prompt = perform_module._build_performance_prompts(profile, original, [])

    assert "Mood this episode" not in system_prompt


def test_perform_stage_reads_moods_from_outline_json(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    system_prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt):
        system_prompts.append(system_prompt)
        return _valid_performance(), _FIXTURE_USAGE

    def fake_generate_fact_check(client, model, system_prompt, user_prompt):
        return [], _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    monkeypatch.setattr(perform_module, "_generate_fact_check", fake_generate_fact_check)
    _patch_episode_dir(monkeypatch, tmp_path)

    perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert "Mood this episode: playful — today's stories lean silly" in system_prompts[0]


def test_validate_performed_source_ids_rejects_unknown_id():
    performance = _valid_performance()
    performance.segments[0].source_ids = ["unknown-id"]

    with pytest.raises(ValueError, match="unknown-id"):
        perform_module.validate_performed_source_ids(performance, known_ids={"abcd1234"})
