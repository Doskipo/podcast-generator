"""Fixed catalog of selectable ElevenLabs voice ids, for the settings UI's
host voice picker. Not fetched live from ElevenLabs: there's no
`client.voices.list()`-style call exercised anywhere in this codebase, and
docs/decisions.md already found this API key lacks some read permissions
(`models_read`, checked during the "Voice and dynamics pass") — rather than
add an unverified live call, this is a small curated list, seeded with the
two voice ids already used in profiles/eudald.yaml. Edit this list directly
to add more."""

from __future__ import annotations

from pydantic import BaseModel


class VoiceOption(BaseModel):
    voice_id: str
    label: str


VOICE_CATALOG: list[VoiceOption] = [
    VoiceOption(voice_id="EST9Ui6982FZPSi7gCHi", label="Alice — expressive, warm"),
    VoiceOption(voice_id="TWutjvRaJqAX89preB4e", label="Bob — stable, deadpan"),
    VoiceOption(voice_id="21m00Tcm4TlvDq8ikWAM", label="Rachel — calm, narration"),
    VoiceOption(voice_id="AZnzlk1XvdvUeBnXmlld", label="Domi — strong, confident"),
    VoiceOption(voice_id="EXAVITQu4vr4xnSDxMaL", label="Bella — soft, friendly"),
    VoiceOption(voice_id="ErXwobaYiN019PkySvjV", label="Antoni — well-rounded, easygoing"),
    VoiceOption(voice_id="MF3mGyEYCl7XYWbV9V6O", label="Elli — youthful, energetic"),
    VoiceOption(voice_id="TxGEqnHWrfWFTfGW9XjX", label="Josh — deep, grounded"),
]
