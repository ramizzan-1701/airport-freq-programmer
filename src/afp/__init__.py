"""Airport Frequency Programmer.

The version lives here rather than being read back out of the installed
distribution's metadata: importlib.metadata needs a dist-info directory,
and a PyInstaller/py2app bundle has no reason to ship one. A module
constant is present wherever the package itself is. pyproject reads it
from here, so there is one number to change.
"""

__version__ = "0.1.0"

# Bumped with __version__, shown on the About screen.
RELEASE_DATE = "2026-09-09"

__all__ = ["__version__", "RELEASE_DATE"]
