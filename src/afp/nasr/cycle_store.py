"""Tracks the last-processed NASR cycle in a small local JSON file, so the
app can compare it against the currently published cycle and surface
"update available" without re-downloading on every launch (spec §1).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path


def load_last_processed(path: Path) -> date | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get("last_processed_cycle")
    return date.fromisoformat(value) if value else None


def save_last_processed(path: Path, cycle_date: date) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"last_processed_cycle": cycle_date.isoformat()}),
        encoding="utf-8",
    )
