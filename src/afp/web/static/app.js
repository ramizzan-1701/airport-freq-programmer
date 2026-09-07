"use strict";

const MAX_RENDERED_OPTIONS = 150;

const selected = {
  states: new Set(),
  cities: new Set(),
  freqCategories: new Set(),
  platformTypes: new Set(),
  facilityStatuses: new Set(),
  includeNonSiteFacilities: false,
  ilsStatuses: new Set(),
  ilsSystemTypes: new Set(),
  radiusFilters: [], // {center, radius_nm, mode}
  includePublic: true,
  includePrivate: false,
  mode: "smart",
};

let filterOptions = null;
let lastQueryResult = null;
let queryDebounceTimer = null;
let freqCategoryAdvancedExpanded = false;

// Which accordion group is expanded, or null for none. Single-open by
// design: it's what keeps an option list from scrolling inside the
// rail's own scroll. Frequency Category is the one people reach for
// first, so it starts open.
let openGroup = "cat";

// City options depend on the selected states, so they're fetched rather
// than read from filterOptions.
let cityOptions = [];

// Mirrors afp.classification.ILS_PSEUDO_CATEGORY -- the "ILS / Localizer"
// checkbox's code in the Frequency Category list.
const ILS_PSEUDO_CATEGORY = "ILS";

let customEntries = [];
let groupSetupAcknowledged = false;
let fixedGroupNames = [];

// ---------- tooltips ----------

/* Any element with data-tip="..." gets a tooltip on hover or keyboard
 * focus. One delegated listener rather than per-element handlers, so
 * markup rendered later (the filter rail re-renders constantly) is
 * covered without re-wiring anything.
 *
 * Preferred over the native title attribute: that can't be styled, waits
 * roughly half a second before appearing, and never shows for keyboard
 * users at all.
 */
function initTooltips() {
  const tip = document.createElement("div");
  tip.id = "tooltip";
  tip.setAttribute("role", "tooltip");
  tip.hidden = true;
  document.body.appendChild(tip);

  let current = null;

  function place(target) {
    // Measure before placing: the tooltip flips above the target when
    // there isn't room below, and is clamped so it never leaves the
    // window on a narrow one.
    const rect = target.getBoundingClientRect();
    const tipRect = tip.getBoundingClientRect();
    const gap = 7;

    let top = rect.bottom + gap;
    if (top + tipRect.height > window.innerHeight - 4) {
      top = rect.top - tipRect.height - gap;
    }
    let left = rect.left;
    if (left + tipRect.width > window.innerWidth - 8) {
      left = window.innerWidth - tipRect.width - 8;
    }

    tip.style.top = `${Math.max(4, top)}px`;
    tip.style.left = `${Math.max(4, left)}px`;
  }

  function show(target) {
    const text = target.getAttribute("data-tip");
    if (!text) return;
    current = target;
    tip.textContent = text;
    tip.hidden = false;
    place(target);
    tip.classList.add("visible");
  }

  function hide() {
    current = null;
    tip.classList.remove("visible");
    tip.hidden = true;
  }

  document.addEventListener("mouseover", (event) => {
    const target = event.target.closest("[data-tip]");
    if (target && target !== current) show(target);
  });
  document.addEventListener("mouseout", (event) => {
    if (current && !current.contains(event.relatedTarget)) hide();
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest("[data-tip]");
    if (target) show(target);
  });
  document.addEventListener("focusout", hide);
  // Anything that moves the page out from under a tooltip should dismiss
  // it rather than leave it floating over unrelated content.
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });

  // Follow the target rather than hiding: tabbing to an off-screen
  // element scrolls it into view, and hiding on scroll would dismiss the
  // tooltip that focus had just opened. Only give up once the target has
  // actually scrolled out of sight.
  window.addEventListener("scroll", () => {
    if (!current) return;
    const rect = current.getBoundingClientRect();
    if (rect.bottom < 0 || rect.top > window.innerHeight) hide();
    else place(current);
  }, true);
}

function debounce(fn, ms) {
  return (...args) => {
    clearTimeout(queryDebounceTimer);
    queryDebounceTimer = setTimeout(() => fn(...args), ms);
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  return response;
}

function buildFilterPayload() {
  return {
    states: selected.states.size ? [...selected.states] : null,
    cities: selected.cities.size ? [...selected.cities] : null,
    freq_categories: selected.freqCategories.size ? [...selected.freqCategories] : null,
    platform_types: selected.platformTypes.size ? [...selected.platformTypes] : null,
    facility_statuses: selected.facilityStatuses.size ? [...selected.facilityStatuses] : null,
    include_non_site_facilities: selected.includeNonSiteFacilities,
    ils_component_statuses: selected.ilsStatuses.size ? [...selected.ilsStatuses] : null,
    ils_system_types: selected.ilsSystemTypes.size ? [...selected.ilsSystemTypes] : null,
    radius_filters: selected.radiusFilters,
    include_public: selected.includePublic,
    include_private: selected.includePrivate,
    mode: selected.mode,
  };
}

// ---------- bootstrap ----------

async function init() {
  initTooltips();
  const status = await (await api("/api/status")).json();
  renderCycleStatus(status);
  groupSetupAcknowledged = status.group_setup_acknowledged;
  fixedGroupNames = status.fixed_group_names;

  if (!status.loaded_cycle) {
    document.getElementById("load-panel").classList.remove("hidden");
    renderCycleList(status.available_cycles);
  } else {
    document.getElementById("workspace").classList.remove("hidden");
    await initWorkspace();
  }

  checkForUpdate();

  document.getElementById("fetch-btn").addEventListener("click", doFetch);
  document.getElementById("group-setup-link").addEventListener("click", () => showGroupSetupModal());
  document.getElementById("clear-filters").addEventListener("click", clearAllFilters);
  document.getElementById("custom-import-input").addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (file) importCustomEntriesFile(file);
    e.target.value = "";
  });
}

async function checkForUpdate() {
  try {
    const res = await api("/api/check-update");
    if (!res.ok) return;
    const data = await res.json();
    if (data.update_available) {
      const el = document.getElementById("cycle-status");
      const badge = document.createElement("span");
      badge.className = "update-badge";
      badge.textContent = `Update available: ${data.current_published_cycle}`;
      el.appendChild(badge);
    }
  } catch {
    // offline / FAA site unreachable -- not fatal, just skip the badge
  }
}

