"""App identity the two platform builds both need.

Both packaging entry points -- afp.spec for Windows, py2app_setup.py for
macOS -- have to stamp the same version, product name and author into
their bundles. Read from one place here so a release cannot ship a
Windows .exe and a .app claiming different things.

Constants are parsed out of the package source rather than imported.
These scripts run as __main__ from the repo root with whatever happens to
be installed, and a packaging script has no business importing the
package it is packaging to read a string: ast needs nothing on sys.path
and executes nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_INIT = REPO_ROOT / "src" / "afp" / "__init__.py"
ABOUT = REPO_ROOT / "src" / "afp" / "about.py"


def read_constant(path: Path, name: str):
    """The value of a module-level constant, without importing.

    Any literal ast.literal_eval accepts -- about.py's copy is tuples of
    strings and of string pairs, not just the plain strings this started
    out reading.

    Both assignment forms, because about.py annotates its constants
    (`NAME: tuple[str, ...] = (...)`) and __init__.py does not. An
    annotated assignment is a different node type, so handling only the
    plain one silently found nothing.
    """
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(node.value)
    raise SystemExit(f"no {name} found in {path}")


def package_version() -> str:
    return read_constant(PACKAGE_INIT, "__version__")


def author() -> str:
    return read_constant(ABOUT, "AUTHOR")


def version_tuple() -> tuple[int, int, int, int]:
    """The Windows VERSIONINFO form: always four integers.

    The package version is three parts, and the resource is a fixed
    struct rather than a string, so the fourth is padding rather than
    something to invent.
    """
    parts = [int(p) for p in package_version().split(".")]
    parts += [0] * (4 - len(parts))
    return tuple(parts[:4])


# What a person sees, everywhere they see it: the window title, the About
# screen's heading, Explorer's Details tab, and the filename of both the
# .exe and the .app. macOS already named the bundle this way, since
# py2app takes it from CFBundleName.
APP_DISPLAY_NAME = "FTA-850 Frequency Manager"

# Space-free, for the things a person does not read: the CI artifact
# names and the version resource's InternalName. Not the executable --
# that is APP_DISPLAY_NAME, spaces included.
#
# Unrelated to afp.paths.APP_NAME, which is space-free for a different
# reason again: it is a real directory holding user data, and renaming it
# orphans that data.
APP_FILE_NAME = "FTA850FrequencyManager"
