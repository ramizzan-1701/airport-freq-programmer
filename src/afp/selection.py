"""Selection logic: turns normalized data into a flat list of export Entry
objects, independent of any export profile's formatting/length rules.

Two modes:
  - "smart" (default): priority logic already established by hand-built
    prior iterations -- prefer Tower over CTAF, prefer ATIS over ASOS over
    AWOS, one LOC entry per runway end, drop a UNICOM entry that duplicates
    the airport's CTAF frequency.
  - "raw": filters applied literally, every matching row becomes its own
    entry, no priority collapsing.

Tag uniqueness and length are export-profile concerns (see afp.export) --
this module only produces candidate tag names from the documented naming
convention ({AIRPORT_ID}-{TYPE}); it deliberately does not deduplicate or
truncate, so collisions surface explicitly at export time rather than being
silently resolved here. The one exception is {AIRPORT_ID} itself for a
standalone facility with no APT_BASE row (see orphan_tag_ids/
_abbreviate_facility_id below): the naming convention assumes a short,
LID-like {AIRPORT_ID}, but FRQ.csv's SERVICED_FACILITY is sometimes a full
place name for those, so it's shortened to fit before tag names are ever
built -- a data-quality fix, not the general truncate-to-fit behavior this
module otherwise avoids.

Comm/nav entries are keyed off Frequency.freq_category (the adapter's
classification, not raw FREQ_USE) -- this module no longer does its own
raw-string matching for CTAF/Tower/Weather detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .classification import (
    ILS_PSEUDO_CATEGORY,
    ILS_SYSTEM_TYPE_LABELS,
    NON_SELECTABLE_CATEGORIES,
)
from .schema import Airport, Frequency, Ils, NormalizedData

# ILS system-type and status priority for smart-mode "one entry per runway
# end" selection: prefer an unrestricted component over a restricted one,
# and prefer richer approach guidance (a full ILS, with or without DME,
# beats LOC-only, which beats LDA, which beats SDF) when a runway end has
# rows for more than one system type. System-type ordering is derived
# from ILS_SYSTEM_TYPE_LABELS' insertion order rather than duplicated here
# -- see afp.classification for the real FAA SYSTEM_TYPE_CODE values this
# keys on (previously this table used made-up codes like "ILS"/"LOC" that
# never matched real data's two-letter codes and was silently a no-op).
_SYSTEM_TYPE_PRIORITY = {code: i for i, code in enumerate(ILS_SYSTEM_TYPE_LABELS)}
_STATUS_PRIORITY = {"OPERATIONAL IFR": 0, "OPERATIONAL RESTRICTED": 1}

_WEATHER_SUBTYPE_PRIORITY = {"ATIS": 0, "ASOS": 1, "AWOS": 2}

# Categories with a stable, canonical short tag suffix regardless of
# within-category raw-text variation (spec naming convention example:
# "SNS-CT", not a mechanical sanitization of the raw column).
_FIXED_TAG_SUFFIXES = {
    "CTAF": "CTAF",
    "TOWER": "CT",
    "GROUND": "GND",
    "CLEARANCE": "CD",
    "UNICOM": "UNICOM",
    "VOR": "VOR",
    "VOT": "VOT",
    "NDB": "NDB",
    "DME": "DME",
    "TACAN": "TACAN",
    "RCAG": "RCAG",
    "TRACON": "TRACON",
    "FSS": "FSS",
    "ARTCC": "ARTCC",
    "EMERGENCY": "EMERG",
}

# Categories where the raw FREQ_USE text carries genuinely useful
# operational distinction beyond the category itself (which TRACON
# sector, which named military ops channel, which unclassified thing) --
# tag suffix derived from sanitized raw_freq_use instead of a fixed code.
# WEATHER_STATION is handled separately via weather_subtype.
_RAW_TEXT_SUFFIX_CATEGORIES = frozenset({"APCH_DEP", "MIL_GOV_OPS", "PROCEDURE_FIX", "OTHER"})

# Cap for the {AIRPORT_ID} half of a standalone facility's tag. Real FAA
# LIDs (APT_BASE.csv ARPT_ID, confirmed nationwide 2026-08-06 cycle) are
# never more than 4 chars, so the naming convention's {AIRPORT_ID}-{TYPE}
# assumes a short prefix -- but for a standalone facility with no APT_BASE
# row (RCAG relay, ARTCC center, ...), FRQ.csv's SERVICED_FACILITY is
# sometimes a full place name instead of a code (e.g. "MEDICINE BOW",
# confirmed present for 590 of 1,605 standalone facility IDs nationwide,
# 2026-08-06 cycle) and blows past the FTA-850L / YCE-46's 14-char tag
# cap. 6 chars leaves room for the longest fixed suffix (-TRACON/-UNICOM,
# 7 chars incl. dash) plus one spare char for _unique_tag's numeric
# disambiguator: 6 + 7 + 1 == 14.
_ORPHAN_ID_MAX_LEN = 6


def _abbreviate_facility_id(facility_id: str) -> str:
    """Deterministically shrink a standalone facility's raw SERVICED_FACILITY
    value to an LID-like code of at most _ORPHAN_ID_MAX_LEN chars, for use
    as the {AIRPORT_ID} half of its tags. Values already at or under the
    cap (the vast majority nationwide -- real short relay/center codes,
    plus plenty of short single-word place names) pass through unchanged
    aside from stripping punctuation, so most tags don't change at all.

    Longer, usually multi-word, place names are abbreviated by keeping the
    first word's lead and the first letter of each subsequent word (e.g.
    "MEDICINE BOW" -> "MEDICB", "FORT BRIDGER" -> "FORTB") so the result
    stays recognizable rather than an arbitrary truncation.
    """
    words = re.findall(r"[A-Z0-9]+", facility_id.upper())
    if not words:
        return "X"
    joined = "".join(words)
    if len(joined) <= _ORPHAN_ID_MAX_LEN:
        return joined
    if len(words) == 1:
        return words[0][:_ORPHAN_ID_MAX_LEN]
    first_len = max(1, _ORPHAN_ID_MAX_LEN - (len(words) - 1))
    abbrev = words[0][:first_len] + "".join(w[0] for w in words[1:])
    return abbrev[:_ORPHAN_ID_MAX_LEN]


def orphan_tag_ids(airport_ids: set[str], facility_ids: set[str]) -> dict[str, str]:
    """Map every standalone facility's raw ID (one in `facility_ids` with
    no matching entry in `airport_ids`) to its short tag-name ID.

    A single pass over all of them (rather than abbreviating one at a
    time) so collisions can be caught and disambiguated here -- both
    between two different facilities that abbreviate to the same code,
    and against `airport_ids` itself (confirmed on real nationwide data:
    the standalone RCAG relay facility "EL DORADO" abbreviates to "ELD",
    which is also the real, unrelated Eldorado Regional airport's LID) --
    deterministic within a call, in sorted order, mirroring _unique_tag's
    plain-then-numbered convention. Exposed publicly (not just used
    internally by select_entries) so callers that need to map a generated
    tag's prefix back to the facility it names -- e.g. the web UI's
    results table -- can reproduce the exact same mapping instead of
    guessing.
    """
    orphan_ids = facility_ids - airport_ids
    assigned: dict[str, str] = {}
    used: set[str] = set(airport_ids)
    for facility_id in sorted(orphan_ids):
        base = _abbreviate_facility_id(facility_id)
        candidate = base
        n = 1
        while candidate in used:
            n += 1
            suffix = str(n)
            candidate = base[: _ORPHAN_ID_MAX_LEN - len(suffix)] + suffix
        used.add(candidate)
        assigned[facility_id] = candidate
    return assigned


@dataclass(frozen=True)
class Entry:
    tag_name: str
    freq_mhz: float
    group: str
    lat: float
    lon: float
    scan_memory: str = "Off"
    shift: str = "Off"
    # The freq_category this entry came from, carried through so the UI can
    # break a result set down by category. Defaults to "" because custom
    # entries are parsed from a radio export that has no such concept --
    # and because local_store round-trips these through JSON, where an
    # older file simply won't have the key.
    category: str = ""


# The 6 fixed group names default_group_for ever returns -- exported as a
# constant so other code (the group-setup CTA, and the custom-entry import
# flow's recognized/custom split) has a single source of truth rather than
# re-deriving or duplicating this list.
# The top of what the radio can tune. The FTA-850 covers the VHF airband
# and the nav band below it; it has no receiver above the airband at all,
# so anything at or above this is not a frequency the user could ever
# select -- it is a memory slot spent on nothing.
#
# NASR carries the military UHF assignments (225-400 MHz) in the same
# table as the VHF ones, plus a few radar entries far higher: 12,354 of
# 40,388 rows in the 2026-09-03 cycle, 31% of the file. Every one was
# eligible for the 400-entry export, and 658 of them were TOWER rows --
# which is also how a UHF tower row could win the primary-comm slot for
# an airport and push the VHF one out.
#
# No lower bound: the nav band (VOR from 108.0, ILS localizers 108.3 to
# 111.95 on this cycle) is below the airband and is tunable. NDB, which
# is far lower and genuinely unusable, is already excluded by category.
MAX_TUNABLE_MHZ = 137.0

FIXED_GROUP_NAMES = frozenset({"0-9", "A-E", "F-J", "K-O", "P-T", "U-Z"})


def default_group_for(airport_id: str) -> str:
    """Fixed group-name scheme, keyed on the airport ID's first character:
    0-9, A-E, F-J, K-O, P-T, U-Z. This is the permanent scheme (not a
    placeholder for future dynamic/balanced grouping) -- the user has to
    manually rename the 9 fixed group slots in YCE-46 to match whatever
    names the app emits (see spec §5), so a stable, predictable set of
    group names matters more than evenly balancing entries across them.
    """
    if not airport_id:
        return "0-9"
    ch = airport_id[0].upper()
    if ch.isdigit():
        return "0-9"
    if "A" <= ch <= "E":
        return "A-E"
    if "F" <= ch <= "J":
        return "F-J"
    if "K" <= ch <= "O":
        return "K-O"
    if "P" <= ch <= "T":
        return "P-T"
    return "U-Z"


def _sanitize(component: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", component.upper())


def _procedure_fix_suffix(raw_freq_use: str | None) -> str:
    """"DNKIN RNAV STAR" -> "STAR"; "TRUKN TWO DP" -> "DP".

    The procedure's own name leads every one of these values, but it's
    the kind of thing the radio's frequency display already identifies,
    and carrying it made tags like CYXX-MADEERNAVSTAR (18 chars) that
    blow past the 14-char cap and block the whole export. The "RNAV"/
    "RNV" variants are noise for this purpose too -- what's worth
    keeping is only whether it's an arrival or a departure.

    Confirmed against the 2026-09-03 cycle: all 2131 rows end in either
    STAR (1128) or DP (1003), the only two procedure types the FAA
    publishes here. Anything else falls back to the old raw-text
    behaviour rather than being silently mislabelled as one of them.
    """
    tokens = (raw_freq_use or "").strip().upper().split()
    if tokens and tokens[-1] in ("STAR", "DP"):
        return tokens[-1]
    return _sanitize(raw_freq_use)


def _apch_dep_suffix(raw_freq_use: str | None) -> str:
    """"APCH/P DEP/P IC" -> "APCHDEP"; "DEP/S" -> "DEP"; "APCH/P" -> "APCH".

    Every one of the 13 distinct values in the 2026-09-03 cycle is built
    from APCH and/or DEP plus a /P (primary) or /S (secondary) marker and
    an optional IC. The markers don't survive: they pushed tags to
    APCHPDEPPIC (11 chars, 16 with an airport ID) and aren't a
    distinction worth the length. Whether the frequency is an arrival, a
    departure, or both is kept -- 532 rows are approach-only and 381
    departure-only nationwide, so collapsing all three to one label would
    put a departure tag on an approach-only frequency.
    """
    text = (raw_freq_use or "").upper()
    has_apch = "APCH" in text
    has_dep = "DEP" in text or "DE/P" in text  # "DE/P" is a real typo in FAA data
    if has_apch and has_dep:
        return "APCHDEP"
    if has_apch:
        return "APCH"
    if has_dep:
        return "DEP"
    return _sanitize(raw_freq_use)


# The radio's tag cap. Duplicated from ExportProfile rather than imported
# because selection stays independent of any one export profile (see the
# module docstring) -- the same reasoning _ORPHAN_ID_MAX_LEN already
# relies on. Export-time validation remains the real enforcement.
_TAG_LEN_BUDGET = 14

# Fallback when the prefix isn't known: the tightest case, a 6-char
# abbreviated orphan ID. 6 + "-" + 6 + one char for _unique_tag's
# numeric disambiguator == 14.
_MAX_SUFFIX_LEN = 6


def _suffix_budget(prefix: str) -> int:
    """Chars available for the suffix given this tag's actual prefix.

    Budgeting against the real prefix rather than the worst case matters:
    a 4-char LID leaves 8, and forcing everything down to the 6 that a
    long orphan ID would need turns perfectly valid suffixes like
    ARNGOPS into ARNGO for no reason.

    One char is held back for _unique_tag's numeric disambiguator, since
    a second frequency in the same category at the same airport appends
    a digit after the fact.
    """
    return max(1, _TAG_LEN_BUDGET - len(prefix) - 1 - 1)

# Multi-word phrases that recur in FAA free text and have an obvious
# short form. Applied before the generic abbreviator below, which would
# otherwise turn "COMD POST" into something less recognisable.
_SUFFIX_PHRASES = (
    ("COMD POST", "CP"),
    ("VFR SEQUENCING", "VFRSEQ"),
    ("AIRSPACE ATIS", "ATIS"),
)


def _shorten_suffix(raw_freq_use: str | None, max_len: int = _MAX_SUFFIX_LEN) -> str:
    """Sanitize a raw FREQ_USE into a tag suffix that can't overflow.

    Known phrases are rewritten first ("ANG COMD POST" -> "ANGCP"), then
    anything still too long is abbreviated the same way facility IDs are
    -- first word's lead plus each later word's initial -- rather than
    blindly truncated, so "MAINT CTL CENTER" reads as MAINCC instead of
    MAINTC.

    The point is that no FAA value, including ones a future cycle
    introduces, can produce a tag that fails validation and blocks the
    entire export.
    """
    text = (raw_freq_use or "").upper()
    for phrase, short in _SUFFIX_PHRASES:
        if phrase in text:
            text = text.replace(phrase, short)
    sanitized = _sanitize(text)
    if len(sanitized) <= max_len:
        return sanitized

    words = re.findall(r"[A-Z0-9]+", text)
    if len(words) <= 1:
        return sanitized[:max_len]
    first_len = max(1, max_len - (len(words) - 1))
    abbrev = words[0][:first_len] + "".join(w[0] for w in words[1:])
    return abbrev[:max_len]


def _other_suffix(raw_freq_use: str | None, max_len: int = _MAX_SUFFIX_LEN) -> str:
    """FAA airport-remark rows ("APT REMARK 100 WITH GCO FREQ") collapse
    to APTRMK; everything else in OTHER passes through unchanged.

    The remark number and the "WITH <x> FREQ" tail are bookkeeping from
    the source data rather than anything identifiable in flight, and they
    produced the longest tags in the whole dataset (up to 29 chars). The
    other 503 OTHER rows nationwide are already short codes (PTD, GCA,
    PMSV METRO) that fit fine and are left alone.
    """
    if (raw_freq_use or "").strip().upper().startswith("APT REMARK"):
        return "APTRMK"
    return _shorten_suffix(raw_freq_use, max_len)


def _tag_suffix(f: Frequency, max_len: int = _MAX_SUFFIX_LEN) -> str:
    if f.freq_category == "WEATHER_STATION":
        return f.weather_subtype or "WX"
    # Checked ahead of _RAW_TEXT_SUFFIX_CATEGORIES, which PROCEDURE_FIX is
    # still a member of: that set also drives the "shortest raw label
    # wins" tie-break in _comm_entries, which stays as it was. Only the
    # tag suffix changes here.
    if f.freq_category == "PROCEDURE_FIX":
        return _procedure_fix_suffix(f.raw_freq_use)
    if f.freq_category == "APCH_DEP":
        return _apch_dep_suffix(f.raw_freq_use)
    if f.freq_category == "OTHER":
        return _other_suffix(f.raw_freq_use, max_len)
    # MIL_GOV_OPS is the remaining raw-text category: mostly short codes
    # ("OPS", "ARNG OPS") that pass through untouched, with the command
    # posts rewritten by _SUFFIX_PHRASES.
    if f.freq_category in _RAW_TEXT_SUFFIX_CATEGORIES:
        return _shorten_suffix(f.raw_freq_use, max_len)
    return _FIXED_TAG_SUFFIXES.get(f.freq_category, _sanitize(f.raw_freq_use))


def _unique_tag(base: str, tag_counts: dict[str, int]) -> str:
    """First use of `base` at this airport keeps it plain; each repeat
    gets a numeric suffix (BASE, BASE2, BASE3, ...).

    Real NASR data has airports with several genuinely distinct
    frequencies sharing one category -- e.g. a TRACON with four separate
    APCH/S sector frequencies, or dual VHF/UHF EMERG -- which would
    otherwise all sanitize to the same tag and collide. The radio's
    display always shows the frequency next to the tag name, so a bare
    numeric suffix is enough to tell them apart.
    """
    tag_counts[base] = tag_counts.get(base, 0) + 1
    n = tag_counts[base]
    return base if n == 1 else f"{base}{n}"


def _make_entry(f: Frequency, airport: Airport, freq_mhz: float, tag_counts: dict[str, int]) -> Entry:
    base = f"{airport.id}-{_tag_suffix(f, _suffix_budget(airport.id))}"
    return Entry(
        tag_name=_unique_tag(base, tag_counts),
        freq_mhz=freq_mhz,
        group="",
        lat=f.lat if f.lat is not None else airport.lat,
        lon=f.lon if f.lon is not None else airport.lon,
        category=f.freq_category,
    )


def _comm_entries(
    airport: Airport, freqs: list[Frequency], mode: str
) -> list[Entry]:
    # Airspace-class annotations (CLASS B/C, TRSA -> AIRSPACE_INFO) aren't
    # a real frequency at all -- spec: "informational metadata, not a
    # selectable filter". Never produce a standalone entry for them, in
    # either mode.
    freqs = [f for f in freqs if f.freq_category not in NON_SELECTABLE_CATEGORIES]
    entries: list[Entry] = []
    tag_counts: dict[str, int] = {}

    if mode == "raw":
        for f in freqs:
            entries.append(_make_entry(f, airport, f.freq_mhz, tag_counts))
        return entries

    # smart mode: Tower beats CTAF, but only on a shared frequency.
    #
    # This took the first TOWER row and dropped CTAF outright, which was
    # wrong twice over on real data (2026-09-03 cycle):
    #
    #   NASR lists a tower's primary (LCL/P) and secondary (LCL/S)
    #   channels as separate rows in no guaranteed order, so "the first
    #   one" could be a secondary. At HWD that published the secondary on
    #   118.9 and dropped the primary on 120.2. 14 airports were affected,
    #   some badly: IDA's first tower row is 109.0, a nav-band artifact,
    #   chosen over the real 118.5.
    #
    #   And dropping CTAF whatever its frequency lost genuinely separate
    #   channels -- AKN tower 118.3 against CTAF 121.9, BIG 119.8 against
    #   122.9. 18 airports had a CTAF on its own frequency suppressed.
    #
    # So: every distinct tower frequency is its own entry (two channels
    # are two channels, not one service listed twice), primary first, and
    # CTAF is suppressed only when a tower already occupies that exact
    # frequency. Same rule the UNICOM check below has always used.
    towers = [f for f in freqs if f.freq_category == "TOWER"]
    ctaf = next((f for f in freqs if f.freq_category == "CTAF"), None)

    seen_freqs: set[float] = set()
    for f in sorted(towers, key=lambda f: (0 if "/P" in f.raw_freq_use else 1, f.freq_mhz)):
        if f.freq_mhz in seen_freqs:
            continue
        seen_freqs.add(f.freq_mhz)
        entries.append(_make_entry(f, airport, f.freq_mhz, tag_counts))

    if ctaf is not None and ctaf.freq_mhz not in seen_freqs:
        entries.append(_make_entry(ctaf, airport, ctaf.freq_mhz, tag_counts))

    # smart mode: ATIS > ASOS > AWOS within the unified Weather Station category
    weather_rows = [f for f in freqs if f.freq_category == "WEATHER_STATION"]
    if weather_rows:
        best = min(weather_rows, key=lambda f: _WEATHER_SUBTYPE_PRIORITY.get(f.weather_subtype, 99))
        entries.append(_make_entry(best, airport, best.freq_mhz, tag_counts))

    # everything else (Ground, Clearance, UNICOM, Approach/Departure, VOR,
    # VOT, NDB, DME, TACAN, Remote Comm Relay, TRACON, FSS, ARTCC, Mil/Gov
    # Ops, Emergency, Procedure fixes, Other) -- one entry per distinct
    # (category, frequency) pair, scoped within its own category rather
    # than collapsed globally by frequency alone, since two different real
    # services coincidentally sharing a channel are still two distinct
    # things a filter might legitimately include one of and not the other.
    by_cat_freq: dict[tuple[str, float], list[Frequency]] = {}
    for f in freqs:
        if f.freq_category in ("TOWER", "CTAF", "WEATHER_STATION"):
            continue
        if f.freq_category == "UNICOM" and ctaf is not None and f.freq_mhz == ctaf.freq_mhz:
            # Real NASR data: 87% of airports with both a CTAF and a UNICOM
            # row (2,502 of 2,877, 2026-08-06 cycle) carry the identical
            # frequency -- a separate UNICOM entry is then just noise, so
            # it's dropped in favor of the already-emitted CTAF/Tower entry
            # above. Only kept when it's actually a distinct frequency.
            continue
        by_cat_freq.setdefault((f.freq_category, f.freq_mhz), []).append(f)

    for (category, freq_mhz), rows in sorted(by_cat_freq.items()):
        if category in _RAW_TEXT_SUFFIX_CATEGORIES:
            # among rows sharing a (category, frequency), the shortest
            # sanitized raw label wins -- in practice favors the generic
            # ATC term (APCH/P, DEP/P) over a specific named procedure
            # (SAN FRANCISCO DP, YOSEM STAR, ...)
            best = min(rows, key=lambda f: (len(_sanitize(f.raw_freq_use)), f.raw_freq_use))
        else:
            best = rows[0]
        entries.append(_make_entry(best, airport, freq_mhz, tag_counts))

    return entries


def _ils_entries(airport: Airport, ils_rows: list[Ils], mode: str) -> list[Entry]:
    entries: list[Entry] = []

    if mode == "raw":
        for i in ils_rows:
            entries.append(
                Entry(
                    tag_name=f"{airport.id}-LOC{_sanitize(i.runway_end_id)}",
                    freq_mhz=i.freq_mhz,
                    group="",
                    lat=airport.lat,
                    lon=airport.lon,
                    category=ILS_PSEUDO_CATEGORY,
                )
            )
        return entries

    by_runway: dict[str, list[Ils]] = {}
    for i in ils_rows:
        by_runway.setdefault(i.runway_end_id, []).append(i)

    for runway_end_id, rows in by_runway.items():
        best = min(
            rows,
            key=lambda i: (
                _STATUS_PRIORITY.get(i.component_status.upper(), 2),
                _SYSTEM_TYPE_PRIORITY.get(i.system_type.upper(), 99),
            ),
        )
        entries.append(
            Entry(
                tag_name=f"{airport.id}-LOC{_sanitize(runway_end_id)}",
                freq_mhz=best.freq_mhz,
                group="",
                lat=airport.lat,
                lon=airport.lon,
                category=ILS_PSEUDO_CATEGORY,
            )
        )

    return entries


def select_entries(
    data: NormalizedData,
    mode: str = "smart",
    include_public: bool = True,
    include_private: bool = False,
) -> list[Entry]:
    """Build export entries from normalized data.

    Public/private use is a combinable filter like any other in §3 -- not
    a hardcoded scope rule -- so both sides are independently toggleable:
    include_public=True, include_private=True includes everything;
    both False yields no entries. Defaults preserve the app's traditional
    public-use-only behavior until a filter UI overrides it.
    """
    if mode not in ("smart", "raw"):
        raise ValueError(f"unknown selection mode: {mode!r}")

    # Out-of-band rows are dropped here rather than in either mode's own
    # path: raw mode means "every registered frequency", not "every row
    # in the file", and a frequency the radio cannot tune is not an
    # interpretation choice. Done before the by-airport grouping so a
    # facility with nothing tunable left produces no entries at all
    # instead of an empty one.
    freqs_by_airport: dict[str, list[Frequency]] = {}
    for f in data.frequencies:
        if f.freq_mhz >= MAX_TUNABLE_MHZ:
            continue
        freqs_by_airport.setdefault(f.airport_id, []).append(f)

    ils_by_airport: dict[str, list[Ils]] = {}
    for i in data.ils:
        ils_by_airport.setdefault(i.airport_id, []).append(i)

    airports_by_id = {a.id: a for a in data.airports}
    orphan_ids = orphan_tag_ids(set(airports_by_id), set(freqs_by_airport))

    # Airports and standalone facilities (VOR, RCAG, ARTCC, ...) are
    # processed together in one ID-sorted pass rather than airports-then-
    # orphans. Standalone facilities -- confirmed on real data as 8.3% of
    # all frequency rows nationwide, 100% for ARTCC/CERAP -- always pass
    # through regardless of include_public/include_private, since that
    # concept doesn't exist for them at all (FACILITY_USE_CODE only lives
    # in APT_BASE.csv). Sorting by ID instead of appending orphans as a
    # second pass matters beyond cosmetics: the web UI truncates its
    # results preview to the first N entries, and a strictly airports-then-
    # orphans order meant every standalone facility silently vanished from
    # that preview (though never from the generated XML) as soon as the
    # airport-only portion exceeded the truncation cap. ILS is never
    # orphaned (localizers are inherently runway-associated), so no
    # standalone-facility handling is needed for it.
    entries: list[Entry] = []
    for facility_id in sorted(set(airports_by_id) | set(freqs_by_airport)):
        airport = airports_by_id.get(facility_id)
        if airport is not None:
            if airport.public_use and not include_public:
                continue
            if not airport.public_use and not include_private:
                continue
        else:
            airport = _pseudo_airport(facility_id, freqs_by_airport[facility_id], orphan_ids[facility_id])

        comm = _comm_entries(airport, freqs_by_airport.get(facility_id, []), mode)
        loc = _ils_entries(airport, ils_by_airport.get(facility_id, []), mode)
        group = default_group_for(facility_id)
        # replace() rather than re-listing every field: this only sets the
        # group, and rebuilding by hand silently drops any field added to
        # Entry later (it already lost `category` once).
        entries.extend(replace(e, group=group) for e in comm + loc)

    return entries


def _pseudo_airport(facility_id: str, freqs: list[Frequency], tag_id: str) -> Airport:
    """A stand-in Airport for a standalone facility with no real airport
    row, built from its own FRQ.csv-carried position/state/city/name
    (SERVICED_STATE/SERVICED_CITY/SERVICED_FAC_NAME -- confirmed present
    even for orphan rows). public_use is unused: orphan facilities are
    handled in their own pass in select_entries that never checks it.

    `.id` is the short tag_id from orphan_tag_ids, not the raw
    facility_id -- SERVICED_FACILITY is sometimes a full place name (see
    orphan_tag_ids) and .id drives tag-name generation. `.name` keeps the
    full raw name for display, unaffected.
    """
    first = freqs[0]
    return Airport(
        id=tag_id,
        name=first.name or facility_id,
        city=first.city or "",
        state=first.state or "",
        lat=first.lat,
        lon=first.lon,
        public_use=True,
    )