function renderCycleStatus(status) {
  const el = document.getElementById("cycle-status");
  el.innerHTML = "";
  const text = document.createElement("span");
  text.textContent = status.loaded_cycle
    ? `Loaded cycle: ${status.loaded_cycle} (${status.airport_count} airports)`
    : "No data loaded";
  el.appendChild(text);
}

function renderCycleList(cycles) {
  const el = document.getElementById("cycle-list");
  el.innerHTML = "";
  if (cycles.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No cached cycles found. Fetch the latest one below.";
    el.appendChild(p);
    return;
  }
  for (const cycle of cycles) {
    const btn = document.createElement("button");
    btn.className = "btn";
    btn.textContent = cycle;
    btn.addEventListener("click", () => loadCycle(cycle));
    el.appendChild(btn);
  }
}

async function loadCycle(cycle) {
  const res = await api("/api/load", { method: "POST", body: JSON.stringify({ cycle }) });
  if (!res.ok) {
    alert("Failed to load cycle " + cycle);
    return;
  }
  const status = await res.json();
  renderCycleStatus(status);
  document.getElementById("load-panel").classList.add("hidden");
  document.getElementById("workspace").classList.remove("hidden");
  await initWorkspace();
}

async function doFetch() {
  const btn = document.getElementById("fetch-btn");
  const statusEl = document.getElementById("fetch-status");
  btn.disabled = true;
  statusEl.textContent = "Downloading current NASR cycle from the FAA -- this can take a minute...";
  try {
    const res = await api("/api/fetch", { method: "POST" });
    if (!res.ok) {
      statusEl.textContent = "Fetch failed. Check your connection and try again.";
      return;
    }
    const status = await res.json();
    renderCycleStatus(status);
    document.getElementById("load-panel").classList.add("hidden");
    document.getElementById("workspace").classList.remove("hidden");
    await initWorkspace();
  } finally {
    btn.disabled = false;
  }
}

// ---------- filter panel ----------

async function initWorkspace() {
  filterOptions = await (await api("/api/filter-options")).json();
  renderFilters();
  await loadCustomEntries();
  await runQuery();
}

// Small "Select all" / "Clear" text buttons for a filter group's header
// row -- lets a user start from everything selected and deselect just the
// few they don't want, instead of hand-checking dozens of boxes.
function groupActions(onSelectAll, onClear) {
  const wrap = document.createElement("span");
  wrap.className = "filter-group-actions";
  const selectAllBtn = document.createElement("button");
  selectAllBtn.type = "button";
  selectAllBtn.className = "text-action";
  selectAllBtn.textContent = "Select all";
  selectAllBtn.addEventListener("click", onSelectAll);
  const clearBtn = document.createElement("button");
  clearBtn.type = "button";
  clearBtn.className = "text-action";
  clearBtn.textContent = "Clear";
  clearBtn.addEventListener("click", onClear);
  wrap.append(selectAllBtn, clearBtn);
  return wrap;
}

// Options may be plain strings (City, Site Type, ILS Component Status --
// the value already reads fine on its own) or {code, label} objects (State
// -- an abbreviation that's meaningless without its full name). Normalize
// to {code, label} internally either way, displaying "code - label" only
// when they actually differ, so plain-string lists render unchanged.
function normalizeOptions(options) {
  return options.map((o) => (typeof o === "string" ? { code: o, label: o } : o));
}

/** The option list for one open accordion group: optional search box,
 * then the checkboxes. The group header (title, summary, Select all /
 * Clear, caret) is the accordion's job -- this only fills the body. */
function buildOptionList(container, { options, selectedSet, searchable = true, onChange, onSummaryChange }) {
  options = normalizeOptions(options);

  let searchInput = null;
  if (searchable) {
    searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Search...";
    searchInput.className = "multiselect-search";
    container.appendChild(searchInput);
  }

  const list = document.createElement("div");
  list.className = "multiselect-list";
  container.appendChild(list);

  function renderList(filterText) {
    list.innerHTML = "";
    const needle = (filterText || "").toLowerCase();
    const filtered = options.filter(
      (o) => o.code.toLowerCase().includes(needle) || o.label.toLowerCase().includes(needle),
    );
    if (filtered.length === 0) {
      const empty = document.createElement("div");
      empty.className = "multiselect-empty";
      empty.textContent = "No matches";
      list.appendChild(empty);
      return;
    }
    // Some fields (City, Primary Approach Radio Call) have thousands of
    // distinct values nationwide -- rendering all of them as DOM
    // checkboxes is both slow and unusable to scroll through. Cap what's
    // rendered and prompt the user to type, same pattern as any searchable
    // combobox; full-text filtering above still runs over every option.
    const overflow = filtered.length > MAX_RENDERED_OPTIONS;
    const toRender = overflow ? filtered.slice(0, MAX_RENDERED_OPTIONS) : filtered;
    for (const opt of toRender) {
      const lbl = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = selectedSet.has(opt.code);
      cb.addEventListener("change", () => {
        if (cb.checked) selectedSet.add(opt.code);
        else selectedSet.delete(opt.code);
        if (onSummaryChange) onSummaryChange();
        onChange();
      });
      const text = opt.code === opt.label ? opt.label : `${opt.code} - ${opt.label}`;
      lbl.append(cb, document.createTextNode(text));
      list.appendChild(lbl);
    }
    if (overflow) {
      const hint = document.createElement("div");
      hint.className = "multiselect-overflow";
      hint.textContent = `Showing ${MAX_RENDERED_OPTIONS} of ${filtered.length} — type to narrow down`;
      list.appendChild(hint);
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => renderList(searchInput.value));
  }

  renderList("");
}

function clearAllFilters() {
  selected.states.clear();
  selected.cities.clear();
  selected.freqCategories.clear();
  selected.platformTypes.clear();
  selected.facilityStatuses.clear();
  selected.includeNonSiteFacilities = false;
  selected.ilsStatuses.clear();
  selected.ilsSystemTypes.clear();
  selected.radiusFilters = [];
  selected.includePublic = true;
  selected.includePrivate = false;
  freqCategoryAdvancedExpanded = false;
  renderFilters();
  runQuery();
}

