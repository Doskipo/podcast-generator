"""Smoke tests for the tts stage. No network: the ElevenLabs calls are
monkeypatched at the tts module's _dialogue_convert and _synthesize seams.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from pydub import AudioSegment

from podcast.models import (
    Episode,
    Host,
    HostVoiceSettings,
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
CLIP_MS = 50


def _host(name: str, voice_id: str, voice_settings: HostVoiceSettings | None = None) -> Host:
    return Host(
        name=name,
        voice_id=voice_id,
        persona=f"{name} is curious and precise.",
        home_turf=[],
        voice_settings=voice_settings,
    )


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
        podcast=PodcastSettings(
            name="Test Podcast",
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
    return ScriptOutput(episode_id=episode_id, generated_at=datetime.now(timezone.utc), model="gpt-5", script=script)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> None:
    def _episode_dir(episode_id: str) -> Path:
        d = tmp_path / "episodes" / episode_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(tts_module, "episode_dir", _episode_dir)


class _FakeVoiceSegment:
    def __init__(self, dialogue_input_index: int, end_time_seconds: float):
        self.dialogue_input_index = dialogue_input_index
        self.end_time_seconds = end_time_seconds


class _FakeDialogueResponse:
    def __init__(self, audio_base_64: str, voice_segments: list):
        self.audio_base_64 = audio_base_64
        self.voice_segments = voice_segments


def _combined_clip_response(line_count: int) -> _FakeDialogueResponse:
    """A fake dialogue response: line_count equal-length silent clips
    concatenated, with voice_segments marking each one's end time."""
    combined = AudioSegment.silent(duration=0)
    segments = []
    for i in range(line_count):
        combined += AudioSegment.silent(duration=CLIP_MS)
        segments.append(_FakeVoiceSegment(dialogue_input_index=i, end_time_seconds=len(combined) / 1000))
    buffer = BytesIO()
    combined.export(buffer, format="mp3")
    audio_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    return _FakeDialogueResponse(audio_b64, segments)


def test_tts_stage_dialogue_mode_splits_audio_per_line(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    captured_inputs = []

    def fake_dialogue_convert(client, inputs, model_id):
        captured_inputs.append((inputs, model_id))
        return _combined_clip_response(len(inputs))

    monkeypatch.setattr(tts_module, "_dialogue_convert", fake_dialogue_convert)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = tts_module.tts_stage(episode, script_output, client=object())

    assert output.synthesis_mode == "dialogue"
    assert len(output.lines) == 4
    assert output.total_characters == sum(len(l) for l in ["Welcome back!", "Here's the story.", "Tell me more.", "See you next time."])

    # dialogue call got one model_id and per-turn voice ids matching the hosts
    inputs, model_id = captured_inputs[0]
    assert model_id == "eleven_v3"
    assert [i.voice_id for i in inputs] == ["voice-nova", "voice-nova", "voice-max", "voice-max"]

    # each line got its own sliced-out file on disk
    for line in output.lines:
        file_path = tmp_path / "episodes" / episode.episode_id / line.file
        assert file_path.exists()
        assert file_path.stat().st_size > 0

    manifest_path = tmp_path / "episodes" / episode.episode_id / "tts_manifest.json"
    reparsed = tts_module.TTSOutput.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert reparsed.synthesis_mode == "dialogue"


def test_tts_stage_falls_back_to_per_line_when_dialogue_fails(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    def failing_dialogue_convert(client, inputs, model_id):
        raise RuntimeError("dialogue endpoint unavailable")

    calls: list[tuple[str, str]] = []

    def fake_synthesize(client, voice_id, model_id, text, voice_settings=None):
        calls.append((voice_id, text))
        return FAKE_AUDIO

    monkeypatch.setattr(tts_module, "_dialogue_convert", failing_dialogue_convert)
    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)
    _patch_episode_dir(monkeypatch, tmp_path)

    output = tts_module.tts_stage(episode, script_output, client=object())

    assert output.synthesis_mode == "per_line"
    assert len(calls) == 4
    assert [line.speaker for line in output.lines] == ["Nova", "Nova", "Max", "Max"]
    assert output.lines[0].file.startswith("segments/000_Nova_") and output.lines[0].file.endswith(".mp3")
    assert output.lines[0].characters == len("Welcome back!")
    assert calls[0][0] == "voice-nova"

    for line in output.lines:
        assert (tmp_path / "episodes" / episode.episode_id / line.file).read_bytes() == FAKE_AUDIO


def test_tts_stage_per_line_fallback_applies_host_voice_settings(tmp_path, monkeypatch):
    profile = _profile()
    profile.podcast.hosts[0] = _host(
        "Nova", "voice-nova", HostVoiceSettings(stability=0.3, style=0.6, similarity=0.75)
    )
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    captured_settings = []

    def failing_dialogue_convert(client, inputs, model_id):
        raise RuntimeError("dialogue endpoint unavailable")

    def fake_synthesize(client, voice_id, model_id, text, voice_settings=None):
        captured_settings.append((voice_id, voice_settings))
        return FAKE_AUDIO

    monkeypatch.setattr(tts_module, "_dialogue_convert", failing_dialogue_convert)
    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)
    _patch_episode_dir(monkeypatch, tmp_path)

    tts_module.tts_stage(episode, script_output, client=object())

    nova_calls = [s for voice_id, s in captured_settings if voice_id == "voice-nova"]
    max_calls = [s for voice_id, s in captured_settings if voice_id == "voice-max"]
    assert all(s is not None and s.stability == 0.3 for s in nova_calls)
    assert all(s is None for s in max_calls)  # Max has no voice_settings configured


