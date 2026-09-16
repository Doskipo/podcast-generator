"""Smoke tests for the perform stage. No network: the OpenAI calls are
monkeypatched at the perform module's _generate_performance and
_generate_fact_check seams.
"""

from __future__ import annotations

import logging
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

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
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


def test_perform_stage_regenerates_once_when_over_word_cap_then_succeeds(tmp_path, monkeypatch):
    """The word cap is a quality signal, not a correctness invariant (see
    docs/decisions.md, "Correctness invariants vs quality signals") — an
    overrun gets one regeneration attempt, same as a correctness-invariant
    failure would, but through perform_stage's own bespoke retry, not
    generate_with_retry (which never even sees a reason to retry here,
    since the bloated performance is structurally valid)."""
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bloated_performance = _valid_performance(extra_cold_open=False)
    bloated_performance.segments[0].lines[0] = PerformedLine(speaker="Nova", text="So get THIS — " + "padding " * 20)
    trimmed_performance = _valid_performance(extra_cold_open=False)

    attempts = {"n": 0}
    prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
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
    assert "word cap" in prompts[1]  # the feedback, fed back verbatim
    assert output.performance.segments[0].lines[0].text == "So get THIS —"
    assert output.retried is True
    assert output.word_overrun == 0
    assert len(output.repairs) == 1
    assert "resolved by regenerating" in output.repairs[0]


def test_perform_stage_keeps_performance_and_flags_overrun_when_still_over_cap_after_retry(tmp_path, monkeypatch, caplog):
    """A cap is a quality signal, not a correctness invariant: if the one
    regeneration attempt doesn't fix it, the run must still succeed — the
    over-cap performance is kept and the overrun recorded on
    PerformOutput.word_overrun for quality_stage to flag, never raised."""
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    bloated_performance = _valid_performance(extra_cold_open=False)
    bloated_performance.segments[0].lines[0] = PerformedLine(speaker="Nova", text="So get THIS — " + "padding " * 20)

    prompts: list[str] = []

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
        prompts.append(user_prompt)
        return bloated_performance, _FIXTURE_USAGE  # every attempt is still bloated

    def fake_generate_fact_check(client, model, system_prompt, user_prompt):
        return [], _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    monkeypatch.setattr(perform_module, "_generate_fact_check", fake_generate_fact_check)
    _patch_episode_dir(monkeypatch, tmp_path)

    with caplog.at_level(logging.WARNING, logger="podcast.stages.perform"):
        output = perform_module.perform_stage(episode, critique_output, articles, client=object())  # does not raise

    assert len(prompts) == 2  # the initial attempt plus the one cap-regeneration attempt
    assert output.word_overrun > 0
    assert output.retried is True  # a regeneration was attempted, even though it didn't stick
    assert output.performance.segments[0].lines[0].text == bloated_performance.segments[0].lines[0].text

    messages = [r.getMessage() for r in caplog.records]
    assert any("keeping the performance" in m and "flagging the overrun" in m for m in messages)

    assert len(output.repairs) == 1
    assert "kept as-is" in output.repairs[0]


def test_word_overrun_is_positive_when_over_the_5_percent_cap():
    original = _original_script("abcd1234")
    performance = _valid_performance(extra_cold_open=False)
    performance.segments[0].lines[0] = PerformedLine(speaker="Nova", text="So get THIS — " + "padding " * 20)

    assert perform_module._word_overrun(performance, original) > 0


def test_word_overrun_is_zero_within_the_5_percent_cap():
    original = _original_script("abcd1234")
    performance = _valid_performance(extra_cold_open=False)

    assert perform_module._word_overrun(performance, original) == 0


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

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
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

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
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

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
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


# ---- segment-count structural fix (final polish, round 3, Part 1) ----------


def _perf_segment_dict(headline: str, speaker: str, text: str) -> dict:
    return {"headline": headline, "source_ids": ["a"], "lines": [{"speaker": speaker, "text": text}]}


def test_response_model_requires_exactly_segment_count_segments():
    """The structural fix for "performance has N segment(s), expected M":
    segments isn't a homogeneous list (which lets the model return any
    count) but an object with one required field per index, so the model
    can't omit — or add — a segment through the real API."""
    model = perform_module._response_model(2)

    ok = model.model_validate(
        {
            "title": "t",
            "cold_open": [{"speaker": "Nova", "text": "hi"}],
            "segments": {
                "segment_0": _perf_segment_dict("h0", "Nova", "first"),
                "segment_1": _perf_segment_dict("h1", "Max", "second"),
            },
            "outro": [{"speaker": "Max", "text": "bye"}],
        }
    )
    assert ok.segments.segment_0.headline == "h0"
    assert ok.segments.segment_1.headline == "h1"

    # missing segment_1 — a shortfall — is rejected at the schema level,
    # the exact failure mode this fixes
    with pytest.raises(ValueError):
        model.model_validate(
            {
                "title": "t",
                "cold_open": [{"speaker": "Nova", "text": "hi"}],
                "segments": {"segment_0": _perf_segment_dict("h0", "Nova", "first")},
                "outro": [{"speaker": "Max", "text": "bye"}],
            }
        )


