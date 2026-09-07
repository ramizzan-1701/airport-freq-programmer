"""Normalized classification vocabulary (spec §2/§3): the fixed sets of
values afp.schema.Frequency's freq_category/platform_type/facility_status
fields can take, plus metadata about them (display labels, which
categories make up the default filter view, which are unusable on the
FTA-850L, which never produce a standalone selectable entry).

This module knows nothing about raw FAA column names or values -- that
classification logic (mapping FREQ_USE/FACILITY_TYPE/SERVICED_SITE_TYPE to
these normalized values) lives in the FAA adapter, the only layer
permitted to know about source-specific columns. Everything here is
normalized-schema vocabulary, safe for selection/query/UI code to import.
"""

from __future__ import annotations

FREQ_CATEGORY_LABELS: dict[str, str] = {
    "CTAF": "CTAF",
    "TOWER": "Tower / Local Control",
    "GROUND": "Ground",
    "CLEARANCE": "Clearance Delivery",
    "WEATHER_STATION": "Weather Station",
    "UNICOM": "UNICOM",
    "APCH_DEP": "Approach / Departure",
    "VOR": "VOR family",
    "VOT": "VOR Test (VOT)",
    "NDB": "NDB family",
    "DME": "DME (standalone)",
    "TACAN": "TACAN",
    "RCAG": "Remote Comm Relay",
    "TRACON": "Approach Control (TRACON)",
    "FSS": "Flight Service Station",
    "ARTCC": "En Route Center",
    "MIL_GOV_OPS": "Military / Gov Ops",
    "EMERGENCY": "Emergency",
    "PROCEDURE_FIX": "Procedure fixes (STAR/DP)",
    # Airspace-class annotations (CLASS B/C, TRSA) aren't a frequency
    # category at all per spec -- they're a usage note on a co-frequency
    # Approach/Departure row. Kept as their own category (rather than
    # dumped into OTHER) so they're identifiable, but never independently
    # selectable -- see NON_SELECTABLE_CATEGORIES.
    "AIRSPACE_INFO": "Airspace class (informational)",
    "OTHER": "Other / Uncategorized",
    # Pseudo-category: ILS/localizer records live in their own table
    # (ils, from ILS_BASE.csv) with no FREQ_USE/FACILITY_TYPE/
    # SERVICED_SITE_TYPE of its own, so this value never actually appears
    # in Frequency.freq_category -- it exists purely so the filter UI can
    # treat "include ILS entries" as one more checkbox in this same list
    # instead of a separate control. Checked = included, unchecked =
    # excluded, exactly like every real category (see
    # afp.query.filters.FilterState.should_include_ils).
    "ILS": "ILS / Localizer",
}

# Terse forms for places that show many categories side by side -- the
# results breakdown, where the full labels are long enough that almost
# every chip took its own line (17 categories filled 15 rows and half the
# window). Mostly the cockpit shorthand a pilot would say out loud.
#
# These never replace FREQ_CATEGORY_LABELS: the filter list still spells
# each one out, and anywhere a short form appears it carries the full
# label as a tooltip. Any category without an entry here falls back to
# its full label.
FREQ_CATEGORY_SHORT_LABELS: dict[str, str] = {
    "TOWER": "Tower",
    "CLEARANCE": "Clearance",
    "WEATHER_STATION": "Weather",
    "APCH_DEP": "APCH/DEP",
    "VOR": "VOR",
    "VOT": "VOT",
    "DME": "DME",
    "RCAG": "RCAG",
    "TRACON": "TRACON",
    "FSS": "FSS",
    "ARTCC": "ARTCC",
    "MIL_GOV_OPS": "Mil/Gov",
    "PROCEDURE_FIX": "STAR/DP",
    "AIRSPACE_INFO": "Airspace",
    "OTHER": "Other",
    "ILS": "ILS",
}


def short_freq_category_label(code: str) -> str:
    """Terse label for `code`, falling back to the full one (CTAF, NDB,
    TACAN, UNICOM and Ground are already short enough to reuse as-is).
    """
    return FREQ_CATEGORY_SHORT_LABELS.get(code, FREQ_CATEGORY_LABELS.get(code, code))


# spec §3: "Default UI view: CTAF, Tower, Ground, Clearance, Weather
# Station, UNICOM, Approach/Departure, VOR, VOT. Everything else behind
# an 'Advanced: show raw values' toggle." ILS added per user direction --
# central enough to the original use case to earn a default-view slot.
# VOT (VOR Test) moved to Advanced per user direction -- a standalone
# ground test signal, not something most users need in the default view.
DEFAULT_VIEW_CATEGORIES: tuple[str, ...] = (
    "CTAF", "TOWER", "GROUND", "CLEARANCE", "WEATHER_STATION",
    "UNICOM", "APCH_DEP", "VOR", "ILS",
)

