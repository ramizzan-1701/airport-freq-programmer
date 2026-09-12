# Airport Frequency Programmer — application icon

The chosen mark (option 5D): a five-tick tuning scale over **122.8**, the most-used CTAF. Ground and accent are the app's own — the taskbar icon and the window agree.

## What to use

| Platform | File(s) |
| --- | --- |
| Windows `.ico` | `png/square/` — build a multi-resolution ICO from 16, 20, 24, 32, 48, 64, 128, 256 |
| macOS `.icns` | `png/macos/` — already inset to Apple's 824/1024 grid on transparent ground |
| Linux / freedesktop | `png/square/icon-256.png` (and 48, 64, 128 for hicolor) |
| In-app, docs, web | `svg/icon-rounded.svg` |

`png/rounded/` is the full-bleed rounded tile — use it anywhere you need the icon as a picture (README, about box, installer banner) rather than as an OS icon.

### Building the bundles

```bash
# Windows — ImageMagick
magick png/square/icon-16.png png/square/icon-20.png png/square/icon-24.png \
       png/square/icon-32.png png/square/icon-48.png png/square/icon-64.png \
       png/square/icon-128.png png/square/icon-256.png app.ico

# macOS — iconutil (rename into an .iconset first; add @2x variants as needed)
mkdir app.iconset && cp png/macos/*.png app.iconset/
iconutil -c icns app.iconset
```

PyInstaller picks these up via `--icon app.ico` on Windows and `--icon app.icns` on macOS; the `pywebview` window on Linux takes the 256px PNG.

## Geometry

512×512 viewBox. Corner radius 112 on the rounded tile, 0 on the square one (Windows and Linux mask nothing, so the square version is the correct source there — do not ship the rounded tile as an `.ico`).

| Element | Geometry |
| --- | --- |
| Ground | `0,0 512×512`, `rx 112` or `0`, `#11151A` |
| Ticks (outer) | `x 72` and `418`, `y 155`, `22×76`, `rx 11`, `#D6D4CC` |
| Ticks (inner) | `x 158.5` and `331.5`, `y 172`, `22×59`, `rx 11`, `#D6D4CC` |
| Tick (tuned) | `x 245`, `y 130`, `22×101`, `rx 11`, `#5b93c2` |
| Number | `122.8`, Chakra Petch 700, `160px`, `letter-spacing -3`, centered `x 256`, baseline `y 401`, `#D6D4CC` |

All ticks are bottom-aligned at `y 231`. The tick row spans `x 72 → 440`, which is exactly the rendered box of the number above it — that shared margin is the whole alignment of the mark, so if the type is ever re-set, re-measure and re-span the ticks to match.

## Colors

```
ground  #11151A
ink     #D6D4CC   ticks (4) and numerals
accent  #5b93c2   tuned tick
```

Same tokens as the app UI (`--bg`, `--ink`, `--acc`). If the app's accent ever changes, the tuned tick changes with it.

## The SVG and its font

`svg/*.svg` keep `122.8` as **live text in Chakra Petch 700**. That makes them editable, but it also means they render with a fallback font on any machine without Chakra Petch installed — do not rasterize them in a build step. The PNGs in this bundle were rendered with the real font and are the shipping assets.

If you want a self-contained SVG, convert the text to outlines once (Inkscape: `inkscape icon-rounded.svg --export-text-to-path --export-plain-svg=icon-rounded-outlined.svg`) and use that.

## Notes

- **Smallest sizes.** The mark holds to 32px. At 16 and 20px the number is a texture rather than readable digits — that is expected and it still reads as a frequency display; the accent tick is what identifies it at that size. Do not add a simplified 16px variant without checking it against the tuning-scale-only mark first.
- **No light-ground version.** The dark tile is deliberate: it is self-contained on light and dark docks alike. If a light variant is ever needed, swap ground and ink (`#D6D4CC` ground, `#11151A` ink) and keep the accent.
- **Alternatives considered** are in `App Icon.dc.html` in the design project — a tuning scale alone, broadcast arcs, and the nine memory slots. Worth a look before any redesign.
