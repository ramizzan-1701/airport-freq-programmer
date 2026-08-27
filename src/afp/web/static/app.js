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
let cityMultiSelect = null;
let lastQueryResult = null;
let queryDebounceTimer = null;
let freqCategoryAdvancedExpanded = false;

// Mirrors afp.classification.ILS_PSEUDO_CATEGORY -- the "ILS / Localizer"
// checkbox's code in the Frequency Category list.
const ILS_PSEUDO_CATEGORY = "ILS";

let customEntries = [];
let groupSetupAcknowledged = false;
let fixedGroupNames = [];

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
  document.getElementById("group-setup-link").addEventListener("click", () => showGroupSetupModal(false));
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

function createMultiSelect(container, { title, options, selectedSet, searchable = true, onChange }) {
  options = normalizeOptions(options);
  const wrap = document.createElement("div");
  wrap.className = "filter-group";

  const labelRow = document.createElement("div");
  labelRow.className = "filter-group-label";
  const labelText = document.createElement("span");
  labelText.textContent = title;
  const badge = document.createElement("span");
  badge.className = "count-badge";
  badge.style.display = "none";
  const actions = groupActions(
    () => {
      for (const opt of options) selectedSet.add(opt.code);
      renderList(searchInput ? searchInput.value : "");
      updateBadge();
      onChange();
    },
    () => {
      selectedSet.clear();
      renderList(searchInput ? searchInput.value : "");
      updateBadge();
      onChange();
    },
  );
  const rightSide = document.createElement("span");
  rightSide.className = "filter-group-right";
  rightSide.append(actions, badge);
  labelRow.append(labelText, rightSide);
  wrap.appendChild(labelRow);

  let searchInput = null;
  if (searchable) {
    searchInput = document.createElement("input");
    searchInput.type = "text";
    searchInput.placeholder = "Search...";
    searchInput.className = "multiselect-search";
    wrap.appendChild(searchInput);
  }

  const list = document.createElement("div");
  list.className = "multiselect-list";
  wrap.appendChild(list);

  function updateBadge() {
    if (selectedSet.size > 0) {
      badge.style.display = "";
      badge.textContent = `${selectedSet.size} selected`;
    } else {
      badge.style.display = "none";
    }
  }

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
        updateBadge();
        onChange();
      });
      const text = opt.code === opt.label ? opt.label : `${opt.code} - ${opt.label}`;
      lbl.append(cb, document.createTextNode(" " + text));
      list.appendChild(lbl);
    }
    if (overflow) {
      const hint = document.createElement("div");
      hint.className = "multiselect-empty";
      hint.textContent = `Showing ${MAX_RENDERED_OPTIONS} of ${filtered.length} -- type to narrow down`;
      list.appendChild(hint);
    }
  }

  if (searchInput) {
    searchInput.addEventListener("input", () => renderList(searchInput.value));
  }

  renderList("");
  updateBadge();
  container.appendChild(wrap);

  return {
    rerender(newOptions) {
      if (newOptions) options = normalizeOptions(newOptions);
      renderList(searchInput ? searchInput.value : "");
      updateBadge();
    },
  };
}

