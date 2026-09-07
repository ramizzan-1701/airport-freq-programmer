"""Turns a FilterState into WHERE-clause SQL against the in-memory DB and
narrows a NormalizedData down to only the rows that survive.

The narrowed NormalizedData is handed unmodified to afp.selection.
select_entries -- filtering and selection stay separate concerns. This is
also why a filtered-out frequency type doesn't remove its airport: airport
membership is decided purely by airport-level filters (state/city/public-
private/radius), never by whether any of its frequencies survived, so
smart mode's "no substitute winner" rule (§3) still applies correctly.

Standalone facilities (VOR, RCAG, ARTCC, ...) whose SERVICED_FACILITY
never matches any APT_BASE.csv row -- "orphans" -- get a second, parallel
query path: filtered by their own state/city/radius (captured from
FRQ.csv's SERVICED_STATE/SERVICED_CITY, confirmed reliable even for
standalone facilities) but never by public/private, since that concept
doesn't exist for them at all (FACILITY_USE_CODE only lives in
APT_BASE.csv). "Orphan" is a structural fact about the whole dataset
(airport_id matches no row in the full airports table), not dependent on
which airports the current filters happen to keep.
"""

from __future__ import annotations

import sqlite3

from ..schema import Airport, Frequency, Ils, NormalizedData
from ..selection import Entry, select_entries
from .filters import FilterState

_ORPHAN_CONDITION = "airport_id NOT IN (SELECT id FROM airports)"


def _in_clause(column: str, values) -> tuple[str, list]:
    values = list(values)
    placeholders = ",".join("?" for _ in values)
    return f"{column} IN ({placeholders})", values


def _radius_clauses(
    filters: FilterState, *, include_template: str, exclude_template: str
) -> tuple[str, list]:
    """Combine the radius filters into a single WHERE fragment.

    Multiple "include" radii are OR'd: asking for LAX+60nm and SFO+60nm
    means "near either one", the same way listing two states means either
    state. AND-ing them (the original behaviour) asked for points within
    60nm of *both* centers, which for any two airports further apart than
    their combined radii is empty -- so a second include radius silently
    zeroed the results instead of widening them.

    Multiple "exclude" radii stay AND'd, which is what "not near X and
    also not near Y" already means -- OR-ing those would make each new
    exclusion cancel the previous one out.

    The two groups are then AND'd together: inside any include zone, and
    outside every exclude zone.

    Templates are passed in because airports and standalone facilities
    read position from different columns and differ on NULL handling.
    """
    include_parts: list[str] = []
    include_params: list = []
    exclude_parts: list[str] = []
    exclude_params: list = []

    for radius in filters.radius_filters:
        args = [radius.center_lat, radius.center_lon, radius.radius_nm]
        if radius.mode == "include":
            include_parts.append(include_template)
            include_params += args
        else:
            exclude_parts.append(exclude_template)
            exclude_params += args

    clauses: list[str] = []
    params: list = []
    # Params must be appended in the same order the clauses are joined,
    # not in the order the filters were declared, or the placeholders bind
    # to the wrong values once includes and excludes are interleaved.
    if include_parts:
        clauses.append("(" + " OR ".join(include_parts) + ")")
        params += include_params
    if exclude_parts:
        clauses.append("(" + " AND ".join(exclude_parts) + ")")
        params += exclude_params

    return " AND ".join(clauses), params


def _airport_where(filters: FilterState) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    if not filters.include_public and not filters.include_private:
        return "0", []  # matches nothing
    if not filters.include_public:
        clauses.append("public_use = 0")
    elif not filters.include_private:
        clauses.append("public_use = 1")

    if filters.states:
        clause, p = _in_clause("state", filters.states)
        clauses.append(clause)
        params += p

    if filters.cities:
        clause, p = _in_clause("city", filters.cities)
        clauses.append(clause)
        params += p

    radius_clause, radius_params = _radius_clauses(
        filters,
        include_template="haversine_nm(lat, lon, ?, ?) <= ?",
        exclude_template="haversine_nm(lat, lon, ?, ?) > ?",
    )
    if radius_clause:
        clauses.append(radius_clause)
        params += radius_params

    return (" AND ".join(clauses) if clauses else "1"), params


