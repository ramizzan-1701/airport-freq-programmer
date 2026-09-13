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
ICON_ICO = Path(SPECPATH) / "icons" / "app.ico"

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
        # anyio picks its backend via import_module() at runtime. A bundled
        # PyInstaller hook currently covers this, so Windows builds work
        # without it -- listed anyway so the build doesn't quietly start
        # failing if that hook ever goes away. It broke the macOS bundle,
        # which has no equivalent hook.
        "anyio._backends._asyncio",
        *webview_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# onefile: everything in one .exe, which unpacks itself to a temp
# directory at launch.
#
# This was briefly onedir, to escape Defender scoring the download as
# Trojan:Win32/Wacatac.B!ml. That did not help -- the detection followed
# the onedir build too, which ruled out the self-extraction behaviour and
# pointed at the bootloader binary itself. Compiling that on the runner
# (see the workflow) is what actually cleared it, and that fix is
# independent of how the app is laid out.
#
# What onedir cost was real: the .exe cannot run without the _internal
# folder beside it, so opening it straight from inside a zip viewer --
# which extracts only the file you clicked -- fails.
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FTA850FrequencyManager",
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
    # Multi-resolution, built from the designer's per-size art by
    # packaging/make_icons.py and committed -- so this build needs no
    # image tooling. PyInstaller copies its entries into the .exe's
    # RT_ICON resources, which is what Explorer, the taskbar and the
    # window's own title bar all read.
    icon=str(ICON_ICO),
)

