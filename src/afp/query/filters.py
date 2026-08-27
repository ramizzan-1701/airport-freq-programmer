"""FilterState: every §3 UI-exposed filter, plus public/private (added per
user direction after milestone 1) -- as a single combinable set of
constraints. All filters AND together; each multi-select field ORs its
own values (spec §3: "AND logic between filter groups, OR within a
multi-select group").

Every field defaults to "no constraint" (None, empty tuple, or False) so
an unfiltered FilterState() matches everything -- except include_public/
include_private, which default to the app's traditional public-use-only
scope (see afp.selection).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..classification import ILS_PSEUDO_CATEGORY


@dataclass(frozen=True)
class RadiusFilter:
    center_lat: float
    center_lon: float
    radius_nm: float
    mode: str  # "include" or "exclude"

    def __post_init__(self) -> None:
        if self.mode not in ("include", "exclude"):
            raise ValueError(f"radius filter mode must be 'include' or 'exclude', got {self.mode!r}")


@dataclass(frozen=True)
class FilterState:
    states: frozenset[str] | None = None
    cities: frozenset[str] | None = None
    # The primary filter for "what kind of frequency is this" (spec §3) --
    # values from afp.classification.ALL_FREQ_CATEGORIES, a single
    # classifier the FAA adapter already derived from FREQ_USE/
    # FACILITY_TYPE/SERVICED_SITE_TYPE together. Replaces the old
    # freq_uses/facility_types/serviced_site_types raw-value filters.
    freq_categories: frozenset[str] | None = None
    # Optional sub-filter beneath Weather Station, distinguishing ATIS
    # from ASOS/AWOS -- only meaningful in combination with
    # freq_categories including "WEATHER_STATION" (or unset).
    weather_subtypes: frozenset[str] | None = None
    # Narrower than freq_categories: which landing-platform type
    # (afp.classification.PLATFORM_TYPES), for rows where that's
    # meaningful at all.
    platform_types: frozenset[str] | None = None
    # Narrower than freq_categories too: towered vs. non-towered only
    # (afp.classification.FACILITY_STATUS_LABELS).
    facility_statuses: frozenset[str] | None = None
    # Both platform_types and facility_statuses are None for every row
    # that isn't a landing-platform/ATCT-tracked site at all (VOR, RCAG,
    # ARTCC, TRACON, FSS, DME, TACAN, NDB, VOT, ...) -- narrowing either
    # filter would otherwise silently exclude these outright, since SQL's
    # IN(...) never matches NULL, even when they're tied to the very
    # airport the filter is trying to keep. Checking this single toggle
    # exempts them from *both* filters at once, rather than needing a
    # separate "also allow non-site" checkbox duplicated in each one.
    include_non_site_facilities: bool = False
    primary_approach_radio_calls: frozenset[str] | None = None
    # Real TOWER_HRS data is messy free text ("24, CLSD HOL.", "OPR H24
    # MON-FRI...", "0600-2400"). This only matches the clean, unambiguous
    # literal value FAA uses for a true 24-hour tower ("24") -- it does
    # not attempt to interpret the long tail of other 24-hour-ish phrasing,
    # since heuristic text matching would introduce more misclassification
    # than it resolves.
    tower_hours_24_only: bool = False
    ils_component_statuses: frozenset[str] | None = None
    ils_system_types: frozenset[str] | None = None
    radius_filters: tuple[RadiusFilter, ...] = field(default_factory=tuple)
    include_public: bool = True
    include_private: bool = False

    @property
    def should_include_ils(self) -> bool:
        """ILS/localizer records come from a wholly separate NASR file
        (ILS_BASE.csv) with no freq_category of their own -- "ILS" is a
        pseudo-value in afp.classification's vocabulary that never
        actually appears in the frequencies table, existing only so this
        checks like any other category: unconstrained (None) or
        explicitly included -> True; freq_categories narrowed to a set
        that doesn't include "ILS" -> False. No separate on/off field
        needed -- narrowing Frequency Category to just e.g. WEATHER_STATION
        naturally excludes ILS the same way it excludes every other
        category not selected.
        """
        return self.freq_categories is None or ILS_PSEUDO_CATEGORY in self.freq_categories