/** One accordion row. Only `openGroup` is expanded; opening another
 * closes this one.
 *
 * Collapsing is what removes the nested scroll the rail used to have:
 * with one group open at a time its option list fits, so nothing scrolls
 * inside the rail's own scroll. A `max-height` on a list would bring
 * that straight back.
 */
function accordionGroup(container, { key, title, summary, actions, buildBody }) {
  const isOpen = openGroup === key;

  const wrap = document.createElement("div");
  wrap.className = "filter-group";

  const head = document.createElement("div");
  head.className = "filter-group-label";
  head.tabIndex = 0;
  head.setAttribute("role", "button");
  head.setAttribute("aria-expanded", String(isOpen));

  const main = document.createElement("div");
  main.className = "filter-group-main";
  const titleEl = document.createElement("div");
  titleEl.className = "filter-group-title";
  titleEl.textContent = title;
  const summaryEl = document.createElement("div");
  summaryEl.className = "filter-group-summary";
  summaryEl.textContent = summary;
  main.append(titleEl, summaryEl);
  head.appendChild(main);

  // Select all / Clear only on the open group -- a closed one has
  // nothing on screen to act on.
  if (isOpen && actions) head.appendChild(actions);

  const caret = document.createElement("span");
  caret.className = "filter-group-caret";
  caret.textContent = isOpen ? "−" : "+";
  head.appendChild(caret);

  function toggle() {
    openGroup = isOpen ? null : key;
    renderFilters();
  }
  head.addEventListener("click", toggle);
  head.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });

  wrap.appendChild(head);

  if (isOpen) {
    const body = document.createElement("div");
    body.className = "filter-group-body";
    buildBody(body);
    wrap.appendChild(body);
  }

  container.appendChild(wrap);
}

/** Summary line for a multi-select group: the selected count, or what
 * an empty set actually means -- no narrowing, everything passes. */
function setSummary(selectedSet, emptyText) {
  return selectedSet.size > 0 ? `${selectedSet.size} selected` : emptyText;
}

function renderFilters() {
  // The rail's scroll container, not the rail itself: the "Filters"
  // heading and "Clear all filters" live in a fixed header above it.
  const root = document.getElementById("filters-body");
  root.innerHTML = "";

  // --- Scope ---
  const scopeSection = section(root, "Scope");

  accordionGroup(scopeSection, {
    key: "mode",
    title: "Data interpretation",
    summary: selected.mode === "smart" ? "Smart" : "Raw",
    buildBody(body) {
      const list = document.createElement("div");
      list.className = "multiselect-list";
      for (const [value, text] of [["smart", "Smart"], ["raw", "Raw"]]) {
        const lbl = document.createElement("label");
        const rb = document.createElement("input");
        rb.type = "radio";
        rb.name = "mode";
        rb.value = value;
        rb.checked = selected.mode === value;
        rb.addEventListener("change", () => {
          selected.mode = value;
          renderFilters();
          scheduleQuery();
        });
        lbl.append(rb, document.createTextNode(text));
        list.appendChild(lbl);
      }
      body.appendChild(list);
    },
  });

  const typeSummary = selected.includePublic && selected.includePrivate
    ? "Public and private"
    : selected.includePublic ? "Public-use only"
    : selected.includePrivate ? "Private-use only"
    : "Neither — no entries";
  accordionGroup(scopeSection, {
    key: "type",
    title: "Type",
    summary: typeSummary,
    buildBody(body) {
      const list = document.createElement("div");
      list.className = "multiselect-list";
      checkboxRow(list, "Include public-use airports", selected.includePublic, (checked) => {
        selected.includePublic = checked;
        renderFilters();
        scheduleQuery();
      });
      checkboxRow(list, "Include private-use airports", selected.includePrivate, (checked) => {
        selected.includePrivate = checked;
        renderFilters();
        scheduleQuery();
      });
      body.appendChild(list);
    },
  });

  // --- Location ---
  const locationSection = section(root, "Location");

  accordionGroup(locationSection, {
    key: "state",
    title: "State",
    summary: setSummary(selected.states, "All states"),
    actions: groupActions(
      () => {
        for (const o of normalizeOptions(filterOptions.states)) selected.states.add(o.code);
        renderFilters();
        refreshCityOptions().then(scheduleQuery);
      },
      () => {
        selected.states.clear();
        renderFilters();
        refreshCityOptions().then(scheduleQuery);
      },
    ),
    buildBody(body) {
      buildOptionList(body, {
        options: filterOptions.states,
        selectedSet: selected.states,
        searchable: true,
        onChange: async () => { await refreshCityOptions(); scheduleQuery(); },
        onSummaryChange: refreshOpenGroupSummary,
      });
    },
  });

  accordionGroup(locationSection, {
    key: "city",
    title: "City",
    summary: setSummary(selected.cities, "All cities"),
    actions: groupActions(
      () => { for (const c of cityOptions) selected.cities.add(c); renderFilters(); scheduleQuery(); },
      () => { selected.cities.clear(); renderFilters(); scheduleQuery(); },
    ),
    buildBody(body) {
      buildOptionList(body, {
        options: cityOptions,
        selectedSet: selected.cities,
        searchable: true,
        onChange: () => scheduleQuery(),
        onSummaryChange: refreshOpenGroupSummary,
      });
    },
  });

  renderRadiusFilters(locationSection);

  // --- Frequency ---
  const freqSection = section(root, "Frequency");
  renderFreqCategoryFilter(freqSection);

  // --- Facility ---
  const facilitySection = section(root, "Facility");
  renderNonSiteToggle(facilitySection);

  accordionGroup(facilitySection, {
    key: "siteType",
    title: "Site Type",
    summary: setSummary(selected.platformTypes, "All site types"),
    actions: groupActions(
      () => {
        for (const o of normalizeOptions(filterOptions.platform_types)) selected.platformTypes.add(o.code);
        renderFilters();
        scheduleQuery();
      },
      () => { selected.platformTypes.clear(); renderFilters(); scheduleQuery(); },
    ),
    buildBody(body) {
      buildOptionList(body, {
        options: filterOptions.platform_types,
        selectedSet: selected.platformTypes,
        searchable: false,
        onChange: () => scheduleQuery(),
        onSummaryChange: refreshOpenGroupSummary,
      });
    },
  });

  accordionGroup(facilitySection, {
    key: "facilityStatus",
    title: "Facility Status",
    summary: setSummary(selected.facilityStatuses, "All statuses"),
    actions: groupActions(
      () => {
        for (const o of filterOptions.facility_statuses) selected.facilityStatuses.add(o.code);
        renderFilters();
        scheduleQuery();
      },
      () => { selected.facilityStatuses.clear(); renderFilters(); scheduleQuery(); },
    ),
    buildBody(body) {
      buildOptionList(body, {
        options: filterOptions.facility_statuses,
        selectedSet: selected.facilityStatuses,
        searchable: false,
        onChange: () => scheduleQuery(),
        onSummaryChange: refreshOpenGroupSummary,
      });
    },
  });
}

