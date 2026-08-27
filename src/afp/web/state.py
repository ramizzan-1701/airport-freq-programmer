"""Process-wide state for the local web UI: the loaded NormalizedData and
its in-memory query DB. A local single-user server has no need for real
session management -- one process, one loaded dataset at a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from sqlite3 import Connection

from ..adapters.faa import FAAAdapter
from ..custom_entries import check_custom_group_capacity, split_recognized_and_custom
from ..export.xml_reader import parse_memory_book_xml
from ..nasr.source import NASRSource
from ..query.db import build_database
from ..schema import NormalizedData
from ..selection import Entry
from . import local_store

_REQUIRED_CSVS = ("APT_BASE.csv", "FRQ.csv", "ILS_BASE.csv")
_CYCLE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class LoadedCycle:
    cycle_date: date
    data: NormalizedData
    conn: Connection


class AppState:
    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.source = NASRSource(cache_dir=self.cache_dir)
        self.loaded: LoadedCycle | None = None

        self.custom_entries_path = self.cache_dir / "custom_entries.json"
        self.group_setup_path = self.cache_dir / "group_setup.json"
        self.custom_entries: list[Entry] = local_store.load_custom_entries(self.custom_entries_path)
        self.group_setup_acknowledged: bool = local_store.load_group_setup_acknowledged(self.group_setup_path)

    def available_cycles(self) -> list[date]:
        """Locally cached cycles that have all three required CSVs already
        extracted (i.e. a completed `fetch`), newest first.
        """
        if not self.cache_dir.exists():
            return []
        found = []
        for entry in self.cache_dir.iterdir():
            if not entry.is_dir() or not _CYCLE_DIR_RE.match(entry.name):
                continue
            if all((entry / name).exists() for name in _REQUIRED_CSVS):
                found.append(date.fromisoformat(entry.name))
        return sorted(found, reverse=True)

    def load_cycle(self, cycle_date: date) -> LoadedCycle:
        cycle_dir = self.cache_dir / cycle_date.isoformat()
        adapter = FAAAdapter(
            apt_base=cycle_dir / "APT_BASE.csv",
            frq=cycle_dir / "FRQ.csv",
            ils_base=cycle_dir / "ILS_BASE.csv",
        )
        data = adapter.parse()
        conn = build_database(data)
        self.loaded = LoadedCycle(cycle_date=cycle_date, data=data, conn=conn)
        return self.loaded

    def import_custom_entries(self, xml_bytes: bytes) -> list[Entry]:
        """Parses a full YCE-64 export, keeps only the entries that don't
        match the app's 6 fixed group names, and replaces (not merges
        with) whatever custom set was previously held -- each import is a
        full snapshot of "everything currently on the radio", so a stale
        entry no longer present must not resurrect.

        Raises CustomGroupCapacityError (leaving the existing custom set
        untouched) if the new custom entries span more than 3 distinct
        group names.
        """
        entries = parse_memory_book_xml(xml_bytes)
        _, custom = split_recognized_and_custom(entries)
        check_custom_group_capacity(custom)
        self.custom_entries = custom
        local_store.save_custom_entries(self.custom_entries_path, self.custom_entries)
        return self.custom_entries

    def remove_custom_entry(self, index: int) -> list[Entry]:
        del self.custom_entries[index]
        local_store.save_custom_entries(self.custom_entries_path, self.custom_entries)
        return self.custom_entries

    def clear_custom_entries(self) -> None:
        self.custom_entries = []
        local_store.save_custom_entries(self.custom_entries_path, self.custom_entries)

    def acknowledge_group_setup(self) -> None:
        self.group_setup_acknowledged = True
        local_store.save_group_setup_acknowledged(self.group_setup_path, True)
