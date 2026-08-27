"""py2app build script for the macOS .app bundle.

Deliberately NOT named setup.py and NOT at the repo root. setuptools
picks up a root setup.py ahead of pyproject.toml, and this file's
setup() call describes an app bundle rather than a distributable
package -- leaving it as ./setup.py breaks `pip install -e .` outright.
Package metadata and dependencies stay in pyproject.toml, which remains
the single source of truth.

Build on macOS (py2app cannot cross-compile), from the repo root:
    pip install -e ".[build-macos]"
    python packaging/py2app_setup.py py2app

Paths below are relative to the repo root, so run it from there --
py2app rejects absolute paths in data_files.

The built app is unsigned: Gatekeeper blocks it on first open until the
user right-click > Open, or it's signed with a paid Apple Developer ID.
"""

from setuptools import setup

# launcher.py, not src/afp/desktop.py directly: py2app runs the entry
# script as __main__, which breaks afp.desktop's relative imports.
APP = ["packaging/launcher.py"]

# Mirrors the `datas` entry in afp.spec, for the same reason: afp.web.app
# resolves its assets as Path(__file__).parent / "static", so they have to
# land at afp/web/static inside the bundle or every asset 404s and the UI
# renders unstyled and inert.
DATA_FILES = [
    (
        "afp/web/static",
        [
            "src/afp/web/static/index.html",
            "src/afp/web/static/app.js",
            "src/afp/web/static/style.css",
        ],
    ),
]

OPTIONS = {
    "packages": ["afp", "uvicorn", "fastapi", "starlette", "pydantic", "webview", "requests", "bs4"],
    # uvicorn resolves its protocol/loop/lifespan classes from strings at
    # runtime, so static analysis can't see them. Without these the bundle
    # builds cleanly and then dies on launch with an import error.
    "includes": [
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "uvicorn.loops.asyncio",
    ],
    "excludes": ["tkinter", "pytest"],
    "plist": {
        "CFBundleName": "Airport Frequency Programmer",
        "CFBundleDisplayName": "Airport Frequency Programmer",
        "CFBundleIdentifier": "com.github.ramizzan-1701.airport-freq-programmer",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # The window loads its UI from the app's own uvicorn server over
        # plain HTTP on 127.0.0.1; without this exception App Transport
        # Security blocks the WebView from loading it at all.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
}

setup(
    name="AirportFreqProgrammer",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
