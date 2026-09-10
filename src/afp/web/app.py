"""FastAPI app for the local web UI: filters + live counter + generate
button (spec §7 build order step 4). Wraps the query layer (milestone 3)
and export pipeline (milestone 1) -- no new business logic lives here,
just request/response plumbing.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import classification
from ..counter import counter_status
from ..custom_entries import CustomGroupCapacityError
from ..export.fta850l import FTA_850L
from ..export.xml_reader import XmlParseError
from ..export.xml_writer import ExportValidationError, build_xml
from ..nasr.source import FetchResult
from ..query import query as query_mod
from ..selection import FIXED_GROUP_NAMES, orphan_tag_ids, select_entries
from .models import (
    CategoryCountOut,
    CustomEntriesOut,
    CustomEntryOut,
    EntryOut,
    FilterOptionsOut,
    FilterStateIn,
    FreqCategoryOption,
    LabeledOption,
    QueryResultOut,
    ResolvedCenterOut,
    StatusOut,
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
    app = FastAPI(title="Airport Frequency Programmer")
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
            group_setup_acknowledged=state.group_setup_acknowledged,
            fixed_group_names=sorted(FIXED_GROUP_NAMES),
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
        state.load_cycle(cycle_date)
        return status()

    @app.post("/api/fetch", response_model=StatusOut)
    def fetch(mark_processed: bool = True) -> StatusOut:
        state: AppState = app.state.afp_state
        result: FetchResult = state.source.fetch_current_cycle()
        if mark_processed:
            state.source.mark_processed(result.cycle)
        state.load_cycle(result.cycle.effective_date)
        return status()

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
        entries = select_entries(filtered_data, mode=body.mode, include_public=True, include_private=True)

        # The radio's cap applies to filtered + held custom entries
        # together (spec §5 step 7), and the preview lists them together
        # too, in the same order /api/generate writes them to the XML --
        # the table is meant to be a picture of the file about to be
        # produced, so an entry that lands in the file belongs in it.
        all_entries = entries + app_state.custom_entries
        total_count = len(all_entries)
        status_result = counter_status(total_count, FTA_850L)
        display = all_entries[:MAX_DISPLAYED_ENTRIES]
        # Everything from this index on came out of the user's radio.
        custom_start = len(entries)
        entries_out = []
        for i, e in enumerate(display):
            if i >= custom_start:
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
            entries=entries_out,
            truncated=len(entries) > MAX_DISPLAYED_ENTRIES,
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
        entries = query_mod.filtered_entries(loaded.conn, filter_state, mode=body.mode) + state.custom_entries

        try:
            xml_bytes = build_xml(entries, FTA_850L)
        except ExportValidationError as exc:
            raise HTTPException(status_code=422, detail={"problems": exc.problems}) from None

        filename = f"airport_frequencies_{loaded.cycle_date.isoformat()}.xml"
        return Response(
            content=xml_bytes,
            media_type="application/xml",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/group-setup/acknowledge", response_model=StatusOut)
    def acknowledge_group_setup() -> StatusOut:
        state: AppState = app.state.afp_state
        state.acknowledge_group_setup()
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
