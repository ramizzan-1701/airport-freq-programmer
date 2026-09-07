"""Pydantic request/response shapes for the web API."""

from __future__ import annotations

from datetime import date
from sqlite3 import Connection
from typing import Literal

from pydantic import BaseModel, Field

from ..query.filters import FilterState, RadiusFilter
from ..query.query import resolve_center


class RadiusFilterIn(BaseModel):
    center: str = Field(..., description="Airport ID, or 'lat,lon'")
    radius_nm: float
    mode: Literal["include", "exclude"]


class FilterStateIn(BaseModel):
    states: list[str] | None = None
    cities: list[str] | None = None
    freq_categories: list[str] | None = None
    weather_subtypes: list[str] | None = None
    platform_types: list[str] | None = None
    facility_statuses: list[str] | None = None
    include_non_site_facilities: bool = False
    primary_approach_radio_calls: list[str] | None = None
    tower_hours_24_only: bool = False
    ils_component_statuses: list[str] | None = None
    ils_system_types: list[str] | None = None
    radius_filters: list[RadiusFilterIn] = Field(default_factory=list)
    include_public: bool = True
    include_private: bool = False
    mode: Literal["smart", "raw"] = "smart"

    def to_filter_state(self, conn: Connection) -> FilterState:
        resolved = []
        for rf in self.radius_filters:
            center_arg: str | tuple[float, float]
            if "," in rf.center:
                lat_str, lon_str = rf.center.split(",", 1)
                center_arg = (float(lat_str), float(lon_str))
            else:
                center_arg = rf.center
            lat, lon = resolve_center(conn, center_arg)
            resolved.append(RadiusFilter(center_lat=lat, center_lon=lon, radius_nm=rf.radius_nm, mode=rf.mode))

        return FilterState(
            states=frozenset(self.states) if self.states else None,
            cities=frozenset(self.cities) if self.cities else None,
            freq_categories=frozenset(self.freq_categories) if self.freq_categories else None,
            weather_subtypes=frozenset(self.weather_subtypes) if self.weather_subtypes else None,
            platform_types=frozenset(self.platform_types) if self.platform_types else None,
            facility_statuses=frozenset(self.facility_statuses) if self.facility_statuses else None,
            include_non_site_facilities=self.include_non_site_facilities,
            primary_approach_radio_calls=frozenset(self.primary_approach_radio_calls) if self.primary_approach_radio_calls else None,
            tower_hours_24_only=self.tower_hours_24_only,
            ils_component_statuses=frozenset(self.ils_component_statuses) if self.ils_component_statuses else None,
            ils_system_types=frozenset(self.ils_system_types) if self.ils_system_types else None,
            radius_filters=tuple(resolved),
            include_public=self.include_public,
            include_private=self.include_private,
        )


class StatusOut(BaseModel):
    loaded_cycle: date | None
    available_cycles: list[date]
    airport_count: int | None = None
    frequency_count: int | None = None
    ils_count: int | None = None
    group_setup_acknowledged: bool = False
    fixed_group_names: list[str] = Field(default_factory=list)


class UpdateCheckOut(BaseModel):
    current_published_cycle: date
    update_available: bool


class LabeledOption(BaseModel):
    code: str
    label: str


class FreqCategoryOption(BaseModel):
    code: str
    label: str
    default_view: bool
    not_usable_on_fta_850l: bool


class FilterOptionsOut(BaseModel):
    states: list[LabeledOption]
    freq_categories: list[FreqCategoryOption]
    weather_subtypes: list[str]
    platform_types: list[str]
    facility_statuses: list[LabeledOption]
    primary_approach_radio_calls: list[str]
    ils_component_statuses: list[str]
    ils_system_types: list[LabeledOption]


class EntryOut(BaseModel):
    tag_name: str
    freq_mhz: float
    group: str
    airport_id: str
    airport_name: str
    city: str
    state: str


class CategoryCountOut(BaseModel):
    code: str
    label: str
    # Terse form for the breakdown chips; `label` rides along as their
    # tooltip so the full name is always one hover away.
    short_label: str
    count: int


class QueryResultOut(BaseModel):
    count: int
    total_count: int
    level: Literal["green", "amber", "red"]
    cap: int
    entries: list[EntryOut]
    truncated: bool
    custom_entry_count: int
    custom_group_count: int
    # Breakdown of `count` by frequency category, largest first. Covers
    # every selected entry, not just the truncated preview page in
    # `entries` -- it answers "what is actually in my 317?", which the
    # preview alone can't when it stops at 500 rows.
    category_counts: list[CategoryCountOut]


class ValidationErrorOut(BaseModel):
    problems: list[str]


class CustomEntryOut(BaseModel):
    index: int
    tag_name: str
    freq_mhz: float
    group: str
    lat: float
    lon: float
    scan_memory: str
    shift: str


class CustomEntriesOut(BaseModel):
    entries: list[CustomEntryOut]
    entry_count: int
    group_count: int


class CustomImportBlockedOut(BaseModel):
    groups: list[str]
    found: int
    available: int
