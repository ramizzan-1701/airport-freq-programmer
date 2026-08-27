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
