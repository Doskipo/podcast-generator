"""Smoke tests for generate.py's CLI orchestration. No network: every stage
function generate.py calls is monkeypatched to a fake that just records that
it ran, so these tests exercise --until's stop-after-stage logic in isolation
from the stages themselves (which have their own tests).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from podcast.models import (
    Angle,
    Critique,
    CritiqueOutput,
    FetchOutput,
    Host,
    Interest,
    Listener,
    Outline,
    OutlineOutput,
    OutlineStory,
    PodcastSettings,
    Profile,
    RankOutput,
    Script,
    ScriptOutput,
    StitchOutput,
    Style,
    TTSOutput,
)
from podcast import generate as generate_module


def _write_profile(path: Path) -> None:
    profile = Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0, feeds=["https://example.com/feed.xml"])],
        podcast=PodcastSettings(
            duration_minutes=8,
            listener=Listener(name="Eudald"),
            hosts=[
                Host(name="Nova", voice_id="voice-nova", persona="Nova is curious and precise."),
                Host(name="Max", voice_id="voice-max", persona="Max is curious and precise."),
            ],
            style=Style(humour=2, depth=2, tangents=True, banter=True),
            tone="curious",
        ),
    )
    profile.to_yaml(path)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(generate_module, "episode_dir", _episode_dir)


def _patch_stages(monkeypatch, calls: list[str]) -> None:
    def fake_fetch_stage(profile, episode_id):
        calls.append("fetch")
        # feeds_count deliberately != len(profile.feeds) (0 here) — this is
        # the exact case that used to print "from 0 feeds" when feeds were
        # actually hit via interests, not top-level profile.feeds.
        return FetchOutput(episode_id=episode_id, fetched_at=datetime.now(timezone.utc), feeds_count=13, articles=[])

    def fake_rank_stage(episode, fetch_output, client=None):
        calls.append("rank")
        return RankOutput(
            episode_id=episode.episode_id,
            ranked_at=datetime.now(timezone.utc),
            model="test-model",
            total_budget=1,
            backfilled=0,
            scored=[],
            selected=[],
        )

    def fake_outline_stage(episode, rank_output, client=None):
        calls.append("outline")
        outline = Outline(
            title="t",
            stories=[
                OutlineStory(
                    headline="h",
                    source_ids=[],
                    angle=Angle(why_it_matters="w", tension_or_surprise="t", host_take="h", tangent="a"),
                )
            ],
        )
        return OutlineOutput(
            episode_id=episode.episode_id, generated_at=datetime.now(timezone.utc), model="test-model", outline=outline
        )

    def fake_script_stage(episode, outline_output, articles, client=None):
        calls.append("script")
        return ScriptOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            model="test-model",
            script=Script(title="t", cold_open=[], segments=[], outro=[]),
        )

    def fake_critique_stage(episode, script_output, articles, outline_output, client=None):
        calls.append("critique")
        return CritiqueOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            model="test-model",
            critique=Critique(flags=[]),
            original_script=script_output.script,
            revised_script=script_output.script,
            total_words=0,
            over_budget_segments=[],
        )

    def fake_tts_stage(episode, script_output, client=None):
        calls.append("tts")
        return TTSOutput(episode_id=episode.episode_id, generated_at=datetime.now(timezone.utc), lines=[])

    def fake_stitch_stage(episode, tts_output):
        calls.append("stitch")
        return StitchOutput(
            episode_id=episode.episode_id,
            generated_at=datetime.now(timezone.utc),
            audio_file="episode.mp3",
            duration_ms=0,
        )

    monkeypatch.setattr(generate_module, "fetch_stage", fake_fetch_stage)
    monkeypatch.setattr(generate_module, "rank_stage", fake_rank_stage)
    monkeypatch.setattr(generate_module, "outline_stage", fake_outline_stage)
    monkeypatch.setattr(generate_module, "script_stage", fake_script_stage)
    monkeypatch.setattr(generate_module, "critique_stage", fake_critique_stage)
    monkeypatch.setattr(generate_module, "tts_stage", fake_tts_stage)
    monkeypatch.setattr(generate_module, "stitch_stage", fake_stitch_stage)
    monkeypatch.setattr(generate_module, "ensure_interest_queries", lambda profile, path: False)


def test_run_until_rank_stops_before_outline(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep1", until="rank")

    # fetch and rank ran; outline/script/critique/tts/stitch never got the chance to
    assert calls == ["fetch", "rank"]


def test_run_until_fetch_stops_before_rank(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep2", until="fetch")

    assert calls == ["fetch"]


def test_run_until_outline_stops_before_script(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep3", until="outline")

    assert calls == ["fetch", "rank", "outline"]


def test_run_until_script_stops_before_critique(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep4", until="script")

    assert calls == ["fetch", "rank", "outline", "script"]


def test_run_until_critique_stops_before_tts(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep5", until="critique")

    assert calls == ["fetch", "rank", "outline", "script", "critique"]


def test_run_prints_actual_feeds_count_not_profile_feeds_length(tmp_path, monkeypatch, capsys):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)  # this profile has no top-level feeds: len(profile.feeds) == 0
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep7", until="fetch")

    out = capsys.readouterr().out
    assert "from 13 feeds" in out
    assert "from 0 feeds" not in out


def test_run_without_until_runs_the_full_pipeline(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)
    calls: list[str] = []
    _patch_stages(monkeypatch, calls)
    _patch_episode_dir(monkeypatch, tmp_path)

    generate_module.run(str(profile_path), episode_id="ep6")

    assert calls == ["fetch", "rank", "outline", "script", "critique", "tts", "stitch"]