/** Updates the open group's summary in place after a checkbox toggle, so
 * the count tracks without re-rendering (and blowing away search text or
 * scroll position) on every click. */
function refreshOpenGroupSummary() {
  const el = document.querySelector(".filter-group-label[aria-expanded='true'] .filter-group-summary");
  if (!el) return;
  const summaries = {
    state: () => setSummary(selected.states, "All states"),
    city: () => setSummary(selected.cities, "All cities"),
    cat: () => setSummary(selected.freqCategories, "All categories"),
    siteType: () => setSummary(selected.platformTypes, "All site types"),
    facilityStatus: () => setSummary(selected.facilityStatuses, "All statuses"),
  };
  if (summaries[openGroup]) el.textContent = summaries[openGroup]();
}

/** The non-site toggle sits above both Facility groups rather than
 * inside either: it exempts rows from Site Type *and* Facility Status,
 * so hiding it inside one collapsed group would bury a control that
 * governs the other. */
function renderNonSiteToggle(container) {
  const wrap = document.createElement("div");
  wrap.className = "non-site-block";

  const note = document.createElement("p");
  note.className = "filter-note";
  note.textContent = "Site Type and Facility Status only apply to airports — non-site facilities (VOR, RCAG, TRACON, etc.) have neither, so narrowing either filter would otherwise exclude them entirely. Check this to keep them regardless of what's selected below.";
  wrap.appendChild(note);

  const list = document.createElement("div");
  list.className = "multiselect-list";
  checkboxRow(list, "Retain non-site facilities (VOR, TRACON, etc.)", selected.includeNonSiteFacilities, (checked) => {
    selected.includeNonSiteFacilities = checked;
    scheduleQuery();
  });
  wrap.appendChild(list);
  container.appendChild(wrap);
}

function section(root, title) {
  const wrap = document.createElement("div");
  wrap.className = "filter-section";
  const h3 = document.createElement("h3");
  h3.textContent = title;
  wrap.appendChild(h3);
  root.appendChild(wrap);
  return wrap;
}

function renderFreqCategoryFilter(container) {
  const advancedOptions = filterOptions.freq_categories.filter((o) => !o.default_view);

  accordionGroup(container, {
    key: "cat",
    title: "Frequency Category",
    summary: setSummary(selected.freqCategories, "All categories"),
    actions: groupActions(
      () => {
        // Advanced categories collapsed -> "select all" means only the
        // visible default-view ones, not the dozens behind the toggle.
        const selectable = freqCategoryAdvancedExpanded
          ? filterOptions.freq_categories
          : filterOptions.freq_categories.filter((o) => o.default_view);
        for (const opt of selectable) selected.freqCategories.add(opt.code);
        renderFilters();
        scheduleQuery();
      },
      () => {
        selected.freqCategories.clear();
        renderFilters();
        scheduleQuery();
      },
    ),
    buildBody(body) {
      function categoryCheckbox(opt) {
        const lbl = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = selected.freqCategories.has(opt.code);
        cb.addEventListener("change", () => {
          if (cb.checked) selected.freqCategories.add(opt.code);
          else selected.freqCategories.delete(opt.code);
          // The ILS sub-block's presence depends on this one checkbox,
          // so it needs a full re-render; the rest only move the count.
          if (opt.code === ILS_PSEUDO_CATEGORY) {
            renderFilters();
          } else {
            refreshOpenGroupSummary();
          }
          scheduleQuery();
        });
        let text = opt.label;
        if (opt.not_usable_on_fta_850l) text += " — not usable on FTA-850L";
        lbl.append(cb, document.createTextNode(text));
        return lbl;
      }

      // spec §3: default view is a curated set of ~9 common categories;
      // everything else (STAR/DP procedure fixes, military ops, RCAG,
      // ...) sits behind an explicit toggle so the panel doesn't
      // overwhelm with dozens of rarely-used checkboxes.
      const list = document.createElement("div");
      list.className = "multiselect-list";
      for (const opt of filterOptions.freq_categories.filter((o) => o.default_view)) {
        list.appendChild(categoryCheckbox(opt));
      }
      if (freqCategoryAdvancedExpanded) {
        for (const opt of advancedOptions) list.appendChild(categoryCheckbox(opt));
      }
      body.appendChild(list);

      if (advancedOptions.length > 0) {
        const toggleBtn = document.createElement("button");
        toggleBtn.className = "more-btn";
        toggleBtn.textContent = freqCategoryAdvancedExpanded
          ? "Hide advanced categories"
          : `Advanced: show raw values (${advancedOptions.length})`;
        toggleBtn.addEventListener("click", () => {
          freqCategoryAdvancedExpanded = !freqCategoryAdvancedExpanded;
          renderFilters();
        });
        body.appendChild(toggleBtn);
      }

      if (selected.freqCategories.has(ILS_PSEUDO_CATEGORY)) {
        renderIlsSubFilters(body);
      }
    },
  });
}

/** ILS Component Status and System Type, nested inside the Frequency
 * Category body rather than sitting beside it.
 *
 * The accent rule down the left is the whole signal that these belong to
 * the "ILS / Localizer" checkbox above -- it replaces the prose hint that
 * used to state the dependency in words.
 */
