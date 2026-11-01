# Radius Map — implementation brief

Feature brief for the FTA-850 Frequency Manager (`airport-freq-programmer`), written against
**v1.0.2 (commit 707efb3)**. A working prototype of the target behaviour is in
`prototype.html`, kept outside this repository (it is 2.3 MB of embedded sample data, which
does not belong in the history of a repo this small) at
`../radius-map-reference/prototype.html` relative to the checkout. Open it in a browser and try it before
reading the interaction spec. The prototype is the reference for *behaviour and look*. It is
not the reference for *architecture*: it computes everything client-side on sample data, and
the real app must not (see §4).

---

## 1. Goal

The Geographic Radius filter works today but is blind. The user types a centre and a distance,
gets a chip like "Within 60nm of LAX", and has to trust the counter. This feature adds:

1. **Editable radius rules.** Any existing rule can be changed in place (centre, distance,
   include/exclude) without deleting and re-adding it.
2. **A radius map** that shows every rule as a ring, every airport coloured by *why* it is in
   or out, and the state/city filters. Rings can be created and edited directly on the map,
   with a slider that resizes the active ring live while the counter follows.
3. **The map in a window of its own,** which can stay open beside the main one and stays in
   sync with the filter rail in both directions.

   This started as two placements -- docked inside the main window, or popped out. The docked
   one was built and then removed: the main window is already dense, and a map squeezed above
   the results table read as one more thing competing for the same column. Everything below
   that describes docking is kept for the record and marked.

The rail's typed form stays as the manual input path. The map is the visual path. Both edit the
same list of rules.

## 2. What already exists (don't rebuild these)

| Piece | Where | Notes |
| --- | --- | --- |
| Radius rule model | `query/filters.py` → `RadiusFilter(center_lat, center_lon, radius_nm, mode)` | `mode` is `"include"` / `"exclude"`. Any number of rules. |
| Combination logic | `query/query.py` → `_radius_clauses` | Includes **OR'd**, excludes **AND'd** (outside every exclusion), the two groups AND'd, then AND'd with state/city/public-private in `_airport_where`. The map must show exactly this. |
| Standalone facilities | `query/query.py` → `_orphan_frequency_where` | VOR/RCAG/TRACON etc. use their own lat/lon. **NULL position:** dropped by include, kept by exclude. |
| Centre resolution | `GET /api/resolve-center` | Airport ID (case-insensitive) **or** a `lat,lon` pair. Returns canonical `center` + `lat`/`lon`. Map pins need no backend change. |
| API shape | `web/models.py` → `RadiusFilterIn {center, radius_nm, mode}` inside `FilterStateIn` | `to_filter_state()` resolves centres. |
| Distance | `query/geo.py` → `haversine_nm` (R = 3440.065 nm) | Also registered as a SQLite function. |
| Rail radius UI | `static/app.js` → `renderRadiusFilters()` | Chips `Within {nm}nm of {center}` / `Exclude …`, form with centre / NM / mode / Add, error text owned by the field. |
| Filter persistence | `PUT/GET /api/filters`, `persistFilters()` in `runQuery()` | Every filter change is already written to disk via `runQuery()`. |
| Desktop shell | `desktop.py` | pywebview window over uvicorn on a random port. **Min size 1180×760**, with a measured comment explaining why the layout breaks below that. |
| Theme | `static/style.css` tokens | `--bg --panel --chrome --input --ink* --line* --acc --accSolid --warn --ok --custom`, Chakra Petch + IBM Plex Mono. |

## 3. Interaction spec

This is what the prototype does. Match it unless a decision in §8 says otherwise.

### 3.1 Rules and the active rule

- There is **one list** of radius rules (`selected.radiusFilters`). The rail, the map window's
  toolbar, and clicks on the map all read and write it.
- At most **one rule is active** (being edited) at a time. The active rule is shared UI state:
  activating it from the map expands it in the rail, and the reverse.
- **The active rule and a staged centre are mutually exclusive** -- there is exactly one
  selection on screen, never two. Staging a centre closes any open rule; opening a rule (from
  the map, from a map chip, or from a rail chip) clears any staged centre. Both halves are
  needed: with either missing, the map can show a blue preview ring and a blue edit handle at
  the same time, and the slider silently drives only one of them.
- Each rule gets a **stable client id** so "active" survives reordering and removal (see §4.4).

### 3.2 Rail (main window)

