"""Builds the Windows icon bundle from the PNGs in packaging/icons/.

Run this only when the icon design changes; the output is committed, so
building or running the app needs neither this script nor any image
tooling.

    pip install Pillow
    python packaging/make_icons.py

Pillow is used only here. An earlier version of this assembled the ICO
by hand, embedding each PNG verbatim -- that is legal, and modern
Windows renders it, but the legacy GDI+ reader behind System.Drawing.Icon
cannot decode PNG entries: asked for 128px it threw outright, and at 16
and 32 it returned colour garbage from reading PNG bytes as a DIB.
Pillow writes the conventional mix instead -- BMP entries for the small
sizes, PNG for 256 -- which both readers handle.

macOS (.icns) is left to Apple's own iconutil, which exists only on
macOS. packaging/py2app_setup.py runs it during the mac build, where it
is guaranteed present.
"""

from __future__ import annotations

import struct
from pathlib import Path

ICONS_DIR = Path(__file__).parent / "icons"
SQUARE_DIR = ICONS_DIR / "square"
ICO_PATH = ICONS_DIR / "app.ico"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Windows draws the icon at many sizes -- 16 in the title bar, 32 in the
# taskbar and alt-tab, 256 in Explorer's extra-large view. Each one is
# packed from art rendered at that size rather than left to Windows to
# downscale, which turns the 122.8 into mush at the small end.
ICO_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def png_dimensions(data: bytes) -> tuple[int, int]:
    """Width and height from a PNG's IHDR, always the first chunk and
    always at a fixed offset.
    """
    if data[:8] != PNG_MAGIC:
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def build_ico() -> None:
    from PIL import Image

    frames = []
    for size in ICO_SIZES:
        path = SQUARE_DIR / f"icon-{size}.png"
        width, height = png_dimensions(path.read_bytes())
        if (width, height) != (size, size):
            raise ValueError(f"{path.name} is {width}x{height}, expected {size}x{size}")
        frames.append(Image.open(path).convert("RGBA"))

    # The base image supplies the first entry and append_images the rest,
    # so the largest leads and the designer's own pixels are used for
    # every size -- Pillow only packs them.
    base = frames[-1]
    base.save(
        ICO_PATH,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=frames[:-1],
        # DIB entries, not Pillow's default PNG ones. PNG-compressed
        # entries are legal and the Windows shell renders them, but the
        # older GDI+ reader cannot decode them at all -- it threw on the
        # 64px and larger entries and returned colour garbage below that.
        # DIB costs file size and nothing else, so there is no reason to
        # ship something a whole class of reader chokes on.
        bitmap_format="bmp",
    )


def main() -> int:
    build_ico()
    print(f"wrote {ICO_PATH.name} ({ICO_PATH.stat().st_size:,} bytes, {len(ICO_SIZES)} sizes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
