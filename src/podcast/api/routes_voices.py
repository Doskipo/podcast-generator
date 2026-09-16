"""GET /api/voices — the fixed selectable voice catalog for the settings UI."""

from __future__ import annotations

from fastapi import APIRouter

from podcast.api.voices import VOICE_CATALOG, VoiceOption

router = APIRouter()


@router.get("/voices", response_model=list[VoiceOption])
def list_voices() -> list[VoiceOption]:
    return VOICE_CATALOG
