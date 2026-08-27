"""Normalized internal data model.

Every downstream layer (selection logic, export profiles) reads only these
types -- never raw source columns. A new source adapter (e.g. for
international data) only needs to populate this schema to be a drop-in
addition; nothing downstream changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Airport:
    id: str
    name: str
    city: str
    state: str
    lat: float
    lon: float
    public_use: bool
    country: str = "US"


@dataclass(frozen=True)
class Frequency:
    airport_id: str
    freq_mhz: float
    # A single canonical classification spanning FREQ_USE, FACILITY_TYPE,
    # and SERVICED_SITE_TYPE together (see afp.classification and the FAA
    # adapter's classifier) -- not a copy of any one raw column. Value is
    # one of afp.classification.ALL_FREQ_CATEGORIES.
    freq_category: str
    # Narrower than freq_category: which of the fixed landing-platform
    # types (afp.classification.PLATFORM_TYPES) this row's site is, when
    # applicable -- None for rows whose site type isn't a landing platform
    # at all (e.g. a VOR, where the site type already equals its category).
    platform_type: str | None = None
    # Narrower still: towered vs. non-towered, when applicable (None for
    # rows -- e.g. standalone navaids -- where tower status doesn't apply).
    # Value is one of afp.classification.FACILITY_STATUS_LABELS.
    facility_status: str | None = None
    # Only set when freq_category == "WEATHER_STATION": "ATIS", "ASOS", or
    # "AWOS". Spec §3 wants the unified Weather Station toggle to stay
    # unified by default but offers an optional sub-filter for users who
    # want the ATIS-vs-automated distinction -- this is what that filters
    # on. A normalized field rather than a raw_freq_use substring check at
    # query time, so it's a plain indexed column like every other filter.
    weather_subtype: str | None = None
    primary_approach_radio_call: str | None = None
    tower_hours: str | None = None
    # Original FREQ_USE text, preserved for tag-naming/display fidelity
    # (e.g. distinguishing ATIS from ASOS from AWOS within the unified
    # WEATHER_STATION category, or VOR from VORTAC within VOR family) --
    # not a filter dimension; downstream filtering always goes through
    # freq_category/platform_type/facility_status.
    raw_freq_use: str = ""
    source_remark: str | None = None
    # FRQ.csv carries its own LAT_DECIMAL/LONG_DECIMAL, distinct from the
    # airport's position -- needed for standalone navaids (VOR, VORTAC...)
    # that aren't co-located with the airport they're filed under. None
    # when the row didn't carry a position (selection falls back to the
    # airport's own lat/lon in that case).
    lat: float | None = None
    lon: float | None = None
    # FRQ.csv's SERVICED_STATE/SERVICED_CITY/SERVICED_FAC_NAME -- the same
    # "serviced" entity as airport_id (SERVICED_FACILITY), always present,
    # even for standalone facilities (VOR, RCAG, ARTCC, ...) that have no
    # matching row in APT_BASE.csv at all and so can't get this from an
    # Airport join. Populated for every row for simplicity; airport-tied
    # rows just don't need it, since filtering/display for those already
    # goes through the joined Airport.
    state: str | None = None
    city: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class Ils:
    airport_id: str
    runway_end_id: str
    freq_mhz: float
    system_type: str
    component_status: str


@dataclass
class NormalizedData:
    airports: list[Airport] = field(default_factory=list)
    frequencies: list[Frequency] = field(default_factory=list)
    ils: list[Ils] = field(default_factory=list)

    def airports_by_id(self) -> dict[str, Airport]:
        return {a.id: a for a in self.airports}
