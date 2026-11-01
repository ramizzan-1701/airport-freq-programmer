// The radius map.
//
// Shows every airport coloured by *why* it is in or out, and every
// radius rule as a ring you can draw and resize directly. The rail's
// typed form stays the manual path; this is the visual one, and both
// edit the same list of rules.
//
// What it is not: a second opinion about the filter. Every status comes
// from /api/map/status, which runs the same SQL apply_filters does. The
// map sits beside the counter it explains, so a disagreement between
// them would read as the counter being wrong.
//
// Two things here are performance decisions rather than style:
//
//   Points are drawn on one <canvas>, not as Leaflet markers. Nationwide
//   with private airports on is about 21,000 of them; that many SVG
//   nodes makes panning unusable.
//
//   The static points and the per-filter statuses come from separate
//   endpoints. The points are 3 MB and change only when the cycle does;
//   the statuses are 20 KB and change on every slider tick.

(function () {
  "use strict";

  const ACC = "#5b93c2";
  const OK = "#3FCB57";
  const WARN = "#E8407D";
  const OUTSIDE_GREY = "#6B737B";
  const FILTERED_GREY = "#3A444E";
  const INK = "#D6D4CC";
  const INK3 = "#8E9398";

  // The codes /api/map/status packs one per point, in /api/map/points
  // order. Single characters because this is what travels on every
  // filter change.
  const SELECTED = "S";
  const EXCLUDED = "E";
  const OUTSIDE = "O";
  const FILTERED = "F";
  const SCOPE = "X";

  /** What each dot colour means, in the one place that says it.
   *
   * Read by the hover and by the legend, which is the point: the legend
   * is the key the hover is read against, so the two phrasing the same
   * status differently -- "State/city filtered" beside the dot and
   * "Filtered out by state/city" under the cursor -- made you stop and
   * work out they were the same thing.
   *
   * "radius" rather than "ring" because that is the word the app uses
   * elsewhere: the rail's section is Geographic Radius.
   */
  const STATUS_TEXT = {
    [SELECTED]: "Selected",
    [EXCLUDED]: "Excluded",
    [OUTSIDE]: "Outside radius",
    [FILTERED]: "City/State filtered",
  };

  // Same radius the filter measures with (afp.query.geo.haversine_nm), so
  // a point drawn inside a ring really is inside it.
  const R_NM = 3440.065;

  /** Ring edges as a geodesic polygon.
   *
   * L.circle is a Mercator approximation: at the latitudes NASR covers
   * it puts the edge in the wrong place by enough to show an airport on
   * the wrong side of its own filter. Drawing the real circle costs 144
   * points and removes the question.
   */
  function geodesicCircle(lat, lon, nm, n = 144) {
    const d = nm / R_NM;
    const rad = Math.PI / 180;
    const p1 = lat * rad;
    const l1 = lon * rad;
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const b = (2 * Math.PI * i) / n;
      const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
      const l2 =
        l1 +
        Math.atan2(
          Math.sin(b) * Math.sin(d) * Math.cos(p1),
          Math.cos(d) - Math.sin(p1) * Math.sin(p2)
        );
      // Longitudes stay continuous around the centre, so a ring over the
      // Aleutians or Guam needs no seam handling.
      pts.push([p2 / rad, l2 / rad]);
    }
    return pts;
  }

  function haversineNm(lat1, lon1, lat2, lon2) {
    const rad = Math.PI / 180;
    const dLat = (lat2 - lat1) * rad;
    const dLon = (lon2 - lon1) * rad;
    const a =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
    return 2 * R_NM * Math.asin(Math.sqrt(a));
  }

  /** The map component. One instance per window; the docked panel and
   * the pop-out both mount this. */
  function createRadiusMap(options) {
    const host = options.host;
    const hooks = options.hooks; // read and write the shared filter state

    let points = null; // static for a cycle: {id,name,city,state,lat,lon,...}
    let statuses = ""; // one code per point, in points order
    // How many of each point's frequencies survive the frequency-level
    // filters, in points order. Read on hover only -- a dot's colour is
    // about where it is, this is about whether it has anything to give.
    let freqMatches = null;
    let unplottable = 0;
    let rings = []; // resolved rules from the server
    let drawn = []; // [point, x, y] for everything on screen, for hit-testing
    let pointsCycle = null;

    host.innerHTML = "";
    host.classList.add("radius-map");

    const head = document.createElement("div");
    head.className = "map-head";
    const title = document.createElement("span");
    title.className = "map-title";
    title.textContent = "Radius map";
    const counter = document.createElement("span");
    counter.className = "map-counter";
    const headActions = document.createElement("span");
    headActions.className = "map-head-actions";
    const fitBtn = button("Fit", "Zoom to the rings, or to what is selected");
    const closeBtn = button("Close", "Hide the map");
    headActions.append(fitBtn, closeBtn);
    head.append(title, counter, headActions);
    host.appendChild(head);

    const toolbar = document.createElement("div");
    toolbar.className = "map-toolbar";
    host.appendChild(toolbar);

    const chipRow = document.createElement("div");
    chipRow.className = "map-chips";
    host.appendChild(chipRow);

    const canvasHost = document.createElement("div");
    canvasHost.className = "map-canvas";
    host.appendChild(canvasHost);

    function button(text, tipText) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "btn btn-small";
      b.textContent = text;
      if (tipText) b.title = tipText;
      return b;
    }

    // ---------- Leaflet ----------

    const map = L.map(canvasHost, {
      zoomControl: true,
      attributionControl: false,
      // The point canvas is redrawn per frame from container coordinates
      // and cannot follow an animated zoom, so it would lag behind by a
      // whole transition every time.
      zoomAnimation: false,
      fadeAnimation: false,
      markerZoomAnimation: false,
      minZoom: 3,
      maxZoom: 12,
      worldCopyJump: false,
      boxZoom: false,
    });
    map.setView([39.5, -98.35], 4); // CONUS until there is anything better to show

    // Leaflet caches its container's size. Without this, resizing the
    // window leaves the new strip blank and every click landing on
    // whatever point used to be under it.
    if (window.ResizeObserver) {
      let first = true;
      new ResizeObserver(() => {
        if (first) {
          // Fires once on observe, before anything has moved.
          first = false;
          return;
        }
        map.invalidateSize({ animate: false });
        queueDraw();
      }).observe(canvasHost);
    }

    map.createPane("states").style.zIndex = 300;
    map.createPane("rings").style.zIndex = 400;
    const stateRenderer = L.svg({ pane: "states", padding: 0.5 });
    const ringRenderer = L.svg({ pane: "rings", padding: 0.5 });
    map.addLayer(stateRenderer);
    map.addLayer(ringRenderer);

    // The exclusion hatch lives in the ring renderer's own <svg>, which
    // is the only place a fill-url can resolve from.
    (function addHatch() {
      const svg = ringRenderer._container;
      const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
      defs.innerHTML =
        '<pattern id="afp-hatch" patternUnits="userSpaceOnUse" width="7" height="7"' +
        ' patternTransform="rotate(45)">' +
        `<rect width="7" height="7" fill="${WARN}" fill-opacity="0.06"></rect>` +
        `<line x1="0" y1="0" x2="0" y2="7" stroke="${WARN}" stroke-opacity="0.55"` +
        ' stroke-width="1.6"></line></pattern>';
      svg.insertBefore(defs, svg.firstChild);
    })();

    const stateLayers = new Map();
    let statesLoaded = false;

    async function loadStates() {
      if (statesLoaded) return;
      const res = await fetch("/static/vendor/us-states.json");
      const states = await res.json();
      for (const s of states) {
        // Stored [lon, lat]; Leaflet wants [lat, lon].
        const latlngs = s.p.map((poly) => poly.map((ring) => ring.map(([x, y]) => [y, x])));
        const layer = L.polygon(latlngs, {
          renderer: stateRenderer,
          interactive: false,
          weight: 1,
          color: "#34414D",
          fillColor: "#161D24",
          fillOpacity: 1,
        });
        layer.addTo(map);
        stateLayers.set(s.c, layer);
      }
      statesLoaded = true;
    }

    function styleStates() {
      const chosen = hooks.selectedStates();
      for (const [code, layer] of stateLayers) {
        const on = chosen.has(code);
        layer.setStyle(
          on
            ? { color: ACC, weight: 1.6, fillColor: "#1B2A38", fillOpacity: 1 }
            : {
                color: "#34414D",
                weight: 1,
                // Unselected states dim once any state is chosen, so the
                // selection reads as a shape rather than a list.
                fillColor: chosen.size ? "#13191F" : "#161D24",
                fillOpacity: 1,
              }
        );
        if (on) layer.bringToFront();
      }
    }

    // ---------- rings ----------

    let ringLayers = [];

    /** The rules as they are right now, not as the last answer had them.
     *
     * `rings` is what the server resolved for the most recent query, so
     * it is a debounce and a round trip behind: a dragged circle would
     * stutter along after the cursor, a new one would stay blue for a
     * moment after Add, and a deleted one would linger. The rail's copy
     * is current and is what the user is actually editing, so it wins
     * on size, mode and membership.
     *
     * The server is still needed for position: a centre typed into the
     * rail has no coordinates on this side until it is resolved, and a
     * rule we cannot place yet is simply not drawn -- it appears on the
     * next answer.
     */
    function liveRings() {
      const resolved = new Map(rings.map((r) => [r.id, r]));
      const out = [];
      for (const rule of hooks.rules()) {
        const fallback = resolved.get(rule.id);
        const lat = rule.lat == null ? fallback && fallback.lat : rule.lat;
        const lon = rule.lon == null ? fallback && fallback.lon : rule.lon;
        if (lat == null || lon == null) continue;
        out.push({
          id: rule.id,
          center: rule.center,
          lat,
          lon,
          radius_nm: rule.radius_nm,
          mode: rule.mode,
        });
      }
      return out;
    }

    function drawRings() {
      ringLayers.forEach((l) => map.removeLayer(l));
      ringLayers = [];
      const activeId = hooks.activeId();
      const staged = hooks.staged();
      const current = liveRings();

      // Excludes after includes so the hatch sits on top; the rule being
      // edited above everything.
      const ordered = [
        ...current.filter((r) => r.mode === "include"),
        ...current.filter((r) => r.mode === "exclude"),
      ];
      const active = ordered.find((r) => r.id === activeId);
      if (active) {
        ordered.splice(ordered.indexOf(active), 1);
        ordered.push(active);
      }

      for (const r of ordered) {
        const on = r.id === activeId;
        const dim = !!activeId && !on;
        const style =
          r.mode === "include"
            ? {
                color: OK,
                fillColor: OK,
                fillOpacity: on ? 0.13 : dim ? 0.04 : 0.07,
              }
            : { color: WARN, fillColor: "url(#afp-hatch)", fillOpacity: dim ? 0.45 : 1 };
        ringLayers.push(
          L.polygon(geodesicCircle(r.lat, r.lon, r.radius_nm), {
            renderer: ringRenderer,
            interactive: false,
            weight: on ? 2.6 : 1.6,
            opacity: dim ? 0.45 : 1,
            ...style,
          }).addTo(map)
        );
      }

      // What Add would create, at the size the slider currently shows.
      // Accent blue throughout, whatever the mode toggle says: blue
      // means "being chosen", and taking the real colour only on Add is
      // what makes the commit visible.
      if (staged) {
        ringLayers.push(
          L.polygon(geodesicCircle(staged.lat, staged.lon, hooks.draftNm()), {
            renderer: ringRenderer,
            interactive: false,
            color: ACC,
            weight: 2,
            dashArray: "6 5",
            fillColor: ACC,
            fillOpacity: 0.1,
          }).addTo(map)
        );
      }
    }

    // ---------- points ----------

    const canvas = document.createElement("canvas");
    canvas.className = "map-points";
    map.getContainer().appendChild(canvas);
    const ctx = canvas.getContext("2d");
    let drawQueued = false;

    function queueDraw() {
      if (drawQueued) return;
      drawQueued = true;
      requestAnimationFrame(() => {
        drawQueued = false;
        drawPoints();
      });
    }

    function drawPoints() {
      const size = map.getSize();
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== size.x * dpr || canvas.height !== size.y * dpr) {
        canvas.width = size.x * dpr;
        canvas.height = size.y * dpr;
        canvas.style.width = size.x + "px";
        canvas.style.height = size.y + "px";
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.x, size.y);
      drawn = [];
      if (!points) return;

      const z = map.getZoom();
      const k = Math.max(0, z - 6);
      const rad = {
        [SELECTED]: 2.6 + k * 0.7,
        [EXCLUDED]: 2.6 + k * 0.6,
        [OUTSIDE]: 1.8 + k * 0.45,
        [FILTERED]: 1.2 + k * 0.25,
      };

      // Painted least to most important, so a selected airport is never
      // hidden under a filtered one.
      const order = [FILTERED, OUTSIDE, EXCLUDED, SELECTED];
      const buckets = { [FILTERED]: [], [OUTSIDE]: [], [EXCLUDED]: [], [SELECTED]: [] };
      const cities = hooks.selectedCities();
      const states = hooks.selectedStates();

      for (let i = 0; i < points.length; i++) {
        const code = statuses[i];
        // Out of scope is not drawn at all: public/private is the one
        // filter that removes a point rather than recolouring it.
        if (!code || code === SCOPE) continue;
        const p = points[i];
        const pt = map.latLngToContainerPoint([p.lat, p.lon]);
        if (pt.x < -20 || pt.y < -20 || pt.x > size.x + 20 || pt.y > size.y + 20) continue;
        buckets[code].push([p, pt, freqMatches ? freqMatches[i] : null]);
      }

      for (const code of order) {
        for (const [p, pt, matches] of buckets[code]) {
          const r = rad[code];
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
          if (code === SELECTED) {
            ctx.fillStyle = OK;
            ctx.fill();
          } else if (code === OUTSIDE) {
            ctx.fillStyle = OUTSIDE_GREY;
            ctx.fill();
          } else if (code === FILTERED) {
            ctx.fillStyle = FILTERED_GREY;
            ctx.fill();
          } else {
            // Excluded: hollow with a slash, so "was in range and then
            // ruled out" does not read as just another dot.
            ctx.fillStyle = "#11151A";
            ctx.fill();
            ctx.strokeStyle = WARN;
            ctx.lineWidth = 1.4;
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(pt.x - r * 0.75, pt.y + r * 0.75);
            ctx.lineTo(pt.x + r * 0.75, pt.y - r * 0.75);
            ctx.stroke();
          }
          const cityMatch =
            cities.size &&
            cities.has(p.city) &&
            (!states.size || states.has(p.state));
          if (cityMatch) {
            ctx.beginPath();
            ctx.arc(pt.x, pt.y, r + 4, 0, Math.PI * 2);
            ctx.strokeStyle = ACC;
            ctx.lineWidth = 1.4;
            ctx.stroke();
          }
          drawn.push([p, pt.x, pt.y, code, matches]);
        }
      }

      // Labels once there is room for them.
      ctx.font = "500 11px 'IBM Plex Mono', ui-monospace, monospace";
      ctx.textBaseline = "middle";
      if (z >= 8) {
        for (const code of z >= 9 ? [OUTSIDE, EXCLUDED, SELECTED] : [SELECTED, EXCLUDED]) {
          for (const [p, pt] of buckets[code]) {
            ctx.fillStyle = code === SELECTED ? INK : code === EXCLUDED ? WARN : INK3;
            ctx.fillText(p.id, pt.x + rad[code] + 4, pt.y);
          }
        }
      }

      drawCentres();
    }

    function drawCentres() {
      const activeId = hooks.activeId();
      const staged = hooks.staged();

      if (staged) {
        const pt = map.latLngToContainerPoint([staged.lat, staged.lon]);
        ctx.save();
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 9, 0, Math.PI * 2);
        ctx.strokeStyle = ACC;
        ctx.lineWidth = 1.6;
        ctx.stroke();
        ctx.restore();
        crosshair(pt, ACC);
        labelBox(pt, staged.center.includes(",") ? "PIN" : staged.center, ACC, true);
      }

      ctx.font = "600 11px 'IBM Plex Mono', ui-monospace, monospace";
      // Live too: the centre's label carries the radius.
      const current = liveRings();
      const ordered = [...current.filter((r) => r.id !== activeId), ...current.filter((r) => r.id === activeId)];
      for (const r of ordered) {
        const pt = map.latLngToContainerPoint([r.lat, r.lon]);
        const colour = r.mode === "include" ? OK : WARN;
        const on = r.id === activeId;
        if (on) {
          ctx.beginPath();
          ctx.arc(pt.x, pt.y, 10, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(91,147,194,.28)";
          ctx.fill();
          ctx.strokeStyle = ACC;
          ctx.lineWidth = 2;
          ctx.stroke();
        }
        // A plus for include, a cross for exclude: the mark says what
        // the ring does without reading its colour.
        ctx.strokeStyle = colour;
        ctx.lineWidth = 2;
        ctx.beginPath();
        if (r.mode === "include") {
          ctx.moveTo(pt.x - 6, pt.y);
          ctx.lineTo(pt.x + 6, pt.y);
          ctx.moveTo(pt.x, pt.y - 6);
          ctx.lineTo(pt.x, pt.y + 6);
        } else {
          ctx.moveTo(pt.x - 5, pt.y - 5);
          ctx.lineTo(pt.x + 5, pt.y + 5);
          ctx.moveTo(pt.x + 5, pt.y - 5);
          ctx.lineTo(pt.x - 5, pt.y + 5);
        }
        ctx.stroke();
        const label = `${r.center.includes(",") ? "PIN" : r.center} ${r.radius_nm}nm`;
        labelBox(pt, label, on ? ACC : colour, false, on);
      }
    }

    function crosshair(pt, colour) {
      ctx.beginPath();
      ctx.moveTo(pt.x - 7, pt.y);
      ctx.lineTo(pt.x + 7, pt.y);
      ctx.moveTo(pt.x, pt.y - 7);
      ctx.lineTo(pt.x, pt.y + 7);
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    }

    function labelBox(pt, text, colour, below, filled) {
      ctx.font = "600 11px 'IBM Plex Mono', ui-monospace, monospace";
      const w = ctx.measureText(text).width;
      const y = below ? pt.y + 6 : pt.y - 20;
      ctx.fillStyle = filled ? "#22364A" : "rgba(17,21,26,.9)";
      ctx.fillRect(pt.x + 11, y, w + 10, 16);
      ctx.save();
      if (below) ctx.setLineDash([3, 2]);
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1;
      ctx.strokeRect(pt.x + 11.5, y + 0.5, w + 9, 15);
      ctx.restore();
      ctx.fillStyle = filled ? INK : colour;
      ctx.fillText(text, pt.x + 16, y + 8);
    }

    map.on("move zoom resize viewreset", queueDraw);

    // ---------- hit testing ----------
    //
    // Over the points actually drawn this frame, so it costs the
    // viewport rather than the 21,000 points behind it.

    function hitPoint(pt) {
      let best = null;
      let bd = 64; // 8px, squared
      for (let i = drawn.length - 1; i >= 0; i--) {
        const [p, x, y] = drawn[i];
        const d = (x - pt.x) ** 2 + (y - pt.y) ** 2;
        if (d < bd) {
          bd = d;
          best = drawn[i];
        }
      }
      return best;
    }

    function hitRingCentre(pt) {
      const current = liveRings();
      for (let i = current.length - 1; i >= 0; i--) {
        const r = current[i];
        const p = map.latLngToContainerPoint([r.lat, r.lon]);
        if ((p.x - pt.x) ** 2 + (p.y - pt.y) ** 2 <= 144) return r; // 12px
      }
      return null;
    }

    // ---------- toolbar ----------
    //
    // With a rule active these controls edit it. With nothing active
    // they set what the next ring will be, and a staged centre makes
    // that visible as a preview ring the slider resizes.

    const toolLabel = document.createElement("span");
    toolLabel.className = "map-tool-label";

    const modeSeg = document.createElement("div");
    modeSeg.className = "seg";
    const modeButtons = {};
    for (const value of ["include", "exclude"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "seg-btn";
      b.textContent = value === "include" ? "Include" : "Exclude";
      b.addEventListener("click", () => setMode(value));
      modeButtons[value] = b;
      modeSeg.appendChild(b);
    }

    const slider = document.createElement("input");
    slider.type = "range";
    slider.className = "map-slider";
    slider.min = "1";
    slider.max = "250";
    slider.step = "1";
    slider.setAttribute("aria-label", "Radius in nautical miles");

    const nmBox = document.createElement("input");
    nmBox.type = "number";
    nmBox.className = "radius-nm";
    nmBox.min = "1";
    nmBox.setAttribute("aria-label", "Radius in nautical miles");
    const nmUnit = document.createElement("span");
    nmUnit.className = "map-unit";
    nmUnit.textContent = "nm";

    const row1 = document.createElement("div");
    row1.className = "map-tool-row";
    row1.append(toolLabel, modeSeg, slider, nmBox, nmUnit);

    const centreInput = document.createElement("input");
    centreInput.className = "radius-center";
    centreInput.placeholder = "Airport ID or lat,lon";
    centreInput.setAttribute("aria-label", "Center");

    const addBtn = button("Add ring", "Create a ring at this center");
    const deleteBtn = button("Delete ring");
    const doneBtn = button("Done");
    const error = document.createElement("div");
    error.className = "field-error";
    error.setAttribute("role", "alert");
    error.hidden = true;

    const row2 = document.createElement("div");
    row2.className = "map-tool-row";
    row2.append(centreInput, addBtn, deleteBtn, doneBtn);
    toolbar.append(row1, row2, error);

    function activeRule() {
      return liveRings().find((r) => r.id === hooks.activeId()) || null;
    }

    function showError(message) {
      error.textContent = message;
      error.hidden = false;
      centreInput.classList.add("invalid");
    }
    function clearError() {
      error.hidden = true;
      centreInput.classList.remove("invalid");
    }
    centreInput.addEventListener("input", () => {
      clearError();
      const staged = hooks.staged();
      // Typing over it abandons the staged centre: the field no longer
      // says what the marker on the map is pointing at.
      if (staged && centreInput.value.trim() !== staged.center) hooks.setStaged(null);
    });

    function syncToolbar() {
      const active = activeRule();
      const staged = hooks.staged();
      const nm = active ? active.radius_nm : hooks.draftNm();
      const mode = active ? active.mode : hooks.draftMode();

      toolLabel.textContent = active
        ? `Editing ${active.center.includes(",") ? "pin" : active.center}`
        : "New ring";
      toolLabel.classList.toggle("editing", !!active);

      for (const [value, b] of Object.entries(modeButtons)) {
        b.classList.toggle("on", value === mode);
        b.setAttribute("aria-pressed", String(value === mode));
      }
      // A typed radius beyond the slider's range extends it rather than
      // being silently clamped back down.
      slider.max = String(Math.max(250, Math.ceil(nm)));
      slider.value = String(nm);
      if (document.activeElement !== nmBox) nmBox.value = nm;
      if (document.activeElement !== centreInput) {
        centreInput.value = active ? active.center : staged ? staged.center : "";
      }

      addBtn.hidden = !!active;
      deleteBtn.hidden = !active;
      doneBtn.hidden = !active;
    }

    function setNm(value) {
      if (!(value > 0)) return;
      const nm = Math.round(value * 10) / 10;
      const active = activeRule();
      if (active) {
        hooks.updateRule(active.id, { radius_nm: nm });
        return;
      }
      hooks.setDraft({ radius_nm: nm });
      syncToolbar();
      // With a centre staged the slider is sizing something visible, so
      // it has to redraw -- otherwise the preview stays at the size it
      // was first drawn at.
      if (hooks.staged()) {
        drawRings();
        renderHint();
        queueDraw();
      }
    }
    slider.addEventListener("input", (e) => setNm(Number(e.target.value)));
    nmBox.addEventListener("input", (e) => setNm(parseFloat(e.target.value)));

    function setMode(value) {
      const active = activeRule();
      if (active) {
        hooks.updateRule(active.id, { mode: value });
        return;
      }
      hooks.setDraft({ mode: value });
      syncToolbar();
      if (hooks.staged()) renderHint();
    }

    async function submitCentre() {
      const active = activeRule();
      const typed = centreInput.value.trim();
      if (active) {
        if (typed && typed !== active.center) {
          const problem = await hooks.updateRule(active.id, { center: typed });
          if (problem) showError(problem);
        }
        return;
      }
      if (!typed) return showError("Enter an airport ID or lat,lon.");
      const problem = await hooks.addRule({
        center: typed,
        radius_nm: hooks.draftNm(),
        mode: hooks.draftMode(),
      });
      if (problem) showError(problem);
    }
    addBtn.addEventListener("click", submitCentre);
    centreInput.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      submitCentre();
    });
    deleteBtn.addEventListener("click", () => {
      const active = activeRule();
      if (active) hooks.removeRule(active.id);
    });
    doneBtn.addEventListener("click", () => hooks.setActive(null));

    // ---------- chips ----------
    //
    // The same rules as the rail. No inline editor here: the toolbar is
    // the editor, and two of them would disagree about which is live.

    function renderChips() {
      chipRow.innerHTML = "";
      for (const r of liveRings()) {
        const label = `${r.mode === "include" ? "Within" : "Exclude"} ${r.radius_nm}nm of ${r.center}`;

        // A span, not a button: it carries a remove button of its own,
        // and a button inside a button is not valid markup -- the inner
        // one stops being reachable by keyboard.
        const chip = document.createElement("span");
        chip.className = "map-chip" + (r.id === hooks.activeId() ? " on" : "");
        chip.setAttribute("role", "button");
        chip.tabIndex = 0;
        chip.title = "Click to edit this ring";

        const dot = document.createElement("span");
        dot.className = "radius-swatch";
        dot.dataset.mode = r.mode;
        chip.appendChild(dot);

        const text = document.createElement("span");
        text.className = "map-chip-text";
        text.textContent = label;
        chip.appendChild(text);

        const open = () => hooks.setActive(r.id);
        chip.addEventListener("click", open);
        chip.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          event.preventDefault();
          open();
        });

        const rm = document.createElement("button");
        rm.type = "button";
        rm.className = "map-chip-remove";
        rm.textContent = "✕";
        rm.title = "Remove this ring";
        rm.setAttribute("aria-label", `Remove ${label}`);
        rm.addEventListener("click", (event) => {
          // The chip's own click would open the rule just removed.
          event.stopPropagation();
          hooks.removeRule(r.id);
        });
        chip.appendChild(rm);

        chipRow.appendChild(chip);
      }
    }

    // ---------- badges, hint and legend ----------

    const badges = document.createElement("div");
    badges.className = "map-badges";
    map.getContainer().appendChild(badges);

    const hint = document.createElement("div");
    hint.className = "map-hint";
    map.getContainer().appendChild(hint);

    const legend = document.createElement("div");
    legend.className = "map-legend";
    map.getContainer().appendChild(legend);

    /** The filters that change what is on the map without colouring
     * anything, named so their effect is not a mystery.
     *
     * Only two qualify. The selected states are already drawn -- their
     * outlines are lifted in --acc -- and the frequency categories
     * change no dot on the map at all, which is why there is no badge
     * for them: it would name a filter and point at nothing. These two
     * are different. Cities tint a halo so small it is easy to miss,
     * and the use scope removes points outright: on a default nationwide
     * map that is about 14,000 airports simply not there, with nothing
     * on screen to say so.
     */
    function renderBadges() {
      const parts = [];

      const cities = [...hooks.selectedCities()];
      if (cities.length) {
        cities.sort();
        parts.push(
          cities.length > 2
            ? `City: ${cities.slice(0, 2).join(", ")} +${cities.length - 2}`
            : `City: ${cities.join(", ")}`
        );
      }

      // Neither ticked is no constraint, not an empty map -- the same
      // reading airport_use_scope gives it -- so it earns no badge
      // either. Both ticked constrains nothing, so nor does that.
      const { public: pub, private: priv } = hooks.useScope();
      if (pub && !priv) parts.push("Public only");
      else if (priv && !pub) parts.push("Private only");

      badges.innerHTML = "";
      badges.hidden = !parts.length;
      for (const text of parts) {
        const badge = document.createElement("span");
        badge.className = "map-badge";
        badge.textContent = text;
        badges.appendChild(badge);
      }
    }

    function renderHint() {
      const active = activeRule();
      const staged = hooks.staged();
      hint.textContent = active
        ? "Slider resizes this ring. Click open map or press Esc when done."
        : staged
          ? `${staged.center.includes(",") ? "Pin" : staged.center} at ${hooks.draftNm()}nm ` +
            "— drag the slider, then press Add ring"
          : "Click an airport to choose it as a center, or a ring's center to edit it";
    }

    function renderLegend() {
      const marks = [
        [OK, STATUS_TEXT[SELECTED]],
        [WARN, STATUS_TEXT[EXCLUDED]],
        [OUTSIDE_GREY, STATUS_TEXT[OUTSIDE]],
        [FILTERED_GREY, STATUS_TEXT[FILTERED]],
        // Not a status of its own: an --acc halo drawn around a dot
        // that already has one, so it has no entry in STATUS_TEXT.
        [ACC, "City match"],
      ];
      legend.innerHTML = "";
      for (const [colour, text] of marks) {
        const item = document.createElement("span");
        item.className = "map-legend-item";
        const dot = document.createElement("span");
        dot.className = "map-legend-dot";
        dot.style.background = colour;
        item.append(dot, document.createTextNode(text));
        legend.appendChild(item);
      }
      if (unplottable) {
        const note = document.createElement("span");
        note.className = "map-legend-item muted";
        // Counted rather than dropped: a facility that cannot be drawn
        // still reaches the export, and losing it here silently would
        // make the map disagree with the file.
        note.textContent = `${unplottable} not on map (no position)`;
        legend.appendChild(note);
      }
    }

    // ---------- hover ----------

    const tip = document.createElement("div");
    tip.className = "map-tip";
    tip.hidden = true;
    map.getContainer().appendChild(tip);

    map.on("mousemove", (e) => {
      const ring = hitRingCentre(e.containerPoint);
      const hit = ring ? null : hitPoint(e.containerPoint);
      map.getContainer().style.cursor = ring || hit ? "pointer" : "crosshair";
      if (!ring && !hit) {
        tip.hidden = true;
        return;
      }
      if (ring) {
        const label = `${ring.mode === "include" ? "Within" : "Exclude"} ${ring.radius_nm}nm of ${ring.center}`;
        tip.innerHTML =
          `<b>${escapeHtml(label)}</b><br><span class="muted">` +
          (ring.id === hooks.activeId()
            ? "Editing. Drag the slider to resize."
            : "Click to edit this ring") +
          "</span>";
      } else {
        const [p, , , code, matches] = hit;
        const use = p.public_use === null ? "facility" : p.public_use ? "public" : "private";
        // The dot says where this point is. It does not say whether
        // the point gives you anything: category, site type and the
        // rest filter frequencies, and the status never saw them, so an
        // airport can sit squarely inside a ring and contribute
        // nothing. The ratio is where that gets told.
        //
        // Always a ratio, including "11 of 11 freq". Showing it only
        // when the two differ reads as less noise and is: it makes the
        // line's shape change between one hover and the next, so the
        // eye has to re-find the number instead of reading the same
        // place twice.
        //
        // Red whenever nothing passes -- including a point that had no
        // frequencies to begin with, which is just as worthless and is
        // the same question being asked.
        const total = p.freq_count;
        const kept = matches == null ? total : matches;
        const freq = `${kept} of ${total} freq`;
        const freqHtml = kept
          ? `<span class="muted">${freq}</span>`
          : `<span class="map-tip-none">${freq}</span>`;
        tip.innerHTML =
          `<b>${escapeHtml(p.id)}</b> ${escapeHtml(p.name)}<br>` +
          `<span class="muted">${escapeHtml(p.city)}${p.state ? ", " + escapeHtml(p.state) : ""}` +
          ` · ${use} · </span>` +
          freqHtml +
          `<br><span class="muted">${STATUS_TEXT[code] || ""}</span>` +
          '<br><span class="muted">Click to choose this as a ring center</span>';
      }
      const box = map.getSize();
      tip.hidden = false;
      tip.style.left = Math.min(e.containerPoint.x + 14, box.x - tip.offsetWidth - 8) + "px";
      tip.style.top = Math.min(e.containerPoint.y + 14, box.y - tip.offsetHeight - 8) + "px";
    });
    map.on("mouseout", () => {
      tip.hidden = true;
    });

    function escapeHtml(value) {
      return String(value).replace(/[&<>"]/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]
      );
    }

    // ---------- clicks ----------

    map.on("click", (e) => {
      const ring = hitRingCentre(e.containerPoint);
      if (ring) {
        hooks.setActive(ring.id);
        return;
      }
      const hit = hitPoint(e.containerPoint);
      if (!hit && hooks.activeId()) {
        hooks.setActive(null);
        return;
      }
      const centre = hit
        ? hit[0].id
        : `${e.latlng.lat.toFixed(4)},${e.latlng.lng.toFixed(4)}`;
      const existing = liveRings().find((r) => r.center === centre);
      if (existing) {
        hooks.setActive(existing.id);
        return;
      }
      const staged = hooks.staged();
      // Clicking the staged centre again clears it, so a mis-click is
      // undone the same way it was made.
      if (staged && staged.center === centre) {
        hooks.setStaged(null);
        return;
      }
      hooks.setStaged({
        center: centre,
        lat: hit ? hit[0].lat : e.latlng.lat,
        lon: hit ? hit[0].lon : e.latlng.lng,
      });
    });

    // ---------- fit ----------

    function fitView() {
      let bounds = null;
      const grow = (b) => {
        bounds = bounds ? bounds.extend(b) : L.latLngBounds(b.getSouthWest(), b.getNorthEast());
      };
      for (const l of ringLayers) grow(l.getBounds());
      if (!bounds) {
        for (const code of hooks.selectedStates()) {
          const layer = stateLayers.get(code);
          if (layer) grow(layer.getBounds());
        }
      }
      if (!bounds && points) {
        // Nothing drawn to frame: fall back to whatever the city filter
        // matched, so Fit still answers "where am I looking".
        const cities = hooks.selectedCities();
        const matching = points.filter((p) => cities.has(p.city));
        if (matching.length) {
          grow(L.latLngBounds(matching.map((p) => [p.lat, p.lon])));
        }
      }
      if (bounds) map.fitBounds(bounds.pad(0.12));
      else map.setView([39.5, -98.35], 4); // CONUS
    }
    fitBtn.addEventListener("click", fitView);

    function redraw() {
      syncToolbar();
      renderChips();
      renderBadges();
      renderHint();
      renderLegend();
      styleStates();
      drawRings();
      queueDraw();
    }

    return {
      map,
      loadStates,
      styleStates,
      drawRings,
      queueDraw,
      redraw,
      fitView,
      syncToolbar,
      renderChips,
      renderHint,
      renderLegend,
      hitPoint,
      hitRingCentre,
      geodesicCircle,
      haversineNm,
      setCounter(text, level) {
        counter.textContent = text;
        counter.className = "map-counter " + (level || "");
      },
      /** What a slider tick needs and no more.
       *
       * redraw() also restyles the state outlines and rebuilds the
       * legend, neither of which a radius change touches. The ring
       * geometry and the centre's label (which carries the radius) do.
       */
      liveRedraw() {
        syncToolbar();
        drawRings();
        renderChips();
        renderHint();
        queueDraw();
      },
      invalidateSize() {
        map.invalidateSize();
        queueDraw();
      },
      elements: { head, toolbar, chipRow, counter, fitBtn, closeBtn, canvasHost },
      setPoints(payload) {
        points = payload.points;
        pointsCycle = payload.cycle_date;
      },
      setStatus(payload) {
        statuses = payload.statuses;
        unplottable = payload.unplottable;
        freqMatches = payload.freq_matches || null;
        rings = payload.rings;
      },
      get points() {
        return points;
      },
      get rings() {
        return liveRings();
      },
      get unplottable() {
        return unplottable;
      },
      get cycle() {
        return pointsCycle;
      },
      get drawnCount() {
        return drawn.length;
      },
      statusTextFor(code) {
        return STATUS_TEXT[code] || "";
      },
    };
  }

  window.AfpRadiusMap = { create: createRadiusMap, geodesicCircle, haversineNm };
})();
