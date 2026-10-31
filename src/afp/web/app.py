"""FastAPI app for the local web UI: filters + live counter + generate
button (spec §7 build order step 4). Wraps the query layer (milestone 3)
and export pipeline (milestone 1) -- no new business logic lives here,
just request/response plumbing.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .. import RELEASE_DATE, __version__, about as about_copy, classification
from ..counter import AMBER_THRESHOLD, counter_status
from ..custom_entries import CustomGroupCapacityError
from ..export.fta850l import FTA_850L
from ..export.xml_reader import XmlParseError
from ..export.xml_writer import ExportValidationError, build_xml
from ..nasr.source import FetchResult
from ..progress import FETCH_STEPS, LOAD_STEPS, Cancelled
from ..query import query as query_mod
from ..selection import (
    GROUP_SCHEMES,
    PRESET_SLOTS,
    Entry,
    group_slots,
    orphan_tag_ids,
    select_entries,
)
from .models import (
    AboutActionOut,
    AboutFeatureOut,
    AboutLinkOut,
    AboutOut,
    CategoryCountOut,
    CustomEntriesOut,
    CustomEntryOut,
    EntryOut,
    FilterOptionsOut,
    FilterStateIn,
    CopyEntriesIn,
    CopyEntriesOut,
    CustomGroupNameIn,
    FreqCategoryOption,
    GroupSchemeIn,
    GroupSlotOut,
    GroupsOut,
    LabeledOption,
    MapPointOut,
    MapPointsOut,
    MapRingOut,
    MapStatusOut,
    ProgressOut,
    QueryResultOut,
    ResolvedCenterOut,
    StatusOut,
    SyncOut,
    UiStateIn,
    UpdateCheckOut,
)
from .state import AppState, LoadedCycle

STATIC_DIR = Path(__file__).parent / "static"
MAX_DISPLAYED_ENTRIES = 500


def _require_loaded(app_state: AppState) -> LoadedCycle:
    if app_state.loaded is None:
        raise HTTPException(status_code=400, detail="no NASR cycle loaded yet")
    return app_state.loaded


def _build_filter_state(body: FilterStateIn, loaded: LoadedCycle):
    try:
        return body.to_filter_state(loaded.conn)
    except ValueError as exc:
        # e.g. an unresolvable radius-filter center (unknown airport ID)
        raise HTTPException(status_code=400, detail=str(exc)) from None


def create_app(cache_dir: Path) -> FastAPI:
    app = FastAPI(title="FTA-850 Frequency Manager")
    app.state.afp_state = AppState(cache_dir=cache_dir)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def no_heuristic_caching(request, call_next):
        # StaticFiles sends Last-Modified but no Cache-Control, which lets
        # browsers apply heuristic freshness (RFC 7234) and silently serve
        # a stale app.js/style.css after this file changes between runs of
        # a local dev server, without even revalidating. no-cache forces a
        # conditional GET (cheap 304 when unchanged) instead of a guess.
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/status", response_model=StatusOut)
    def status() -> StatusOut:
        state: AppState = app.state.afp_state
        loaded = state.loaded
        return StatusOut(
            loaded_cycle=loaded.cycle_date if loaded else None,
            available_cycles=state.available_cycles(),
            airport_count=len(loaded.data.airports) if loaded else None,
            frequency_count=len(loaded.data.frequencies) if loaded else None,
            ils_count=len(loaded.data.ils) if loaded else None,
            about_acknowledged=state.about_acknowledged,
        )

    @app.get("/api/about", response_model=AboutOut)
    def about() -> AboutOut:
        return AboutOut(
            intro=list(about_copy.INTRO_PARAGRAPHS),
            intro_links=[
                AboutLinkOut(phrase=phrase, url=url)
                for phrase, url in about_copy.INTRO_LINKS
            ],
            features=[
                AboutFeatureOut(lead=lead, text=text)
                for lead, text in about_copy.FEATURES
            ],
            workflow_intro=about_copy.WORKFLOW_INTRO,
            workflow_steps=list(about_copy.WORKFLOW_STEPS),
            workflow_actions=[
                AboutActionOut(phrase=phrase, action=action)
                for phrase, action in about_copy.WORKFLOW_ACTIONS
            ],
            author=about_copy.AUTHOR,
            contact_email=about_copy.CONTACT_EMAIL,
            app_version=__version__,
            release_date=RELEASE_DATE,
        )

    @app.get("/api/filters", response_model=FilterStateIn)
    def saved_filters() -> FilterStateIn:
        """The filter selections from the last session.

        Typed as FilterStateIn, the same shape /api/query takes, so
        anything restored here is by construction something the query
        accepts. With nothing saved this is a default-constructed one,
        which is exactly "no filters" -- the rail's own starting state.
        """
        state: AppState = app.state.afp_state
        saved = state.load_saved_filters()
        if not saved:
            return FilterStateIn()
        try:
            return FilterStateIn(**saved)
        except ValidationError:
            # Written by an older version whose shape has since changed.
            # Starting clean beats refusing to open the rail.
            return FilterStateIn()

    @app.put("/api/filters", response_model=FilterStateIn)
    def save_filters(
        body: FilterStateIn,
        x_client_id: str | None = Header(default=None),
    ) -> FilterStateIn:
        state: AppState = app.state.afp_state
        # The writer's id rides along so the other window can tell this
        # change from one of its own; see /api/sync.
        state.save_filters(body.model_dump(mode="json"), source=x_client_id)
        return body

    @app.put("/api/ui-state", response_model=UiStateIn)
    def put_ui_state(
        body: UiStateIn,
        x_client_id: str | None = Header(default=None),
    ) -> UiStateIn:
        """UI state both windows share. Held in memory only: which rule
        is open is worth agreeing about while the app runs and worth
        nothing once it closes.
        """
        state: AppState = app.state.afp_state
        # exclude_unset, or every field the caller left out would be
        # merged in at its default and quietly clear what is there. A
        # field sent as null is still set, so closing a rule travels.
        patch = body.model_dump(mode="json", exclude_unset=True)
        merged = state.set_ui_state(patch, source=x_client_id)
        return UiStateIn(**merged)

    @app.get("/api/sync", response_model=SyncOut)
    def sync(
        filters_since: int = Query(default=-1),
        ui_since: int = Query(default=-1),
    ) -> SyncOut:
        """What has changed since the caller last looked.

        Polled by each window a few times a second. Both halves are left
        out when they are not newer than the revisions quoted, so the
        common answer -- nothing happened -- is two integers rather than
        a whole filter state.

        Defaults of -1, not 0: revisions start at 0, so a caller that has
        never synced has to be able to ask for a revision older than the
        first one.
        """
        state: AppState = app.state.afp_state

        filters: FilterStateIn | None = None
        if state.filters_revision > filters_since:
            saved = state.load_saved_filters()
            try:
                filters = FilterStateIn(**saved) if saved else FilterStateIn()
            except ValidationError:
                filters = FilterStateIn()

        ui: UiStateIn | None = None
        if state.ui_revision > ui_since:
            ui = UiStateIn(**state.ui_state)

        return SyncOut(
            filters_revision=state.filters_revision,
            filters=filters,
            filters_source=state.filters_source if filters is not None else None,
            ui_revision=state.ui_revision,
            ui=ui,
            ui_source=state.ui_source if ui is not None else None,
        )

    @app.get("/api/check-update", response_model=UpdateCheckOut)
    def check_update() -> UpdateCheckOut:
        state: AppState = app.state.afp_state
        try:
            cycle = state.source.get_current_cycle()
        except Exception as exc:  # network/scrape failure shouldn't crash the UI
            raise HTTPException(status_code=502, detail=f"could not reach FAA NASR page: {exc}") from None
        return UpdateCheckOut(
            current_published_cycle=cycle.effective_date,
            update_available=state.source.is_update_available(current=cycle),
        )

    @app.post("/api/load", response_model=StatusOut)
    def load(body: dict) -> StatusOut:
        state: AppState = app.state.afp_state
        cycle_date = date.fromisoformat(body["cycle"])
        if cycle_date not in state.available_cycles():
            raise HTTPException(status_code=404, detail=f"cycle {cycle_date} not found in cache dir")
        tracker = state.begin_progress(LOAD_STEPS)
        try:
            state.load_cycle(cycle_date, progress=tracker)
        except Exception as exc:
            tracker.fail(str(exc))
            raise
        tracker.finish()
        return status()

    # Left as a blocking request that returns the new status, exactly as
    # before. Progress is a side channel -- see /api/fetch/progress --
    # rather than a streamed response, so this endpoint's success and
    # failure behaviour, and everything already relying on it, is
    # unchanged. A streamed 200 could not report a mid-flight failure as
    # an HTTP error any more.
    @app.post("/api/fetch", response_model=StatusOut)
    def fetch(mark_processed: bool = True) -> StatusOut:
        state: AppState = app.state.afp_state
        tracker = state.begin_progress(FETCH_STEPS)
        try:
            result: FetchResult = state.source.fetch_current_cycle(progress=tracker)
            if mark_processed:
                state.source.mark_processed(result.cycle)
            state.load_cycle(result.cycle.effective_date, progress=tracker)
        except Cancelled:
            # Asked for, not gone wrong. 409 rather than a 5xx so the
            # frontend can tell the two apart without inspecting text.
            raise HTTPException(status_code=409, detail="fetch cancelled")
        except Exception as exc:
            # Recorded on the tracker as well as raised: the last poll
            # can land after this response, and finding a stale
            # in-progress snapshot there would leave the bar frozen
            # part-way with no explanation.
            tracker.fail(str(exc))
            raise
        tracker.finish()
        return status()

    @app.get("/api/fetch/progress", response_model=ProgressOut)
    def fetch_progress() -> ProgressOut:
        """Polled a few times a second while a fetch or load runs.

        Served concurrently with the blocking POST above because
        FastAPI runs sync endpoints in a threadpool -- the one holding
        the fetch does not hold up this one.
        """
        state: AppState = app.state.afp_state
        if state.progress is None:
            return ProgressOut(fraction=0.0, label="", detail="", done=False)
        return ProgressOut(**state.progress.snapshot())

    @app.post("/api/fetch/cancel", response_model=ProgressOut)
    def fetch_cancel() -> ProgressOut:
        """Sets the flag the download loop checks between chunks.

        Returns immediately -- the work stops on its own once the
        in-flight chunk lands, and the POST that started it answers 409.
        """
        state: AppState = app.state.afp_state
        if state.progress is None:
            raise HTTPException(status_code=404, detail="nothing running")
        state.progress.cancel()
        return ProgressOut(**state.progress.snapshot())

    @app.get("/api/filter-options", response_model=FilterOptionsOut)
    def filter_options() -> FilterOptionsOut:
        loaded = _require_loaded(app.state.afp_state)
        conn = loaded.conn

        freq_category_codes = [
            code for code in query_mod.list_freq_categories(conn)
            if code not in classification.HIDDEN_FROM_WEB_UI_CATEGORIES
        ]
        if query_mod.has_ils_data(conn):
            freq_category_codes = [*freq_category_codes, classification.ILS_PSEUDO_CATEGORY]

        freq_categories = [
            FreqCategoryOption(
                code=code,
                label=classification.FREQ_CATEGORY_LABELS.get(code, code),
                default_view=code in classification.DEFAULT_VIEW_CATEGORIES,
                not_usable_on_fta_850l=code in classification.NOT_USABLE_ON_FTA_850L,
            )
            for code in freq_category_codes
        ]
        facility_statuses = [
            LabeledOption(code=code, label=classification.FACILITY_STATUS_LABELS.get(code, code))
            for code in query_mod.list_facility_statuses(conn)
        ]
        ils_system_types = [
            LabeledOption(code=code, label=classification.ILS_SYSTEM_TYPE_LABELS.get(code, code))
            for code in query_mod.list_ils_system_types(conn)
        ]
        states = [
            LabeledOption(code=code, label=classification.US_STATE_LABELS.get(code, code))
            for code in query_mod.list_states(conn)
        ]

        return FilterOptionsOut(
            states=states,
            freq_categories=freq_categories,
            weather_subtypes=query_mod.list_weather_subtypes(conn),
            platform_types=query_mod.list_platform_types(conn),
            facility_statuses=facility_statuses,
            primary_approach_radio_calls=query_mod.list_primary_approach_radio_calls(conn),
            ils_component_statuses=query_mod.list_ils_component_statuses(conn),
            ils_system_types=ils_system_types,
        )

    @app.get("/api/cities")
    def cities(states: list[str] | None = Query(default=None)) -> list[str]:
        loaded = _require_loaded(app.state.afp_state)
        return query_mod.list_cities(loaded.conn, states=frozenset(states) if states else None)

    @app.get("/api/resolve-center", response_model=ResolvedCenterOut)
    def resolve_radius_center(center: str = Query(...)) -> ResolvedCenterOut:
        """Checks one radius centre on its own.

        The centre is the only free-text field in the rail, and an
        unresolvable one used to fail the whole query -- every other
        filter the user had set stopped reporting, under a generic
        "Query failed", because of one typo. Resolving it separately lets
        the field own its error.
        """
        loaded = _require_loaded(app.state.afp_state)
        raw = center.strip()
        if not raw:
            raise HTTPException(status_code=400, detail="Enter an airport ID or lat,lon.")

        # Airport IDs are stored uppercase; echoing the canonical form
        # back is what keeps the chip reading "60nm of LAX" after someone
        # types "lax", and what gets stored on the filter thereafter.
        arg: str | tuple[float, float] = raw
        canonical = raw.upper()
        if "," in raw:
            lat_str, _, lon_str = raw.partition(",")
            try:
                arg = (float(lat_str), float(lon_str))
                canonical = raw
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Not a valid lat,lon pair."
                ) from None

        try:
            lat, lon = query_mod.resolve_center(loaded.conn, arg)
        except ValueError:
            raise HTTPException(
                status_code=404, detail=f"No airport with ID {raw.upper()}."
            ) from None
        return ResolvedCenterOut(center=canonical, lat=lat, lon=lon)

    @app.post("/api/query", response_model=QueryResultOut)
    def run_query(body: FilterStateIn) -> QueryResultOut:
        app_state: AppState = app.state.afp_state
        loaded = _require_loaded(app_state)
        filter_state = _build_filter_state(body, loaded)
        filtered_data = query_mod.apply_filters(loaded.conn, filter_state)
        airports_by_id = {a.id: a for a in filtered_data.airports}
        # Standalone facilities (VOR, RCAG, ARTCC, ...) have no Airport
        # record at all -- fall back to a representative Frequency row's
        # own state/city/name (captured from FRQ.csv's SERVICED_* columns)
        # so these don't show blank in the results table. Keyed by the
        # same short tag_id select_entries used to build that facility's
        # tag names (not its raw airport_id, which can be a full place
        # name -- see afp.selection.orphan_tag_ids), so it matches what
        # tag_name.split below actually recovers.
        orphan_ids = orphan_tag_ids(
            set(airports_by_id), {f.airport_id for f in filtered_data.frequencies}
        )
        facility_by_id: dict[str, object] = {}
        for f in filtered_data.frequencies:
            if f.airport_id in airports_by_id:
                continue
            tag_id = orphan_ids[f.airport_id]
            if tag_id not in facility_by_id:
                facility_by_id[tag_id] = f
        entries = select_entries(
            filtered_data,
            mode=body.mode,
            include_public=True,
            include_private=True,
            scheme=app_state.group_scheme,
        )

        # The radio's cap applies to filtered + held custom entries
        # together (spec §5 step 7), and the preview lists them together
        # too -- the table is meant to be a picture of the file about to
        # be produced, so an entry that lands in the file belongs in it.
        #
        # Custom entries lead, which is the one place this deliberately
        # differs from the XML's own order (/api/generate appends them).
        # They are the handful of rows the user is most likely looking
        # for, and the preview stops at MAX_DISPLAYED_ENTRIES: trailing
        # them behind 26,000 FAA rows would put them past the cut in
        # every unfiltered view, which is exactly when someone wants to
        # confirm their own entries survived.
        custom = app_state.custom_entries
        all_entries = custom + entries
        total_count = len(all_entries)
        status_result = counter_status(total_count, FTA_850L)
        display = all_entries[:MAX_DISPLAYED_ENTRIES]
        entries_out = []
        for i, e in enumerate(display):
            if i < len(custom):
                # No airport lookup: the tag prefix of a hand-added entry
                # is whatever the user typed into the radio, so matching
                # it against FAA records would attach a real airport's
                # name and city to a row that never came from one.
                entries_out.append(
                    EntryOut(
                        tag_name=e.tag_name,
                        freq_mhz=e.freq_mhz,
                        group=e.group,
                        airport_id="",
                        airport_name="",
                        city="",
                        state="",
                        is_custom=True,
                        lat=e.lat,
                        lon=e.lon,
                        category=e.category,
                        # Its position in the held custom entries, which
                        # is what the delete endpoint addresses. Safe
                        # because custom entries lead the list in the
                        # same order they are held.
                        custom_index=i,
                    )
                )
                continue
            airport_id = e.tag_name.split("-", 1)[0]
            airport = airports_by_id.get(airport_id)
            if airport is not None:
                name, city, state = airport.name, airport.city, airport.state
            else:
                facility = facility_by_id.get(airport_id)
                name = (facility.name or airport_id) if facility else ""
                city = (facility.city or "") if facility else ""
                state = (facility.state or "") if facility else ""
            entries_out.append(
                EntryOut(
                    tag_name=e.tag_name,
                    freq_mhz=e.freq_mhz,
                    group=e.group,
                    airport_id=airport_id,
                    airport_name=name,
                    city=city,
                    state=state,
                    lat=e.lat,
                    lon=e.lon,
                    category=e.category,
                )
            )

        # Counted over every selected entry, not the truncated preview
        # page -- the breakdown describes the whole result set.
        by_category = Counter(e.category for e in entries if e.category)
        category_counts = [
            CategoryCountOut(
                code=code,
                label=classification.FREQ_CATEGORY_LABELS.get(code, code),
                short_label=classification.short_freq_category_label(code),
                count=n,
            )
            # Largest first, then by code so equal counts don't reorder
            # between two otherwise identical queries.
            for code, n in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        # Pinned last rather than sorted in by size: custom entries have no
        # freq_category at all (they are parsed from a radio export, which
        # has no such concept), so this is a residue bucket, not a peer of
        # the categories above it. Without it the chips would sum to less
        # than the count they are describing.
        if app_state.custom_entries:
            category_counts.append(
                CategoryCountOut(
                    code=classification.CUSTOM_PSEUDO_CATEGORY,
                    label="Custom (from your radio)",
                    short_label="Custom",
                    count=len(app_state.custom_entries),
                )
            )

        return QueryResultOut(
            count=len(entries),
            total_count=total_count,
            level=status_result.level,
            cap=status_result.cap,
            amber_threshold=AMBER_THRESHOLD,
            entries=entries_out,
            # Measured over what the table actually lists, not the FAA
            # rows alone: with 498 filtered and 6 custom, the page stops
            # at 500 but the old test said nothing was cut, so the note
            # read "Showing all 504 entries" above 500 of them.
            truncated=total_count > MAX_DISPLAYED_ENTRIES,
            custom_entry_count=len(app_state.custom_entries),
            custom_group_count=len({e.group for e in app_state.custom_entries}),
            category_counts=category_counts,
        )

    @app.post("/api/generate")
    def generate(body: FilterStateIn) -> Response:
        state: AppState = app.state.afp_state
        loaded = _require_loaded(state)
        filter_state = _build_filter_state(body, loaded)
        # Custom entries keep their original group untouched and are never
        # deduplicated against the freshly generated set (spec §5 step 9).
        entries = (
            query_mod.filtered_entries(
                loaded.conn, filter_state, mode=body.mode, scheme=state.group_scheme
            )
            + state.custom_entries
        )

        # The file now declares the radio's group names itself, which is
        # what retired the old "go and rename six groups in YCE-46 by
        # hand first" step. Slots 0-5 are the active scheme's; 6-8 hold
        # whatever custom groups the user's own entries occupy.
        #
        # Custom names are taken in sorted order for now, so the same set
        # of groups always lands on the same slots. Remembering a group's
        # slot across a rename belongs with the UI that does the
        # renaming; until then there is nothing to remember it from, and
        # the file is self-consistent either way because every name it
        # uses is a name it defines.
        slots = group_slots(state.group_scheme, state.custom_slot_names)

        try:
            xml_bytes = build_xml(entries, FTA_850L, slots)
        except ExportValidationError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from None

        filename = f"airport_frequencies_{loaded.cycle_date.isoformat()}.xml"
        return Response(
            content=xml_bytes,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/about/acknowledge", response_model=StatusOut)
    def acknowledge_about() -> StatusOut:
        state: AppState = app.state.afp_state
        state.acknowledge_about()
        return status()

    def _custom_entries_out(state: AppState) -> CustomEntriesOut:
        entries_out = [
            CustomEntryOut(
                index=i,
                tag_name=e.tag_name,
                freq_mhz=e.freq_mhz,
                group=e.group,
                lat=e.lat,
                lon=e.lon,
                scan_memory=e.scan_memory,
                shift=e.shift,
            )
            for i, e in enumerate(state.custom_entries)
        ]
        return CustomEntriesOut(
            entries=entries_out,
            entry_count=len(entries_out),
            group_count=len({e.group for e in state.custom_entries}),
        )

    # ---------- the radius map ----------
    #
    # Split in two on purpose. The points are static for a loaded cycle
    # and large; the statuses are small and change on every slider tick.
    # Sending them together would re-send 19k coordinates to answer a
    # question about colour.

    # One code per status, in /api/map/points order. Single characters
    # because this is the part that travels on every filter change.
    _STATUS_CODES = {
        query_mod.SELECTED: "S",
        query_mod.EXCLUDED: "E",
        query_mod.OUTSIDE: "O",
        query_mod.FILTERED: "F",
        query_mod.SCOPE: "X",
    }

    def _map_point_ids(loaded: LoadedCycle) -> list[str]:
        """The point order, cached for the cycle. See LoadedCycle."""
        if loaded.map_point_ids is None:
            loaded.map_point_ids = [row[0] for row in query_mod.map_points(loaded.conn)]
        return loaded.map_point_ids

    @app.get("/api/map/points", response_model=MapPointsOut)
    def map_points() -> MapPointsOut:
        """Every drawable point, in a fixed order. Fetched once per
        cycle; the order is what /api/map/status indexes into."""
        loaded = _require_loaded(app.state.afp_state)
        return MapPointsOut(
            points=[
                MapPointOut(
                    id=row[0],
                    name=row[1] or "",
                    city=row[2] or "",
                    state=row[3] or "",
                    lat=row[4],
                    lon=row[5],
                    public_use=None if row[6] is None else bool(row[6]),
                    kind=row[7],
                    freq_count=row[8],
                )
                for row in query_mod.map_points(loaded.conn)
            ],
            cycle_date=loaded.cycle_date,
        )

    @app.post("/api/map/status", response_model=MapStatusOut)
    def map_status(body: FilterStateIn) -> MapStatusOut:
        """Why each point is in or out, for one filter state.

        Takes the same body as /api/query and resolves centres the same
        way, so an unresolvable centre fails here exactly as it does
        there rather than drawing a map of a filter that cannot run.
        """
        loaded = _require_loaded(app.state.afp_state)
        filter_state = _build_filter_state(body, loaded)
        result = query_mod.classify_points(loaded.conn, filter_state)

        # Flattened against the same order /api/map/points returned. A
        # point with no status cannot happen -- classify_points walks
        # every row of both tables -- but a missing code would silently
        # shift every colour after it, so it is spelled out.
        codes = "".join(_STATUS_CODES[result.statuses[pid]] for pid in _map_point_ids(loaded))

        rings = [
            MapRingOut(
                id=rule.id,
                center=rule.center,
                lat=resolved.center_lat,
                lon=resolved.center_lon,
                radius_nm=resolved.radius_nm,
                mode=resolved.mode,
            )
            for rule, resolved in zip(body.radius_filters, filter_state.radius_filters)
        ]

        return MapStatusOut(
            statuses=codes,
            unplottable=result.unplottable,
            rings=rings,
            cycle_date=loaded.cycle_date,
        )

    # ---------- groups ----------

    def _groups_out(state: AppState) -> GroupsOut:
        counts = Counter(e.group for e in state.custom_entries)
        return GroupsOut(
            scheme=state.group_scheme,
            schemes={name: list(names) for name, names in GROUP_SCHEMES.items()},
            preset_names=list(GROUP_SCHEMES[state.group_scheme]),
            custom_slots=[
                GroupSlotOut(
                    slot=PRESET_SLOTS + offset,
                    name=name,
                    entry_count=counts.get(name, 0) if name else 0,
                )
                for offset, name in enumerate(state.custom_slot_names)
            ],
            max_group_name_length=FTA_850L.max_group_name_length,
        )

    @app.get("/api/groups", response_model=GroupsOut)
    def get_groups() -> GroupsOut:
        return _groups_out(app.state.afp_state)

    @app.put("/api/groups/scheme", response_model=GroupsOut)
    def set_group_scheme(body: GroupSchemeIn) -> GroupsOut:
        """Changing this re-groups every generated entry on the next
        query -- the entries themselves do not move, the names they are
        filed under do.
        """
        state: AppState = app.state.afp_state
        try:
            state.set_group_scheme(body.scheme)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return _groups_out(state)

    @app.put("/api/groups/custom/{offset}", response_model=GroupsOut)
    def rename_custom_group(offset: int, body: CustomGroupNameIn) -> GroupsOut:
        """Names or renames one of the user's three slots.

        A name that collides with a scheme's or with another slot is
        suffixed rather than refused -- WEATHER becomes WEATHER1 -- so
        the answer is always a usable name and the UI reflects back what
        was actually used.
        """
        state: AppState = app.state.afp_state
        try:
            state.rename_custom_slot(offset, body.name)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return _groups_out(state)

    @app.post("/api/groups/copy", response_model=CopyEntriesOut)
    def copy_to_custom_group(body: CopyEntriesIn) -> CopyEntriesOut:
        """Snapshots the given entries into one of the user's slots.

        The entries arrive from the client rather than being re-derived
        here on purpose: a copy is a snapshot of what was on screen at
        the moment it was made, which is also what makes it independent
        of later NASR changes.
        """
        state: AppState = app.state.afp_state
        entries = [
            Entry(
                tag_name=e.tag_name,
                freq_mhz=e.freq_mhz,
                group="",
                lat=e.lat,
                lon=e.lon,
                category=e.category,
            )
            for e in body.entries
        ]
        try:
            copied, skipped = state.copy_to_custom_slot(body.slot, entries)
        except IndexError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        return CopyEntriesOut(
            group=state.custom_slot_names[body.slot] or "",
            copied=len(copied),
            skipped=skipped,
        )

    @app.get("/api/custom-entries", response_model=CustomEntriesOut)
    def get_custom_entries() -> CustomEntriesOut:
        state: AppState = app.state.afp_state
        return _custom_entries_out(state)

    @app.post("/api/custom-entries/import", response_model=CustomEntriesOut)
    async def import_custom_entries(request: Request) -> CustomEntriesOut:
        state: AppState = app.state.afp_state
        xml_bytes = await request.body()
        try:
            state.import_custom_entries(xml_bytes)
        except XmlParseError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except CustomGroupCapacityError as exc:
            raise HTTPException(
                status_code=422,
                detail={"groups": exc.groups, "found": exc.found, "available": exc.available},
            ) from None
        return _custom_entries_out(state)

    @app.delete("/api/custom-entries/{index}", response_model=CustomEntriesOut)
    def remove_custom_entry(index: int) -> CustomEntriesOut:
        state: AppState = app.state.afp_state
        try:
            state.remove_custom_entry(index)
        except IndexError:
            raise HTTPException(status_code=404, detail=f"no custom entry at index {index}") from None
        return _custom_entries_out(state)

    @app.post("/api/custom-entries/clear", response_model=CustomEntriesOut)
    def clear_custom_entries() -> CustomEntriesOut:
        state: AppState = app.state.afp_state
        state.clear_custom_entries()
        return _custom_entries_out(state)

    return app
