"""Stitch stage: concatenate the synthesized lines into the final episode mp3.

Typed input: Episode and TTSOutput. Typed output: StitchOutput, persisted as
data/episodes/<episode_id>/stitch_manifest.json. The audio artefact itself is
written to data/episodes/<episode_id>/episode.mp3.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydub import AudioSegment

from podcast.models import Episode, StitchOutput, TTSOutput
from podcast.paths import episode_dir

SILENCE_MS = 400


def stitch_stage(episode: Episode, tts_output: TTSOutput) -> StitchOutput:
    base_dir = episode_dir(episode.episode_id)
    silence = AudioSegment.silent(duration=SILENCE_MS)

    combined = AudioSegment.empty()
    for index, line in enumerate(tts_output.lines):
        if index > 0:
            combined += silence
        combined += AudioSegment.from_file(base_dir / line.file, format="mp3")

    audio_file = "episode.mp3"
    combined.export(base_dir / audio_file, format="mp3")

    output = StitchOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        audio_file=audio_file,
        duration_ms=len(combined),
    )

    out_path = base_dir / "stitch_manifest.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