function renderIlsSubFilters(container) {
  const wrap = document.createElement("div");
  wrap.className = "subfilters";

  const note = document.createElement("p");
  note.className = "filter-note";
  note.textContent = "These only narrow which ILS records show — unchecking \"ILS / Localizer\" excludes ILS entries outright regardless of these.";
  wrap.appendChild(note);

  for (const spec of [
    {
      title: "ILS Component Status",
      options: filterOptions.ils_component_statuses,
      selectedSet: selected.ilsStatuses,
    },
    {
      title: "ILS System Type",
      options: filterOptions.ils_system_types,
      selectedSet: selected.ilsSystemTypes,
    },
  ]) {
    const block = document.createElement("div");
    block.className = "subfilter";

    const label = document.createElement("div");
    label.className = "subfilter-label";
    label.textContent = spec.title;
    const badge = document.createElement("span");
    badge.className = "count-badge";
    badge.textContent = spec.selectedSet.size > 0 ? `${spec.selectedSet.size} selected` : "";
    label.appendChild(badge);
    block.appendChild(label);

    buildOptionList(block, {
      options: spec.options,
      selectedSet: spec.selectedSet,
      searchable: false,
      onChange: () => scheduleQuery(),
      onSummaryChange: () => {
        badge.textContent = spec.selectedSet.size > 0 ? `${spec.selectedSet.size} selected` : "";
      },
    });
    wrap.appendChild(block);
  }

  container.appendChild(wrap);
}

function checkboxRow(container, labelText, checked, onChange) {
  const row = document.createElement("div");
  row.className = "checkbox-row";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = checked;
  cb.addEventListener("change", () => onChange(cb.checked));
  const lbl = document.createElement("label");
  lbl.append(cb, document.createTextNode(" " + labelText));
  row.appendChild(lbl);
  container.appendChild(row);
  return row;
}

async function refreshCityOptions() {
  const params = new URLSearchParams();
  for (const s of selected.states) params.append("states", s);
  const cities = await (await api("/api/cities?" + params.toString())).json();
  // drop selections that fall outside the newly scoped city list
  for (const c of [...selected.cities]) {
    if (!cities.includes(c)) selected.cities.delete(c);
  }
  cityOptions = cities;
  // Only re-render when the list is actually on screen; otherwise the
  // next open picks up the new options anyway.
  if (openGroup === "city") renderFilters();
}

function renderRadiusFilters(container) {
  // Outside the accordion deliberately: this is a form you fill in, not
  // a selection set you pick from, so there's no list to collapse and no
  // "n selected" to summarise.
  const wrap = document.createElement("div");
  wrap.className = "radius-block";
  const label = document.createElement("div");
  label.className = "filter-group-title";
  label.textContent = "Geographic radius";
  wrap.appendChild(label);

  const list = document.createElement("div");
  wrap.appendChild(list);

  function renderList() {
    list.innerHTML = "";
    selected.radiusFilters.forEach((rf, idx) => {
      const item = document.createElement("div");
      item.className = "radius-item";
      item.textContent = `${rf.mode === "include" ? "Within" : "Exclude"} ${rf.radius_nm}nm of ${rf.center}`;
      const rm = document.createElement("button");
      rm.textContent = "×";
      rm.title = "Remove";
      rm.addEventListener("click", () => {
        selected.radiusFilters.splice(idx, 1);
        renderList();
        scheduleQuery();
      });
      item.appendChild(rm);
      list.appendChild(item);
    });
  }
  renderList();

  const form = document.createElement("div");
  form.className = "radius-form";
  const centerInput = document.createElement("input");
  centerInput.placeholder = "Airport ID or lat,lon";
  const radiusInput = document.createElement("input");
  radiusInput.type = "number";
  radiusInput.placeholder = "NM";
  radiusInput.min = "0";
  const modeSelect = document.createElement("select");
  for (const [value, text] of [["include", "Include"], ["exclude", "Exclude"]]) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    modeSelect.appendChild(opt);
  }
  const addBtn = document.createElement("button");
  addBtn.className = "btn btn-small";
  addBtn.textContent = "Add";
  addBtn.addEventListener("click", () => {
    const center = centerInput.value.trim();
    const radiusNm = parseFloat(radiusInput.value);
    if (!center || !Number.isFinite(radiusNm) || radiusNm <= 0) return;
    selected.radiusFilters.push({ center, radius_nm: radiusNm, mode: modeSelect.value });
    centerInput.value = "";
    radiusInput.value = "";
    renderList();
    scheduleQuery();
  });
  form.append(centerInput, radiusInput, modeSelect, addBtn);
  wrap.appendChild(form);

  container.appendChild(wrap);
}

// ---------- custom entries & group setup ----------
//
// Custom entries (spec §5): a user's hand-added frequencies from YCE-64,
// preserved across regeneration by importing their full current radio
// export and keeping only what doesn't match the app's 6 fixed group
// names. Held server-side (persisted across restarts); this module just
// mirrors that state for display.

async function loadCustomEntries() {
  const body = await (await api("/api/custom-entries")).json();
  customEntries = body.entries;
  renderCustomBar();
}

function renderCustomBar() {
  const bar = document.getElementById("custom-bar");
  bar.innerHTML = "";

  const title = document.createElement("span");
  title.className = "custom-bar-title";
  title.textContent = "Custom Frequencies (from your radio)";
  bar.appendChild(title);

  if (customEntries.length === 0) {
    const meta = document.createElement("span");
    meta.className = "muted";
    meta.textContent = "None imported yet";
    bar.appendChild(meta);

    const actions = document.createElement("span");
    actions.className = "custom-bar-actions";
    const importBtn = document.createElement("button");
    importBtn.className = "btn btn-small";
    importBtn.textContent = "Import Existing XML";
    importBtn.addEventListener("click", () => document.getElementById("custom-import-input").click());
    actions.appendChild(importBtn);
    bar.appendChild(actions);
    return;
  }

  const groupCount = new Set(customEntries.map((e) => e.group)).size;
  const meta = document.createElement("span");
  meta.className = "muted";
  meta.textContent = `${customEntries.length} entries · ${groupCount} groups imported`;
  bar.appendChild(meta);

  const actions = document.createElement("span");
  actions.className = "custom-bar-actions";
  const openBtn = document.createElement("button");
  openBtn.className = "btn btn-small";
  openBtn.textContent = "Open";
  openBtn.addEventListener("click", openCustomEntriesModal);
  const clearBtn = document.createElement("button");
  clearBtn.className = "btn btn-small";
  clearBtn.textContent = "Clear";
  clearBtn.addEventListener("click", clearCustomEntries);
  actions.append(openBtn, clearBtn);
  bar.appendChild(actions);
}