ALL_FREQ_CATEGORIES: tuple[str, ...] = tuple(FREQ_CATEGORY_LABELS.keys())

ILS_PSEUDO_CATEGORY = "ILS"

# spec §3: NDB family is below the VHF aviation band -- flag as not
# usable on the FTA-850L rather than silently including it.
NOT_USABLE_ON_FTA_850L: frozenset[str] = frozenset({"NDB"})

# spec §3: airspace-class rows are "informational metadata, not a
# selectable filter" -- they never produce their own entry.
NON_SELECTABLE_CATEGORIES: frozenset[str] = frozenset({"AIRSPACE_INFO"})

# Removed from the web UI's Frequency Category list entirely (not just
# tucked behind "Advanced") per user direction -- CLI/API filtering by
# these still works, this only trims what the web UI exposes:
#   AIRSPACE_INFO -- already in NON_SELECTABLE_CATEGORIES, so it never
#     produces an actual entry regardless of whether it's checked; a
#     functionally inert filter option.
#   EMERGENCY -- a single universally-known frequency (121.5) pilots
#     already have memorized, not something worth filtering on.
#   NDB -- already NOT_USABLE_ON_FTA_850L (below the VHF band); removed
#     outright rather than just flagged unusable.
HIDDEN_FROM_WEB_UI_CATEGORIES: frozenset[str] = frozenset({"AIRSPACE_INFO", "EMERGENCY", "NDB"})

# spec §3: "Platform / Site Type ... Scope this filter to just: AIRPORT,
# HELIPORT, SEAPLANE BASE, GLIDERPORT, BALLOONPORT, ULTRALIGHT."
PLATFORM_TYPES: tuple[str, ...] = (
    "AIRPORT", "HELIPORT", "SEAPLANE BASE", "GLIDERPORT", "BALLOONPORT", "ULTRALIGHT",
)

FACILITY_STATUS_LABELS: dict[str, str] = {
    "TOWERED": "Towered Airport",
    "NON_TOWERED": "Non-Towered Airport",
}

# ILS_BASE.csv's SYSTEM_TYPE_CODE, passed through onto Ils.system_type
# unmodified -- unlike freq_category/platform_type/facility_status there's
# no adapter-side classification step for it, since the FAA already
# reports a small fixed code set here. Codes and descriptions are the
# FAA's own, from the NASR "ILS Data Layout" reference doc (SD -- SDF/DME
# -- is a documented code that just hasn't appeared in any downloaded
# cycle yet). Order matters: afp.selection derives its "prefer richer
# guidance" runway-end tie-break directly from this dict's insertion
# order, from full ILS-equivalent guidance (localizer + glideslope, with
# or without DME) down through LOC-only, LDA, and finally SDF.
ILS_SYSTEM_TYPE_LABELS: dict[str, str] = {
    "LS": "ILS (Instrument Landing System)",
    "LD": "ILS/DME",
    "LG": "LOC/GS (Localizer/Glide Slope)",
    "LE": "LOC/DME",
    "LC": "LOC (Localizer)",
    "DD": "LDA/DME",
    "LA": "LDA (Localizer-type Directional Aid)",
    "SD": "SDF/DME",
    "SF": "SDF (Simplified Directional Facility)",
}

# APT_BASE.csv's STATE_CODE / FRQ.csv's SERVICED_STATE, passed through
# unmodified onto Airport.state / Frequency.state -- like ILS_SYSTEM_TYPE_
# LABELS above, this is real FAA-assigned vocabulary rather than an
# adapter classification step. Confirmed against the live 2026-08-06
# cycle: 63 distinct values total -- the 50 states plus DC, the standard
# US territories (PR, VI, GU, AS, MP), and a handful of non-US/non-state
# codes for remote facilities still under FAA jurisdiction (Bahamas,
# Bermuda, Turks and Caicos, Midway/Wake/Palmyra Atolls, Gulf of Mexico
# offshore platforms).
US_STATE_LABELS: dict[str, str] = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia",
    "PR": "Puerto Rico",
    "VI": "U.S. Virgin Islands",
    "GU": "Guam",
    "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
    "BS": "Bahamas",
    "IB": "Turks and Caicos Islands",
    "OA": "Bermuda",
    "OG": "Gulf of Mexico offshore platforms",
    "QM": "Midway Atoll",
    "QW": "Wake Island",
    "XL": "Palmyra Atoll",
}
