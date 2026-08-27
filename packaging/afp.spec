# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows build.

Run from the repo root:  pyinstaller packaging/afp.spec

Two things here are load-bearing and easy to lose in a "cleanup":

1. The static/ datas entry. afp.web.app resolves its assets as
   Path(__file__).parent / "static". Under PyInstaller __file__ points
   inside the unpacked bundle, so that line keeps working *only* if the
   files are shipped at the matching relative path. Drop this and the
   app still launches -- index.html is read through the same mechanism
   -- but every asset 404s and you get an unstyled, inert page.

2. The uvicorn hiddenimports. uvicorn resolves its protocol, loop and
   lifespan implementations from strings at runtime, so PyInstaller's
   static analysis cannot see them. Without these the .exe builds
   cleanly and then dies on startup with an import error.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

# SPECPATH is set by PyInstaller to this file's directory.
REPO_ROOT = Path(SPECPATH).parent
STATIC_SRC = REPO_ROOT / "src" / "afp" / "web" / "static"

# pywebview loads its platform backend (EdgeChromium/WebView2 here)
# dynamically and ships non-Python support files, so collect it wholesale
# rather than trying to enumerate the pieces.
webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")

a = Analysis(
    # launcher.py, not afp/desktop.py directly: PyInstaller runs the entry
    # script as __main__, which breaks afp.desktop's relative imports.
    [str(REPO_ROOT / "packaging" / "launcher.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=webview_binaries,
    datas=[
        (str(STATIC_SRC), "afp/web/static"),
        *webview_datas,
    ],
    hiddenimports=[
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.loops.asyncio",
        "uvicorn.logging",
        *webview_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AirportFreqProgrammer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # No console window behind the app -- this is a GUI, and a stray
    # terminal reads as a crash to a non-technical user.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon: added during the visual-design pass, once there's a real logo.
)
