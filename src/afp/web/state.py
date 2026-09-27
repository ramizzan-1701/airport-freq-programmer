"""Process-wide state for the local web UI: the loaded NormalizedData and
its in-memory query DB. A local single-user server has no need for real
session management -- one process, one loaded dataset at a time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from sqlite3 import Connection

from ..adapters.faa import FAAAdapter
from ..custom_entries import (
    check_custom_group_capacity,
    custom_group_names,
    split_recognized_and_custom,
)
from ..export.xml_reader import parse_memory_book_xml
from ..nasr.source import NASRSource
from ..progress import ProgressTracker
from ..query.db import build_database
from ..schema import NormalizedData
from ..selection import (
    ALPHABETICAL,
    CUSTOM_SLOTS,
    GROUP_SCHEMES,
    PRESET_SLOTS,
    Entry,
    default_custom_group_name,
    unreserved_group_name,
)
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
        # The fetch or load currently running, or the last one to finish.
        # None until the first one starts.
        self.progress: ProgressTracker | None = None

        self.custom_entries_path = self.cache_dir / "custom_entries.json"
        self.about_path = self.cache_dir / "about.json"
        self.filters_path = self.cache_dir / "filters.json"
        self.groups_path = self.cache_dir / "groups.json"
        self.custom_entries: list[Entry] = local_store.load_custom_entries(self.custom_entries_path)

        # Which of the two naming schemes the six managed groups use, and
        # what the user has called their own three slots. Defaults to
        # alphabetical, which is what the app produced before there was a
        # choice -- so an existing install's exports do not change shape
        # until someone changes this deliberately.
        self.group_scheme: str = ALPHABETICAL
        self.custom_slot_names: list[str | None] = [None] * CUSTOM_SLOTS
        self._restore_groups()
        # False on a fresh install, which is what makes the About screen
        # show itself once ahead of the load screen.
        self.about_acknowledged: bool = local_store.load_about_acknowledged(self.about_path)

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

    def load_cycle(
        self, cycle_date: date, progress: ProgressTracker | None = None
    ) -> LoadedCycle:
        cycle_dir = self.cache_dir / cycle_date.isoformat()
        adapter = FAAAdapter(
            apt_base=cycle_dir / "APT_BASE.csv",
            frq=cycle_dir / "FRQ.csv",
            ils_base=cycle_dir / "ILS_BASE.csv",
        )
        # Only about a second and a half between them, but it is the tail
        # of the fetch as well as the whole of a cached load -- so the
        # same two step keys appear in both step lists and this reports
        # into whichever tracker it was handed.
        if progress:
            progress.begin("parse")
        data = adapter.parse()
        if progress:
            progress.begin("build")
        conn = build_database(data)
        self.loaded = LoadedCycle(cycle_date=cycle_date, data=data, conn=conn)
        return self.loaded

    # ---------- the one operation slow enough to watch ----------

    def begin_progress(self, steps) -> ProgressTracker:
        """Install a tracker for a fetch or load that is about to start.

        A single-user local app has exactly one of these at a time, so
        this is a field rather than a job table. The previous tracker is
        replaced rather than cleared on completion: the frontend's last
        poll lands just after the work ends, and it needs to find the
        outcome there rather than an empty slot.
        """
        self.progress = ProgressTracker(steps)
        return self.progress

    def import_custom_entries(self, xml_bytes: bytes) -> list[Entry]:
        """Parses a full YCE-46 export, keeps only the entries that don't
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

    # ---------- group scheme and the user's three slots ----------

    def _restore_groups(self) -> None:
        saved = local_store.load_groups(self.groups_path)
        if saved:
            scheme = saved.get("scheme")
            if scheme in GROUP_SCHEMES:
                self.group_scheme = scheme
            names = saved.get("custom_names")
            if isinstance(names, list):
                for i, name in enumerate(names[:CUSTOM_SLOTS]):
                    self.custom_slot_names[i] = name if isinstance(name, str) and name else None
            return

        # Nothing saved: either a fresh install, or one upgrading from a
        # version that had no slots at all. Existing custom entries were
        # imported under the old scheme and carry group names but no slot
        # assignment, so seat them in order -- otherwise their groups
        # would not be declared in the next export and every one of those
        # entries would land ungrouped on the radio.
        for i, name in enumerate(custom_group_names(self.custom_entries)[:CUSTOM_SLOTS]):
            self.custom_slot_names[i] = name
        if any(self.custom_slot_names):
            self._save_groups()

    def _save_groups(self) -> None:
        local_store.save_groups(
            self.groups_path,
            {"scheme": self.group_scheme, "custom_names": self.custom_slot_names},
        )

    def set_group_scheme(self, scheme: str) -> None:
        if scheme not in GROUP_SCHEMES:
            raise ValueError(f"unknown group scheme: {scheme!r}")
        self.group_scheme = scheme
        self._save_groups()

    def rename_custom_slot(self, offset: int, name: str) -> str:
        """Names or renames one of the user's three slots.

        Returns the name actually used, which may differ from the one
        asked for: a name colliding with a scheme's or with another slot
        is suffixed rather than refused, so the user's choice survives in
        recognisable form.

        Entries already in the slot are renamed with it. They carry the
        group *name*, not the slot, so leaving them behind would strand
        them in a group nothing declares.
        """
        if not 0 <= offset < CUSTOM_SLOTS:
            raise IndexError(f"no custom slot {offset}")
        others = [n for i, n in enumerate(self.custom_slot_names) if n and i != offset]
        final = unreserved_group_name(name, taken=others)

        previous = self.custom_slot_names[offset]
        self.custom_slot_names[offset] = final
        if previous and previous != final:
            self.custom_entries = [
                replace(e, group=final) if e.group == previous else e
                for e in self.custom_entries
            ]
            local_store.save_custom_entries(self.custom_entries_path, self.custom_entries)
        self._save_groups()
        return final

    def custom_slot_group_name(self, offset: int) -> str:
        """The group name for a slot, naming it if it has none yet.

        A slot holding entries has to be called something: an entry
        naming a group no <GROUPS> slot declares still imports, but lands
        with no group at all.
        """
        name = self.custom_slot_names[offset]
        if not name:
            name = self.rename_custom_slot(
                offset, default_custom_group_name(PRESET_SLOTS + offset)
            )
        return name

    def copy_to_custom_slot(
        self, offset: int, entries: list[Entry]
    ) -> tuple[list[Entry], list[str]]:
        """Snapshots `entries` into one of the user's slots.

        A copy, not a link and not a move: the data is captured as it is
        now and stored independently, so it does not follow later NASR
        changes and the original stays where it was.

        Tag names must be unique within a group, so an entry whose tag is
        already in the target is skipped rather than failing the whole
        operation -- the caller reports those back. Returns
        (copied, skipped_tag_names).
        """
        if not 0 <= offset < CUSTOM_SLOTS:
            raise IndexError(f"no custom slot {offset}")
        group = self.custom_slot_group_name(offset)
        taken = {e.tag_name for e in self.custom_entries if e.group == group}

        copied: list[Entry] = []
        skipped: list[str] = []
        for entry in entries:
            if entry.tag_name in taken:
                skipped.append(entry.tag_name)
                continue
            taken.add(entry.tag_name)
            copied.append(replace(entry, group=group))

        if copied:
            self.custom_entries = self.custom_entries + copied
            local_store.save_custom_entries(self.custom_entries_path, self.custom_entries)
        return copied, skipped

    def load_saved_filters(self) -> dict | None:
        """Read from disk each time rather than caching in memory: the
        rail writes far more often than it reads, and a stale copy here
        would be a second source of truth for no gain.
        """
        return local_store.load_filters(self.filters_path)

    def save_filters(self, filters: dict) -> None:
        local_store.save_filters(self.filters_path, filters)

    def acknowledge_about(self) -> None:
        self.about_acknowledged = True
        local_store.save_about_acknowledged(self.about_path, True)
