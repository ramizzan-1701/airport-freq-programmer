// ---------- the radius map, in a window of its own ----------
//
// This is the whole application for /map. It owns no filters of its
// own: the server holds them, this window keeps a working copy, and
// /api/sync reconciles the two. map.js draws; this decides what.
//
// The one rule that keeps the two windows from fighting: a change this
// window caused comes back on the next poll, and applying it again
// would re-persist it, bump the revision, and bounce it to the other
// window forever. So every write carries CLIENT_ID and every poll
// ignores anything stamped with it.

(() => {
  "use strict";

  // Per window, per load. Two map windows would be two clients, which
  // is the correct reading of "who wrote this".
  const CLIENT_ID = `map-${Math.random().toString(36).slice(2, 10)}`;
  const POLL_MS = 400;

  let filters = null; // the full FilterStateIn, as the server has it
  let rules = []; // filters.radius_filters, with ids and resolved positions
  let activeId = null;
  let staged = null;
  let draft = { radius_nm: 25, mode: "include" };

  let filtersRevision = -1;
  let uiRevision = -1;
  let pointsCycle = null;
  let radiusMap = null;
  let queryToken = 0;

  async function api(path, options = {}) {
    return fetch(path, {
      headers: { "Content-Type": "application/json", "X-Client-Id": CLIENT_ID },
      ...options,
    });
  }

  // ---------- the filter payload ----------

  /** What /api/query and /api/map/status take.
   *
   * lat/lon are cached per rule so rings can be drawn without resolving
   * a centre again; they are display-only and the server does not take
   * them, so they are stripped here rather than on the way in.
   */
  function payload() {
    const body = { ...filters };
    body.radius_filters = rules.map((r) => ({
      id: r.id,
      center: r.center,
      radius_nm: r.radius_nm,
      mode: r.mode,
    }));
    return body;
  }

  function persist() {
    api("/api/filters", { method: "PUT", body: JSON.stringify(payload()) }).catch(() => {});
  }

  function pushActive() {
    api("/api/ui-state", {
      method: "PUT",
      body: JSON.stringify({ active_radius_id: activeId }),
    }).catch(() => {});
  }

  // ---------- applying what the server says ----------

  function newRuleId() {
    return Math.random().toString(36).slice(2, 8);
  }

  /** Takes the server's filter state as the truth.
   *
   * Resolved positions are kept across the swap where the centre has
   * not changed: they are this window's cache, the server neither
   * stores nor returns them, and dropping them would blank every ring
   * until each centre had been resolved again.
   */
  function applyFilters(next) {
    const positions = new Map(rules.map((r) => [r.id, r]));
    filters = next;
    rules = (next.radius_filters || []).map((r) => {
      const rule = { ...r };
      if (!rule.id) rule.id = newRuleId();
      const had = positions.get(rule.id);
      if (had && had.center === rule.center) {
        rule.lat = had.lat;
        rule.lon = had.lon;
      }
      return rule;
    });
    resolveMissingCentres();
  }

  /** Fills in positions for rules this window has not placed yet.
   *
   * A rule added in the main window arrives as a centre and a radius
   * and nothing else. Until it is resolved it cannot be drawn, so it is
   * simply absent from the map rather than drawn in the wrong place.
   */
  async function resolveMissingCentres() {
    const missing = rules.filter((r) => r.lat == null || r.lon == null);
    if (!missing.length) return;
    for (const rule of missing) {
      try {
        const res = await api(`/api/resolve-center?center=${encodeURIComponent(rule.center)}`);
        if (!res.ok) continue;
        const body = await res.json();
        rule.lat = body.lat;
        rule.lon = body.lon;
      } catch {
        // Leave it unplaced; the next poll tries again.
      }
    }
    if (radiusMap) radiusMap.liveRedraw();
  }

  async function poll() {
    try {
      const res = await fetch(
        `/api/sync?filters_since=${filtersRevision}&ui_since=${uiRevision}`
      );
      if (!res.ok) return;
      const body = await res.json();

      let changed = false;
      if (body.filters) {
        filtersRevision = body.filters_revision;
        // Our own write, come back around. Applying it would re-persist
        // it and start the loop this id exists to prevent.
        if (body.filters_source !== CLIENT_ID) {
          applyFilters(body.filters);
          changed = true;
        }
      } else {
        filtersRevision = body.filters_revision;
      }

      if (body.ui) {
        uiRevision = body.ui_revision;
        if (body.ui_source !== CLIENT_ID) {
          const next = body.ui.active_radius_id;
          if (next !== activeId) {
            activeId = next;
            // Only one selection exists at a time, in either window.
            if (activeId) staged = null;
            changed = true;
          }
        }
      } else {
        uiRevision = body.ui_revision;
      }

      if (changed) {
        if (radiusMap) radiusMap.redraw();
        refresh({ persist: false });
      }
    } catch {
      // The main window is gone or the server is restarting. Keep
      // polling: there is nothing useful to do with the failure, and a
      // window that stopped would need reopening to recover.
    }
  }

  // ---------- drawing what the filters select ----------

  /** Refetches the per-point status and the counter.
   *
   * `persist` is what separates a local edit from an applied remote
   * one: a remote change must redraw and re-count without writing the
   * filters back, or the two windows re-persist each other's edits.
   */
  async function refresh({ persist: shouldPersist = true } = {}) {
    if (shouldPersist) persist();
    const body = payload();
    const token = ++queryToken;

    try {
      const [statusRes, queryRes] = await Promise.all([
        api("/api/map/status", { method: "POST", body: JSON.stringify(body) }),
        api("/api/query", { method: "POST", body: JSON.stringify(body) }),
      ]);
      // A slower earlier request must not paint over a newer answer.
      if (token !== queryToken) return;

      if (statusRes.ok) {
        const status = await statusRes.json();
        if (pointsCycle !== status.cycle_date) {
          const pointsRes = await api("/api/map/points");
          if (pointsRes.ok) {
            const points = await pointsRes.json();
            radiusMap.setPoints(points);
            pointsCycle = points.cycle_date;
          }
        }
        radiusMap.setStatus(status);
      }

      if (queryRes.ok) {
        const result = await queryRes.json();
        radiusMap.setCounter(
          `${result.total_count.toLocaleString()} / ${result.cap.toLocaleString()} entries`,
          result.level === "green" ? "" : result.level
        );
      }
    } catch {
      // Leaves the last good picture up rather than blanking the map.
      return;
    }
    radiusMap.redraw();
  }

  // ---------- what map.js asks of us ----------

  function setActive(id) {
    if (id) staged = null;
    activeId = id;
    pushActive();
    if (radiusMap) radiusMap.redraw();
  }

  function setStaged(value) {
    if (value && activeId) {
      // Staging closes an open rule: exactly one selection at a time,
      // or the slider has two plausible subjects.
      activeId = null;
      pushActive();
    }
    staged = value;
    if (radiusMap) radiusMap.redraw();
  }

  async function resolveCentre(centre) {
    try {
      const res = await api(`/api/resolve-center?center=${encodeURIComponent(centre)}`);
      const body = await res.json().catch(() => ({}));
      if (!res.ok) return { error: body.detail || "Couldn't find that location." };
      return { center: body.center || centre, lat: body.lat, lon: body.lon };
    } catch {
      return { error: "Couldn't check that location." };
    }
  }

  const hooks = {
    activeId: () => activeId,
    staged: () => staged,
    rules: () => rules,
    draftNm: () => draft.radius_nm,
    draftMode: () => draft.mode,
    selectedStates: () => new Set(filters && filters.states ? filters.states : []),
    selectedCities: () => new Set(filters && filters.cities ? filters.cities : []),
    // The server's defaults, spelled out: a filter state that has never
    // been saved arrives without these keys, and undefined would read
    // as "private only" rather than as the public-use-only scope the
    // app has always started in.
    useScope: () => ({
      public: !filters || filters.include_public !== false,
      private: !!(filters && filters.include_private),
    }),

    setActive,
    setStaged,
    setDraft(patch) {
      Object.assign(draft, patch);
    },

    async addRule({ center, radius_nm, mode }) {
      const resolved = await resolveCentre(center);
      if (resolved.error) return resolved.error;
      const id = newRuleId();
      rules.push({ id, center: resolved.center, radius_nm, mode, lat: resolved.lat, lon: resolved.lon });
      staged = null;
      activeId = id;
      pushActive();
      radiusMap.liveRedraw();
      refresh();
      return null;
    },

    async updateRule(id, patch) {
      const rule = rules.find((r) => r.id === id);
      if (!rule) return null;
      if (patch.center !== undefined) {
        const resolved = await resolveCentre(patch.center);
        if (resolved.error) return resolved.error;
        rule.center = resolved.center;
        rule.lat = resolved.lat;
        rule.lon = resolved.lon;
      }
      if (patch.radius_nm !== undefined) rule.radius_nm = patch.radius_nm;
      if (patch.mode !== undefined) rule.mode = patch.mode;
      // A new ring starts at what was last dialled in.
      if (patch.radius_nm !== undefined) draft.radius_nm = patch.radius_nm;
      if (patch.mode !== undefined) draft.mode = patch.mode;
      radiusMap.liveRedraw();
      scheduleRefresh();
      return null;
    },

    removeRule(id) {
      rules = rules.filter((r) => r.id !== id);
      if (activeId === id) {
        activeId = null;
        pushActive();
      }
      radiusMap.liveRedraw();
      refresh();
    },
  };

  // The slider lands here on every tick; the drawing is already live,
  // so this is only about not running a query per pixel.
  let refreshTimer = null;
  function scheduleRefresh() {
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => refresh(), 200);
  }

  // ---------- start ----------

  async function start() {
    const host = document.getElementById("map-window");
    radiusMap = window.AfpRadiusMap.create({ host, hooks });

    // No Close in a window that has its own close button.
    radiusMap.elements.closeBtn.hidden = true;

    await radiusMap.loadStates();

    const res = await fetch("/api/sync");
    if (res.ok) {
      const body = await res.json();
      filtersRevision = body.filters_revision;
      uiRevision = body.ui_revision;
      applyFilters(body.filters || {});
      if (body.ui) activeId = body.ui.active_radius_id;
    } else {
      applyFilters({});
    }

    radiusMap.invalidateSize();
    await refresh({ persist: false });
    radiusMap.fitView();

    setInterval(poll, POLL_MS);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    if (activeId) setActive(null);
    else if (staged) setStaged(null);
  });

  window.addEventListener("resize", () => {
    if (radiusMap) radiusMap.invalidateSize();
  });

  start();
})();
