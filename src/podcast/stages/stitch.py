"""Stitch stage: concatenate the synthesized lines into the final episode mp3.

Typed input: Episode and TTSOutput. Typed output: StitchOutput, persisted as
data/episodes/<episode_id>/stitch_manifest.json. The audio artefact itself is
written to data/episodes/<episode_id>/episode.mp3.

Gap behavior depends on tts_output.synthesis_mode: in "dialogue" mode, each
line's clip already has any natural inter-turn pause baked in (tts_stage
sliced dialogue-mode audio that way), so no extra gap is added; in
"per_line" mode, each line's own pause_ms sets the gap after it (default
DEFAULT_PAUSE_MS if unset) — see docs/decisions.md ("Voice and dynamics
pass").
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydub import AudioSegment

from podcast.models import Episode, StitchOutput, TTSOutput
from podcast.paths import episode_dir

DEFAULT_PAUSE_MS = 200


def stitch_stage(episode: Episode, tts_output: TTSOutput) -> StitchOutput:
    base_dir = episode_dir(episode.episode_id)
    in_dialogue_mode = tts_output.synthesis_mode == "dialogue"

    combined = AudioSegment.empty()
    for index, line in enumerate(tts_output.lines):
        if index > 0 and not in_dialogue_mode:
            gap_ms = tts_output.lines[index - 1].pause_ms
            combined += AudioSegment.silent(duration=gap_ms if gap_ms is not None else DEFAULT_PAUSE_MS)
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
