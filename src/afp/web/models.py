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
    # Assigned by the client so a rule keeps its identity across
    # reordering and removal -- which is what lets one window say which
    # rule is being edited. Ignored by to_filter_state: it is not part of
    # what the rule matches, and two rules with the same centre and
    # radius filter identically whatever their ids.
    id: str | None = None


class MapPointOut(BaseModel):
    """One drawable point. Static for a loaded cycle."""

    id: str
    name: str
    city: str
    state: str
    lat: float
    lon: float
    # None for a standalone facility: FACILITY_USE_CODE lives in
    # APT_BASE.csv and has no equivalent for them, which is why the
    # filters never judge them on it either.
    public_use: bool | None
    kind: Literal["airport", "facility"]
    freq_count: int


class MapPointsOut(BaseModel):
    points: list[MapPointOut]
    # Doubles as the client's cache key: the points only change when the
    # loaded cycle does.
    cycle_date: date


class MapRingOut(BaseModel):
    """A radius rule with its centre resolved, so the map can draw it
    without resolving centres itself."""

    id: str | None = None
    center: str
    lat: float
    lon: float
    radius_nm: float
    mode: Literal["include", "exclude"]


class MapStatusOut(BaseModel):
    # One code per point, in /api/map/points order. A string rather than
    # a list: nationwide is about 19k points, and one character each
    # keeps it to roughly 19 KB instead of a JSON array many times that.
    statuses: str
    # Standalone facilities with no position. Counted rather than drawn,
    # so the legend can account for them instead of losing them.
    unplottable: int
    # How many of each point's frequencies survive the frequency-level
    # filters, in the same order. A plain array of small integers: the
    # alternative, sending only the points whose count differs from the
    # static one, is larger in the case that actually happens, because
    # the default view already has a category filter on and most points
    # differ under it.
    #
    # Read only on hover, so it never gates a redraw.
    freq_matches: list[int]
    rings: list[MapRingOut]
    cycle_date: date


class UiStateIn(BaseModel):
    """The parts of the UI both windows have to agree about.

    Not filter state: which rule is open changes nothing about the
    query, so it is shared between windows but never persisted.
    """

    active_radius_id: str | None = None


class SyncOut(BaseModel):
    """What changed since the caller last looked.

    Each half is sent only when it is newer than the revision the caller
    quoted, so a poll that finds nothing new costs two integers. The
    `source` fields carry the client id of whoever wrote it, which is how
    a window tells its own change from someone else's.
    """

    filters_revision: int
    filters: FilterStateIn | None = None
    filters_source: str | None = None

    ui_revision: int
    ui: UiStateIn | None = None
    ui_source: str | None = None


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


class CycleOptionsOut(BaseModel):
    """Which situation the download screen is in, and the dates it needs
    to name.

    Python decides *which* situation; the page decides how it reads. The
    same split the About copy uses, and the reason it is worth having:
    the four states fall out of two facts that only the server can get
    at -- what the FAA publishes right now, and what is on disk -- and
    deriving them in JS would mean the branch nothing can test.

    `update_available` on /api/check-update does not answer this. It
    compares the published cycle against cycle_store.json's
    last_processed watermark, so it reads false while you work on data
    two months old, which is the state that prompted all of this.
    """

    # "update_available": the FAA has a cycle that is not on disk.
    # "current":          the published cycle is downloaded.
    # "offline":          the FAA could not be reached; there is data.
    # "empty":            nothing is downloaded.
    state: Literal["update_available", "current", "offline", "empty"]
    # None when the FAA could not be reached, which is the one case the
    # page cannot name a cycle to fetch.
    published_cycle: date | None = None
    newest_downloaded: date | None = None
    # The one to fall back to if the newest turns out to be bad. None
    # until a second cycle has been downloaded.
    previous_downloaded: date | None = None


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
    # Carried so a row can be copied into a custom group without the
    # server re-deriving it: a copy is a snapshot of what was on screen,
    # so the row holds everything the snapshot needs.
    lat: float = 0.0
    lon: float = 0.0
    category: str = ""
    # Where this sits in the held custom entries, for the rows that are
    # deletable. None for a generated entry, which is not.
    custom_index: int | None = None


class GroupSlotOut(BaseModel):
    """One of the user's three slots."""

    slot: int
    # None when the slot has never been named. The UI shows it as
    # unnamed; naming happens on first use.
    name: str | None = None
    entry_count: int = 0


class GroupsOut(BaseModel):
    scheme: str
    # Both schemes, so the selector can label them without a second copy
    # of the names living in the frontend.
    schemes: dict[str, list[str]]
    preset_names: list[str]
    custom_slots: list[GroupSlotOut]
    max_group_name_length: int


class GroupSchemeIn(BaseModel):
    scheme: str


class CustomGroupNameIn(BaseModel):
    name: str


class CopyEntriesIn(BaseModel):
    slot: int
    entries: list[EntryOut]


class CopyEntriesOut(BaseModel):
    """What a copy actually did.

    `skipped` is the tag names already present in the target group --
    reported rather than silently dropped, and rather than failing the
    whole copy over one collision.
    """

    group: str
    copied: int
    skipped: list[str]


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
    # The fraction of the cap at which the counter turns amber. Sent
    # rather than repeated in the frontend because the rolling counter
    # recolours itself from the value it is currently showing, not from
    # `level` above -- which describes the destination. A second copy of
    # the threshold would be free to drift from afp.counter's.
    amber_threshold: float
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
