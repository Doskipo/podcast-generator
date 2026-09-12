"""TTS stage: synthesize each script line with ElevenLabs, one file per line.

Typed input: Episode (+ Profile snapshot) and ScriptOutput. Typed output:
TTSOutput, persisted as data/episodes/<episode_id>/tts_manifest.json. Audio
files are written under data/episodes/<episode_id>/segments/.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from elevenlabs.client import ElevenLabs

from podcast.env import require_env
from podcast.models import Episode, ScriptOutput, TTSLine, TTSOutput
from podcast.paths import episode_dir
from podcast.stages.script import flatten_lines

logger = logging.getLogger(__name__)


def _sanitize_speaker(speaker: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", speaker).strip("_") or "speaker"


def _synthesize(client: ElevenLabs, voice_id: str, model_id: str, text: str) -> bytes:
    """Boundary around the ElevenLabs call — the seam tests monkeypatch."""
    chunks = client.text_to_speech.convert(voice_id, text=text, model_id=model_id)
    return b"".join(chunks)


def tts_stage(episode: Episode, script_output: ScriptOutput, client: ElevenLabs | None = None) -> TTSOutput:
    if client is None:
        client = ElevenLabs(api_key=require_env("ELEVENLABS_API_KEY"))

    profile = episode.profile
    lines = flatten_lines(script_output.script)
    voice_by_speaker = {host.name: host.voice_id for host in profile.podcast.hosts}

    segments_dir = episode_dir(episode.episode_id) / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    entries: list[TTSLine] = []
    for index, line in enumerate(lines):
        filename = f"{index:03d}_{_sanitize_speaker(line.speaker)}.mp3"
        file_path = segments_dir / filename

        if file_path.exists():
            logger.info("segment %s already exists, skipping synthesis", filename)
        else:
            voice_id = voice_by_speaker.get(line.speaker)
            if voice_id is None:
                raise ValueError(
                    f"no voice configured for speaker {line.speaker!r}; "
                    "add a matching host to profile.podcast.hosts"
                )
            audio_bytes = _synthesize(client, voice_id, profile.tts.model_id, line.text)
            file_path.write_bytes(audio_bytes)

        entries.append(
            TTSLine(index=index, speaker=line.speaker, file=f"segments/{filename}", characters=len(line.text))
        )

    output = TTSOutput(episode_id=episode.episode_id, generated_at=datetime.now(timezone.utc), lines=entries)

    out_path = episode_dir(episode.episode_id) / "tts_manifest.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