- Each rule chip is clickable. Clicking anywhere except ✕ makes it active.
- The active chip expands to an **inline editor**: centre input, NM number, Include/Exclude
  select, **Done**. Highlight it with `--acc` border.
  - NM edits apply live, on input.
  - Mode applies on change.
  - Centre applies on Enter or blur, validated via `/api/resolve-center`. An invalid centre
    shows the existing field-error text (`No airport with ID ZZZ9.`, `Not a valid lat,lon pair.`,
    `Enter an airport ID or lat,lon.`) and keeps the previous centre.
- ✕ removes the rule. If it was active, nothing is active afterwards.
- The existing typed form (centre / NM / mode / Add) is unchanged. Adding from the rail does
  **not** activate the new rule.
- A **Show map / Hide map** text action sits in the Geographic Radius block header.

### 3.3 Map window toolbar

- **Row 1:**
  - Label: `NEW RING`, or `EDITING {center}` (`EDITING PIN` for a lat,lon centre) in `--acc`.
  - Include/Exclude segmented control.
  - Radius slider: 1–250 nm, step 1, with the max extending to fit a larger typed value.
  - NM number box.
- **Row 2:**
  - Centre input.
  - With no active rule: **Add ring**. Adds the rule and activates it.
  - With an active rule: **Delete ring** and **Done**. Enter or blur in the centre input moves
    the active ring.
- **What the controls do:**
  - With a rule active, the slider, NM box, and mode toggle edit that rule live.
  - With nothing active, they set the defaults for the next ring -- and with a centre staged,
    the slider resizes the preview ring, so those defaults are visible while being chosen.
  - Every live edit also updates those defaults, so a new ring starts at the last size and mode
    dialled in.
- **Below the toolbar:** a row of rule chips, the same chips as the rail. Clicking one activates
  it. These chips have no inline editor; the toolbar is the editor here.