def test_generate_performance_reassembles_segments_in_order():
    """_generate_performance must reassemble the response's per-index
    segment_0/segment_1/... fields back into Performance.segments in the
    same order — not, say, dict/insertion order, which JSON parsing could
    scramble for a model that's less disciplined about key order."""
    response_model = perform_module._response_model(2)
    parsed = response_model.model_validate(
        {
            "title": "Test Episode",
            "cold_open": [{"speaker": "Nova", "text": "hi"}],
            "segments": {
                "segment_0": _perf_segment_dict("h0", "Nova", "first"),
                "segment_1": _perf_segment_dict("h1", "Max", "second"),
            },
            "outro": [{"speaker": "Max", "text": "bye"}],
        }
    )

    class _FakeUsage:
        prompt_tokens = 10
        completion_tokens = 5

    class _FakeMessage:
        def __init__(self, parsed):
            self.parsed = parsed

    class _FakeChoice:
        def __init__(self, parsed):
            self.message = _FakeMessage(parsed)

    class _FakeCompletion:
        def __init__(self, parsed):
            self.choices = [_FakeChoice(parsed)]
            self.usage = _FakeUsage()

    class _FakeCompletions:
        def parse(self, **kwargs):
            return _FakeCompletion(parsed)

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    performance, usage = perform_module._generate_performance(_FakeClient(), "gpt-4o", "sys", "user", 2)

    assert [seg.headline for seg in performance.segments] == ["h0", "h1"]
    assert performance.segments[0].lines[0].text == "first"
    assert performance.segments[1].lines[0].text == "second"
    assert usage == TokenUsage(model="gpt-4o", prompt_tokens=10, completion_tokens=5)


def test_perform_stage_records_segment_count_repair(tmp_path, monkeypatch):
    """The trimmed-extra-segments repair from _reconcile_segment_count
    (see docs/decisions.md, "Correctness invariants vs quality signals")
    is surfaced on PerformOutput.repairs, not just logged."""
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    critique_output = _critique_output(episode.episode_id, "abcd1234")

    extra_segment = PerformedSegment(headline="extra", source_ids=["x"], lines=[PerformedLine(speaker="Nova", text="oops")])
    bloated_performance = _valid_performance(extra_cold_open=False)
    bloated_performance = bloated_performance.model_copy(update={"segments": [*bloated_performance.segments, extra_segment]})

    def fake_generate_performance(client, model, system_prompt, user_prompt, segment_count):
        return bloated_performance, _FIXTURE_USAGE

    def fake_generate_fact_check(client, model, system_prompt, user_prompt):
        return [], _FIXTURE_USAGE

    monkeypatch.setattr(perform_module, "_generate_performance", fake_generate_performance)
    monkeypatch.setattr(perform_module, "_generate_fact_check", fake_generate_fact_check)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = perform_module.perform_stage(episode, critique_output, articles, client=object())

    assert len(output.performance.segments) == 1
    assert any("dropped positionally" in r for r in output.repairs)


def test_reconcile_segment_count_drops_extra_segments_positionally():
    """Defensive backstop, not the normal path (see _response_model) — if a
    performance somehow still arrives with more segments than the original
    script, the extras are dropped positionally rather than raising."""
    original = _original_script("abcd1234")  # 1 segment
    performance = _valid_performance(extra_cold_open=False)
    extra_segment = PerformedSegment(headline="extra", source_ids=["x"], lines=[PerformedLine(speaker="Nova", text="oops")])
    performance = performance.model_copy(update={"segments": [*performance.segments, extra_segment]})

    reconciled, repair = perform_module._reconcile_segment_count(performance, original)

    assert len(reconciled.segments) == 1
    assert reconciled.segments[0].headline != "extra"
    assert repair is not None
    assert "dropped positionally" in repair


def test_reconcile_segment_count_leaves_a_matching_count_untouched():
    original = _original_script("abcd1234")
    performance = _valid_performance(extra_cold_open=False)

    reconciled, repair = perform_module._reconcile_segment_count(performance, original)

    assert reconciled == performance
    assert repair is None
