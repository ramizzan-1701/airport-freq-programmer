"""Frozen-build entry script for both PyInstaller and py2app.

Both bundlers execute their entry script as __main__ rather than as a
module inside its package, which breaks the relative imports in
afp.desktop (`from .paths import ...`) with "attempted relative import
with no known parent package". Every other module in this codebase uses
relative imports -- afp.cli included -- so the fix is this two-line
shim that enters through the package properly, rather than making
afp.desktop the one module that imports differently from its siblings.
"""

from afp.desktop import main

if __name__ == "__main__":
    raise SystemExit(main())
