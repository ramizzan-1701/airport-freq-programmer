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

import subprocess
import sys
from pathlib import Path

from setuptools import Distribution, setup

HERE = Path(__file__).parent
ICONSET = HERE / "icons" / "FTA850FrequencyManager.iconset"
ICNS = HERE / "icons" / "FTA850FrequencyManager.icns"

# Version, author and product name come from the package, through the
# same helper afp.spec uses -- so a release cannot ship a .app and a .exe
# that disagree about what they are. Typing the version here as well is
# what this replaced: Finder's Get Info reported one number while the
# About screen reported another, for as long as it took to notice.
sys.path.insert(0, str(HERE))
import _appinfo  # noqa: E402  (needs HERE on the path first)

VERSION = _appinfo.package_version()


def build_icns() -> str:
    """Compile the .iconset into the .icns py2app wants, using Apple's
    own iconutil.

    The .icns is generated here rather than committed because iconutil
    exists only on macOS -- which is also the only place py2app runs, so
    there is no chicken-and-egg problem. The .iconset beside it is the
    committed source: the designer's per-size PNGs under the names
    iconutil expects.
    """
    if sys.platform != "darwin":
        raise SystemExit("py2app builds only run on macOS; nothing to do here.")
    subprocess.run(
        ["iconutil", "--convert", "icns", str(ICONSET), "--output", str(ICNS)],
        check=True,
    )
    return str(ICNS)


class Py2appDistribution(Distribution):
    """Drops install_requires before py2app ever sees it.

    This script runs from the repo root, so setuptools finds the
    pyproject.toml sitting there and injects its [project].dependencies
    as install_requires. py2app rejects that outright ("install_requires
    is no longer supported", build_app.py) and the build dies before it
    starts -- even though nothing here asked for dependency handling.

    parse_config_files is where setuptools applies pyproject.toml, so
    clearing the field immediately afterwards removes it without
    touching the real dependency list, which stays in pyproject.toml as
    the single source of truth for actually installing the package.
    """

    def parse_config_files(self, *args, **kwargs):
        super().parse_config_files(*args, **kwargs)
        self.install_requires = None

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
        # Globbed, not listed. This was three hard-coded filenames, and
        # adding the favicon PNGs beside them would have shipped a mac
        # build quietly missing its tab icon -- the same way the fonts
        # were once missed a directory further down.
        sorted(str(p) for p in Path("src/afp/web/static").iterdir() if p.is_file()),
    ),
    # Self-hosted fonts, in their own destination directory so the
    # url("fonts/...") references in style.css resolve. Globbed rather
    # than listed so adding a weight doesn't silently miss the bundle.
    ("afp/web/static/fonts", sorted(str(p) for p in Path("src/afp/web/static/fonts").glob("*.woff2"))),
]

OPTIONS = {
    # Built from the committed .iconset just above -- see build_icns.
    "iconfile": build_icns(),
    # anyio is listed as a whole package on purpose: it picks its backend
    # with import_module(f"anyio._backends._{name}") at runtime, so the
    # dependency graph shows nothing and py2app shipped anyio without
    # anyio/_backends/. The app then launched fine and 500'd on the first
    # request ("No module named 'anyio._backends'"), since Starlette's
    # BaseHTTPMiddleware builds an anyio primitive per request. Naming the
    # package rather than the one submodule keeps this fixed if anyio
    # rearranges its backends.
    "packages": [
        "afp", "uvicorn", "fastapi", "starlette", "pydantic", "webview",
        "requests", "bs4", "anyio",
    ],
    # Same runtime-string-import problem, module by module: uvicorn
    # resolves its protocol/loop/lifespan classes from strings, so static
    # analysis can't see them. Without these the bundle builds cleanly and
    # then dies on launch.
    "includes": [
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "uvicorn.loops.asyncio",
        "anyio._backends._asyncio",
    ],
    "excludes": ["tkinter", "pytest"],
    "plist": {
        "CFBundleName": _appinfo.APP_DISPLAY_NAME,
        "CFBundleDisplayName": _appinfo.APP_DISPLAY_NAME,
        "CFBundleIdentifier": "com.github.ramizzan-1701.airport-freq-programmer",
        "CFBundleVersion": VERSION,
        "CFBundleShortVersionString": VERSION,
        "NSHighResolutionCapable": True,
        # The window loads its UI from the app's own uvicorn server over
        # plain HTTP on 127.0.0.1; without this exception App Transport
        # Security blocks the WebView from loading it at all.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
}

setup(
    name=_appinfo.APP_FILE_NAME,
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
    distclass=Py2appDistribution,
)
