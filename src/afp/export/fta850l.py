"""Export profile for the Yaesu FTA-850, via its YCE-46 programming
software. Values confirmed by hand against the actual software:
14-char tag cap, 400-entry cap, 9 group slots, 10-char group names.
"""

from .profile import ExportProfile

FTA_850L = ExportProfile(
    name="FTA-850 / YCE-46",
    max_tag_length=14,
    max_entries=400,
    # 9 slots at indices 0-8, names capped at 10 characters -- both
    # confirmed by testing against the radio, which is also where the
    # <GROUPS> block itself was found.
    max_groups=9,
    max_group_name_length=10,
)
