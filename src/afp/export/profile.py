"""Export profile: radio/software-specific output rules.

Kept as plain data so a new radio/software target is a new instance, not a
code change -- selection and XML-writing logic stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExportProfile:
    name: str
    max_tag_length: int
    max_entries: int
    # Memory groups the radio has slots for, and how long each name may
    # be. Only meaningful since the app began writing a <GROUPS> block:
    # before that the names were whatever the user had typed into the
    # programming software by hand, and the app never saw them.
    max_groups: int = 9
    max_group_name_length: int = 10
