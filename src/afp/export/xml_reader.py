"""Parses a YCE-64 memory-book XML file back into Entry objects -- the
inverse of xml_writer.py. Needed for the custom-entry preserve/merge flow
(spec §5): a user exports their full current radio memory from YCE-64 and
re-imports it here so hand-added entries survive a regeneration cycle.

Format-only concern, same as xml_writer.py -- the recognized/custom split
and capacity check are business rules and live in afp.custom_entries.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..selection import Entry


class XmlParseError(Exception):
    pass


def _parse_coordinate(text: str, hemisphere: str, positive: str) -> float:
    """Inverse of xml_writer._format_coordinate: "37°37.567" + "N"/"S" (or
    "E"/"W") -> decimal degrees.
    """
    try:
        degrees_str, minutes_str = text.split("°", 1)
        value = int(degrees_str) + float(minutes_str) / 60
    except ValueError as exc:
        raise XmlParseError(f"malformed coordinate: {text!r}") from exc
    return value if hemisphere == positive else -value


def _text(element: ET.Element | None, tag: str, parent_desc: str) -> str:
    if element is None:
        raise XmlParseError(f"{parent_desc} is missing <{tag}>")
    child = element.find(tag)
    if child is None or child.text is None:
        raise XmlParseError(f"{parent_desc} is missing <{tag}>")
    return child.text


def parse_memory_book_xml(xml_bytes: bytes) -> list[Entry]:
    """Parse a YCE-64-exported <FILE><MEMORY_BOOK> XML into Entry objects.

    Raises XmlParseError on malformed XML or a MEMORY_BOOK_GROUP missing
    any required field -- never lets a bare parse/attribute error escape.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise XmlParseError(f"not valid XML: {exc}") from exc

    book = root.find("MEMORY_BOOK")
    if book is None:
        raise XmlParseError("missing <MEMORY_BOOK> element")

    entries: list[Entry] = []
    for group_el in book.findall("MEMORY_BOOK_GROUP"):
        desc = "a <MEMORY_BOOK_GROUP>"
        tag_name = _text(group_el, "TAG_NAME", desc)
        freq_text = _text(group_el, "FREQUENCY", desc)
        try:
            freq_mhz = float(freq_text)
        except ValueError as exc:
            raise XmlParseError(f"malformed <FREQUENCY>: {freq_text!r}") from exc
        group = _text(group_el, "GROUP", desc)

        position = group_el.find("POSITION")
        if position is None:
            raise XmlParseError(f"{desc} ({tag_name}) is missing <POSITION>")
        lat_text = _text(position, "LAT", f"<POSITION> of {tag_name}")
        ns = _text(position, "NS", f"<POSITION> of {tag_name}")
        lon_text = _text(position, "LON", f"<POSITION> of {tag_name}")
        ew = _text(position, "EW", f"<POSITION> of {tag_name}")
        lat = _parse_coordinate(lat_text, ns, "N")
        lon = _parse_coordinate(lon_text, ew, "E")

        scan_memory = _text(group_el, "SCAN_MEMORY", desc)
        shift = _text(group_el, "SHIFT", desc)

        entries.append(
            Entry(
                tag_name=tag_name,
                freq_mhz=freq_mhz,
                group=group,
                lat=lat,
                lon=lon,
                scan_memory=scan_memory,
                shift=shift,
            )
        )

    return entries
