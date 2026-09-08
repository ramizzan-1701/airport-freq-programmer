"""Export profile for the Yaesu FTA-850L, via its YCE-64 programming
software. Values confirmed by hand against the actual software:
14-char tag cap, 400-entry cap.
"""

from .profile import ExportProfile

FTA_850L = ExportProfile(
    name="FTA-850 / YCE-64",
    max_tag_length=14,
    max_entries=400,
)
