"""The application icon: the committed bundles, and the wiring that puts
them into each platform's build.

None of this is exercised by running the app, so nothing here would fail
loudly on its own -- a missing icon just quietly ships as a default one.
"""

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from afp.web import create_app
from afp.web.app import STATIC_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = REPO_ROOT / "packaging" / "icons"
ICO_PATH = ICONS_DIR / "app.ico"
ICONSET = ICONS_DIR / "FTA850FrequencyManager.iconset"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Matches packaging/make_icons.py. Duplicated rather than imported: this
# is the contract the .ico has to meet, so reading it from the script
# that produced the file would let both drift together silently.
EXPECTED_ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)

# What `iconutil --convert icns` requires. A missing name is not an
# error there -- it just produces an .icns without that resolution.
EXPECTED_ICONSET_FILES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


@pytest.fixture
def empty_client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == PNG_MAGIC, f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def ico_entries(path: Path) -> list[tuple[int, int, bytes]]:
    """(width, height, image blob) for each entry, width 0 meaning 256."""
    data = path.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert reserved == 0 and kind == 1, "not an icon-type ICO"
    out = []
    for i in range(count):
        head = 6 + i * 16
        width, height, _pal, _res, _planes, _bpp, nbytes, offset = struct.unpack(
            "<BBBBHHII", data[head : head + 16]
        )
        assert offset + nbytes <= len(data), f"entry {i} runs past end of file"
        out.append((width or 256, height or 256, data[offset : offset + nbytes]))
    return out


# ---------- Windows ----------


def test_the_ico_carries_every_size_windows_draws():
    """16 in the title bar, 32 in the taskbar, 256 in Explorer's
    extra-large view -- each packed from art rendered at that size.
    """
    entries = ico_entries(ICO_PATH)
    assert tuple(w for w, _h, _d in entries) == EXPECTED_ICO_SIZES
    for width, height, _data in entries:
        assert width == height, f"{width}x{height} entry is not square"


def test_the_ico_uses_dib_entries_not_png():
    """PNG-compressed entries are legal and the Windows shell renders
    them, but the older GDI+ reader cannot decode them -- it throws on
    the larger ones and returns colour garbage on the rest. DIB costs
    only file size.
    """
    for width, _height, blob in ico_entries(ICO_PATH):
        assert blob[:8] != PNG_MAGIC, f"the {width}px entry is PNG-compressed"


def test_every_ico_entry_matches_its_source_png():
    """Pillow packs the designer's per-size art; it must not have
    resampled one image down to fill the others.
    """
    pytest.importorskip(
        "PIL", reason="Pillow is an icon-authoring tool, not a build or runtime dependency"
    )
    from PIL import IcoImagePlugin, Image

    with ICO_PATH.open("rb") as handle:
        ico = IcoImagePlugin.IcoFile(handle)
        for size in EXPECTED_ICO_SIZES:
            packed = ico.getimage((size, size)).convert("RGBA")
            source = Image.open(ICONS_DIR / "square" / f"icon-{size}.png").convert("RGBA")
            assert packed.tobytes() == source.tobytes(), (
                f"the {size}px entry is not the {size}px source art"
            )


def test_the_windows_spec_points_at_the_ico():
    spec = (REPO_ROOT / "packaging" / "afp.spec").read_text(encoding="utf-8")
    assert "icon=str(ICON_ICO)" in spec
    assert 'ICON_ICO = Path(SPECPATH) / "icons" / "app.ico"' in spec


def test_the_windows_build_is_onefile():
    """One .exe, nothing beside it.

    This was onedir for one release, trying to shake Defender's
    Wacatac.B!ml verdict. It did not shake it -- compiling the bootloader
    on the runner did, and that fix is independent of the layout -- and
    onedir cost real usability: the .exe will not start without the
    _internal folder next to it, so opening it from inside a zip viewer
    fails.
    """
    spec = (REPO_ROOT / "packaging" / "afp.spec").read_text(encoding="utf-8")
    assert "exclude_binaries=True" not in spec, "EXE() is holding the binaries back again"
    assert "COLLECT(" not in spec, "a COLLECT step means this is a onedir build"
    assert "a.binaries," in spec and "a.datas," in spec, (
        "the runtime and assets are no longer packed into the .exe"
    )


