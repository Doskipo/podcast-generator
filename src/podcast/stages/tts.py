"""TTS stage: synthesize the script's dialogue with ElevenLabs.

Primary path: the text_to_dialogue endpoint, the whole flattened script as
speaker-tagged turns in one (or a few, if chunked for length) request(s) —
this is what makes inter-line prosody/timing sound like an actual
conversation instead of independently-synthesized clips stitched together.
Falls back to per-line synthesis (text_to_speech, one call per line) if the
dialogue endpoint fails for any reason. Both paths use a v3-class model, for
audio-tag (Line.delivery) support.

Typed input: Episode (+ Profile snapshot) and ScriptOutput. Typed output:
TTSOutput, persisted as data/episodes/<episode_id>/tts_manifest.json. Audio
files are written under data/episodes/<episode_id>/segments/, one per line
either way — dialogue-mode audio is split into per-line clips using the
endpoint's voice_segments timestamps. See docs/decisions.md ("Voice and
dynamics pass") for why, and for what dialogue mode can't do (per-host
voice_settings — DialogueInput has no per-turn settings field, so those only
take effect in the per-line fallback).
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from elevenlabs.client import ElevenLabs
from elevenlabs.types.dialogue_input import DialogueInput
from elevenlabs.types.voice_settings import VoiceSettings
from pydub import AudioSegment

from podcast.env import require_env
from podcast.models import Episode, Host, HostVoiceSettings, Line, ScriptOutput, TTSLine, TTSOutput
from podcast.paths import episode_dir
from podcast.stages.script import flatten_lines

logger = logging.getLogger(__name__)

# Conservative cap on characters per text_to_dialogue request. Not an
# ElevenLabs-documented number (their docs don't state one as of this
# writing) — chosen to stay well clear of a length error rather than found
# by probing the real limit. Revisit if a length-related failure surfaces in
# practice; see docs/decisions.md.
DIALOGUE_CHUNK_CHAR_LIMIT = 2500


def _sanitize_speaker(speaker: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", speaker).strip("_") or "speaker"


def _tagged_text(line: Line) -> str:
    """Prefix the line's delivery as a bracketed audio tag ("[laughs] ...")
    — understood by v3-class models, which both synthesis paths use here.
    Skips prefixing if the writer already opened the line with a bracketed
    tag itself (seen in practice: a line with delivery="laughs" whose own
    text already starts "[laughs] ...") — otherwise it'd double up."""
    if not line.delivery:
        return line.text
    if line.text.lstrip().startswith("["):
        return line.text
    return f"[{line.delivery}] {line.text}"


def _chunk_lines(lines: list[Line], char_limit: int) -> list[list[Line]]:
    chunks: list[list[Line]] = []
    current: list[Line] = []
    current_chars = 0
    for line in lines:
        if current and current_chars + len(line.text) > char_limit:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(line)
        current_chars += len(line.text)
    if current:
        chunks.append(current)
    return chunks


def _voice_settings_for(settings: HostVoiceSettings | None) -> VoiceSettings | None:
    if settings is None:
        return None
    return VoiceSettings(stability=settings.stability, similarity_boost=settings.similarity, style=settings.style)


def _dialogue_convert(client: ElevenLabs, inputs: list[DialogueInput], model_id: str):
    """Boundary around the ElevenLabs text_to_dialogue call — the seam tests
    monkeypatch. Returns an AudioWithTimestampsAndVoiceSegmentsResponseModel."""
    return client.text_to_dialogue.convert_with_timestamps(inputs=inputs, model_id=model_id)


def _synthesize(
    client: ElevenLabs, voice_id: str, model_id: str, text: str, voice_settings: VoiceSettings | None = None
) -> bytes:
    """Boundary around the per-line ElevenLabs call — the seam tests monkeypatch."""
    chunks = client.text_to_speech.convert(voice_id, text=text, model_id=model_id, voice_settings=voice_settings)
    return b"".join(chunks)


def _slice_dialogue_audio(response, line_count: int) -> list[bytes]:
    """Decode the dialogue response's audio and slice it into one clip per
    turn using voice_segments (dialogue_input_index -> start/end time). Each
    clip runs from the end of the previous turn (0 for the first) to this
    turn's own end (or the full audio length, for the last turn, to catch
    any trailing tail) — so any natural pause ElevenLabs left between turns
    stays embedded as trailing silence on the earlier clip instead of being
    dropped, and concatenating the clips back-to-back exactly reconstructs
    the original combined audio."""
    audio_bytes = base64.b64decode(response.audio_base_64)
    full = AudioSegment.from_file(BytesIO(audio_bytes), format="mp3")

    segments_by_index = {segment.dialogue_input_index: segment for segment in response.voice_segments}

    clips: list[bytes] = []
    prev_end_ms = 0
    for i in range(line_count):
        segment = segments_by_index.get(i)
        if segment is None:
            raise RuntimeError(f"dialogue response missing a voice segment for turn {i}")
        end_ms = len(full) if i == line_count - 1 else int(segment.end_time_seconds * 1000)
        clip = full[prev_end_ms:end_ms]
        buffer = BytesIO()
        clip.export(buffer, format="mp3")
        clips.append(buffer.getvalue())
        prev_end_ms = end_ms
    return clips


