"""Smoke tests for the critique stage. No network: the OpenAI call is
monkeypatched at the critique module's _generate_critique seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Angle,
    Article,
    Critique,
    CritiqueFlag,
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
    ScriptOutput,
    Segment,
    Style,
)
from podcast.stages import critique as critique_module


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


def _script(source_id: str) -> Script:
    return Script(
        title="Test Episode",
        cold_open=[Line(speaker="Nova", text="Welcome back to the show, everyone.")],
        segments=[
            Segment(
                headline="Something happened",
                source_ids=[source_id],
                lines=[
                    Line(speaker="Nova", text="Here is the story, which is very robotic sounding."),
                    Line(speaker="Max", text="Tell me more."),
                ],
            )
        ],
        outro=[Line(speaker="Max", text="See you next time.")],
    )


def _script_output(episode_id: str, source_id: str) -> ScriptOutput:
    return ScriptOutput(
        episode_id=episode_id, generated_at=datetime.now(timezone.utc), model="gpt-4o", script=_script(source_id)
    )


def _angle() -> Angle:
    return Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent=None)


def _stances() -> list[HostStance]:
    return [
        HostStance(host="Nova", attitude="excited", why="her home turf"),
        HostStance(host="Max", attitude="skeptical", why="wants the numbers"),
    ]


def _outline_output(episode_id: str, source_id: str, word_budget: int = 100) -> OutlineOutput:
    outline = Outline(
        title="Test Episode",
        stories=[
            OutlineStory(
                headline="Something happened",
                source_ids=[source_id],
                angle=_angle(),
                word_budget=word_budget,
                stances=_stances(),
            )
        ],
    )
    return OutlineOutput(episode_id=episode_id, generated_at=datetime.now(timezone.utc), model="gpt-4o-mini", outline=outline)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(critique_module, "episode_dir", _episode_dir)


def test_critique_stage_rewrites_only_flagged_lines(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    # line_index 1 is the flattened index of "Here is the story..." (after the cold_open line)
    fixture_critique = Critique(
        flags=[CritiqueFlag(line_index=1, issue="robotic", rewritten_lines=[Line(speaker="Nova", text="So get this —")])]
    )
    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: fixture_critique,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    # only the flagged line changed
    assert output.revised_script.segments[0].lines[0].text == "So get this —"
    assert output.revised_script.segments[0].lines[1].text == "Tell me more."
    assert output.revised_script.cold_open[0].text == "Welcome back to the show, everyone."

    # the original is preserved unchanged alongside the revision
    assert output.original_script.segments[0].lines[0].text == "Here is the story, which is very robotic sounding."

    # structure/grounding untouched
    assert output.revised_script.segments[0].source_ids == ["abcd1234"]

    # total_words counts the revised script
    assert output.total_words == sum(
        len(line.text.split()) for line in critique_module.flatten_lines(output.revised_script)
    )

    critique_path = tmp_path / "episodes" / episode.episode_id / "critique.json"
    assert critique_path.exists()
    reparsed = critique_module.CritiqueOutput.model_validate_json(critique_path.read_text(encoding="utf-8"))
    assert reparsed.revised_script.segments[0].lines[0].text == "So get this —"


def test_critique_stage_splits_a_flagged_line_into_multiple_lines(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    fixture_critique = Critique(
        flags=[
            CritiqueFlag(
                line_index=1,
                issue="too_long",
                rewritten_lines=[
                    Line(speaker="Nova", text="So get this."),
                    Line(speaker="Max", text="What happened?"),
                    Line(speaker="Nova", text="It's a big deal."),
                ],
            )
        ]
    )
    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: fixture_critique,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    # one line became three; the lines after it shifted but kept their content
    segment_lines = output.revised_script.segments[0].lines
    assert [line.text for line in segment_lines] == [
        "So get this.",
        "What happened?",
        "It's a big deal.",
        "Tell me more.",
    ]
    # cold_open/outro, which weren't flagged, are untouched
    assert output.revised_script.cold_open[0].text == "Welcome back to the show, everyone."
    assert output.revised_script.outro[0].text == "See you next time."


def test_critique_stage_ignores_out_of_range_line_index(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    fixture_critique = Critique(
        flags=[CritiqueFlag(line_index=999, issue="robotic", rewritten_lines=[Line(speaker="Nova", text="unused")])]
    )
    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: fixture_critique,
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    # doesn't raise — out-of-range flags are logged and skipped
    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())
    assert output.revised_script == output.original_script


def test_critique_stage_flags_segment_over_word_budget(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    # the segment has 2 lines: "Here is the story, which is very robotic sounding." (9
    # words) + "Tell me more." (3 words) = 12 words
    outline_output = _outline_output(episode.episode_id, "abcd1234", word_budget=5)  # way under 12

    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: Critique(flags=[]),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    assert len(output.over_budget_segments) == 1
    flag = output.over_budget_segments[0]
    assert flag.segment_index == 0
    assert flag.word_budget == 5
    assert flag.actual_words == 12
    assert flag.over_by_percent > 20.0


def test_critique_stage_does_not_flag_segment_within_budget(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    outline_output = _outline_output(episode.episode_id, "abcd1234", word_budget=1000)  # plenty of headroom

    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: Critique(flags=[]),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    assert output.over_budget_segments == []


def test_over_length_line_indices_excludes_recurring_bit_segment():
    long_line = "word " * 50  # 50 words, over MAX_LINE_WORDS (45)
    script = Script(
        title="t",
        cold_open=[],
        segments=[
            Segment(headline="bit segment", source_ids=[], lines=[Line(speaker="Nova", text=long_line)]),
            Segment(headline="normal segment", source_ids=[], lines=[Line(speaker="Max", text=long_line)]),
        ],
        outro=[],
    )
    # segment 0 carries the bit, segment 1 doesn't
    indices = critique_module._over_length_line_indices(script, bit_segment_flags=[True, False])
    assert indices == [1]  # only the non-bit segment's long line is flagged


def test_critique_stage_uses_stronger_model(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: Critique(flags=[]),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    assert output.model == profile.llm.script_model
    assert output.model != profile.llm.model


def test_critique_stage_requires_openai_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        critique_module.critique_stage(episode, script_output, articles, outline_output)


def test_repeated_correct_line_indices_flags_only_repeats():
    script = Script(
        title="t",
        cold_open=[Line(speaker="Nova", text="Correct.")],  # first use: allowed
        segments=[
            Segment(
                headline="h",
                source_ids=[],
                lines=[
                    Line(speaker="Max", text="Correct."),  # 2nd use: flagged
                    Line(speaker="Nova", text="Sure, that tracks."),
                ],
            )
        ],
        outro=[Line(speaker="Max", text="Correct.")],  # 3rd use: flagged
    )
    indices = critique_module._repeated_correct_line_indices(script)
    assert indices == [1, 3]  # cold_open(0)=allowed, segment line(1)=flagged, line(2)=unrelated, outro(3)=flagged


def test_critique_stage_flags_repeated_correct(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    script_output.script.cold_open = [Line(speaker="Nova", text="Correct.")]
    script_output.script.segments[0].lines[1] = Line(speaker="Max", text="Correct.")
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    captured_prompt = {}

    def fake_generate_critique(client, model, system_prompt, user_prompt):
        captured_prompt["system"] = system_prompt
        return Critique(flags=[])

    monkeypatch.setattr(critique_module, "_generate_critique", fake_generate_critique)
    _patch_episode_dir(monkeypatch, tmp_path)

    critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    assert "repeated_correct" in captured_prompt["system"]


def test_build_prompts_includes_written_not_spoken_criterion(tmp_path, monkeypatch):
    profile = _profile()
    script = _script("abcd1234")
    outline = _outline_output("ep1", "abcd1234").outline

    system_prompt, _user_prompt = critique_module._build_prompts(profile, script, outline)

    assert "written_not_spoken" in system_prompt


def test_host_brevity_flags_detects_terse_host():
    script = Script(
        title="t",
        cold_open=[],
        segments=[
            Segment(
                headline="h",
                source_ids=[],
                lines=[
                    Line(speaker="Max", text="Sure."),
                    Line(speaker="Max", text="Right."),
                    Line(speaker="Max", text="Yeah."),
                    Line(speaker="Max", text="This one line from Max is long enough to not count as short at all."),
                    Line(speaker="Nova", text="I think this deserves a much longer, more expansive kind of line."),
                ],
            )
        ],
        outro=[],
    )
    flags = critique_module._host_brevity_flags(script)
    assert len(flags) == 1
    assert flags[0].host == "Max"
    assert flags[0].line_count == 4
    assert flags[0].short_line_fraction == 0.75  # 3 of 4 lines are <= 8 words


def test_host_brevity_flags_ignores_host_within_threshold():
    script = Script(
        title="t",
        cold_open=[],
        segments=[
            Segment(
                headline="h",
                source_ids=[],
                lines=[
                    Line(speaker="Max", text="Sure."),
                    Line(speaker="Max", text="This is a longer line that pushes the average up nicely."),
                    Line(speaker="Max", text="And here's another longer line to keep the ratio healthy."),
                ],
            )
        ],
        outro=[],
    )
    flags = critique_module._host_brevity_flags(script)
    assert flags == []


def test_critique_stage_reports_terse_hosts(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    articles = [_article("abcd1234")]
    script_output = _script_output(episode.episode_id, "abcd1234")
    # make every one of Max's lines short
    script_output.script.segments[0].lines[1] = Line(speaker="Max", text="Sure.")
    script_output.script.outro = [Line(speaker="Max", text="Bye.")]
    outline_output = _outline_output(episode.episode_id, "abcd1234")

    monkeypatch.setattr(
        critique_module,
        "_generate_critique",
        lambda client, model, system_prompt, user_prompt: Critique(flags=[]),
    )
    _patch_episode_dir(monkeypatch, tmp_path)

    output = critique_module.critique_stage(episode, script_output, articles, outline_output, client=object())

    terse_by_host = {f.host: f for f in output.terse_hosts}
    assert "Max" in terse_by_host
    assert "Nova" not in terse_by_host
