"""Small local JSON files persisting web-UI-only state across restarts,
under the same cache_dir as everything else -- mirrors
afp.nasr.cycle_store's exact load/save pattern. AppState is the sole
consumer, so (like cycle_store living in nasr/ next to its sole consumer
NASRSource) this lives under web/ rather than as a top-level module.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from ..selection import Entry


def load_custom_entries(path: Path) -> list[Entry]:
    if not path.exists():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [Entry(**row) for row in rows]


def save_custom_entries(path: Path, entries: list[Entry]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([dataclasses.asdict(e) for e in entries]),
        encoding="utf-8",
    )


def load_group_setup_acknowledged(path: Path) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return bool(data.get("acknowledged", False))


def save_group_setup_acknowledged(path: Path, acknowledged: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"acknowledged": acknowledged}), encoding="utf-8")


def load_about_acknowledged(path: Path) -> bool:
    if not path.exists():
        return False
    data = json.loads(path.read_text(encoding="utf-8"))
    return bool(data.get("acknowledged", False))


def save_about_acknowledged(path: Path, acknowledged: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"acknowledged": acknowledged}), encoding="utf-8")


def load_filters(path: Path) -> dict | None:
    """The filter selections saved from the last session, or None if
    nothing has been saved yet.

    Returns None rather than {} for an unreadable file too: a corrupt one
    should leave the rail at its defaults, not stop the app opening.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def save_filters(path: Path, filters: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(filters), encoding="utf-8")