def _site_and_status_clauses(filters: FilterState) -> tuple[str, list]:
    """The platform_type / facility_status conditions, as they apply to a
    row of the frequencies table.

    Shared with the ILS query, which has no such columns of its own -- see
    _ils_airport_clause.
    """
    clauses: list[str] = []
    params: list = []

    if filters.platform_types:
        clause, p = _in_clause("platform_type", filters.platform_types)
        # platform_type is None for every row that isn't a landing-platform
        # site at all (VOR, RCAG, ARTCC, ...) -- SQL's IN(...) never
        # matches NULL, so narrowing to e.g. AIRPORT would otherwise
        # silently drop a VOR at that same airport. include_non_site_
        # facilities exempts these rows from this filter (and
        # facility_statuses below) rather than requiring a separate
        # "also allow non-site" option duplicated in each one.
        if filters.include_non_site_facilities:
            clause = f"({clause} OR platform_type IS NULL)"
        clauses.append(clause)
        params += p
    if filters.facility_statuses:
        clause, p = _in_clause("facility_status", filters.facility_statuses)
        if filters.include_non_site_facilities:
            clause = f"({clause} OR facility_status IS NULL)"
        clauses.append(clause)
        params += p

    return " AND ".join(clauses), params


def _ils_airport_clause(filters: FilterState) -> tuple[str, list]:
    """Restrict ILS records to airports that satisfy the site-type and
    facility-status filters.

    Those two describe the airport, but the schema carries them on its
    frequency rows -- the ils table has neither. Filtering ILS by airport
    id alone therefore ignored both completely: asking for CA + ILS +
    NON_TOWERED returned SJC, SMF, LAX and every other towered field in
    the state, because the only thing narrowing ILS was the airport's
    state.

    An airport qualifies when at least one of its frequency rows matches,
    which is how the same filters already decide whether to keep that
    airport's comm frequencies.
    """
    inner, params = _site_and_status_clauses(filters)
    if not inner:
        return "1", []
    return f"airport_id IN (SELECT airport_id FROM frequencies WHERE {inner})", params


def _frequency_where(filters: FilterState) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    if filters.freq_categories:
        clause, p = _in_clause("freq_category", filters.freq_categories)
        clauses.append(clause)
        params += p
    if filters.weather_subtypes:
        clause, p = _in_clause("weather_subtype", filters.weather_subtypes)
        clauses.append(clause)
        params += p
    site_clause, site_params = _site_and_status_clauses(filters)
    if site_clause:
        clauses.append(site_clause)
        params += site_params
    if filters.primary_approach_radio_calls:
        clause, p = _in_clause("primary_approach_radio_call", filters.primary_approach_radio_calls)
        clauses.append(clause)
        params += p
    if filters.tower_hours_24_only:
        clauses.append("tower_hours = '24'")

    return (" AND ".join(clauses) if clauses else "1"), params


def _orphan_frequency_where(filters: FilterState) -> tuple[str, list]:
    """State/city/radius for standalone facilities, using the frequency's
    own state/city/lat/lon columns instead of a joined airport's -- never
    gated by public/private, which has no equivalent for them at all.
    """
    clauses: list[str] = []
    params: list = []

    if filters.states:
        clause, p = _in_clause("state", filters.states)
        clauses.append(clause)
        params += p

    if filters.cities:
        clause, p = _in_clause("city", filters.cities)
        clauses.append(clause)
        params += p

    # Unlike airports, standalone facilities' own lat/lon (FRQ.csv's
    # LAT_DECIMAL/LONG_DECIMAL) isn't guaranteed present -- real data has
    # rows (e.g. some TRACON/APCH_DEP facilities) that report a frequency
    # but no position at all. haversine_nm() has no NULL handling and
    # previously crashed the whole query (sqlite raises "user-defined
    # function raised exception") the moment any radius filter touched one
    # of these rows. A row with no known position can't be confirmed
    # inside a radius, so: excluded under "include" (fails the distance
    # check), kept under "exclude" (never confirmed to be in the exclusion
    # zone, so not safe to drop).
    radius_clause, radius_params = _radius_clauses(
        filters,
        include_template="(lat IS NOT NULL AND lon IS NOT NULL AND haversine_nm(lat, lon, ?, ?) <= ?)",
        exclude_template="(lat IS NULL OR lon IS NULL OR haversine_nm(lat, lon, ?, ?) > ?)",
    )
    if radius_clause:
        clauses.append(radius_clause)
        params += radius_params

    return (" AND ".join(clauses) if clauses else "1"), params


