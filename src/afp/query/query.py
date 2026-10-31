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
from dataclasses import dataclass

from ..schema import Airport, Frequency, Ils, NormalizedData
from ..selection import ALPHABETICAL, Entry, select_entries
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
    inc_clause, inc_params, exc_clause, exc_params = radius_halves(
        filters, include_template=include_template, exclude_template=exclude_template
    )

    clauses: list[str] = []
    params: list = []
    # Params must be appended in the same order the clauses are joined,
    # not in the order the filters were declared, or the placeholders bind
    # to the wrong values once includes and excludes are interleaved.
    if inc_clause:
        clauses.append(inc_clause)
        params += inc_params
    if exc_clause:
        clauses.append(exc_clause)
        params += exc_params

    return " AND ".join(clauses), params


def radius_halves(
    filters: FilterState, *, include_template: str, exclude_template: str
) -> tuple[str, list, str, list]:
    """The include and exclude sides as separate fragments.

    _radius_clauses ANDs them together, which is what filtering wants.
    The map wants them apart: an airport outside every include ring and
    an airport sitting inside an exclude ring are both absent from the
    results but mean different things to someone looking at a map, and
    only separate tests can tell them apart.

    Shared rather than reimplemented so the two can never disagree about
    what a ring contains -- which, on a map drawn next to the number it
    explains, would be visible as the map contradicting the counter.

    Either half is "" when no rule of that kind exists.
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

    inc = "(" + " OR ".join(include_parts) + ")" if include_parts else ""
    exc = "(" + " AND ".join(exclude_parts) + ")" if exclude_parts else ""
    return inc, include_params, exc, exclude_params


def _airport_where(filters: FilterState) -> tuple[str, list]:
    clauses: list[str] = []
    params: list = []

    include_public, include_private = filters.airport_use_scope
    if not include_public:
        clauses.append("public_use = 0")
    elif not include_private:
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
    if filters.excluded_freq_categories:
        values = list(filters.excluded_freq_categories)
        placeholders = ",".join("?" for _ in values)
        clauses.append(f"freq_category NOT IN ({placeholders})")
        params += values
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



# ---------- the radius map's per-point status ----------
#
# The map colours every airport by *why* it is in or out, which the
# filtered result set cannot say: it holds only the survivors, and an
# airport outside every include ring looks exactly like one in the wrong
# state once it is gone.
#
# Built from the same SQL fragments apply_filters uses rather than a
# second predicate in Python. The map sits next to the counter it
# explains, so any disagreement between them is visible -- and would be
# read as the counter being wrong.

SELECTED = "selected"   # passes every filter
EXCLUDED = "excluded"   # inside an exclude ring
OUTSIDE = "outside"     # outside every include ring
FILTERED = "filtered"   # fails the state or city filter
SCOPE = "scope"         # fails public/private, so never drawn

# The first test a point fails decides its status. Ordered, because an
# airport in the wrong state that also happens to sit inside an exclude
# ring is filtered, not excluded -- it was never a candidate to exclude.
_PRECEDENCE = (SCOPE, FILTERED, OUTSIDE, EXCLUDED, SELECTED)


@dataclass(frozen=True)
class MapStatus:
    """Why each point is in or out, for one filter state."""

    # id -> one of the constants above. Airport ids and standalone
    # facility ids cannot collide: a facility is defined as a frequency
    # whose airport_id is not an airport.
    statuses: dict[str, str]
    # Standalone facilities with no position at all. Real NASR data has
    # them (some TRACON/APCH_DEP rows report a frequency and no lat/lon).
    # They cannot be drawn, so they are counted instead of vanishing.
    unplottable: int
    # id -> how many of that point's frequencies survive the
    # frequency-level filters: category, weather subtype, site type,
    # facility status, approach call, 24-hour tower, plus its ILS
    # records when the ILS pseudo-category is on. Ids with none are
    # absent rather than zero.
    #
    # This is not part of the status, and deliberately so. A status is
    # about where a point is; this is about whether it has anything to
    # give once it is there, which are different questions with
    # different answers -- an airport can sit squarely inside a ring and
    # contribute nothing because none of its frequencies are in the
    # categories you asked for. The map still draws it as selected,
    # because it is; the hover is where the difference is told.
    matching_frequencies: dict[str, int]


def _status_from_flags(in_scope: int, passes_sc: int, in_include: int, outside_excludes: int) -> str:
    if not in_scope:
        return SCOPE
    if not passes_sc:
        return FILTERED
    if not in_include:
        return OUTSIDE
    if not outside_excludes:
        return EXCLUDED
    return SELECTED


def _matching_frequency_counts(conn: sqlite3.Connection, filters: FilterState) -> dict[str, int]:
    """How many frequencies each point still has once the frequency-level
    filters are applied.

    Counted here, not folded into the status, because the status answers
    "where is this point" and this answers "is it worth anything". The
    map draws the first; the hover tells you the second.

    Deliberately the frequency-level filters only -- no state, city,
    radius or use scope. Those are already in the status, and counting
    them twice would make a point that fails them read as a point whose
    frequencies were all filtered away, which is a different sentence.

    Two queries rather than one per point: a GROUP BY over the whole
    table costs about what one correlated subquery would, and this runs
    on every status refresh.

    Note that this can come out below the point's total even when the
    user has filtered nothing. FilterStateIn fills excluded_freq_categories
    with HIDDEN_FROM_WEB_UI_CATEGORIES whenever no categories are chosen,
    so AIRSPACE_INFO, EMERGENCY and NDB are always dropped. That is the
    honest number -- those never reach the export either -- but it is why
    the hover says "the frequency filters" and not "your".
    """
    freq_where, freq_params = _frequency_where(filters)
    counts: dict[str, int] = dict(
        conn.execute(
            f"SELECT airport_id, COUNT(*) FROM frequencies WHERE {freq_where} GROUP BY airport_id",
            freq_params,
        ).fetchall()
    )

    # ILS lives in its own table and is kept or dropped as a whole by
    # the pseudo-category, so it is counted separately or not at all.
    # Leaving it out would report "none pass your filters" for every
    # airport under an ILS-only selection -- the one case where the
    # count matters most, and the one it would get exactly backwards.
    if filters.should_include_ils:
        ils_where, ils_params = _ils_where(filters)
        airport_clause, airport_params = _ils_airport_clause(filters)
        rows = conn.execute(
            f"SELECT airport_id, COUNT(*) FROM ils "
            f"WHERE {ils_where} AND {airport_clause} GROUP BY airport_id",
            ils_params + airport_params,
        ).fetchall()
        for point_id, n in rows:
            counts[point_id] = counts.get(point_id, 0) + n

    return counts


def classify_points(conn: sqlite3.Connection, filters: FilterState) -> MapStatus:
    """Per-airport and per-positioned-facility status for the radius map.

    The invariant worth holding on to, and the one the tests assert: the
    ids classified SELECTED are exactly the airport ids apply_filters
    returns for the same FilterState. The map is a picture of the filter,
    not a second opinion about it.
    """
    statuses: dict[str, str] = {}

    # --- airports ---
    include_public, include_private = filters.airport_use_scope
    if not include_public:
        scope_sql, scope_params = "public_use = 0", []
    elif not include_private:
        scope_sql, scope_params = "public_use = 1", []
    else:
        scope_sql, scope_params = "1", []

    sc_parts: list[str] = []
    sc_params: list = []
    if filters.states:
        clause, p = _in_clause("state", filters.states)
        sc_parts.append(clause)
        sc_params += p
    if filters.cities:
        clause, p = _in_clause("city", filters.cities)
        sc_parts.append(clause)
        sc_params += p
    sc_sql = " AND ".join(sc_parts) if sc_parts else "1"

    inc_sql, inc_params, exc_sql, exc_params = radius_halves(
        filters,
        include_template="haversine_nm(lat, lon, ?, ?) <= ?",
        exclude_template="haversine_nm(lat, lon, ?, ?) > ?",
    )
    # No include rules means no include constraint -- everything is
    # "inside", the same reading apply_filters gives an absent clause.
    # No exclude rules means nothing is excluded.
    inc_sql = inc_sql or "1"
    exc_sql = exc_sql or "1"

    rows = conn.execute(
        f"SELECT id, ({scope_sql}), ({sc_sql}), ({inc_sql}), ({exc_sql}) FROM airports",
        scope_params + sc_params + inc_params + exc_params,
    ).fetchall()
    for point_id, in_scope, passes_sc, in_include, outside_excludes in rows:
        statuses[point_id] = _status_from_flags(in_scope, passes_sc, in_include, outside_excludes)

    # --- standalone facilities ---
    #
    # Never gated by public/private: FACILITY_USE_CODE lives in
    # APT_BASE.csv and has no equivalent for these at all, which is why
    # _orphan_frequency_where leaves the scope test out too.
    #
    # One row per facility. A facility reports many frequencies and the
    # position columns ride along on each of them, so an unaggregated
    # query would classify the same id once per frequency.
    f_sc_parts: list[str] = []
    f_sc_params: list = []
    if filters.states:
        clause, p = _in_clause("state", filters.states)
        f_sc_parts.append(clause)
        f_sc_params += p
    if filters.cities:
        clause, p = _in_clause("city", filters.cities)
        f_sc_parts.append(clause)
        f_sc_params += p
    f_sc_sql = " AND ".join(f_sc_parts) if f_sc_parts else "1"

    # Counted separately, and then excluded from the classification
    # query below, for a reason that is easy to trip over: SQLite
    # short-circuits AND in a WHERE clause but not in a projection. The
    # templates _orphan_frequency_where uses guard NULL positions with
    # "lat IS NOT NULL AND ... haversine_nm(...)", which is safe there
    # and raises here -- the function gets called with NULLs anyway.
    #
    # Dropping them first means the guards are no-ops, so the plain
    # airport templates are exactly equivalent on what remains, and the
    # map still uses the same distance expression as the filter.
    unplottable = conn.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT airport_id FROM frequencies
             WHERE {_ORPHAN_CONDITION}
             GROUP BY airport_id
            HAVING MAX(lat IS NOT NULL AND lon IS NOT NULL) = 0
        )
        """
    ).fetchone()[0]

    f_inc_sql, f_inc_params, f_exc_sql, f_exc_params = radius_halves(
        filters,
        include_template="haversine_nm(lat, lon, ?, ?) <= ?",
        exclude_template="haversine_nm(lat, lon, ?, ?) > ?",
    )
    f_inc_sql = f_inc_sql or "1"
    f_exc_sql = f_exc_sql or "1"

    facility_rows = conn.execute(
        f"""
        SELECT airport_id, ({f_sc_sql}), ({f_inc_sql}), ({f_exc_sql})
          FROM frequencies
         WHERE rowid IN (
               SELECT MIN(rowid) FROM frequencies
                WHERE {_ORPHAN_CONDITION}
                  AND lat IS NOT NULL AND lon IS NOT NULL
                GROUP BY airport_id
         )
        """,
        f_sc_params + f_inc_params + f_exc_params,
    ).fetchall()

    for point_id, passes_sc, in_include, outside_excludes in facility_rows:
        statuses[point_id] = _status_from_flags(1, passes_sc, in_include, outside_excludes)

    matching = _matching_frequency_counts(conn, filters)

    return MapStatus(statuses=statuses, unplottable=unplottable, matching_frequencies=matching)



