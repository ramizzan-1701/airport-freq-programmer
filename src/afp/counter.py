"""Live entry counter status against an export profile's cap (§4).

Generic over how the count was produced -- the query layer's live filter
counter and any future UI both feed a plain int in here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .export.profile import ExportProfile

AMBER_THRESHOLD = 0.9  # "approaching the limit (e.g. within ~10%)"


@dataclass(frozen=True)
class CounterStatus:
    count: int
    cap: int
    level: str  # "green" | "amber" | "red"

    @property
    def over_cap(self) -> bool:
        return self.count > self.cap


def counter_status(count: int, profile: ExportProfile) -> CounterStatus:
    if count > profile.max_entries:
        level = "red"
    elif count >= profile.max_entries * AMBER_THRESHOLD:
        level = "amber"
    else:
        level = "green"
    return CounterStatus(count=count, cap=profile.max_entries, level=level)