def _ils_where(filters: FilterState) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    if filters.ils_component_statuses:
        clause, p = _in_clause("component_status", filters.ils_component_statuses)
        clauses.append(clause)
        params += p
    if filters.ils_system_types:
        clause, p = _in_clause("system_type", filters.ils_system_types)
        clauses.append(clause)
        params += p

    return (" AND ".join(clauses) if clauses else "1"), params


def resolve_center(conn: sqlite3.Connection, center: str | tuple[float, float]) -> tuple[float, float]:
    """Resolve a radius filter's center: either an (lat, lon) pair, or an
    airport ID looked up in the loaded data.
    """
    if isinstance(center, tuple):
        return center
    # Airport IDs are stored exactly as FAA provides them in ARPT_ID
    # (always uppercase in real NASR data) -- the web UI's radius-center
    # field is free text, so a user typing "lax" would otherwise silently
    # fail the lookup and surface as a generic "Query failed" error.
    row = conn.execute("SELECT lat, lon FROM airports WHERE id = ?", (center.strip().upper(),)).fetchone()
    if row is None:
        raise ValueError(f"unknown airport id for radius center: {center!r}")
    return row[0], row[1]


_FREQ_COLUMNS = (
    "airport_id, freq_mhz, freq_category, platform_type, facility_status, "
    "weather_subtype, primary_approach_radio_call, tower_hours, raw_freq_use, "
    "source_remark, lat, lon, state, city, name"
)


def _row_to_frequency(r) -> Frequency:
    return Frequency(
        airport_id=r[0], freq_mhz=r[1], freq_category=r[2], platform_type=r[3],
        facility_status=r[4], weather_subtype=r[5], primary_approach_radio_call=r[6],
        tower_hours=r[7], raw_freq_use=r[8], source_remark=r[9], lat=r[10], lon=r[11],
        state=r[12], city=r[13], name=r[14],
    )


def apply_filters(conn: sqlite3.Connection, filters: FilterState) -> NormalizedData:
    airport_where, airport_params = _airport_where(filters)
    airport_rows = conn.execute(
        f"SELECT id, name, city, state, country, lat, lon, public_use "
        f"FROM airports WHERE {airport_where}",
        airport_params,
    ).fetchall()
    airport_ids = [r[0] for r in airport_rows]
    airports = [
        Airport(id=r[0], name=r[1], city=r[2], state=r[3], country=r[4], lat=r[5], lon=r[6], public_use=bool(r[7]))
        for r in airport_rows
    ]

    freq_where, freq_params = _frequency_where(filters)
    frequencies: list[Frequency] = []

    if airport_ids:
        id_clause, id_params = _in_clause("airport_id", airport_ids)
        freq_rows = conn.execute(
            f"SELECT {_FREQ_COLUMNS} FROM frequencies WHERE {id_clause} AND {freq_where}",
            id_params + freq_params,
        ).fetchall()
        frequencies.extend(_row_to_frequency(r) for r in freq_rows)

        if filters.should_include_ils:
            ils_where, ils_params = _ils_where(filters)
            airport_clause, airport_params = _ils_airport_clause(filters)
            ils_rows = conn.execute(
                f"SELECT airport_id, runway_end_id, freq_mhz, system_type, component_status "
                f"FROM ils WHERE {id_clause} AND {ils_where} AND {airport_clause}",
                id_params + ils_params + airport_params,
            ).fetchall()
            ils = [
                Ils(airport_id=r[0], runway_end_id=r[1], freq_mhz=r[2], system_type=r[3], component_status=r[4])
                for r in ils_rows
            ]
        else:
            ils = []
    else:
        ils = []

    orphan_where, orphan_params = _orphan_frequency_where(filters)
    orphan_rows = conn.execute(
        f"SELECT {_FREQ_COLUMNS} FROM frequencies "
        f"WHERE {_ORPHAN_CONDITION} AND {freq_where} AND {orphan_where}",
        freq_params + orphan_params,
    ).fetchall()
    frequencies.extend(_row_to_frequency(r) for r in orphan_rows)

    return NormalizedData(airports=airports, frequencies=frequencies, ils=ils)


