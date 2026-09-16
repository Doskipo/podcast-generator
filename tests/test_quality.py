"""Smoke tests for the quality stage — proxy grounding/naturalness metrics
computed from existing artefacts, plus the one cheap-model judge call
(monkeypatched at the module's own _generate_judge seam; no network).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from podcast.models import (
    Angle,
    Article,
    Critique,
    CritiqueFlag,
    CritiqueOutput,
    Episode,
    FactChangeFlag,
    Host,
    HostStance,
    Interest,
    JudgeScore,
    Line,
    Listener,
    Outline,
    OutlineOutput,
    OutlineStory,
    Performance,
    PerformedLine,
    PerformedSegment,
    PerformOutput,
    PodcastSettings,
    Profile,
    QualityJudge,
    Script,
    ScriptOutput,
    Segment,
    Style,
    TokenUsage,
)
from podcast.stages import quality as quality_module

_FIXTURE_USAGE = TokenUsage(model="gpt-4o-mini", prompt_tokens=10, completion_tokens=5)


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


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _stances() -> list[HostStance]:
    return [
        HostStance(host="Nova", attitude="excited", why="her home turf"),
        HostStance(host="Max", attitude="skeptical", why="wants the numbers"),
    ]


def _line(speaker: str, text: str) -> PerformedLine:
    return PerformedLine(speaker=speaker, text=text)


# ---- naturalness proxy unit tests ---------------------------------------


def test_audio_tag_density_counts_bracketed_tags_per_100_words():
    lines = [_line("Nova", "[laughs] " + "word " * 49)]  # 1 tag, 50 words
    assert quality_module._audio_tag_density_per_100_words(lines) == 2.0


def test_audio_tag_density_is_zero_for_no_words():
    assert quality_module._audio_tag_density_per_100_words([]) == 0.0


def test_interjection_or_dash_share_counts_openers_and_trailing_dashes():
    lines = [
        _line("Nova", "Wait, that can't be right."),  # interjection opener
        _line("Max", "So get THIS —"),  # trailing dash
        _line("Nova", "That's a normal declarative line."),  # neither
        _line("Max", "Completely unrelated statement."),  # neither
    ]
    assert quality_module._interjection_or_dash_share(lines) == 0.5


def test_interjection_or_dash_share_is_zero_for_no_lines():
    assert quality_module._interjection_or_dash_share([]) == 0.0


def test_host_balance_is_one_when_hosts_have_equal_mean_line_length():
    lines = [_line("Nova", "one two three four"), _line("Max", "five six seven eight")]
    assert quality_module._host_balance(lines) == 1.0


def test_host_balance_reflects_lopsided_line_lengths():
    lines = [
        _line("Nova", "one"),  # 1 word
        _line("Max", "one two three four"),  # 4 words
    ]
    assert quality_module._host_balance(lines) == 0.25


def test_host_balance_is_one_when_only_one_speaker_has_lines():
    lines = [_line("Nova", "one two three")]
    assert quality_module._host_balance(lines) == 1.0


def test_catchphrase_count_finds_a_verbatim_repeated_four_word_phrase():
    lines = [
        _line("Nova", "you know what I mean"),
        _line("Nova", "and honestly, you know what I mean, truly"),
        _line("Max", "nothing repeated here at all"),
    ]
    # the repeated 5-word run "you know what i mean" contains two distinct
    # overlapping 4-word windows ("you know what i" and "know what i
    # mean") — both recur, so this counts as 2, not 1. A known quirk of
    # the sliding-window approach: a single longer repeated phrase inflates
    # the count. See docs/decisions.md ("Quality metrics").
    assert quality_module._catchphrase_count(lines) == 2


def test_catchphrase_count_is_zero_with_no_repetition():
    lines = [_line("Nova", "a unique sentence here"), _line("Max", "another distinct sentence entirely")]
    assert quality_module._catchphrase_count(lines) == 0


# ---- grounding ------------------------------------------------------------


def _outline_output_for_grounding(retried: bool = False) -> OutlineOutput:
    return OutlineOutput(
        episode_id="ep1",
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o-mini",
        outline=Outline(
            title="t",
            stories=[
                OutlineStory(
                    headline="Story A",
                    source_ids=["a1", "a2"],
                    angle=Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent=None),
                    stances=_stances(),
                ),
                OutlineStory(
                    headline="Primer story",
                    source_ids=["wiki1"],
                    angle=Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent=None),
                    stances=_stances(),
                    is_primer=True,
                ),
            ],
        ),
        retried=retried,
    )


def _critique_output_for_grounding(n_flags: int, n_original_lines: int, retried: bool = False) -> CritiqueOutput:
    lines = [Line(speaker="Nova" if i % 2 == 0 else "Max", text=f"line {i}") for i in range(n_original_lines)]
    script = Script(title="t", cold_open=[], segments=[Segment(headline="h", source_ids=[], lines=lines)], outro=[])
    flags = [CritiqueFlag(line_index=i, issue="expository", rewritten_lines=[lines[i]]) for i in range(n_flags)]
    return CritiqueOutput(
        episode_id="ep1",
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o-mini",
        critique=Critique(flags=flags),
        original_script=script,
        revised_script=script,
        total_words=sum(len(line.text.split()) for line in lines),
        over_budget_segments=[],
        terse_hosts=[],
        retried=retried,
    )


def _perform_output_for_grounding(n_fact_flags: int, retried: bool = False) -> PerformOutput:
    fact_flags = [
        FactChangeFlag(segment_index=0, speaker="Nova", original_text="a", performed_text="b", reason="drift")
        for _ in range(n_fact_flags)
    ]
    return PerformOutput(
        episode_id="ep1",
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o",
        fact_check_model="gpt-4o-mini",
        performance=Performance(title="t", cold_open=[], segments=[], outro=[]),
        fact_flags=fact_flags,
        retried=retried,
    )


def test_grounding_quality_counts_distinct_sources_across_stories():
    outline_output = _outline_output_for_grounding()
    grounding = quality_module._grounding_quality(
        outline_output,
        _critique_output_for_grounding(n_flags=0, n_original_lines=4),
        _perform_output_for_grounding(n_fact_flags=0),
        stage_retries={"outline": False, "script": False, "critique": False, "perform": False},
    )
    assert grounding.source_count == 3  # a1, a2, wiki1
    assert grounding.evergreen_share == 0.5  # 1 of 2 stories is a primer


def test_grounding_quality_computes_critique_rewrite_rate():
    grounding = quality_module._grounding_quality(
        _outline_output_for_grounding(),
        _critique_output_for_grounding(n_flags=2, n_original_lines=4),
        _perform_output_for_grounding(n_fact_flags=3),
        stage_retries={"outline": False, "script": False, "critique": False, "perform": False},
    )
    assert grounding.critique_flags == 2
    assert grounding.critique_rewrite_rate == 0.5  # 2 flags / 4 original lines
    assert grounding.fact_drift_flags == 3


def test_grounding_quality_reports_retries_from_each_outputs_own_field():
    grounding = quality_module._grounding_quality(
        _outline_output_for_grounding(retried=True),
        _critique_output_for_grounding(n_flags=0, n_original_lines=2, retried=True),
        _perform_output_for_grounding(n_fact_flags=0, retried=False),
        stage_retries={"outline": True, "script": False, "critique": True, "perform": False},
    )
    assert grounding.stage_retries == {"outline": True, "script": False, "critique": True, "perform": False}
    assert grounding.total_retries == 2


def test_grounding_quality_handles_zero_stories_and_zero_lines():
    empty_outline = OutlineOutput(
        episode_id="ep1", generated_at=datetime.now(timezone.utc), model="m", outline=Outline(title="t", stories=[])
    )
    empty_critique = _critique_output_for_grounding(n_flags=0, n_original_lines=0)
    grounding = quality_module._grounding_quality(
        empty_outline,
        empty_critique,
        _perform_output_for_grounding(n_fact_flags=0),
        stage_retries={"outline": False, "script": False, "critique": False, "perform": False},
    )
    assert grounding.source_count == 0
    assert grounding.evergreen_share == 0.0
    assert grounding.critique_rewrite_rate == 0.0  # no division by zero


# ---- full quality_stage (disk I/O + judge seam) ---------------------------


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(quality_module, "episode_dir", _episode_dir)


def _write_upstream_artefacts(tmp_path: Path, episode_id: str) -> tuple[CritiqueOutput, PerformOutput]:
    d = tmp_path / "episodes" / episode_id
    d.mkdir(parents=True, exist_ok=True)

    outline_output = _outline_output_for_grounding()
    (d / "outline.json").write_text(outline_output.model_dump_json(), encoding="utf-8")

    script_output = ScriptOutput(
        episode_id=episode_id,
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o",
        script=Script(title="t", cold_open=[], segments=[], outro=[]),
    )
    (d / "script.json").write_text(script_output.model_dump_json(), encoding="utf-8")

    critique_output = _critique_output_for_grounding(n_flags=1, n_original_lines=2)
    (d / "critique.json").write_text(critique_output.model_dump_json(), encoding="utf-8")

    perform_output = PerformOutput(
        episode_id=episode_id,
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o",
        fact_check_model="gpt-4o-mini",
        performance=Performance(
            title="t",
            cold_open=[_line("Nova", "Hey, welcome back.")],
            segments=[PerformedSegment(headline="h", source_ids=["a1"], lines=[_line("Max", "So get THIS —")])],
            outro=[_line("Max", "See you next time.")],
        ),
        fact_flags=[],
    )
    return critique_output, perform_output


def test_quality_stage_persists_quality_json(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    _, perform_output = _write_upstream_artefacts(tmp_path, episode.episode_id)
    _patch_episode_dir(monkeypatch, tmp_path)

    fixture_judge = QualityJudge(
        naturalness=JudgeScore(score=4, reason="mostly natural, a couple stiff lines"),
        stance_clarity=JudgeScore(score=3, reason="Max's skepticism is flat"),
        model="gpt-4o-mini",
    )
    monkeypatch.setattr(
        quality_module,
        "_generate_judge",
        lambda client, model, system_prompt, user_prompt: (fixture_judge, _FIXTURE_USAGE),
    )

    output = quality_module.quality_stage(episode, perform_output, client=object())

    quality_path = tmp_path / "episodes" / episode.episode_id / "quality.json"
    assert quality_path.exists()
    reparsed = quality_module.QualityOutput.model_validate_json(quality_path.read_text(encoding="utf-8"))
    assert reparsed.judge.naturalness.score == 4
    assert reparsed.judge.stance_clarity.reason == "Max's skepticism is flat"
    assert output.grounding.source_count == 3
    assert output.grounding.critique_flags == 1
    assert output.usage == [_FIXTURE_USAGE]


def test_quality_stage_calls_judge_with_the_cheap_model_exactly_once(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    _, perform_output = _write_upstream_artefacts(tmp_path, episode.episode_id)
    _patch_episode_dir(monkeypatch, tmp_path)

    calls: list[str] = []

    def fake_generate_judge(client, model, system_prompt, user_prompt):
        calls.append(model)
        judge = QualityJudge(
            naturalness=JudgeScore(score=5, reason="great"),
            stance_clarity=JudgeScore(score=5, reason="great"),
            model=model,
        )
        return judge, _FIXTURE_USAGE

    monkeypatch.setattr(quality_module, "_generate_judge", fake_generate_judge)

    quality_module.quality_stage(episode, perform_output, client=object())

    assert calls == [profile.llm.model]  # the cheap model, not script_model — exactly one call
