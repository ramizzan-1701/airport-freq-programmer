"""Orchestrates the full core pipeline: adapter -> normalized schema ->
selection -> export profile -> XML bytes. No filtering, no UI -- filters
are layered on top of NormalizedData in a later milestone.
"""

from __future__ import annotations

from .adapters.base import SourceAdapter
from .export.profile import ExportProfile
from .export.xml_writer import build_xml
from .selection import select_entries


def generate_xml(
    adapter: SourceAdapter,
    profile: ExportProfile,
    mode: str = "smart",
    include_public: bool = True,
    include_private: bool = False,
) -> bytes:
    data = adapter.parse()
    entries = select_entries(
        data,
        mode=mode,
        include_public=include_public,
        include_private=include_private,
    )
    return build_xml(entries, profile)
