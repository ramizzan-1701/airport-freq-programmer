"""FAA-specific classification: maps FRQ.csv's raw FREQ_USE, FACILITY_TYPE,
and SERVICED_SITE_TYPE columns to the normalized vocabulary in
afp.classification (spec §3). This is the only module permitted to know
what these raw column values actually look like -- the rest of the app
only ever sees the classified output.

Rules and their ordering are validated against the live 2026-08-06 NASR
cycle (40,936 FRQ.csv rows): the WEATHER_STATION rule alone reproduces
the spec's own stated real-data count exactly (773 ATIS/D-ATIS +
2,337 ASOS_AWOS-facility = 3,110 rows), and the full ruleset classifies
97% of all rows, with the remainder falling into OTHER by design (spec:
"so nothing is silently dropped") -- confirmed to be genuine long-tail
noise (military precision-approach abbreviations, "APT REMARK N WITH ...
FREQ" quirks), not a gap in the rule table.

Two corrections made against what a literal reading of the spec's rule
table would produce, both driven by real data:
  - GROUND matches an explicit list of real GND/* variants, not a bare
    "starts with GND" prefix -- real data has a "GNDLF STAR" procedure-fix
    row that would otherwise false-positive as Ground Control.
  - Airspace-class rows (CLASS B/C, TRSA) get their own AIRSPACE_INFO
    category rather than falling into OTHER, so they're identifiable as
    the specific thing the spec describes ("informational metadata, not a
    selectable filter") rather than indistinguishable noise. See
    afp.classification.NON_SELECTABLE_CATEGORIES for how selection.py
    acts on that.
"""

from __future__ import annotations

_WEATHER_FREQ_USES = frozenset({"ATIS", "D-ATIS"})
_TOWER_FREQ_USES = frozenset({"LCL/P", "LCL/S", "LCL/P IC"})
_GROUND_FREQ_USES = frozenset({"GND/P", "GND/S", "GND/P IC", "GND METERING"})
_APCH_DEP_PREFIXES = ("APCH/", "DEP/")
_AIRSPACE_INFO_FREQ_USES = frozenset({"CLASS B", "CLASS C", "CLASS C/S", "TRSA"})
_VOR_VALUES = frozenset({"VOR", "VORTAC", "VOR/DME"})
_NDB_VALUES = frozenset({"NDB", "NDB/DME", "MARINE NDB"})
_RCAG_VALUES = frozenset({"RCAG", "RCO", "RCO1", "RADIO"})
_ARTCC_VALUES = frozenset({"ARTCC", "CERAP"})
_MIL_GOV_OPS_FREQ_USES = frozenset({
    "OPS", "ARNG OPS", "ANG OPS", "COMD POST", "NASA OPS",
    "NG OPS", "BASE OPS", "RANGE CTL", "RAMP CTL",
})


def classify_freq_category(freq_use: str, facility_type: str, serviced_site_type: str) -> str:
    fu = (freq_use or "").upper().strip()
    ft = (facility_type or "").upper().strip()
    sst = (serviced_site_type or "").upper().strip()

    # Weather Station spans all three columns: ATIS is a broadcast a
    # tower performs (FREQ_USE only), while ASOS/AWOS is the FAA's own
    # separate unstaffed-equipment record (FACILITY_TYPE and
    # SERVICED_SITE_TYPE, never FREQ_USE alone, and never FACILITY_TYPE
    # ATCT*, confirmed against every ASOS/AWOS row nationwide).
    if fu in _WEATHER_FREQ_USES or ft == "ASOS_AWOS" or sst == "ASOS" or sst.startswith("AWOS"):
        return "WEATHER_STATION"
    if fu == "CTAF":
        return "CTAF"
    if fu in _TOWER_FREQ_USES:
        return "TOWER"
    if fu in _GROUND_FREQ_USES:
        return "GROUND"
    if fu.startswith("CD/") or "CD PRE" in fu:
        return "CLEARANCE"
    if fu == "UNICOM":
        return "UNICOM"
    if fu.startswith(_APCH_DEP_PREFIXES):
        return "APCH_DEP"
    if fu in _AIRSPACE_INFO_FREQ_USES:
        return "AIRSPACE_INFO"
    if fu in _VOR_VALUES or sst in _VOR_VALUES:
        return "VOR"
    if fu == "VOT" or sst == "VOT":
        return "VOT"
    if fu in _NDB_VALUES or sst in _NDB_VALUES:
        return "NDB"
    if sst == "DME":
        return "DME"
    if fu == "TACAN" or sst == "TACAN":
        return "TACAN"
    if ft in _RCAG_VALUES or sst in _RCAG_VALUES:
        return "RCAG"
    if ft == "TRACON" or sst == "TRACON":
        return "TRACON"
    if ft == "FSS" or sst == "FSS":
        return "FSS"
    if ft in _ARTCC_VALUES or sst in _ARTCC_VALUES:
        return "ARTCC"
    if (
        fu in _MIL_GOV_OPS_FREQ_USES
        or fu.endswith(" OPS")
        or fu.endswith(" CTL")
        or "COMD POST" in fu
    ):
        return "MIL_GOV_OPS"
    if fu == "EMERG":
        return "EMERGENCY"
    if fu.endswith("STAR") or fu.endswith("DP") or "RNAV" in fu:
        return "PROCEDURE_FIX"
    return "OTHER"


# spec §3: "Platform / Site Type ... Scope this filter to just: AIRPORT,
# HELIPORT, SEAPLANE BASE, GLIDERPORT, BALLOONPORT, ULTRALIGHT."
_PLATFORM_TYPES = frozenset({
    "AIRPORT", "HELIPORT", "SEAPLANE BASE", "GLIDERPORT", "BALLOONPORT", "ULTRALIGHT",
})


def classify_platform_type(serviced_site_type: str) -> str | None:
    sst = (serviced_site_type or "").upper().strip()
    return sst if sst in _PLATFORM_TYPES else None


def classify_weather_subtype(freq_use: str, serviced_site_type: str) -> str | None:
    """ATIS vs ASOS vs AWOS within the unified WEATHER_STATION category
    (spec §3's optional sub-filter). Only meaningful when
    classify_freq_category(...) == "WEATHER_STATION" for the same row.

    Real NASR data prefixes every single ASOS/AWOS FREQ_USE with the
    facility ID (e.g. "CVH AWOS-3", "0J4 ASOS") -- never the plain
    "AWOS-3"/"ASOS" a literal column-value match would expect. Checking
    for the token anywhere in FREQ_USE (rather than an exact match)
    handles that; falling back to SERVICED_SITE_TYPE covers the rows
    where FREQ_USE doesn't even mention it. Confirmed against every one
    of the 2,337 real ASOS_AWOS-facility rows in the live 2026-08-06
    cycle: all 2,337 carry an explicit ASOS/AWOS signal in FREQ_USE or
    SERVICED_SITE_TYPE, so no further fallback is needed.
    """
    fu = (freq_use or "").upper().strip()
    sst = (serviced_site_type or "").upper().strip()

    if fu in _WEATHER_FREQ_USES:
        return "ATIS"
    if "ASOS" in fu or sst == "ASOS":
        return "ASOS"
    if "AWOS" in fu or sst.startswith("AWOS"):
        return "AWOS"
    return None


def classify_facility_status(facility_type: str) -> str | None:
    ft = (facility_type or "").upper().strip()
    if ft == "NON-ATCT":
        return "NON_TOWERED"
    if ft.startswith("ATCT"):
        return "TOWERED"
    return None
