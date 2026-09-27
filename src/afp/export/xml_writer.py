"""XML generation for the YCE-46 memory-book format.

Encodes the lessons learned building this by hand as validation checks
(not just documentation):
  - UTF-8 BOM required before the XML declaration, or YCE-46 rejects the
    file.
  - FREQUENCY must render with exactly 3 decimal places.
  - TAG_NAME must not exceed the profile's max length, and must be unique
    within its GROUP. Not within the file: a custom group is often just a
    user's shortlist of frequencies they already have elsewhere, so the
    same tag legitimately appears in both that group and a generated one
    (spec §5 step 9 keeps custom entries un-deduplicated for exactly this
    reason). A repeat inside one group is still a collision.

    The file-wide rule this replaced was written from a hand-built file
    and was wrong. Cross-group duplicates have since been confirmed on
    the radio itself: a generated file carrying them imports and works
    with no issues. Do not tighten this back up on suspicion.
  - Total entry count over the profile's cap causes an outright import
    failure on the radio (not a partial import) -- so we refuse to
    generate rather than silently truncate.
  - POSITION is DD°MM.mmm (degrees + decimal minutes), not decimal degrees
    or DD°MM'SS".
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Mapping

from ..selection import UNASSIGNED_GROUP, Entry
from .profile import ExportProfile

_BOM = b"\xef\xbb\xbf"


class ExportValidationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def validate(
    entries: list[Entry],
    profile: ExportProfile,
    group_slots: Mapping[int, str] | None = None,
) -> list[str]:
    """Blocking problems only -- anything here refuses the export.

    Whether an entry names a group that exists is deliberately not in
    here: an undefined name still imports, the entry just lands
    ungrouped. See ungrouped_warning().
    """
    problems: list[str] = []

    if group_slots:
        if len(group_slots) > profile.max_groups:
            problems.append(
                f"{len(group_slots)} groups exceeds {profile.name} cap of "
                f"{profile.max_groups}"
            )
        for slot, name in sorted(group_slots.items()):
            if len(name) > profile.max_group_name_length:
                problems.append(
                    f"group name '{name}' is {len(name)} chars, exceeds "
                    f"{profile.name} max of {profile.max_group_name_length}"
                )
            if not name.strip():
                problems.append(f"group slot {slot} has an empty name")
            if name == UNASSIGNED_GROUP:
                problems.append(
                    f"'{UNASSIGNED_GROUP}' is the radio's pseudo-group for "
                    "unassigned entries and cannot be defined as a group"
                )
        # Two slots sharing a name leaves the radio with no way to tell
        # which one an entry meant.
        by_name: dict[str, list[int]] = {}
        for slot, name in sorted(group_slots.items()):
            by_name.setdefault(name.casefold(), []).append(slot)
        for name, slots in by_name.items():
            if len(slots) > 1:
                problems.append(
                    f"group name '{group_slots[slots[0]]}' is defined on "
                    f"slots {', '.join(str(s) for s in slots)}"
                )

    seen: set[tuple[str, str]] = set()
    for e in entries:
        if len(e.tag_name) > profile.max_tag_length:
            problems.append(
                f"tag '{e.tag_name}' is {len(e.tag_name)} chars, "
                f"exceeds {profile.name} max of {profile.max_tag_length}"
            )
        key = (e.group, e.tag_name)
        if key in seen:
            problems.append(f"duplicate tag name in group '{e.group}': '{e.tag_name}'")
        seen.add(key)

    if len(entries) > profile.max_entries:
        problems.append(
            f"{len(entries)} entries exceeds {profile.name} cap of "
            f"{profile.max_entries}"
        )

    return problems


def ungrouped_warning(
    entries: list[Entry], group_slots: Mapping[int, str] | None = None
) -> str | None:
    """How many entries will land with no group on the radio, if any.

    Not a validation problem. An entry naming a group that no <GROUPS>
    slot defines still imports perfectly well -- it just arrives
    unassigned, reachable under the radio's ALL view. Refusing to export
    over that would be refusing to do the thing the user asked for on
    account of something the radio handles.
    """
    defined = {name.casefold() for name in (group_slots or {}).values()}
    defined.add(UNASSIGNED_GROUP.casefold())
    stray = [e for e in entries if e.group.casefold() not in defined]
    if not stray:
        return None
    groups = sorted({e.group for e in stray if e.group})
    named = ", ".join(f"'{g}'" for g in groups[:3])
    if len(groups) > 3:
        named += f" and {len(groups) - 3} more"
    return (
        f"{len(stray)} entr{'y' if len(stray) == 1 else 'ies'} will land in "
        f"{UNASSIGNED_GROUP} on the radio: no group is defined for {named}"
        if groups
        else f"{len(stray)} entries have no group and will land in {UNASSIGNED_GROUP}"
    )


def _format_coordinate(value: float, positive: str, negative: str) -> tuple[str, str]:
    hemisphere = positive if value >= 0 else negative
    abs_value = abs(value)
    degrees = int(abs_value)
    minutes = (abs_value - degrees) * 60
    return f"{degrees}°{minutes:06.3f}", hemisphere


def build_xml(
    entries: list[Entry],
    profile: ExportProfile,
    group_slots: Mapping[int, str] | None = None,
) -> bytes:
    """Build the full YCE-46-importable XML file, BOM included.

    Raises ExportValidationError (never truncates/renames) if entries
    violate the profile's tag-length, per-group uniqueness, or entry-count
    rules.

    `group_slots` maps a radio slot index to the name to put on it, and
    becomes the <GROUPS> block. A slot left out is left alone: the radio
    keeps whatever name it already had there, which is the only way to
    avoid clobbering groups the app does not manage. Omitted entirely,
    no <GROUPS> block is written at all and the file is exactly what
    earlier versions produced.
    """
    problems = validate(entries, profile, group_slots)
    if problems:
        raise ExportValidationError(problems)

    root = ET.Element("FILE")

    # Before MEMORY_BOOK: the groups have to exist before the entries
    # that name them.
    if group_slots:
        groups_el = ET.SubElement(root, "GROUPS")
        for slot, name in sorted(group_slots.items()):
            group_el = ET.SubElement(groups_el, "GROUP")
            group_el.set("index", str(slot))
            group_el.text = name

    book = ET.SubElement(root, "MEMORY_BOOK")
    for e in entries:
        group_el = ET.SubElement(book, "MEMORY_BOOK_GROUP")
        ET.SubElement(group_el, "TAG_NAME").text = e.tag_name
        ET.SubElement(group_el, "FREQUENCY").text = f"{e.freq_mhz:.3f}"
        ET.SubElement(group_el, "GROUP").text = e.group

        position = ET.SubElement(group_el, "POSITION")
        lat_str, ns = _format_coordinate(e.lat, "N", "S")
        lon_str, ew = _format_coordinate(e.lon, "E", "W")
        ET.SubElement(position, "LAT").text = lat_str
        ET.SubElement(position, "NS").text = ns
        ET.SubElement(position, "LON").text = lon_str
        ET.SubElement(position, "EW").text = ew

        ET.SubElement(group_el, "SCAN_MEMORY").text = e.scan_memory
        ET.SubElement(group_el, "SHIFT").text = e.shift

    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode")
    declaration = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
    return _BOM + (declaration + body + "\n").encode("utf-8")