def has_ils_data(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM ils LIMIT 1").fetchone() is not None


def filtered_entries(conn: sqlite3.Connection, filters: FilterState, mode: str = "smart") -> list[Entry]:
    """Apply filters, then run selection -- public/private is already
    resolved by apply_filters, so select_entries is told to keep
    everything it's handed rather than re-applying its own default scope.
    """
    data = apply_filters(conn, filters)
    return select_entries(data, mode=mode, include_public=True, include_private=True)


def count_entries(conn: sqlite3.Connection, filters: FilterState, mode: str = "smart") -> int:
    return len(filtered_entries(conn, filters, mode=mode))


def _list_distinct(conn: sqlite3.Connection, table: str, column: str, where: str = "1", params=()) -> list[str]:
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM {table} "
        f"WHERE {column} IS NOT NULL AND {column} != '' AND ({where}) "
        f"ORDER BY {column}",
        params,
    ).fetchall()
    return [r[0] for r in rows]


def list_states(conn: sqlite3.Connection) -> list[str]:
    airport_states = _list_distinct(conn, "airports", "state")
    orphan_states = _list_distinct(conn, "frequencies", "state", where=_ORPHAN_CONDITION)
    return sorted(set(airport_states) | set(orphan_states))


def list_cities(conn: sqlite3.Connection, states: frozenset[str] | None = None) -> list[str]:
    """City options, scoped to whatever state(s) are currently selected
    (§3: "City ... scoped to whatever state(s) are currently selected") --
    includes both airport cities and standalone-facility cities.
    """
    if states:
        state_clause, state_params = _in_clause("state", states)
        airport_cities = _list_distinct(conn, "airports", "city", where=state_clause, params=state_params)
        orphan_cities = _list_distinct(
            conn, "frequencies", "city",
            where=f"{state_clause} AND {_ORPHAN_CONDITION}",
            params=state_params,
        )
    else:
        airport_cities = _list_distinct(conn, "airports", "city")
        orphan_cities = _list_distinct(conn, "frequencies", "city", where=_ORPHAN_CONDITION)
    return sorted(set(airport_cities) | set(orphan_cities))


def list_freq_categories(conn: sqlite3.Connection) -> list[str]:
    """Distinct freq_category codes actually present in the loaded data
    (a subset of afp.classification.ALL_FREQ_CATEGORIES) -- callers add
    human-readable labels and the default/advanced split via that module.
    Does not include the "ILS" pseudo-category -- callers add that
    separately, gated on has_ils_data().
    """
    return _list_distinct(conn, "frequencies", "freq_category")


def list_weather_subtypes(conn: sqlite3.Connection) -> list[str]:
    return _list_distinct(conn, "frequencies", "weather_subtype")


def list_platform_types(conn: sqlite3.Connection) -> list[str]:
    return _list_distinct(conn, "frequencies", "platform_type")


def list_facility_statuses(conn: sqlite3.Connection) -> list[str]:
    return _list_distinct(conn, "frequencies", "facility_status")


def list_primary_approach_radio_calls(conn: sqlite3.Connection) -> list[str]:
    return _list_distinct(conn, "frequencies", "primary_approach_radio_call")


def list_ils_component_statuses(conn: sqlite3.Connection) -> list[str]:
    return _list_distinct(conn, "ils", "component_status")


def list_ils_system_types(conn: sqlite3.Connection) -> list[str]:
    return _list_distinct(conn, "ils", "system_type")
