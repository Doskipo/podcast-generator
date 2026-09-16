"""Tests for the host presets loader (presets/hosts/*.yaml -> list[Host]),
used by GET /api/hosts/presets and the settings UI's preset picker. See
docs/decisions.md ("Persona rigidity")."""

from __future__ import annotations

from pathlib import Path

from podcast.host_presets import load_host_presets
from podcast.models import Host


def _write_preset(dir_path: Path, filename: str, name: str, voice_id: str = "voice-x") -> None:
    dir_path.mkdir(parents=True, exist_ok=True)
    (dir_path / filename).write_text(
        f'name: {name}\nvoice_id: {voice_id}\npersona: |\n  Background: test.\nhome_turf: []\n',
        encoding="utf-8",
    )


def test_load_host_presets_parses_each_file_into_a_host(tmp_path):
    presets_dir = tmp_path / "hosts"
    _write_preset(presets_dir, "a.yaml", "Alice", voice_id="voice-alice")

    presets = load_host_presets(presets_dir)

    assert len(presets) == 1
    assert isinstance(presets[0], Host)
    assert presets[0].name == "Alice"
    assert presets[0].voice_id == "voice-alice"


def test_load_host_presets_sorts_by_filename(tmp_path):
    presets_dir = tmp_path / "hosts"
    _write_preset(presets_dir, "b.yaml", "Second")
    _write_preset(presets_dir, "a.yaml", "First")

    presets = load_host_presets(presets_dir)

    assert [h.name for h in presets] == ["First", "Second"]


def test_load_host_presets_returns_empty_list_when_directory_missing(tmp_path):
    assert load_host_presets(tmp_path / "does-not-exist") == []


def test_the_shipped_presets_directory_has_four_distinct_hosts():
    """Integration check against the real presets/hosts/ shipped with the
    repo (Part 4 of "Persona rigidity": the two current hosts plus two new
    ones, distinct in register) — catches a preset file that doesn't parse
    or a naming collision, not just the loader's own logic."""
    presets = load_host_presets()

    names = [h.name for h in presets]
    assert names == sorted(names)  # stable, filename-sorted order
    assert len(set(names)) == len(names) == 4
    assert {"Alice", "Bob"} <= set(names)  # the two current hosts, carried over verbatim
    for host in presets:
        assert host.voice_id
        assert host.persona.strip()
