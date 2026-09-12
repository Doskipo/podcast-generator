"""Smoke tests for the tts stage. No network: the ElevenLabs call is
monkeypatched at the tts module's _synthesize seam.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from podcast.models import (
    Episode,
    Host,
    Interest,
    Line,
    Listener,
    PodcastSettings,
    Profile,
    Script,
    ScriptOutput,
    Segment,
    Style,
)
from podcast.stages import tts as tts_module

FAKE_AUDIO = b"FAKE-MP3-BYTES"


def _host(name: str, voice_id: str) -> Host:
    return Host(name=name, voice_id=voice_id, persona=f"{name} is curious and precise.", home_turf=[])


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
        podcast=PodcastSettings(
            duration_minutes=8,
            listener=Listener(name="Eudald"),
            hosts=[_host("Nova", "voice-nova"), _host("Max", "voice-max")],
            style=Style(humour=2, depth=2, tangents=True, banter=True),
            tone="curious",
        ),
    )


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _script_output(episode_id: str) -> ScriptOutput:
    script = Script(
        title="Test Episode",
        cold_open=[Line(speaker="Nova", text="Welcome back!")],
        segments=[
            Segment(
                headline="Something happened",
                source_ids=[],
                lines=[
                    Line(speaker="Nova", text="Here's the story."),
                    Line(speaker="Max", text="Tell me more."),
                ],
            )
        ],
        outro=[Line(speaker="Max", text="See you next time.")],
    )
    return ScriptOutput(episode_id=episode_id, generated_at=datetime.now(timezone.utc), model="gpt-4o", script=script)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(tts_module, "episode_dir", _episode_dir)


def test_tts_stage_synthesizes_and_writes_manifest(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    calls: list[tuple[str, str]] = []

    def fake_synthesize(client, voice_id, model_id, text):
        calls.append((voice_id, text))
        return FAKE_AUDIO

    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = tts_module.tts_stage(episode, script_output, client=object())

    assert len(output.lines) == 4
    assert len(calls) == 4
    assert [line.speaker for line in output.lines] == ["Nova", "Nova", "Max", "Max"]
    assert output.lines[0].file == "segments/000_Nova.mp3"
    assert output.lines[0].characters == len("Welcome back!")

    # voice ids came from profile.podcast.hosts, matched by speaker name
    assert calls[0][0] == "voice-nova"
    assert calls[2][0] == "voice-max"

    segments_dir = tmp_path / "episodes" / episode.episode_id / "segments"
    for line in output.lines:
        assert (tmp_path / "episodes" / episode.episode_id / line.file).read_bytes() == FAKE_AUDIO
    assert segments_dir.exists()

    manifest_path = tmp_path / "episodes" / episode.episode_id / "tts_manifest.json"
    assert manifest_path.exists()
    reparsed = tts_module.TTSOutput.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert len(reparsed.lines) == 4


def test_tts_stage_skips_existing_files(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    _patch_episode_dir(monkeypatch, tmp_path)

    # pre-create the first line's file, as if a previous run already synthesized it
    segments_dir = tmp_path / "episodes" / episode.episode_id / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    existing_file = segments_dir / "000_Nova.mp3"
    existing_file.write_bytes(b"EXISTING-AUDIO")

    calls: list[str] = []

    def fake_synthesize(client, voice_id, model_id, text):
        calls.append(text)
        return FAKE_AUDIO

    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)

    output = tts_module.tts_stage(episode, script_output, client=object())

    # the pre-existing file was not touched, and synthesis wasn't called for it
    assert existing_file.read_bytes() == b"EXISTING-AUDIO"
    assert len(calls) == 3

    # but it's still recorded in the manifest, with the correct character count
    assert output.lines[0].file == "segments/000_Nova.mp3"
    assert output.lines[0].characters == len("Welcome back!")


def test_tts_stage_missing_host_voice_raises(tmp_path, monkeypatch):
    profile = _profile()
    profile.podcast.hosts = [_host("Nova", "voice-nova")]  # Max has no matching host
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    monkeypatch.setattr(tts_module, "_synthesize", lambda client, voice_id, model_id, text: FAKE_AUDIO)
    _patch_episode_dir(monkeypatch, tmp_path)

    # Nova's lines (voice known) synthesize fine; Max's line has no matching
    # host and raises before any further synthesis is attempted
    with pytest.raises(ValueError, match="Max"):
        tts_module.tts_stage(episode, script_output, client=object())


def test_tts_stage_requires_elevenlabs_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    _patch_episode_dir(monkeypatch, tmp_path)

    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        tts_module.tts_stage(episode, script_output)