async function importCustomEntriesFile(file) {
  const res = await api("/api/custom-entries/import", {
    method: "POST",
    body: file,
    headers: { "Content-Type": "application/xml" },
  });
  if (res.status === 422) {
    const body = await res.json();
    showBlockedImportModal(body.detail);
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert("Import failed: " + (body.detail || "the file couldn't be read as a YCE-64 export."));
    return;
  }
  const body = await res.json();
  customEntries = body.entries;
  renderCustomBar();
  // Show what actually came in. An import silently returning to the main
  // page gives no confirmation of *which* entries were kept -- and the
  // split matters here, since everything in the 6 generated group names
  // is discarded on the way in.
  openCustomEntriesModal();
  await runQuery();
}

async function removeCustomEntry(index) {
  const body = await (await api(`/api/custom-entries/${index}`, { method: "DELETE" })).json();
  customEntries = body.entries;
  renderCustomBar();
  if (!document.getElementById("custom-entries-modal").classList.contains("hidden")) {
    openCustomEntriesModal();
  }
  await runQuery();
}

async function clearCustomEntries() {
  const body = await (await api("/api/custom-entries/clear", { method: "POST" })).json();
  customEntries = body.entries;
  renderCustomBar();
  document.getElementById("custom-entries-modal").classList.add("hidden");
  await runQuery();
}

function openCustomEntriesModal() {
  const modal = document.getElementById("custom-entries-modal");
  const box = modal.querySelector(".modal-box");
  box.innerHTML = "";
  // Header and actions stay put; only the entry list scrolls. This list
  // is the one unbounded thing in any modal here -- it's however many
  // entries the radio had.
  box.classList.add("modal-split");

  const h2 = document.createElement("h2");
  h2.textContent = "Custom Frequencies (from your radio)";
  box.appendChild(h2);

  const sub = document.createElement("p");
  sub.className = "muted";
  const groupCount = new Set(customEntries.map((e) => e.group)).size;
  // Worded identically to the compact bar's summary -- same facts, and
  // two phrasings for one thing read as two different things.
  sub.textContent = `${customEntries.length} entries · ${groupCount} groups imported`;
  box.appendChild(sub);

  const scroll = document.createElement("div");
  scroll.className = "modal-scroll";

  if (customEntries.length === 0) {
    // Reachable right after an import: a file whose every entry sat in
    // one of the 6 generated group names has all of it discarded, which
    // otherwise shows up as an empty table with no explanation.
    const empty = document.createElement("p");
    empty.textContent = "No custom frequencies were kept from that file.";
    scroll.appendChild(empty);
    const why = document.createElement("p");
    why.className = "muted";
    why.textContent = "Every entry in it used one of the 6 group names this app generates, so they were treated as previously generated entries and discarded -- they'll be recreated when you generate. Only entries in your own group names are kept here.";
    scroll.appendChild(why);
  }

  const table = document.createElement("table");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>Tag</th><th>Frequency</th><th>Group</th><th>Position</th><th></th></tr>";
  table.appendChild(thead);
  const tbody = document.createElement("tbody");
  for (const e of customEntries) {
    const tr = document.createElement("tr");
    for (const val of [e.tag_name, e.freq_mhz.toFixed(3), e.group, `${e.lat.toFixed(3)}, ${e.lon.toFixed(3)}`]) {
      const td = document.createElement("td");
      td.textContent = val;
      tr.appendChild(td);
    }
    const removeTd = document.createElement("td");
    const removeBtn = document.createElement("button");
    removeBtn.className = "text-action";
    removeBtn.textContent = "✕ remove";
    removeBtn.addEventListener("click", () => removeCustomEntry(e.index));
    removeTd.appendChild(removeBtn);
    tr.appendChild(removeTd);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);

  // Both only make sense alongside actual rows: a bare header row reads
  // as a broken table, and the note below refers to "the list above".
  if (customEntries.length > 0) {
    scroll.appendChild(table);

    // Stays with the list rather than in the pinned footer -- it
    // annotates the entries, and permanently occupying footer space
    // would cost more than it's worth.
    const note = document.createElement("p");
    note.className = "muted";
    note.textContent = "Note: placing a custom entry into one of the 6 fixed group names above will cause it to be discarded on the next import, since the app can't tell it apart from its own regenerated entries.";
    scroll.appendChild(note);
  }
  box.appendChild(scroll);

  const footer = document.createElement("div");
  footer.className = "modal-footer";
  const actionsRow = document.createElement("div");
  actionsRow.className = "modal-actions";
  const clearBtn = document.createElement("button");
  clearBtn.className = "btn";
  clearBtn.textContent = "Clear imported data";
  clearBtn.addEventListener("click", clearCustomEntries);
  const reimportBtn = document.createElement("button");
  reimportBtn.className = "btn";
  reimportBtn.textContent = "Re-import";
  reimportBtn.addEventListener("click", () => document.getElementById("custom-import-input").click());
  const closeBtn = document.createElement("button");
  closeBtn.className = "btn";
  closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
  actionsRow.append(clearBtn, reimportBtn, closeBtn);
  footer.appendChild(actionsRow);
  box.appendChild(footer);

  modal.classList.remove("hidden");
}

function showBlockedImportModal({ groups, found, available }) {
  const modal = document.getElementById("blocked-import-modal");
  const box = modal.querySelector(".modal-box");
  box.innerHTML = "";

  const h2 = document.createElement("h2");
  h2.textContent = "Too many custom groups";
  box.appendChild(h2);

  const p1 = document.createElement("p");
  p1.textContent = `Your imported file uses more distinct custom group names than the radio has room for. The FTA-850L has 9 total slots -- 6 are permanently reserved for this app's standard scheme, leaving ${available} remaining slots for anything else.`;
  box.appendChild(p1);

  const list = document.createElement("ul");
  list.className = "group-name-list";
  for (const g of groups) {
    const li = document.createElement("li");
    li.textContent = g;
    list.appendChild(li);
  }
  box.appendChild(list);

  const p2 = document.createElement("p");
  p2.className = "muted";
  p2.textContent = `Found: ${found} distinct custom groups. Available: ${available}. Over by: ${found - available}.`;
  box.appendChild(p2);

  const p3 = document.createElement("p");
  p3.textContent = `In YCE-64, consolidate these ${found} groups down to ${available} or fewer -- merge entries into fewer groups, or delete ones you don't need -- then re-export and re-import.`;
  box.appendChild(p3);

  const actionsRow = document.createElement("div");
  actionsRow.className = "modal-actions";
  const closeBtn = document.createElement("button");
  closeBtn.className = "btn";
  closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
  actionsRow.appendChild(closeBtn);
  box.appendChild(actionsRow);

  modal.classList.remove("hidden");
}