def map_points(conn: sqlite3.Connection) -> list[tuple]:
    """Every drawable point for the radius map, in a fixed order.

    Static for a loaded cycle: position, name and kind do not depend on
    any filter, so the client fetches this once and then asks only for
    statuses as the filters change. Nationwide with private airports on
    is about 19k rows -- re-sending coordinates on every slider tick
    would be most of the payload and none of the information.

    The order is the contract. classify_points returns a mapping, and
    the status endpoint flattens it against this list, so both ends have
    to walk the points the same way.

    Standalone facilities with no position are left out: they cannot be
    drawn. classify_points counts them separately so the legend can say
    so rather than quietly losing them.
    """
    rows = conn.execute(
        """
        SELECT a.id, a.name, a.city, a.state, a.lat, a.lon, a.public_use, 'airport',
               -- ILS counts here because MapStatus.matching_frequencies
               -- counts it too, and the hover shows the two side by
               -- side. Different universes would read as nonsense:
               -- "2 freq, 3 pass your filters".
               (SELECT COUNT(*) FROM frequencies f WHERE f.airport_id = a.id)
               + (SELECT COUNT(*) FROM ils i WHERE i.airport_id = a.id)
          FROM airports a
         ORDER BY a.id
        """
    ).fetchall()

    facility_rows = conn.execute(
        f"""
        SELECT airport_id,
               MIN(name),
               MIN(city),
               MIN(state),
               MIN(lat),
               MIN(lon),
               NULL,
               'facility',
               COUNT(*)
          FROM frequencies
         WHERE {_ORPHAN_CONDITION}
           AND lat IS NOT NULL AND lon IS NOT NULL
         GROUP BY airport_id
         ORDER BY airport_id
        """
    ).fetchall()

    return list(rows) + list(facility_rows)


def has_ils_data(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM ils LIMIT 1").fetchone() is not None


def filtered_entries(
    conn: sqlite3.Connection,
    filters: FilterState,
    mode: str = "smart",
    scheme: str = ALPHABETICAL,
) -> list[Entry]:
    """Apply filters, then run selection -- public/private is already
    resolved by apply_filters, so select_entries is told to keep
    everything it's handed rather than re-applying its own default scope.
    """
    data = apply_filters(conn, filters)
    return select_entries(
        data, mode=mode, include_public=True, include_private=True, scheme=scheme
    )


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