def _synthesize_dialogue(
    client: ElevenLabs,
    profile_hosts: list[Host],
    model_id: str,
    lines: list[Line],
    filenames: list[str],
    segments_dir: Path,
) -> bool:
    """Attempt dialogue-mode synthesis, chunked to DIALOGUE_CHUNK_CHAR_LIMIT.
    Returns True if every chunk succeeded (files written), False if any
    chunk's API call failed — the caller falls back to per-line synthesis
    for the whole episode in that case (chunks already written on disk from
    an earlier, successful attempt are left in place and simply reused)."""
    if not lines:
        return True

    voice_by_speaker = {host.name: host.voice_id for host in profile_hosts}
    chunks = _chunk_lines(lines, DIALOGUE_CHUNK_CHAR_LIMIT)

    offset = 0
    for chunk in chunks:
        chunk_filenames = filenames[offset : offset + len(chunk)]
        if all((segments_dir / fn).exists() for fn in chunk_filenames):
            logger.info("dialogue chunk of %d line(s) already synthesized, skipping", len(chunk))
            offset += len(chunk)
            continue

        dialogue_inputs = [
            DialogueInput(text=_tagged_text(line), voice_id=voice_by_speaker[line.speaker]) for line in chunk
        ]
        try:
            response = _dialogue_convert(client, dialogue_inputs, model_id)
            clips = _slice_dialogue_audio(response, len(chunk))
        except Exception:
            logger.warning(
                "text_to_dialogue failed on a %d-line chunk, falling back to per-line synthesis",
                len(chunk),
                exc_info=True,
            )
            return False

        for filename, clip_bytes in zip(chunk_filenames, clips):
            (segments_dir / filename).write_bytes(clip_bytes)
        offset += len(chunk)

    return True


def _synthesize_per_line(
    client: ElevenLabs,
    profile_hosts: list[Host],
    model_id: str,
    lines: list[Line],
    filenames: list[str],
    segments_dir: Path,
) -> None:
    voice_by_speaker = {host.name: host.voice_id for host in profile_hosts}
    settings_by_speaker = {host.name: host.voice_settings for host in profile_hosts}

    for line, filename in zip(lines, filenames):
        file_path = segments_dir / filename
        if file_path.exists():
            logger.info("segment %s already exists, skipping synthesis", filename)
            continue
        voice_settings = _voice_settings_for(settings_by_speaker.get(line.speaker))
        audio_bytes = _synthesize(
            client, voice_by_speaker[line.speaker], model_id, _tagged_text(line), voice_settings
        )
        file_path.write_bytes(audio_bytes)


def tts_stage(episode: Episode, script_output: ScriptOutput, client: ElevenLabs | None = None) -> TTSOutput:
    if client is None:
        client = ElevenLabs(api_key=require_env("ELEVENLABS_API_KEY"))

    profile = episode.profile
    lines = flatten_lines(script_output.script)
    known_speakers = {host.name for host in profile.podcast.hosts}
    for line in lines:
        if line.speaker not in known_speakers:
            raise ValueError(
                f"no voice configured for speaker {line.speaker!r}; add a matching host to profile.podcast.hosts"
            )

    segments_dir = episode_dir(episode.episode_id) / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    filenames = [f"{i:03d}_{_sanitize_speaker(line.speaker)}.mp3" for i, line in enumerate(lines)]

    dialogue_ok = _synthesize_dialogue(
        client, profile.podcast.hosts, profile.tts.dialogue_model_id, lines, filenames, segments_dir
    )
    if dialogue_ok:
        mode = "dialogue"
    else:
        _synthesize_per_line(client, profile.podcast.hosts, profile.tts.model_id, lines, filenames, segments_dir)
        mode = "per_line"

    entries = [
        TTSLine(
            index=i,
            speaker=line.speaker,
            file=f"segments/{filenames[i]}",
            # length of what's actually sent to ElevenLabs (including any
            # delivery tag prefix) — that's what's billed, not just line.text
            characters=len(_tagged_text(line)),
            pause_ms=line.pause_ms,
        )
        for i, line in enumerate(lines)
    ]

    output = TTSOutput(
        episode_id=episode.episode_id,
        generated_at=datetime.now(timezone.utc),
        lines=entries,
        synthesis_mode=mode,
        total_characters=sum(entry.characters for entry in entries),
    )

    out_path = episode_dir(episode.episode_id) / "tts_manifest.json"
    out_path.write_text(output.model_dump_json(indent=2), encoding="utf-8")
    return output
