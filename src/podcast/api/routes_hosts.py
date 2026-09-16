"""GET /api/hosts/presets — ready-made Host presets for the settings UI's
"Choose a preset" picker. See docs/decisions.md ("Persona rigidity")."""

from __future__ import annotations

from fastapi import APIRouter

from podcast.host_presets import load_host_presets
from podcast.models import Host

router = APIRouter()


@router.get("/hosts/presets", response_model=list[Host])
def list_host_presets() -> list[Host]:
    return load_host_presets()
