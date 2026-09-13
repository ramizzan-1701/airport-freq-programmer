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

from ..selection import Entry
from .profile import ExportProfile

_BOM = b"\xef\xbb\xbf"


class ExportValidationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


def validate(entries: list[Entry], profile: ExportProfile) -> list[str]:
    problems: list[str] = []
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


def _format_coordinate(value: float, positive: str, negative: str) -> tuple[str, str]:
    hemisphere = positive if value >= 0 else negative
    abs_value = abs(value)
    degrees = int(abs_value)
    minutes = (abs_value - degrees) * 60
    return f"{degrees}°{minutes:06.3f}", hemisphere


def build_xml(entries: list[Entry], profile: ExportProfile) -> bytes:
    """Build the full YCE-46-importable XML file, BOM included.

    Raises ExportValidationError (never truncates/renames) if entries
    violate the profile's tag-length, per-group uniqueness, or entry-count
    rules.
    """
    problems = validate(entries, profile)
    if problems:
        raise ExportValidationError(problems)

    root = ET.Element("FILE")
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