/** The group-setup instructions themselves, shared by the topbar's
 * reference modal and the pre-generate confirmation gate. Kept in one
 * place deliberately: two copies of this text would drift, and the whole
 * point of the confirmation is that it says the same thing.
 */
function appendGroupSetupInstructions(box) {
  const p1 = document.createElement("p");
  p1.textContent = "This app will sort frequencies into 6 alphabetized Groups for quick recall on your radio. The Yaesu YCE-64 editor can't create or edit Group names on import - they must already exist there, or that group's frequencies will be dropped.";
  box.appendChild(p1);

  const p2 = document.createElement("p");
  p2.textContent = "There are 9 Group slots available. Rename 6 of them:";
  box.appendChild(p2);

  const steps = document.createElement("ol");
  steps.className = "setup-steps";
  for (const step of [
    "Open the YCE-64 editor. Go to: Setup -> Memory Group Name.",
    "Rename 6 of the 9 Groups to the following names - a one time setup",
    "The remaining 3 Groups are yours - you can name them whatever you like for your own Custom frequencies.",
  ]) {
    const li = document.createElement("li");
    li.textContent = step;
    steps.appendChild(li);
  }
  box.appendChild(steps);

  const list = document.createElement("ul");
  list.className = "group-name-list";
  for (const name of fixedGroupNames) {
    const li = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = name;
    // Per-name rather than one bulk copy: these get pasted into six
    // separate YCE-64 fields, so a single comma-joined string can't
    // actually be used. Typing them is easy enough, but a mistyped name
    // fails silently -- YCE-64 just drops that entry's grouping.
    li.append(label, copyNameButton(name));
    list.appendChild(li);
  }
  box.appendChild(list);
}

function copyNameButton(name) {
  const btn = document.createElement("button");
  btn.className = "text-action copy-name";
  btn.textContent = "⧉ copy";
  btn.title = `Copy "${name}"`;
  btn.setAttribute("aria-label", `Copy group name ${name}`);
  btn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(name);
      btn.textContent = "✓ copied";
    } catch {
      // Clipboard access can be refused (permissions, insecure context).
      // Say so rather than showing a success state for a copy that
      // didn't happen -- the name is right there to type instead.
      btn.textContent = "couldn't copy";
    }
    btn.disabled = true;
    setTimeout(() => {
      btn.textContent = "⧉ copy";
      btn.disabled = false;
    }, 1200);
  });
  return btn;
}

/** Reference view, opened from the topbar link -- read-only, no gate. */
function showGroupSetupModal() {
  const modal = document.getElementById("group-setup-modal");
  const box = modal.querySelector(".modal-box");
  box.innerHTML = "";

  const h2 = document.createElement("h2");
  h2.textContent = "Rename your memory groups first";
  box.appendChild(h2);

  appendGroupSetupInstructions(box);

  const actionsRow = document.createElement("div");
  actionsRow.className = "modal-actions";
  const closeBtn = document.createElement("button");
  closeBtn.className = "btn btn-primary";
  closeBtn.textContent = "Close";
  closeBtn.addEventListener("click", () => modal.classList.add("hidden"));
  actionsRow.append(closeBtn);
  box.appendChild(actionsRow);

  modal.classList.remove("hidden");
}

/** Confirmation gate shown when "Generate XML" is clicked. Resolves true
 * to proceed with generation, false if the user cancels.
 *
 * This runs before generating rather than after downloading: renaming the
 * groups is a prerequisite for the file to import correctly, and getting
 * the reminder after the file is already saved is too late to act on.
 */
function confirmGroupSetupBeforeGenerate() {
  return new Promise((resolve) => {
    const modal = document.getElementById("generate-confirm-modal");
    const box = modal.querySelector(".modal-box");
    box.innerHTML = "";

    const h2 = document.createElement("h2");
    h2.textContent = "Before you generate: check your memory groups";
    box.appendChild(h2);

    appendGroupSetupInstructions(box);

    // Opt-out, not auto-dismiss: the gate keeps appearing until the user
    // deliberately says they're done with it. The instructions stay
    // reachable from the topbar's "Group setup instructions" button, so
    // dismissing this loses nothing.
    const suppressRow = document.createElement("div");
    suppressRow.className = "checkbox-row";
    const suppressCb = document.createElement("input");
    suppressCb.type = "checkbox";
    suppressCb.id = "suppress-group-setup-gate";
    const suppressLabel = document.createElement("label");
    suppressLabel.htmlFor = suppressCb.id;
    suppressLabel.append(suppressCb, document.createTextNode(" Don't show this message again"));
    suppressRow.appendChild(suppressLabel);
    box.appendChild(suppressRow);

    let settled = false;
    const finish = (proceed) => {
      if (settled) return; // guard against a double-fire resolving twice
      settled = true;
      modal.classList.add("hidden");
      document.removeEventListener("keydown", onKeydown);
      resolve(proceed);
    };

    function onKeydown(event) {
      if (event.key === "Escape") finish(false);
    }

    const actionsRow = document.createElement("div");
    actionsRow.className = "modal-actions";

    const cancelBtn = document.createElement("button");
    cancelBtn.className = "btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => finish(false));

    const proceedBtn = document.createElement("button");
    proceedBtn.className = "btn btn-primary";
    proceedBtn.textContent = "I Understand. Proceed";
    proceedBtn.addEventListener("click", () => {
      // Only persist when the user actually ticked the box. Proceeding
      // on its own means "yes, this export" -- not "stop asking me".
      if (suppressCb.checked) {
        groupSetupAcknowledged = true;
        api("/api/group-setup/acknowledge", { method: "POST" }).catch(() => {
          // A failed save must never block the export the user just asked
          // for -- worst case the gate reappears next time.
          groupSetupAcknowledged = false;
        });
      }
      finish(true);
    });

    actionsRow.append(cancelBtn, proceedBtn);
    box.appendChild(actionsRow);

    document.addEventListener("keydown", onKeydown);
    modal.classList.remove("hidden");
    proceedBtn.focus();
  });
}

// ---------- query + results ----------

const scheduleQuery = debounce(runQuery, 200);

