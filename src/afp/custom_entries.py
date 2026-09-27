"""Business rules for the custom-entry preserve/merge flow (spec §5):
splitting an imported YCE-46 memory-book export into entries the app
itself would regenerate ("recognized") vs. entries a user hand-added
("custom"), and enforcing the radio's fixed slot capacity for the latter.
"""

from __future__ import annotations

from .selection import CUSTOM_SLOTS, RESERVED_GROUP_NAMES, UNASSIGNED_GROUP, Entry

# 9 total memory-group slots on the FTA-850; 6 are held by whichever
# naming scheme is active, leaving 3 for the user's own.
MAX_CUSTOM_GROUPS = CUSTOM_SLOTS

# Recognized means "the app would regenerate this from current FAA data",
# and that has to cover both schemes' names rather than just the active
# one. A user who switches from alphabetical to category and re-imports
# would otherwise meet their own six previous group names as six brand
# new custom groups -- over the 3-slot cap before they had added
# anything.
_REGENERATED_GROUP_NAMES = RESERVED_GROUP_NAMES - {UNASSIGNED_GROUP}


class CustomGroupCapacityError(Exception):
    def __init__(self, groups: list[str]) -> None:
        self.groups = sorted(groups)
        self.found = len(self.groups)
        self.available = MAX_CUSTOM_GROUPS
        super().__init__(
            f"{self.found} distinct custom groups found, only {self.available} available"
        )


def split_recognized_and_custom(entries: list[Entry]) -> tuple[list[Entry], list[Entry]]:
    """Recognized = GROUP matches a name either scheme would produce
    (regenerated from current FAA data, so discarded here). Custom =
    everything else, presumed user-added, preserved as-is.

    Entries in ALL are custom. They are the radio's unassigned ones, and
    while they belong to no named group they are still the user's data --
    dropping them here would delete them on the next import, since
    importing replaces the whole memory book rather than merging into it.
    They are kept, exported back with GROUP=ALL, and land unassigned
    again. What they never do is consume one of the three slots; see
    custom_group_names().
    """
    recognized = [e for e in entries if e.group in _REGENERATED_GROUP_NAMES]
    custom = [e for e in entries if e.group not in _REGENERATED_GROUP_NAMES]
    return recognized, custom


def custom_group_names(custom_entries: list[Entry]) -> list[str]:
    """The distinct named groups the custom entries occupy.

    ALL is not one of them: it is the absence of a group, not a group,
    and it has no slot to occupy.
    """
    return sorted({e.group for e in custom_entries if e.group and e.group != UNASSIGNED_GROUP})


def check_custom_group_capacity(custom_entries: list[Entry]) -> None:
    """Raises CustomGroupCapacityError if the custom entries span more than
    MAX_CUSTOM_GROUPS distinct group names. Not a check for whether the 6
    scheme names are already present -- a first-time import matching none
    of them is expected, not an error.
    """
    groups = custom_group_names(custom_entries)
    if len(groups) > MAX_CUSTOM_GROUPS:
        raise CustomGroupCapacityError(groups)
