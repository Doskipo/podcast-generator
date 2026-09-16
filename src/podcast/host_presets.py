"""Ready-made Host presets for the settings UI's "Choose a preset" picker —
the two current hosts (profiles/eudald.yaml's Alice and Bob, copied
verbatim, Catchphrase line included) plus two new ones written in the same
format but a distinct register (Priya: precise, hedging, analyst; Theo:
fast, informal, internet-literate). A preset only fills a host row's
fields; the row stays fully editable afterward. See docs/decisions.md
("Persona rigidity").

Loaded from presets/hosts/*.yaml, one Host per file (same shape as a
profile's podcast.hosts[] entry), sorted by filename for a stable order in
the UI.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from podcast.models import Host

PRESETS_DIR = Path("presets") / "hosts"


def load_host_presets(presets_dir: Path | None = None) -> list[Host]:
    directory = presets_dir if presets_dir is not None else PRESETS_DIR
    if not directory.is_dir():
        return []
    return [
        Host.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    ]
