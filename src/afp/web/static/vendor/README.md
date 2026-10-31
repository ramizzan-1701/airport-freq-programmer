# Vendored, not fetched

Everything the radius map needs is served from here rather than from a CDN. The app is a
desktop build that works offline once a NASR cycle is downloaded, and a map that needs the
network to draw would quietly break that — on exactly the slow or absent connections the rest
of the app was built to cope with.

It also keeps the packaged builds self-contained: `afp.spec` and `py2app_setup.py` copy the
whole `static/` tree, so these files land in the `.exe` and the `.app` with no extra step.
`pyproject.toml` needs its own `package-data` line per directory, though — `static/*` does not
match nested folders.

| Path | What | Licence |
| --- | --- | --- |
| `leaflet/` | Leaflet 1.9.4 — `leaflet.js`, `leaflet.css`, `images/` | BSD-2-Clause (`leaflet/LICENSE`) |
| `us-states.json` | US state and territory outlines | ISC (see below) |

## us-states.json

State, district and territory outlines — 56 of them, including PR, GU, VI, AS and MP, so the
map is not silently missing the places NASR actually covers.

Derived from [us-atlas](https://github.com/topojson/us-atlas) `states-10m.json` (ISC licence,
© Mike Bostock), converted to plain coordinate arrays at 0.01° precision. 216 KB.

The shape is `[{c, n, p}]` — code, name, and polygons as `[[[lon, lat], …]]` — rather than
GeoJSON. It is the same data and the same precision the prototype was built and checked
against, and `L.polygon` takes it directly after a lat/lon swap. Re-deriving it from the
TopoJSON would have meant writing a decoder to arrive at bytes we already had.

There is no tile layer, and adding one is a separate decision: tiles would bring back the
network dependency this directory exists to avoid, and every public tile server has a usage
policy attached.