function renderFilters() {
  const root = document.getElementById("filters");
  root.innerHTML = "";

  const clearBtn = document.createElement("button");
  clearBtn.className = "btn btn-small clear-all";
  clearBtn.textContent = "Clear all filters";
  clearBtn.addEventListener("click", () => {
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
  });
  root.appendChild(clearBtn);

  // --- Scope ---
  const scopeSection = section(root, "Scope");
  const modeRow = document.createElement("div");
  modeRow.className = "filter-group";
  const modeLabel = document.createElement("div");
  modeLabel.className = "filter-group-label";
  modeLabel.innerHTML = "<span>Data interpretation</span>";
  modeRow.appendChild(modeLabel);
  const radioRow = document.createElement("div");
  radioRow.className = "radio-row";
  for (const [value, text] of [["smart", "Smart"], ["raw", "Raw"]]) {
    const lbl = document.createElement("label");
    const rb = document.createElement("input");
    rb.type = "radio";
    rb.name = "mode";
    rb.value = value;
    rb.checked = selected.mode === value;
    rb.addEventListener("change", () => { selected.mode = value; scheduleQuery(); });
    lbl.append(rb, document.createTextNode(" " + text));
    radioRow.appendChild(lbl);
  }
  modeRow.appendChild(radioRow);
  scopeSection.appendChild(modeRow);

  const typeGroup = document.createElement("div");
  typeGroup.className = "filter-group";
  const typeLabel = document.createElement("div");
  typeLabel.className = "filter-group-label";
  typeLabel.innerHTML = "<span>Type</span>";
  typeGroup.appendChild(typeLabel);
  checkboxRow(typeGroup, "Include public-use airports", selected.includePublic, (checked) => {
    selected.includePublic = checked; scheduleQuery();
  });
  checkboxRow(typeGroup, "Include private-use airports", selected.includePrivate, (checked) => {
    selected.includePrivate = checked; scheduleQuery();
  });
  scopeSection.appendChild(typeGroup);

  // --- Location ---
  const locationSection = section(root, "Location");
  createMultiSelect(locationSection, {
    title: "State",
    options: filterOptions.states,
    selectedSet: selected.states,
    onChange: async () => { await refreshCityOptions(); scheduleQuery(); },
  });
  cityMultiSelect = createMultiSelect(locationSection, {
    title: "City",
    options: [],
    selectedSet: selected.cities,
    onChange: () => scheduleQuery(),
  });
  refreshCityOptions();

  renderRadiusFilters(locationSection);

  // --- Frequency ---
  const freqSection = section(root, "Frequency");
  renderFreqCategoryFilter(freqSection);

  // These two only narrow *which* ILS records show -- they're meaningless
  // (and hidden) unless "ILS / Localizer" is explicitly checked above,
  // since unchecking it excludes ILS entries outright regardless of these.
  if (selected.freqCategories.has(ILS_PSEUDO_CATEGORY)) {
    createMultiSelect(freqSection, {
      title: "ILS Component Status",
      options: filterOptions.ils_component_statuses,
      selectedSet: selected.ilsStatuses,
      onChange: () => scheduleQuery(),
    });
    renderIlsSystemTypeFilter(freqSection);
  } else {
    const ilsHint = document.createElement("p");
    ilsHint.className = "muted";
    ilsHint.textContent = "Check \"ILS / Localizer\" under Frequency Category above to filter by ILS Component Status or System Type.";
    freqSection.appendChild(ilsHint);
  }

  // --- Facility ---
  const facilitySection = section(root, "Facility");
  const nonSiteHint = document.createElement("p");
  nonSiteHint.className = "muted";
  nonSiteHint.textContent = "Site Type and Facility Status only apply to airports -- non-site facilities (VOR, RCAG, TRACON, etc.) have neither, so narrowing either filter would otherwise exclude them entirely. Check this to keep them regardless of what's selected below.";
  facilitySection.appendChild(nonSiteHint);
  checkboxRow(facilitySection, "Retain non-site facilities (VOR, TRACON, etc.)", selected.includeNonSiteFacilities, (checked) => {
    selected.includeNonSiteFacilities = checked; scheduleQuery();
  });
  createMultiSelect(facilitySection, {
    title: "Site Type",
    options: filterOptions.platform_types,
    selectedSet: selected.platformTypes,
    searchable: false,
    onChange: () => scheduleQuery(),
  });
  renderFacilityStatusFilter(facilitySection);
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
  const wrap = document.createElement("div");
  wrap.className = "filter-group";

  const labelRow = document.createElement("div");
  labelRow.className = "filter-group-label";
  const labelText = document.createElement("span");
  labelText.textContent = "Frequency Category";
  const badge = document.createElement("span");
  badge.className = "count-badge";
  badge.style.display = "none";
  const actions = groupActions(
    () => {
      // Advanced categories collapsed -> "select all" only means the
      // visible default-view ones, not the dozens hidden behind the
      // toggle. Expanded -> everything on screen is fair game.
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
  );
  const rightSide = document.createElement("span");
  rightSide.className = "filter-group-right";
  rightSide.append(actions, badge);
  labelRow.append(labelText, rightSide);
  wrap.appendChild(labelRow);

  function updateBadge() {
    if (selected.freqCategories.size > 0) {
      badge.style.display = "";
      badge.textContent = `${selected.freqCategories.size} selected`;
    } else {
      badge.style.display = "none";
    }
  }

  function categoryCheckbox(opt) {
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = selected.freqCategories.has(opt.code);
    cb.addEventListener("change", () => {
      if (cb.checked) selected.freqCategories.add(opt.code);
      else selected.freqCategories.delete(opt.code);
      // The ILS sub-filters' visibility depends on this one checkbox, so
      // it needs a full re-render; the others only need the badge count.
      if (opt.code === ILS_PSEUDO_CATEGORY) {
        renderFilters();
      } else {
        updateBadge();
      }
      scheduleQuery();
    });
    let text = opt.label;
    if (opt.not_usable_on_fta_850l) text += " -- not usable on FTA-850L";
    lbl.append(cb, document.createTextNode(" " + text));
    return lbl;
  }

  // spec §3: default view is a curated set of ~9 common categories;
  // everything else (STAR/DP procedure fixes, military ops, RCAG, ...)
  // sits behind an explicit toggle so the panel doesn't overwhelm with
  // dozens of rarely-used checkboxes.
  const list = document.createElement("div");
  list.className = "multiselect-list";
  const defaultOptions = filterOptions.freq_categories.filter((o) => o.default_view);
  const advancedOptions = filterOptions.freq_categories.filter((o) => !o.default_view);
  for (const opt of defaultOptions) list.appendChild(categoryCheckbox(opt));

  if (advancedOptions.length > 0) {
    const advancedList = document.createElement("div");
    advancedList.className = freqCategoryAdvancedExpanded ? "" : "hidden";
    for (const opt of advancedOptions) advancedList.appendChild(categoryCheckbox(opt));
    list.appendChild(advancedList);

    const toggleBtn = document.createElement("button");
    toggleBtn.className = "btn btn-small";
    toggleBtn.style.marginTop = "6px";
    toggleBtn.textContent = freqCategoryAdvancedExpanded
      ? "Hide advanced categories"
      : `Advanced: show raw values (${advancedOptions.length})`;
    toggleBtn.addEventListener("click", () => {
      freqCategoryAdvancedExpanded = !freqCategoryAdvancedExpanded;
      renderFilters();
    });
    list.appendChild(toggleBtn);
  }

  wrap.appendChild(list);
  updateBadge();
  container.appendChild(wrap);
}

function renderFacilityStatusFilter(container) {
  const wrap = document.createElement("div");
  wrap.className = "filter-group";
  const label = document.createElement("div");
  label.className = "filter-group-label";
  const labelText = document.createElement("span");
  labelText.textContent = "Facility Status";
  const actions = groupActions(
    () => {
      for (const opt of filterOptions.facility_statuses) selected.facilityStatuses.add(opt.code);
      renderFilters();
      scheduleQuery();
    },
    () => {
      selected.facilityStatuses.clear();
      renderFilters();
      scheduleQuery();
    },
  );
  label.append(labelText, actions);
  wrap.appendChild(label);

  for (const opt of filterOptions.facility_statuses) {
    checkboxRow(wrap, opt.label, selected.facilityStatuses.has(opt.code), (checked) => {
      if (checked) selected.facilityStatuses.add(opt.code);
      else selected.facilityStatuses.delete(opt.code);
      scheduleQuery();
    });
  }

  container.appendChild(wrap);
}

function renderIlsSystemTypeFilter(container) {
  const wrap = document.createElement("div");
  wrap.className = "filter-group";
  const label = document.createElement("div");
  label.className = "filter-group-label";
  const labelText = document.createElement("span");
  labelText.textContent = "ILS System Type";
  const actions = groupActions(
    () => {
      for (const opt of filterOptions.ils_system_types) selected.ilsSystemTypes.add(opt.code);
      renderFilters();
      scheduleQuery();
    },
    () => {
      selected.ilsSystemTypes.clear();
      renderFilters();
      scheduleQuery();
    },
  );
  label.append(labelText, actions);
  wrap.appendChild(label);

  for (const opt of filterOptions.ils_system_types) {
    checkboxRow(wrap, opt.label, selected.ilsSystemTypes.has(opt.code), (checked) => {
      if (checked) selected.ilsSystemTypes.add(opt.code);
      else selected.ilsSystemTypes.delete(opt.code);
      scheduleQuery();
    });
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
  if (cityMultiSelect) cityMultiSelect.rerender(cities);
}

function renderRadiusFilters(container) {
  const wrap = document.createElement("div");
  wrap.className = "filter-group";
  const label = document.createElement("div");
  label.className = "filter-group-label";
  label.innerHTML = "<span>Geographic radius</span>";
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
  title.textContent = "Custom Frequencies";
  bar.appendChild(title);

  if (customEntries.length === 0) {
    const meta = document.createElement("span");
    meta.className = "muted";
    meta.textContent = "None imported yet -- from your radio, not FAA data";
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

  const h2 = document.createElement("h2");
  h2.textContent = "Custom Frequencies";
  box.appendChild(h2);

  const sub = document.createElement("p");
  sub.className = "muted";
  const groupCount = new Set(customEntries.map((e) => e.group)).size;
  sub.textContent = `${customEntries.length} entries · ${groupCount} groups · imported from your radio`;
  box.appendChild(sub);

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
  box.appendChild(table);

  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = "Note: placing a custom entry into one of the 6 fixed group names above will cause it to be discarded on the next import, since the app can't tell it apart from its own regenerated entries.";
  box.appendChild(note);

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
  box.appendChild(actionsRow);

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

function showGroupSetupModal(isAutoPrompt) {
  const modal = document.getElementById("group-setup-modal");
  const box = modal.querySelector(".modal-box");
  box.innerHTML = "";

  const h2 = document.createElement("h2");
  h2.textContent = "Rename your memory groups first";
  box.appendChild(h2);

  const p1 = document.createElement("p");
  p1.textContent = "YCE-64 can't create new groups -- it only has 9 fixed slots (GROUP1-GROUP9) that you rename. If a group name in the XML doesn't already exist in YCE-64, that entry's grouping is silently dropped.";
  box.appendChild(p1);

  const p2 = document.createElement("p");
  p2.textContent = "Open YCE-64 -> Setup -> Memory Group Name, and rename 6 of the 9 slots to match these exactly (same six names for every export, regardless of filters):";
  box.appendChild(p2);

  const list = document.createElement("ul");
  list.className = "group-name-list";
  for (const name of fixedGroupNames) {
    const li = document.createElement("li");
    li.textContent = name;
    list.appendChild(li);
  }
  box.appendChild(list);

  const actionsRow = document.createElement("div");
  actionsRow.className = "modal-actions";
  const copyBtn = document.createElement("button");
  copyBtn.className = "btn";
  copyBtn.textContent = "Copy names";
  copyBtn.addEventListener("click", () => {
    navigator.clipboard?.writeText(fixedGroupNames.join(", "));
  });
  const continueBtn = document.createElement("button");
  continueBtn.className = "btn btn-primary";
  continueBtn.textContent = isAutoPrompt ? "I've renamed my groups -- Continue" : "Close";
  continueBtn.addEventListener("click", async () => {
    if (!groupSetupAcknowledged) {
      await api("/api/group-setup/acknowledge", { method: "POST" });
      groupSetupAcknowledged = true;
    }
    modal.classList.add("hidden");
  });
  actionsRow.append(copyBtn, continueBtn);
  box.appendChild(actionsRow);

  modal.classList.remove("hidden");
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
  const num = document.createElement("span");
  num.className = "count-number";
  num.textContent = result.total_count;
  const cap = document.createElement("span");
  cap.className = "cap-text";
  cap.textContent = `entries (cap ${result.cap})`;
  el.append(num, cap);
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
    for (const val of [e.tag_name, e.freq_mhz.toFixed(3), e.group, `${e.airport_id} - ${e.airport_name}`, `${e.city}, ${e.state}`]) {
      const td = document.createElement("td");
      td.textContent = val;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
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

    if (!groupSetupAcknowledged) showGroupSetupModal(true);
  } finally {
    btn.disabled = lastQueryResult ? lastQueryResult.level === "red" || lastQueryResult.total_count === 0 : false;
  }
});

init();
