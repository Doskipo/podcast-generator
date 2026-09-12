"""Smoke tests for the stitch stage. No network: pydub/ffmpeg run locally
against tiny fixture mp3s (short silent clips) built on the fly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydub import AudioSegment

from podcast.models import Episode, Host, Interest, Listener, PodcastSettings, Profile, Style, TTSLine, TTSOutput
from podcast.stages import stitch as stitch_module

CLIP_MS = 50


def _profile() -> Profile:
    return Profile(
        name="Test",
        interests=[Interest(topic="testing", weight=1.0)],
        feeds=["https://example.com/feed.xml"],
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


def _episode(profile: Profile, episode_id: str = "ep1") -> Episode:
    return Episode(episode_id=episode_id, created_at=datetime.now(timezone.utc), profile=profile)


def _patch_episode_dir(monkeypatch, tmp_path: Path) -> Path:
    base_dir = tmp_path / "episodes" / "ep1"
    base_dir.mkdir(parents=True, exist_ok=True)

    def _episode_dir(episode_id: str) -> Path:
        return base_dir

    monkeypatch.setattr(stitch_module, "episode_dir", _episode_dir)
    return base_dir


def _write_clip(path: Path) -> None:
    AudioSegment.silent(duration=CLIP_MS).export(path, format="mp3")


def test_stitch_stage_concatenates_with_silence(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    base_dir = _patch_episode_dir(monkeypatch, tmp_path)

    segments_dir = base_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    _write_clip(segments_dir / "000_Nova.mp3")
    _write_clip(segments_dir / "001_Max.mp3")

    tts_output = TTSOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        lines=[
            TTSLine(index=0, speaker="Nova", file="segments/000_Nova.mp3", characters=10),
            TTSLine(index=1, speaker="Max", file="segments/001_Max.mp3", characters=10),
        ],
    )

    output = stitch_module.stitch_stage(episode, tts_output)

    assert output.audio_file == "episode.mp3"
    episode_mp3 = base_dir / "episode.mp3"
    assert episode_mp3.exists()

    # two ~50ms clips plus one 400ms silence gap between them
    assert output.duration_ms >= stitch_module.SILENCE_MS + 2 * CLIP_MS - 20  # mp3 encoding rounds durations

    manifest_path = base_dir / "stitch_manifest.json"
    assert manifest_path.exists()
    reparsed = stitch_module.StitchOutput.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert reparsed.duration_ms == output.duration_ms


def test_stitch_stage_single_line_has_no_gap(tmp_path, monkeypatch):
    profile = _profile()
    episode = _episode(profile)
    base_dir = _patch_episode_dir(monkeypatch, tmp_path)

    segments_dir = base_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    _write_clip(segments_dir / "000_Nova.mp3")

    tts_output = TTSOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        lines=[TTSLine(index=0, speaker="Nova", file="segments/000_Nova.mp3", characters=10)],
    )

    output = stitch_module.stitch_stage(episode, tts_output)

    # no silence gap to add with only one line
    assert output.duration_ms < stitch_module.SILENCE_MS + CLIP_MS + 20
