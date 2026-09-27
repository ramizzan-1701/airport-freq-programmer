"""Pydantic request/response shapes for the web API."""

from __future__ import annotations

from datetime import date
from sqlite3 import Connection
from typing import Literal

from pydantic import BaseModel, Field

from ..classification import HIDDEN_FROM_WEB_UI_CATEGORIES
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
            # Only when nothing was asked for explicitly. "No category
            # filter" otherwise means everything in the dataset, so the
            # web UI shipped Emergency and NDB -- 936 entries nationwide,
            # NDB not even tunable on the radio -- despite never listing
            # them for the user to deselect.
            #
            # A caller that names a category still gets it: the API keeps
            # the full filter dimension, and the CLI, which builds its
            # own FilterState, is untouched either way.
            excluded_freq_categories=(
                None if self.freq_categories else HIDDEN_FROM_WEB_UI_CATEGORIES
            ),
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


class ProgressOut(BaseModel):
    """A snapshot of the running fetch or load, polled a few times a
    second. Every field is safe to read when nothing is running.
    """

    # 0..1. Weighted by measured cost, not by step count -- see
    # afp.progress for why equal weighting would misinform.
    fraction: float
    # What is happening, e.g. "Downloading airport data".
    label: str
    # The moving part underneath it, e.g. "3.2 MB of 8.0 MB". Empty on
    # steps that have no sub-progress to report.
    detail: str = ""
    done: bool = False
    error: str | None = None
    cancelled: bool = False


class StatusOut(BaseModel):
    loaded_cycle: date | None
    available_cycles: list[date]
    airport_count: int | None = None
    frequency_count: int | None = None
    ils_count: int | None = None
    # False until the About screen has been dismissed once, which is what
    # makes it appear ahead of the load screen on a fresh install.
    about_acknowledged: bool = False


class AboutFeatureOut(BaseModel):
    """One "What it does" bullet, pre-split at its bold lead so the screen
    can style it without markdown reaching the frontend.
    """
    lead: str
    text: str


class AboutLinkOut(BaseModel):
    """A phrase in the intro text that should render as a link."""
    phrase: str
    url: str


class AboutActionOut(BaseModel):
    """A phrase in a workflow step that opens something in this app.

    `action` names a handler the frontend maps to a function -- copy
    cannot carry a callback, and a URL would be wrong for something that
    never leaves the page.
    """
    phrase: str
    action: str


class AboutOut(BaseModel):
    intro: list[str]
    intro_links: list[AboutLinkOut]
    features: list[AboutFeatureOut]
    # Page 2. Steps carry **bold** markers, rendered by the frontend.
    workflow_intro: str
    workflow_steps: list[str]
    workflow_actions: list[AboutActionOut]
    author: str
    contact_email: str
    app_version: str
    release_date: str


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


class ResolvedCenterOut(BaseModel):
    """Echoes back what a radius centre resolved to, so the field can be
    checked before the filter is added rather than failing the whole
    query afterwards.
    """
    center: str
    lat: float
    lon: float


class EntryOut(BaseModel):
    tag_name: str
    freq_mhz: float
    group: str
    airport_id: str
    airport_name: str
    city: str
    state: str
    # Preserved from the user's radio rather than derived from FAA data.
    # Carries no airport, city or state -- the fields above are empty.
    is_custom: bool = False


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