- **Header:**
  - Title `RADIUS MAP`.
  - Mini counter `{n} / 400 entries`, coloured like the main counter (green, amber at ≥90%, red
    over the cap).
  - Buttons: **Fit**, **Pop out**/**Dock**, **Close**.

### 3.4 Map clicks (priority order)

1. A click within 12 px of a **ring centre** activates that rule.
2. A click on an **airport** (hit radius 8 px) **stages** it as the centre for the next ring:
   its ID is written into both centre inputs (the map toolbar's and the rail's), and a preview
   ring is drawn at the toolbar's radius which the slider resizes live -- but **no ring is
   created**. **Add ring** or the rail's **Add** creates it. If a rule
   is already centred there, that rule is activated instead. Clicking the staged point again
   clears it.
3. A click on **open map with a rule active** deactivates it.
4. A click on **open map with nothing active** stages a pin centre
   (`lat.toFixed(4),lon.toFixed(4)`) the same way, again without creating anything.
5. **Esc** deactivates, or clears a staged centre, and blurs any focused input. (It never has
   both to clear -- see §3.1.)

Creating on click was the original design (see §8). It was changed after trying it: both forms
already ask for a centre and then an Add, so creating on the click alone was the odd one out,
and on a map of 20k airports a stray click became a ring. Staging keeps the two-step the user
already knows and makes a mis-click free.

Leaflet already suppresses the click after a drag, so panning never creates rings.

### 3.5 Hover

- **On an airport:**
  - ID and name.
  - City, state · public/private · `{kept} of {total} freq` — how many of its frequencies
    survive the frequency-level filters, always written as a ratio even at `11 of 11`.
    Printing it only when the two differ reads as less noise and is not: the line changes
    shape between one hover and the next, so the eye has to re-find the number instead of
    reading the same place twice. In `--warn` when nothing passes, including a point that
    had no frequencies to begin with — equally worthless, same question.

    This is the one place the map admits what a dot cannot say. Categories filter
    *frequencies*; the status is computed from airports, so it never sees them. On real
    data a 60nm ring around SJC holds 42 selected airports whatever the categories say —
    and under Tower-only, 28 of those 42 contribute nothing while being drawn exactly
    like the 14 that carry the result.
  - Its status text (§3.6).
  - `Click to add a ring here`.
  - ~~Its distance to each rule centre.~~ **Removed.** Every rule already names its centre
    on a pill above the map, and with four rings down the list was most of the tooltip.
    What is lost is this airport's distance to each centre, which the pills do not carry;
    restoring it for the *active* ring alone would be a line if it is ever missed.
- **On a ring centre:** the rule label, plus `Click to edit this ring`, or, when it is the
  active rule, `Editing. Drag the slider to resize.`
- **Cursor:** pointer over hits, crosshair elsewhere.

### 3.6 Visual encoding

| Airport status | Meaning | Mark |
| --- | --- | --- |
| `selected` | passes every filter | filled `--ok` dot |
| `excluded` | passes state/city/use and the include rings, but sits inside an exclude ring | `--bg` fill, `--warn` ring and a diagonal slash |
| `outside` | passes state/city/use, but is outside every include ring | filled mid grey (`#6B737B`) |
| `filtered` | fails the state or city filter | small dark dot (`#3A444E`) |
| *(not drawn)* | fails the public/private scope | — |
| city match (overlay) | its city is in the selected cities (and its state in the selected states, if any) | `--acc` halo ring around the dot |

Status precedence is **filtered → outside → excluded → selected**: the first test an airport
fails decides its status. Standalone facilities with a position are classified the same way.
Count the ones without a position as "not on map" in the legend; don't drop them silently.

- **Include ring:** `--ok` stroke at 1.6 px, dashed 6/5, with a 7% `--ok` fill.
- **Exclude ring:** `--warn` stroke at 1.6 px, with a hatch fill: an SVG `<pattern>` of 45°
  lines at 55% opacity over a 6% tint.
- **Active ring:**
  - Solid stroke at 2.6 px and a 13% fill, drawn on top.
  - An `--acc` handle around its centre (r 10, 28% fill).
  - The other rings fade to 45% opacity.
- **Ring edges are geodesic:** a polygon from the destination-point formula (144 steps) using
  the same R as `haversine_nm`. Leaflet's `L.circle` is a Mercator approximation; it would show
  airports on the wrong side of the edge near the boundary. The prototype's `geodesicCircle()`
  can be ported as-is.
- **Ring centres:** a `+` for include, an `×` for exclude, and a boxed label `WVI 40nm`
  (`PIN 25nm` for lat,lon centres). The active rule's label gets the `--acc` treatment.
- **State filter:** the selected states are outlined in `--acc` with a slightly lifted fill;
  unselected states are dimmed when any state is selected.
- **Badges (top-left, clear of Leaflet's zoom control):** `City: SAN JOSE, SANTA CLARA
  +2` and the use scope (`Public only` / `Private only`). Both off is no constraint and
  earns no badge; both on constrains nothing and earns none either.

  Narrowed from the original list. **No state badge:** the selected states are already
  drawn, outlined in `--acc`, so it would name a filter that is already visible. **No
  category badge:** a badge names a filter and points at a mark, and the category filter
  changes no dot on the map at all — `{n} categories` would point at nothing and say
  nothing actionable. The hover carries that instead (§3.5). The two that remain are
  there because cities tint a halo small enough to miss, and the use scope removes points
  outright — on a default nationwide map that is about 14,000 airports simply absent,
  with nothing on screen to explain it.
- **Hint (top-right):** `Click an airport to choose it as a center, or a ring's center to edit
  it`; with a centre staged, `{ID} chosen - press Add ring, or Add in the rail`; with a rule
  active, `Slider resizes this ring. Click open map or press Esc when done.`
- **Staged centre:** a dashed `--acc` crosshair and label box, plus a **live preview ring** at
  the toolbar's current radius -- `--acc` stroke 2px, dashed 6/5, 10% fill. The slider resizes
  it before anything is committed, so the choice is made by looking rather than by guessing a
  number.

  The preview stays `--acc` whatever the mode toggle says: blue means "being chosen". It takes
  the include green or the exclude pink and hatch only on **Add**, which is what makes the
  commit visible.
- **Legend (bottom-left):** one entry per mark above, plus `City match` (an overlay, not a
  status) and the not-on-map count.
- **Status labels** are one table, read by both the legend and the hover — `Selected`,
  `Excluded`, `Outside radius`, `City/State filtered`. They were two tables that disagreed
  on half their entries (`State/city filtered` beside the dot, `Filtered out by state/city`
  under the cursor), which made the key stop being a key. "radius" rather than "ring"
  because that is the word the rest of the app uses: the rail's section is Geographic
  Radius.
- **Labels:** airport IDs appear at zoom ≥ 8 (selected and excluded only), and at zoom ≥ 9 for
  every drawn airport.

**Fit:** fit to the rings. With no rings, fit to the selected states, then to the city-matched
airports, then to CONUS.

## 4. Architecture

### 4.1 Status classification lives in Python (single source of truth)

The prototype re-implements the filter logic in JS. **Don't do that in the app.** The README is
explicit that `FilterState` is the single source of truth, and the map must never disagree with
the export.

Add a function to `src/afp/query/query.py`, next to `_radius_clauses`:

```python
def classify_points(conn, filters: FilterState) -> MapStatus:
    """Per-airport and per-positioned-facility status for the radius map:
    'selected' | 'excluded' | 'outside' | 'filtered' | 'scope' (fails public/private),
    with the same predicates apply_filters uses. Facilities with no position are counted,
    not classified."""
```

- Implement it with the same SQL fragments rather than a parallel Python predicate. Either:
  - run the include and exclude clause halves as separate `CASE` expressions, or
  - select `lat, lon` and evaluate with `haversine_nm` and the same `<=` / `>` comparisons.
- **The invariant to test:** `{ids with status 'selected'} == {a.id for a in apply_filters(...).airports}`,
  and the same for orphan facilities, across a spread of filter states.

### 4.2 API: split static points from dynamic status

Nationwide with private airports on is roughly 19k airports plus facilities. Re-sending
coordinates on every slider tick would be wasteful.

- **`GET /api/map/points`.** Called once per loaded cycle. It returns compact parallel arrays
  (or rows) of
  `id, name, city, state, lat, lon, public_use, kind ('airport'|'facility'), freq_count`,
  plus `cycle_date` as a cache key. Order is fixed for the cycle.
- **`POST /api/map/status`.** It takes `FilterStateIn` (the same body as `/api/query`) and
  returns:
  - `statuses`: one code per point, in `/points` order. A string like `"SSOXF…"` keeps it to
    about 19 KB.
  - `unplottable`: the count of facilities with no position.
  - `rings`: `[{id, center, lat, lon, radius_nm, mode}]`, resolved server-side.
  - `cycle_date`.
- **Live slider feel:** the client redraws the ring geometry **immediately** on input (it has
  the centre's lat/lon from `resolve-center`). `/api/map/status` and `/api/query` refresh
  through the existing `scheduleQuery` debounce (200 ms). On localhost this feels live.

### 4.3 Frontend structure

- **`static/map.js`** (new): the map component — Leaflet setup, state outlines, point canvas,
  rings, hit-testing, toolbar, chips, legend. It is mounted into `static/map.html`, served at
  `GET /map`. (Originally also into a docked panel in `index.html`; that placement is gone.)
- **Point rendering:** one `<canvas>` overlay redrawn on `move`/`zoom`/`resize`, coalesced
  through `requestAnimationFrame`, with hit-testing over the drawn points. Thousands of SVG
  markers or `L.circleMarker`s are too slow. The prototype's `drawPoints()`/`hitTest()` show the
  approach. Set `zoomAnimation: false` so the canvas never lags a zoom.
- **Rings:** Leaflet SVG renderer in its own pane, with the hatch `<pattern>` injected into the
  renderer's `<svg>`. Use `interactive: false`; hit-testing for centres is done in canvas space.
- **Basemap:** vendored US state outlines only, **no tile server**. The app then works offline,
  matches the dark theme, and avoids tile-usage policies. The prototype's outlines come from
  `us-atlas@3 states-10m.json` (ISC licence), converted to GeoJSON at 0.01° precision (~220 KB),
  and include PR, GU, VI, AS, MP. An optional online tile layer can come later (§8).
- **Vendored libraries**, so the desktop build has no CDN dependency:
  - `static/vendor/leaflet/` — Leaflet 1.9.4 (BSD-2), `leaflet.js` + `leaflet.css`.
  - `static/vendor/us-states.geojson`.
- **Packaging:** add `"static/vendor/*", "static/vendor/leaflet/*"` to
  `[tool.setuptools.package-data] "afp.web"` in `pyproject.toml`. The existing comment there
  explains that nested folders are **not** matched by `static/*`; the fonts needed their own
  line for the same reason. `afp.spec` and `py2app_setup.py` copy the whole `static/` tree
  already, but verify the vendor files land in both bundles.

### 4.4 Rule ids and the active rule

- Add an optional `id: str | None = None` to `RadiusFilterIn`. `to_filter_state()` ignores it.
  The client assigns a short random id when it creates a rule, and saved filters keep it.
  Old saved filters without ids get one on restore (`resolvableRadiusFilters`).
- The client also caches the resolved `lat`/`lon` per rule from `/api/resolve-center` to draw
  rings. These are display-only; they are not sent in the filter payload, or are stripped in
  `buildFilterPayload()`.
- The active rule is **UI state, not filter state**: don't persist it to `filters.json`. It
  still has to be shared between windows (§4.5).

### 4.5 Keeping the main window and the pop-out in sync

Each window is a separate JS context with its own copy of `selected`. Make the **server the
source of truth**, using the polling pattern the app already uses for fetch progress.

- **`AppState`:**
  - Add `filters_revision: int`, bumped on every `PUT /api/filters`.
  - Record the writer's `X-Client-Id` header as `filters_source`.
  - Add a small in-memory `ui_state = {active_radius_id, revision, source}`.
- **`PUT /api/ui-state`:** sets `active_radius_id` (and anything else shared later, e.g. map
  placement).
- **`GET /api/sync?filters_since=N&ui_since=M`:** returns
  `{filters_revision, filters (only if newer), filters_source, ui_revision, ui (only if newer), ui_source}`.
  Plain polling every ~400 ms is fine on localhost; long-polling is optional.
- **Each window:**
  - Generates a `clientId` once and sends it on every write.
  - **Ignores updates whose source is itself.** That rule is what stops echo loops: a remote
    update re-renders and re-queries but **must not re-persist**.
  - `runQuery()` currently always calls `persistFilters()`. Split it into
    `runQuery({ persist: true })` for local changes and `{ persist: false }` for applied
    remote ones.
- **Main window, on a remote filter change:** replaces `selected`, calls `renderFilters()`, and
  runs the query without persisting. Keep the open accordion group and scroll position: the
  existing `openGroup` / `anchorGroupHeader` logic covers this.
- **While the user is typing in a field:** don't overwrite it from a remote update. The
  prototype's `syncEditors()` skips `document.activeElement` for the same reason.

### 4.6 The pop-out window

- **Desktop (`desktop.py`):**
  - Pass a `js_api` object to `create_window` with `open_map_window()` and
    `close_map_window()`.
  - `open_map_window()` creates `webview.create_window("Radius Map", f"{base}/map", width=720, height=640, min_size=(480, 420))`
    the first time; on later calls it shows or focuses the existing window.
  - Closing the main window must also close the map window. pywebview keeps the process alive
    while any window is open, which would orphan the server thread's lifetime logic in `main()`.
  - **Verify on both platforms** that creating a window from a `js_api` call works with the
    macOS main-thread constraint described at the top of `desktop.py`. Check pywebview's
    documentation for the version pinned; if macOS misbehaves, this is the first place to look.
- **Browser mode (`afp serve`):** `window.open("/map", "afp-radius-map", "popup,width=720,height=640")`.
  If it returns null (popup blocked), fall back to the docked placement and say so in one line.
- ~~**Docked placement.**~~ **Removed.** It was built (a collapsible panel between the counter
  band and the results table) and then taken out: too much in one column. There is no fallback
  placement, so a blocked pop-up in browser mode now says so rather than docking.
  The 1180×760 floor in `desktop.py` is therefore unaffected by the map; re-verified against
  that comment's 20-category case with the panel gone (table top 596px of 760, no page scroll).
- **Placement memory.** The map window's size and position are remembered across sessions in
  `map_window.json` beside the filters, along with whether it was open — **not** the active
  rule, which is UI state for one session.
  - The numbers come from pywebview's `resized`/`moved` events, not from the page. Inside a
    webview there is no browser chrome, so JS reads the content box as the whole window, and
    restoring that as the window size would lose the title bar's height on every launch.
  - A saved position is only reused if a title-bar-sized point on it still falls on an attached
    screen, and the size is capped to that screen. The case this exists for is a second monitor
    that is no longer plugged in: the coordinates stay valid and the window opens where nothing
    can be seen or dragged.
  - If it was open at the last quit it reopens — but from the main window, at the end of
    `initWorkspace()`, not at startup: the map has nothing to draw before a cycle is loaded,
    and on a fresh install it would open over the About screen.
  - Desktop only. A browser blocks `window.open` outside a user gesture, so in browser mode the
    map is always opened by the button.

## 5. Edge cases

- **Both public and private off** means no constraint (`airport_use_scope`). The map must draw
  every airport in that case, not none.
- **Cities are scoped to the selected states** (`list_cities`). Removing a state can orphan a
  selected city; the rail already handles this, and the map simply follows.
- **Unknown saved centre:** `resolvableRadiusFilters()` already drops it on restore, so the map
  needs no handling beyond that.
- **Facilities with no position:** never drawn; counted in the legend.
- **Typed radius above 250 nm:** the slider's max extends to fit, and the box accepts any
  positive number.
- **Rings near ±180°** (Aleutians, Guam): `geodesicCircle()` produces continuous longitudes
  around the centre, so no seam split is needed. Check one Aleutian ring visually.
- **Airport ID vs ICAO:** centres are FAA LIDs (`WVI`, not `KWVI`), which matches today's
  behaviour. Accepting ICAO as an alias is out of scope (§8).
- **Large result sets:** nationwide with private on is about 19k points on one canvas. That is
  fine for drawing; keep hit-testing to points drawn in the current viewport.

## 6. Tests

Follow the existing style: pytest, plain-language names, and docstrings that say why.

- `tests/test_map_status.py`
  - **Parity:** for a table of filter states, the `selected` ids equal the ids from
    `apply_filters`. Cover:
    - one include;
    - two includes (OR);
    - include + exclude;
    - exclude only;
    - a `lat,lon` centre;
    - state only;
    - state + city + ring;
    - private on;
    - both use flags off.
  - **Precedence:** an airport in another state that is also inside an exclude ring is
    `filtered`, not `excluded`.
  - **Orphans:** a positioned facility is classified; a facility with no position is counted
    in `unplottable` under both include and exclude.
- `tests/test_web_api.py` additions:
  - `/api/map/points` is stable for one loaded cycle.
  - `/api/map/status` returns one code per point.
  - `/api/map/status` rejects an unresolvable centre the same way `/api/query` does.
- `tests/test_sync.py`:
  - The revision bumps on PUT.
  - The source is recorded.
  - `since` returns nothing newer when nothing has changed.
  - UI state is not written to `filters.json`.
- `tests/test_saved_filters.py`: `RadiusFilterIn.id` round-trips, and old files without `id`
  still load.
- `tests/test_filter_rail.py`-style static checks for `map.js` / CSS where useful, e.g. the
  vendor paths referenced in HTML exist in `package-data`.
- **Manual checklist (both platforms):**
  - open, close, and open again;
  - move and resize it, quit, relaunch: it comes back where it was, and only if it was open;
  - edit in the rail and watch the map; edit on the map and watch the rail;
  - drag the slider and watch the counter roll;
  - Esc;
  - try the map window at its 520×460 floor.

## 7. Suggested order of work (one PR each)

1. **Backend status.** `classify_points`, `/api/map/points`, `/api/map/status`, parity tests.
   No UI yet.
2. **Editable rules in the rail.** Rule ids, the inline editor, and the active rule within one
   window. Useful on its own even before the map.
3. **Map component, docked.** Vendored Leaflet and outlines, `map.js`, the docked panel,
   toolbar, clicks, hover, legend, Fit, plus the package-data change.
4. **Pop-out and sync.** `/map` page, the `js_api` window (desktop) and `window.open` (browser),
   `/api/sync` and `/api/ui-state`, echo suppression.
5. **Polish.** Placement memory, the min-size verification, About/README mention of the map.

## 8. Decisions left open

- ~~**Click-to-create.**~~ **Decided:** clicking stages a centre; **Add ring** or the rail's
  **Add** creates the ring. See §3.4. The prototype was changed to match, so it is the
  reference for this too.
- **Dragging rings.** Drag a ring centre to move it (snap to the nearest airport on release),
  or drag the edge to resize. Not in the prototype; a good follow-up.
- **Online basemap.** An optional tile layer when online, with the vendored outlines as the
  default and fallback.
- **ICAO aliases.** Accept `KWVI` as `WVI` in `/api/resolve-center`. It would need a lookup
  column from APT_BASE.
- ~~**Docked default.**~~ **Decided:** there is no docked placement. See §4.6.

## 9. How the prototype differs from the real app

It approximates on purpose; don't port these parts.

- **Data:** OurAirports, not NASR. "Public use" is guessed from 3-character IDs, frequency rows
  are sparser than NASR so entry counts run low, and smart-mode selection is not reproduced.
- **Filter logic:** computed in JS (`evaluate()`). It matched the app's Python radius predicates
  exactly in four test cases, but in the app this belongs server-side (§4.1).
- **Pop-out:** simulated as a draggable panel inside one page. There is no second window and no
  sync layer.
- **Leaflet:** loaded from cdnjs, with the CSS inlined. The app vendors both.
- **Export and groups:** not wired.