async function runQuery() {
  const res = await api("/api/query", { method: "POST", body: JSON.stringify(buildFilterPayload()) });
  if (!res.ok) {
    document.getElementById("counter").textContent = "Query failed -- adjust filters and try again.";
    return;
  }
  const result = await res.json();
  lastQueryResult = result;
  renderCounter(result);
  renderResults(result);
  updateGenerateButton(result);
}

function renderCounter(result) {
  const el = document.getElementById("counter");
  el.className = "counter " + (result.level === "green" ? "" : result.level);
  el.innerHTML = "";

  const line = document.createElement("div");
  line.className = "counter-line";
  const num = document.createElement("span");
  num.className = "count-number";
  num.textContent = result.total_count;
  const cap = document.createElement("span");
  cap.className = "cap-text";
  cap.textContent = `entries (cap ${result.cap})`;
  line.append(num, cap);
  el.appendChild(line);

  const bar = document.createElement("div");
  bar.className = "counter-bar";
  const fill = document.createElement("span");
  // Clamped so an over-cap result fills the bar rather than overflowing
  // it; the count and the slots line below carry the actual overage.
  fill.style.width = `${Math.min(100, (result.total_count / result.cap) * 100)}%`;
  bar.appendChild(fill);
  el.appendChild(bar);

  const slots = document.createElement("div");
  slots.className = "counter-slots";
  const remaining = result.cap - result.total_count;
  slots.textContent = remaining >= 0
    ? `${remaining} slots remaining`
    : `${-remaining} over the cap`;
  el.appendChild(slots);

  renderBreakdown(result);
}

function renderBreakdown(result) {
  const el = document.getElementById("breakdown");
  el.innerHTML = "";
  const counts = result.category_counts || [];
  if (counts.length === 0) return;

  const label = document.createElement("div");
  label.className = "breakdown-label";
  label.textContent = "Frequency Category";
  el.appendChild(label);

  const chips = document.createElement("div");
  chips.className = "breakdown-chips";
  for (const c of counts) {
    const chip = document.createElement("span");
    chip.className = "breakdown-chip";
    // Short label on screen, full name on hover: 17 categories at full
    // length filled 15 rows and half the window.
    chip.setAttribute("data-tip", c.label);
    chip.tabIndex = 0;
    const n = document.createElement("b");
    n.textContent = c.count;
    chip.append(n, document.createTextNode(" " + (c.short_label || c.label)));
    chips.appendChild(chip);
  }
  el.appendChild(chips);
}

function renderResults(result) {
  const note = document.getElementById("results-note");
  note.textContent = result.truncated
    ? `Showing first ${result.entries.length} of ${result.count} entries.`
    : (result.count > 0 ? `Showing all ${result.count} entries.` : "No entries match the current filters.");

  const body = document.getElementById("results-body");
  body.innerHTML = "";
  for (const e of result.entries) {
    const tr = document.createElement("tr");

    tr.appendChild(cell("cell-tag", e.tag_name));
    tr.appendChild(cell("cell-freq", e.freq_mhz.toFixed(3)));

    const groupTd = document.createElement("td");
    const pill = document.createElement("span");
    pill.className = "group-pill";
    pill.textContent = e.group;
    groupTd.appendChild(pill);
    tr.appendChild(groupTd);

    // Truncated columns carry the full text as a tooltip, since the
    // ellipsis hides real content rather than decoration.
    const airport = `${e.airport_id} - ${e.airport_name}`;
    const airportTd = cell("cell-apt", airport);
    airportTd.title = airport;
    tr.appendChild(airportTd);

    // City and state are separate elements so only the city truncates --
    // the state code is two characters and identifies the region, so
    // losing it to an ellipsis would cost more than the city name does.
    const cityTd = document.createElement("td");
    cityTd.className = "cell-city";
    const cityName = document.createElement("span");
    cityName.className = "city-name";
    cityName.textContent = e.city;
    const cityState = document.createElement("span");
    cityState.className = "city-state";
    cityState.textContent = e.state ? `, ${e.state}` : "";
    cityTd.append(cityName, cityState);
    cityTd.title = e.state ? `${e.city}, ${e.state}` : e.city;
    tr.appendChild(cityTd);

    body.appendChild(tr);
  }
}

function cell(className, text) {
  const td = document.createElement("td");
  td.className = className;
  td.textContent = text;
  return td;
}

function updateGenerateButton(result) {
  const btn = document.getElementById("generate-btn");
  const hint = document.getElementById("generate-hint");
  document.getElementById("generate-errors").classList.add("hidden");

  if (result.total_count === 0) {
    btn.disabled = true;
    hint.textContent = "No entries match the current filters.";
  } else if (result.level === "red") {
    btn.disabled = true;
    hint.textContent = `Over the ${result.cap}-entry cap -- narrow your filters to generate.`;
  } else {
    btn.disabled = false;
    hint.textContent = "";
  }
}

document.getElementById("generate-btn").addEventListener("click", async () => {
  const btn = document.getElementById("generate-btn");
  const errorsEl = document.getElementById("generate-errors");
  errorsEl.classList.add("hidden");

  // Skipped once the user has ticked "Don't show this message again";
  // the instructions stay available from the topbar button.
  if (!groupSetupAcknowledged && !(await confirmGroupSetupBeforeGenerate())) {
    return; // cancelled
  }

  btn.disabled = true;
  try {
    const res = await api("/api/generate", { method: "POST", body: JSON.stringify(buildFilterPayload()) });
    if (res.status === 422) {
      const body = await res.json();
      const problems = (body.detail && body.detail.problems) || [];
      errorsEl.innerHTML = `<strong>Cannot generate XML -- ${problems.length} problem(s):</strong><ul>${problems
        .slice(0, 20)
        .map((p) => `<li>${p}</li>`)
        .join("")}</ul>`;
      errorsEl.classList.remove("hidden");
      return;
    }
    if (!res.ok) {
      errorsEl.textContent = "Generation failed unexpectedly.";
      errorsEl.classList.remove("hidden");
      return;
    }
    const blob = await res.blob();
    const disposition = res.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="(.+)"/);
    const filename = match ? match[1] : "airport_frequencies.xml";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } finally {
    btn.disabled = lastQueryResult ? lastQueryResult.level === "red" || lastQueryResult.total_count === 0 : false;
  }
});

init();
