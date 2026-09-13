"""Filesystem locations that differ between running from a source
checkout and running as a packaged desktop app.

The CLI keeps its own cwd-relative `./nasr_cache` default (see
afp.cli) -- that's the right behaviour for a developer running commands
inside a checkout, where a visible cache dir next to the source is a
feature. A double-clicked .exe/.app has no meaningful cwd (it can be
C:\\, /, or wherever the launcher happened to be), so writing a cache
dir relative to it would scatter 27+ MB of FAA CSVs into arbitrary
places -- or fail outright inside a read-only .app bundle. The desktop
entry point uses the per-user data dir below instead.
"""

from __future__ import annotations

from pathlib import Path

from platformdirs import user_data_dir

# Deliberately not the display name, and deliberately unchanged when that
# name changes: this string is a real on-disk path. The app was renamed to
# "FTA-850 Frequency Manager" and this stayed put, because renaming it
# would point a running install at an empty directory -- orphaning the
# downloaded NASR cycles (tens of MB per cycle), the imported custom
# entries, and both acknowledgment flags, with no error to explain where
# they went. Changing it later means writing a migration, not an edit.
APP_NAME = "AirportFreqProgrammer"
APP_AUTHOR = "afp"


def default_cache_dir() -> Path:
    """Where the packaged app stores downloaded NASR cycles and its
    small JSON state (custom entries, group-setup acknowledgment).

    Per-user and outside the install location, so it survives
    reinstalling or replacing the app bundle:
      Windows  %LOCALAPPDATA%\\afp\\AirportFreqProgrammer
      macOS    ~/Library/Application Support/AirportFreqProgrammer
    """
    return Path(user_data_dir(APP_NAME, APP_AUTHOR))