def test_ci_uploads_the_single_exe_under_its_real_name():
    """The executable's filename has spaces in it, so the upload path has
    to match exactly -- a stale path fails the build rather than shipping
    the wrong thing, but only because if-no-files-found is set to error.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "build-desktop.yml").read_text(encoding="utf-8")
    windows_job = workflow.split("build-windows:", 1)[1].split("build-macos:", 1)[0]
    upload_paths = [
        line.strip() for line in windows_job.splitlines() if line.strip().startswith("path:")
    ]
    assert upload_paths == ['path: "dist/FTA-850 Frequency Manager.exe"'], upload_paths
    assert "if-no-files-found: error" in windows_job


def test_ci_compiles_its_own_pyinstaller_bootloader():
    """Defender scores the stock bootloader as Wacatac.B!ml -- the C
    launcher every PyInstaller app on Windows shares, seen in enough real
    malware to be flagged on sight. Going onedir did not help, which
    ruled out the self-extraction behaviour and left the binary itself.

    Building it on the runner yields a launcher that is not the one in
    their heuristics. Dropping back to the wheel would silently restore
    the flagged binary, so the step is pinned here.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "build-desktop.yml").read_text(encoding="utf-8")
    windows_job = workflow.split("build-windows:", 1)[1].split("build-macos:", 1)[0]
    assert "--no-binary pyinstaller" in windows_job, (
        "the Windows job installs PyInstaller as a wheel again"
    )
    # pip falls back to a wheel without failing, so the step checks the
    # build actually happened rather than trusting the flag.
    assert "Building wheel for pyinstaller" in windows_job, (
        "nothing verifies the bootloader was really compiled from source"
    )


# ---------- macOS ----------


def test_the_iconset_has_exactly_the_names_iconutil_expects():
    present = {p.name for p in ICONSET.iterdir() if p.suffix == ".png"}
    assert present == set(EXPECTED_ICONSET_FILES), (
        "iconset contents differ from what iconutil reads"
    )


def test_every_iconset_file_is_the_resolution_its_name_claims():
    """@2x names carry double the pixels, not the same image again --
    iconutil trusts the filename and does not check.
    """
    for name, expected in EXPECTED_ICONSET_FILES.items():
        width, height = png_size(ICONSET / name)
        assert (width, height) == (expected, expected), (
            f"{name} is {width}x{height}, but its name promises {expected}x{expected}"
        )


def test_the_mac_build_compiles_and_uses_the_icns():
    setup_py = (REPO_ROOT / "packaging" / "py2app_setup.py").read_text(encoding="utf-8")
    assert '"iconfile": build_icns()' in setup_py
    assert "iconutil" in setup_py


# ---------- the web UI's own tab icon ----------


def test_the_favicon_files_ship_with_the_web_assets():
    for name, expected in (("icon-32.png", 32), ("icon-256.png", 256)):
        path = STATIC_DIR / name
        assert path.exists(), f"{name} is missing from the served static directory"
        assert png_size(path) == (expected, expected)


def test_the_page_links_the_favicon():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'href="/static/icon-32.png"' in html
    assert 'href="/static/icon-256.png"' in html


def test_the_favicon_is_served(empty_client):
    for name in ("icon-32.png", "icon-256.png"):
        response = empty_client.get(f"/static/{name}")
        assert response.status_code == 200
        assert response.content[:8] == PNG_MAGIC


def test_the_mac_build_ships_the_whole_static_directory():
    """py2app takes an explicit file list, so anything added to static/
    has to be globbed in or it silently goes missing from that build
    alone -- which is how the fonts were once dropped.
    """
    setup_py = (REPO_ROOT / "packaging" / "py2app_setup.py").read_text(encoding="utf-8")
    assert 'Path("src/afp/web/static").iterdir()' in setup_py, (
        "py2app is back to listing individual static files"
    )