def test_tts_stage_skips_existing_files_in_per_line_mode(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    _patch_episode_dir(monkeypatch, tmp_path)

    # pre-create the first line's file under its real content-addressed name,
    # as if a previous run already synthesized this exact line
    segments_dir = tmp_path / "episodes" / episode.episode_id / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    first_line = script_output.script.cold_open[0]
    expected_key = tts_module._content_key(tts_module._tagged_text(first_line))
    existing_file = segments_dir / f"000_Nova_{expected_key}.mp3"
    existing_file.write_bytes(b"EXISTING-AUDIO")

    calls: list[str] = []

    def failing_dialogue_convert(client, inputs, model_id):
        raise RuntimeError("dialogue endpoint unavailable")

    def fake_synthesize(client, voice_id, model_id, text, voice_settings=None):
        calls.append(text)
        return FAKE_AUDIO

    monkeypatch.setattr(tts_module, "_dialogue_convert", failing_dialogue_convert)
    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)

    output = tts_module.tts_stage(episode, script_output, client=object())

    # the pre-existing file was not touched, and synthesis wasn't called for it
    assert existing_file.read_bytes() == b"EXISTING-AUDIO"
    assert len(calls) == 3

    assert output.lines[0].file == f"segments/000_Nova_{expected_key}.mp3"
    assert output.lines[0].characters == len("Welcome back!")


def test_tts_stage_does_not_reuse_stale_file_when_line_text_changes(tmp_path, monkeypatch):
    """The exact bug this fixes: a leftover file from a *different* script's
    line at the same index+speaker must never be mistaken for the current
    line — the content hash in the filename makes that a different filename
    entirely, so it's simply not found and gets resynthesized."""
    profile = _profile()
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    _patch_episode_dir(monkeypatch, tmp_path)

    segments_dir = tmp_path / "episodes" / episode.episode_id / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    # a stale file at the same index+speaker slot, but from different text
    stale_file = segments_dir / "000_Nova_deadbeef.mp3"
    stale_file.write_bytes(b"STALE-AUDIO-FROM-A-DIFFERENT-SCRIPT")

    calls: list[str] = []

    def failing_dialogue_convert(client, inputs, model_id):
        raise RuntimeError("dialogue endpoint unavailable")

    def fake_synthesize(client, voice_id, model_id, text, voice_settings=None):
        calls.append(text)
        return FAKE_AUDIO

    monkeypatch.setattr(tts_module, "_dialogue_convert", failing_dialogue_convert)
    monkeypatch.setattr(tts_module, "_synthesize", fake_synthesize)

    output = tts_module.tts_stage(episode, script_output, client=object())

    # the stale file is untouched and unused; line 0 was freshly synthesized
    assert stale_file.read_bytes() == b"STALE-AUDIO-FROM-A-DIFFERENT-SCRIPT"
    assert "Welcome back!" in calls
    assert output.lines[0].file != "segments/000_Nova_deadbeef.mp3"
    fresh_path = tmp_path / "episodes" / episode.episode_id / output.lines[0].file
    assert fresh_path.read_bytes() == FAKE_AUDIO


def test_tts_stage_missing_host_voice_raises(tmp_path, monkeypatch):
    profile = _profile()
    profile.podcast.hosts = [_host("Nova", "voice-nova")]  # Max has no matching host
    episode = _episode(profile)
    script_output = _script_output(episode.episode_id)

    _patch_episode_dir(monkeypatch, tmp_path)

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


def test_tagged_text_prefixes_delivery():
    line_with_delivery = Line(speaker="Nova", text="No way.", delivery="laughs")
    line_plain = Line(speaker="Nova", text="No way.")
    assert tts_module._tagged_text(line_with_delivery) == "[laughs] No way."
    assert tts_module._tagged_text(line_plain) == "No way."


def test_tagged_text_avoids_double_tagging(tmp_path, monkeypatch):
    # seen in a real run: the writer sometimes opens a line with its own
    # bracketed tag AND sets delivery to the same thing — must not stack.
    line = Line(speaker="Nova", text="[laughs] There it is.", delivery="laughs")
    assert tts_module._tagged_text(line) == "[laughs] There it is."


def test_chunk_lines_respects_char_limit():
    lines = [Line(speaker="Nova", text="x" * 10) for _ in range(5)]
    chunks = tts_module._chunk_lines(lines, char_limit=25)
    # 10 chars each -> 2 lines (20 chars) fit, a 3rd would exceed 25
    assert [len(c) for c in chunks] == [2, 2, 1]
