"""Business rules for the custom-entry preserve/merge flow (spec §5):
splitting an imported YCE-64 memory-book export into entries the app
itself would regenerate ("recognized") vs. entries a user hand-added
("custom"), and enforcing the radio's fixed slot capacity for the latter.
"""

from __future__ import annotations

from .selection import FIXED_GROUP_NAMES, Entry

# 9 total memory-group slots on the FTA-850L; 6 are permanently reserved
# for this app's fixed naming scheme (FIXED_GROUP_NAMES), leaving 3 for
# anything else.
MAX_CUSTOM_GROUPS = 3


class CustomGroupCapacityError(Exception):
    def __init__(self, groups: list[str]) -> None:
        self.groups = sorted(groups)
        self.found = len(self.groups)
        self.available = MAX_CUSTOM_GROUPS
        super().__init__(
            f"{self.found} distinct custom groups found, only {self.available} available"
        )


def split_recognized_and_custom(entries: list[Entry]) -> tuple[list[Entry], list[Entry]]:
    """Recognized = GROUP matches one of the app's 6 fixed names (will be
    freshly regenerated from current FAA data, so discarded here). Custom
    = everything else, presumed user-added, preserved as-is.
    """
    recognized = [e for e in entries if e.group in FIXED_GROUP_NAMES]
    custom = [e for e in entries if e.group not in FIXED_GROUP_NAMES]
    return recognized, custom


def check_custom_group_capacity(custom_entries: list[Entry]) -> None:
    """Raises CustomGroupCapacityError if the custom entries span more than
    MAX_CUSTOM_GROUPS distinct group names. Not a check for whether the 6
    fixed names are already present -- a first-time import matching none
    of them is expected, not an error.
    """
    groups = {e.group for e in custom_entries}
    if len(groups) > MAX_CUSTOM_GROUPS:
        raise CustomGroupCapacityError(list(groups))
