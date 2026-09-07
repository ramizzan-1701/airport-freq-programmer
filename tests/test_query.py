import pytest

from afp.query import (
    FilterState,
    RadiusFilter,
    apply_filters,
    build_database,
    count_entries,
    list_cities,
    list_states,
    resolve_center,
)
from afp.schema import Airport, Frequency, Ils, NormalizedData


@pytest.fixture
def sample_data() -> NormalizedData:
    return NormalizedData(
        airports=[
            Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True),
            Airport(id="BBB", name="B Field", city="SAN FRANCISCO", state="CA", lat=37.0, lon=-122.0, public_use=True),
            Airport(id="CCC", name="C Field", city="LAS VEGAS", state="NV", lat=36.0, lon=-115.0, public_use=True),
            Airport(id="DDD", name="D Strip", city="LOS ANGELES", state="CA", lat=34.05, lon=-118.05, public_use=False),
        ],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF", platform_type="AIRPORT",
                       facility_status="NON_TOWERED", primary_approach_radio_call="LA APPROACH", tower_hours=""),
            Frequency(airport_id="AAA", freq_mhz=118.0, freq_category="TOWER", facility_status="TOWERED", tower_hours="24"),
            Frequency(airport_id="BBB", freq_mhz=126.0, freq_category="WEATHER_STATION", weather_subtype="ATIS",
                       facility_status="TOWERED", tower_hours="0600-2200"),
            Frequency(airport_id="CCC", freq_mhz=122.9, freq_category="CTAF", facility_status="NON_TOWERED", tower_hours=""),
            Frequency(airport_id="DDD", freq_mhz=123.0, freq_category="CTAF", facility_status="NON_TOWERED", tower_hours=""),
        ],
        ils=[
            Ils(airport_id="AAA", runway_end_id="25L", freq_mhz=109.9, system_type="LS", component_status="OPERATIONAL IFR"),
            Ils(airport_id="BBB", runway_end_id="28", freq_mhz=110.3, system_type="LC", component_status="OPERATIONAL RESTRICTED"),
        ],
    )


@pytest.fixture
def conn(sample_data):
    return build_database(sample_data)


def _ids(data):
    return {a.id for a in data.airports}


def test_no_filters_matches_all_public_airports(conn):
    result = apply_filters(conn, FilterState())
    assert _ids(result) == {"AAA", "BBB", "CCC"}  # DDD is private, excluded by default


def test_state_filter_is_or_within_group(conn):
    result = apply_filters(conn, FilterState(states=frozenset({"CA"})))
    assert _ids(result) == {"AAA", "BBB"}

    result = apply_filters(conn, FilterState(states=frozenset({"CA", "NV"})))
    assert _ids(result) == {"AAA", "BBB", "CCC"}


def test_city_filter_scoped_by_state(conn):
    assert set(list_cities(conn)) == {"LAS VEGAS", "LOS ANGELES", "SAN FRANCISCO"}
    assert set(list_cities(conn, states=frozenset({"NV"}))) == {"LAS VEGAS"}


def test_city_filter_narrows_results(conn):
    result = apply_filters(conn, FilterState(cities=frozenset({"LOS ANGELES"})))
    assert _ids(result) == {"AAA"}  # DDD is also LA but private, excluded by default


def test_state_and_city_combine_with_and(conn):
    result = apply_filters(conn, FilterState(states=frozenset({"NV"}), cities=frozenset({"LOS ANGELES"})))
    assert _ids(result) == set()  # no airport is both NV and LOS ANGELES


def test_include_private_and_exclude_public(conn):
    both = apply_filters(conn, FilterState(include_public=True, include_private=True))
    assert _ids(both) == {"AAA", "BBB", "CCC", "DDD"}

    private_only = apply_filters(conn, FilterState(include_public=False, include_private=True))
    assert _ids(private_only) == {"DDD"}

    neither = apply_filters(conn, FilterState(include_public=False, include_private=False))
    assert _ids(neither) == set()


def test_freq_category_filter_narrows_frequencies_but_keeps_airport(conn):
    """Filtering to a category with zero matches must keep the airports
    (so smart mode can legitimately produce zero entries for them) rather
    than dropping them outright (§3).
    """
    result = apply_filters(conn, FilterState(freq_categories=frozenset({"VOR"})))
    assert _ids(result) == {"AAA", "BBB", "CCC"}  # airports still present...
    assert result.frequencies == []  # ...but no frequency rows survive


def test_facility_status_filter(conn):
    result = apply_filters(conn, FilterState(facility_statuses=frozenset({"TOWERED"})))
    freq_airports = {f.airport_id for f in result.frequencies}
    assert freq_airports == {"AAA", "BBB"}


def test_platform_type_filter(conn):
    result = apply_filters(conn, FilterState(platform_types=frozenset({"AIRPORT"})))
    freq_airports = {f.airport_id for f in result.frequencies}
    assert freq_airports == {"AAA"}


def test_platform_type_filter_excludes_null_platform_rows_by_default(conn):
    """AAA has a CTAF row with platform_type="AIRPORT" and a TOWER row
    with no platform_type at all (None, same as a real VOR/RCAG row) --
    filtering to just AIRPORT must keep only the CTAF row, unchanged
    behavior when include_non_site_facilities isn't checked.
    """
    result = apply_filters(conn, FilterState(platform_types=frozenset({"AIRPORT"})))
    assert len(result.frequencies) == 1
    assert result.frequencies[0].freq_category == "CTAF"


def test_platform_type_filter_with_include_non_site_keeps_null_platform_rows_too(conn):
    """Checking AIRPORT + include_non_site_facilities together must keep
    both AAA's AIRPORT-platform CTAF row and its no-platform TOWER row --
    this is the fix for "choosing Airport loses VOR towers".
    """
    result = apply_filters(
        conn, FilterState(platform_types=frozenset({"AIRPORT"}), include_non_site_facilities=True)
    )
    aaa_categories = {f.freq_category for f in result.frequencies if f.airport_id == "AAA"}
    assert aaa_categories == {"CTAF", "TOWER"}


def test_include_non_site_facilities_alone_has_no_effect_without_a_narrowing_filter(conn):
    """include_non_site_facilities only exempts non-site rows from
    platform_types/facility_statuses -- with neither of those set, there's
    nothing to exempt them from, so it must be a no-op.
    """
    baseline = apply_filters(conn, FilterState())
    with_flag = apply_filters(conn, FilterState(include_non_site_facilities=True))
    assert len(with_flag.frequencies) == len(baseline.frequencies)


def test_non_site_facility_survives_both_platform_type_and_facility_status_filters_at_once(conn):
    """The scenario Option B specifically exists for: a genuine non-site
    row (both platform_type and facility_status None, e.g. a VOR) must
    survive Site Type AND Facility Status being narrowed simultaneously
    with a single include_non_site_facilities toggle -- not require
    checking a separate "also allow non-site" option in each filter.
    """
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=118.0, freq_category="CTAF", platform_type="AIRPORT", facility_status="TOWERED"),
            Frequency(airport_id="AAA", freq_mhz=112.6, freq_category="VOR", platform_type=None, facility_status=None),
        ],
        ils=[],
    )
    local_conn = build_database(data)

    combined_without_flag = apply_filters(
        local_conn,
        FilterState(platform_types=frozenset({"AIRPORT"}), facility_statuses=frozenset({"TOWERED"})),
    )
    assert {f.freq_category for f in combined_without_flag.frequencies} == {"CTAF"}  # VOR dropped

    combined_with_flag = apply_filters(
        local_conn,
        FilterState(
            platform_types=frozenset({"AIRPORT"}),
            facility_statuses=frozenset({"TOWERED"}),
            include_non_site_facilities=True,
        ),
    )
    assert {f.freq_category for f in combined_with_flag.frequencies} == {"CTAF", "VOR"}


def test_weather_subtype_filter(conn):
    result = apply_filters(conn, FilterState(weather_subtypes=frozenset({"ATIS"})))
    freq_airports = {f.airport_id for f in result.frequencies}
    assert freq_airports == {"BBB"}


def test_ils_folded_into_freq_categories_excluded_when_narrowed_without_it(conn):
    """ILS is a pseudo freq_category ("ILS") that never actually appears
    in the frequencies table -- narrowing freq_categories to a set that
    doesn't include it excludes ILS the same way it excludes every other
    unselected category (spec discussion: fold ILS into Frequency
    Category rather than a separate control).
    """
    result = apply_filters(conn, FilterState(freq_categories=frozenset({"WEATHER_STATION"})))
    assert result.ils == []


def test_ils_folded_into_freq_categories_included_when_explicitly_selected(conn):
    result = apply_filters(conn, FilterState(freq_categories=frozenset({"WEATHER_STATION", "ILS"})))
    assert {i.airport_id for i in result.ils} == {"AAA", "BBB"}


def test_ils_unaffected_by_narrowing_other_filter_dimensions(conn):
    """Accepted tradeoff from the ILS-folding discussion: only
    freq_categories governs ILS visibility now. Narrowing Platform Type,
    Facility Status, Weather Subtype, Approach Call, or Tower Hours alone
    -- without touching Frequency Category -- does NOT hide ILS.
    """
    for kwargs in (
        {"platform_types": frozenset({"AIRPORT"})},
        {"facility_statuses": frozenset({"TOWERED"})},
        {"weather_subtypes": frozenset({"ATIS"})},
        {"primary_approach_radio_calls": frozenset({"LA APPROACH"})},
        {"tower_hours_24_only": True},
    ):
        result = apply_filters(conn, FilterState(**kwargs))
        assert result.ils != [], f"expected ILS still present for {kwargs}"


def test_ils_specific_filters_alone_do_not_exclude_ils(conn):
    """Setting an ILS-specific filter (component status / system type)
    implies wanting ILS data, not excluding it.
    """
    result = apply_filters(conn, FilterState(ils_component_statuses=frozenset({"OPERATIONAL IFR"})))
    assert {i.airport_id for i in result.ils} == {"AAA"}


def test_ils_included_by_default_when_unfiltered(conn):
    result = apply_filters(conn, FilterState())
    assert len(result.ils) == 2


def test_tower_hours_24_only_matches_literal_value(conn):
    result = apply_filters(conn, FilterState(tower_hours_24_only=True))
    freq_airports = {f.airport_id for f in result.frequencies}
    assert freq_airports == {"AAA"}  # only AAA's TOWER row is tower_hours == "24"


def test_ils_component_status_filter(conn):
    result = apply_filters(conn, FilterState(ils_component_statuses=frozenset({"OPERATIONAL IFR"})))
    assert {i.airport_id for i in result.ils} == {"AAA"}
    # airports themselves are untouched by ILS-level filters
    assert _ids(result) == {"AAA", "BBB", "CCC"}


def test_ils_system_type_filter(conn):
    result = apply_filters(conn, FilterState(ils_system_types=frozenset({"LC"})))
    assert {i.airport_id for i in result.ils} == {"BBB"}


def test_primary_approach_radio_call_filter(conn):
    result = apply_filters(conn, FilterState(primary_approach_radio_calls=frozenset({"LA APPROACH"})))
    assert {f.airport_id for f in result.frequencies} == {"AAA"}


def test_radius_include_filter(conn):
    # AAA/BBB/CCC roughly hundreds of nm apart; a small radius around AAA
    # should only include AAA itself.
    result = apply_filters(
        conn,
        FilterState(radius_filters=(RadiusFilter(center_lat=34.0, center_lon=-118.0, radius_nm=5, mode="include"),)),
    )
    assert _ids(result) == {"AAA"}


def test_radius_exclude_filter(conn):
    result = apply_filters(
        conn,
        FilterState(radius_filters=(RadiusFilter(center_lat=34.0, center_lon=-118.0, radius_nm=5, mode="exclude"),)),
    )
    assert _ids(result) == {"BBB", "CCC"}


def test_multiple_exclude_radius_filters_and_together(conn):
    # exclude near AAA AND exclude near BBB -> only CCC survives.
    # Excludes must AND: "not near either one". OR-ing them would let each
    # new exclusion cancel the previous one out.
    result = apply_filters(
        conn,
        FilterState(
            radius_filters=(
                RadiusFilter(center_lat=34.0, center_lon=-118.0, radius_nm=5, mode="exclude"),
                RadiusFilter(center_lat=37.0, center_lon=-122.0, radius_nm=5, mode="exclude"),
            )
        ),
    )
    assert _ids(result) == {"CCC"}


def test_multiple_include_radius_filters_or_together(conn):
    """Two include radii mean "near either center", not "near both".

    AAA and BBB are ~300nm apart, so AND-ing these (the original
    behaviour) returned nothing at all -- adding a second include radius
    silently zeroed the results instead of widening them.
    """
    result = apply_filters(
        conn,
        FilterState(
            radius_filters=(
                RadiusFilter(center_lat=34.0, center_lon=-118.0, radius_nm=5, mode="include"),
                RadiusFilter(center_lat=37.0, center_lon=-122.0, radius_nm=5, mode="include"),
            )
        ),
    )
    # CCC (Las Vegas) is outside both circles. DDD sits within 5nm of
    # AAA's center but is private-use, which FilterState excludes by
    # default -- unrelated to radius, but it keeps this set to two.
    assert _ids(result) == {"AAA", "BBB"}


def test_include_radius_union_still_respects_excludes(conn):
    """The include group and the exclude group AND with each other:
    inside any include zone, and outside every exclude zone.
    """
    result = apply_filters(
        conn,
        FilterState(
            radius_filters=(
                RadiusFilter(center_lat=34.0, center_lon=-118.0, radius_nm=5, mode="include"),
                RadiusFilter(center_lat=37.0, center_lon=-122.0, radius_nm=5, mode="include"),
                # carve AAA (and its close neighbour DDD) back out
                RadiusFilter(center_lat=34.0, center_lon=-118.0, radius_nm=5, mode="exclude"),
            )
        ),
    )
    assert _ids(result) == {"BBB"}


def test_list_states(conn):
    assert set(list_states(conn)) == {"CA", "NV"}


def test_count_entries_reflects_filters(conn):
    unfiltered = count_entries(conn, FilterState())
    ca_only = count_entries(conn, FilterState(states=frozenset({"CA"})))
    assert ca_only < unfiltered
    assert ca_only > 0


def test_resolve_center_by_airport_id(conn):
    assert resolve_center(conn, "AAA") == (34.0, -118.0)


def test_resolve_center_by_airport_id_is_case_insensitive(conn):
    """The web UI's radius-center field is free text -- a user typing
    "aaa" or " aaa " must resolve the same as "AAA", not fail with a
    generic "unknown airport id" error.
    """
    assert resolve_center(conn, "aaa") == (34.0, -118.0)
    assert resolve_center(conn, " AaA  ") == (34.0, -118.0)


def test_resolve_center_by_lat_lon_tuple(conn):
    assert resolve_center(conn, (1.0, 2.0)) == (1.0, 2.0)


def test_resolve_center_unknown_airport_raises(conn):
    with pytest.raises(ValueError, match="ZZZ"):
        resolve_center(conn, "ZZZ")


# ---------- standalone / orphan facilities (no matching airport row) ----------


@pytest.fixture
def orphan_data() -> NormalizedData:
    return NormalizedData(
        airports=[
            Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True),
        ],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF", facility_status="NON_TOWERED"),
            # standalone VOR, no matching Airport row -- real bug report:
            # AVE (Avenal VOR/DME), CA
            Frequency(
                airport_id="AVE", freq_mhz=117.1, freq_category="VOR", raw_freq_use="AVE VOR/DME",
                state="CA", city="AVENAL", name="AVENAL", lat=35.647, lon=-119.979,
            ),
            # a second orphan in a different state, for state-filter tests
            Frequency(
                airport_id="XYZ", freq_mhz=112.5, freq_category="VOR", raw_freq_use="XYZ VORTAC",
                state="NV", city="TONOPAH", name="TONOPAH", lat=38.0, lon=-117.0,
            ),
        ],
        ils=[],
    )


@pytest.fixture
def orphan_conn(orphan_data):
    return build_database(orphan_data)


def test_orphan_facility_dropped_without_the_fix_is_now_included(orphan_conn):
    result = apply_filters(orphan_conn, FilterState())
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert "AVE" in freq_airport_ids
    assert "XYZ" in freq_airport_ids


def test_orphan_facility_filtered_by_its_own_state(orphan_conn):
    result = apply_filters(orphan_conn, FilterState(states=frozenset({"CA"})))
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert "AVE" in freq_airport_ids  # CA
    assert "XYZ" not in freq_airport_ids  # NV, filtered out


def test_orphan_facility_filtered_by_its_own_city(orphan_conn):
    result = apply_filters(orphan_conn, FilterState(cities=frozenset({"AVENAL"})))
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert freq_airport_ids == {"AVE"}


def test_orphan_facilities_honour_include_radius_union(orphan_conn):
    """Standalone facilities run through a separate WHERE builder with its
    own NULL-position handling, so the include-radii-OR fix has to hold on
    that path too -- AVE and XYZ are ~180nm apart and would both drop out
    if these AND'd.
    """
    result = apply_filters(
        orphan_conn,
        FilterState(
            radius_filters=(
                RadiusFilter(center_lat=35.647, center_lon=-119.979, radius_nm=5, mode="include"),  # AVE
                RadiusFilter(center_lat=38.0, center_lon=-117.0, radius_nm=5, mode="include"),  # XYZ
            )
        ),
    )
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert {"AVE", "XYZ"} <= freq_airport_ids


def test_orphan_facility_filtered_by_radius_using_its_own_position(orphan_conn):
    # tight radius around AVE's own coordinates should include only AVE
    result = apply_filters(
        orphan_conn,
        FilterState(radius_filters=(RadiusFilter(center_lat=35.647, center_lon=-119.979, radius_nm=5, mode="include"),)),
    )
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert freq_airport_ids == {"AVE"}


def test_orphan_facility_ignores_public_private_filter(orphan_conn):
    """No FACILITY_USE_CODE-equivalent exists for standalone facilities --
    they must survive every combination of include_public/include_private,
    including the "neither" combination that excludes every real airport.
    """
    result = apply_filters(orphan_conn, FilterState(include_public=False, include_private=False))
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert freq_airport_ids == {"AVE", "XYZ"}
    assert result.airports == []  # AAA correctly excluded


def test_orphan_facility_still_respects_freq_category_filter(orphan_conn):
    result = apply_filters(orphan_conn, FilterState(freq_categories=frozenset({"CTAF"})))
    freq_airport_ids = {f.airport_id for f in result.frequencies}
    assert freq_airport_ids == {"AAA"}  # neither AVE nor XYZ is CTAF -- AAA (real airport) still is


def test_orphan_facility_with_no_position_does_not_crash_radius_filter():
    """Real NASR data: some standalone facilities (e.g. certain TRACON/
    APCH_DEP rows) report a frequency but carry no LAT_DECIMAL/LONG_DECIMAL
    at all -- haversine_nm() has no NULL handling, so evaluating it against
    one of these rows used to crash the whole query (sqlite raised
    "user-defined function raised exception", surfacing as a 500 in the
    web UI) the moment any radius filter was applied to a result set that
    included one.
    """
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AVE", freq_mhz=117.1, freq_category="VOR", state="CA", city="AVENAL", lat=35.647, lon=-119.979),
            Frequency(airport_id="A11", freq_mhz=125.0, freq_category="TRACON", state="CA", city="LOS ANGELES", lat=None, lon=None),
        ],
        ils=[],
    )
    conn = build_database(data)

    include_result = apply_filters(
        conn, FilterState(radius_filters=(RadiusFilter(center_lat=35.647, center_lon=-119.979, radius_nm=5, mode="include"),))
    )
    assert {f.airport_id for f in include_result.frequencies} == {"AVE"}  # no-position row can't be confirmed inside -- excluded

    exclude_result = apply_filters(
        conn, FilterState(radius_filters=(RadiusFilter(center_lat=35.647, center_lon=-119.979, radius_nm=5, mode="exclude"),))
    )
    assert {f.airport_id for f in exclude_result.frequencies} == {"A11"}  # no-position row can't be confirmed inside -- not excluded


def test_list_states_includes_orphan_only_states(orphan_conn):
    # NV has no real airport in this fixture, only the orphan XYZ VOR
    assert set(list_states(orphan_conn)) == {"CA", "NV"}


def test_list_cities_includes_orphan_only_cities(orphan_conn):
    assert set(list_cities(orphan_conn)) == {"LOS ANGELES", "AVENAL", "TONOPAH"}
    assert set(list_cities(orphan_conn, states=frozenset({"NV"}))) == {"TONOPAH"}


# ---------- ILS records vs. the site/status filters ----------


@pytest.fixture
def ils_status_conn():
    """A towered airport and a non-towered one, each with an ILS.

    platform_type and facility_status describe the airport but live on
    its frequency rows; the ils table has neither, so these filters used
    to pass straight over ILS records.
    """
    data = NormalizedData(
        airports=[
            Airport(id="TWR", name="Towered", city="X", state="CA", lat=34.0, lon=-118.0, public_use=True),
            Airport(id="NON", name="Non-towered", city="X", state="CA", lat=34.1, lon=-118.1, public_use=True),
        ],
        frequencies=[
            Frequency(airport_id="TWR", freq_mhz=118.0, freq_category="TOWER",
                      platform_type="AIRPORT", facility_status="TOWERED"),
            Frequency(airport_id="NON", freq_mhz=122.8, freq_category="CTAF",
                      platform_type="AIRPORT", facility_status="NON_TOWERED"),
        ],
        ils=[
            Ils(airport_id="TWR", runway_end_id="09", freq_mhz=110.3,
                system_type="LS", component_status="OPERATIONAL IFR"),
            Ils(airport_id="NON", runway_end_id="27", freq_mhz=111.5,
                system_type="LS", component_status="OPERATIONAL IFR"),
        ],
    )
    return build_database(data)


def _ils_airports(result):
    return {i.airport_id for i in result.ils}


def test_facility_status_filter_applies_to_ils_records(ils_status_conn):
    """Real bug report: CA + ILS/Localizer + Non-Towered returned SJC,
    SMF, LAX and every other towered field in the state, because the only
    thing narrowing the ILS query was the airport's state.
    """
    non_towered = apply_filters(
        ils_status_conn, FilterState(facility_statuses=frozenset({"NON_TOWERED"}))
    )
    assert _ils_airports(non_towered) == {"NON"}

    towered = apply_filters(
        ils_status_conn, FilterState(facility_statuses=frozenset({"TOWERED"}))
    )
    assert _ils_airports(towered) == {"TWR"}


def test_platform_type_filter_applies_to_ils_records(ils_status_conn):
    """Same hole, the other column."""
    result = apply_filters(
        ils_status_conn, FilterState(platform_types=frozenset({"HELIPORT"}))
    )
    assert _ils_airports(result) == set()


def test_unfiltered_status_still_returns_every_ils_record(ils_status_conn):
    result = apply_filters(ils_status_conn, FilterState())
    assert _ils_airports(result) == {"TWR", "NON"}


def test_ils_kept_when_any_row_at_that_airport_matches_the_status():
    """A part-time tower (Visalia, and 1,044 other airports in the
    2026-09-03 cycle) publishes both TOWERED and NON_TOWERED rows. Its
    ILS belongs to both sets, the same way its frequencies already do.
    """
    data = NormalizedData(
        airports=[Airport(id="VIS", name="Visalia", city="X", state="CA", lat=36.3, lon=-119.4, public_use=True)],
        frequencies=[
            Frequency(airport_id="VIS", freq_mhz=118.5, freq_category="APCH_DEP", facility_status="TOWERED"),
            Frequency(airport_id="VIS", freq_mhz=123.05, freq_category="CTAF", facility_status="NON_TOWERED"),
        ],
        ils=[Ils(airport_id="VIS", runway_end_id="30", freq_mhz=110.3,
                 system_type="LS", component_status="OPERATIONAL IFR")],
    )
    conn = build_database(data)
    for status in ("TOWERED", "NON_TOWERED"):
        result = apply_filters(conn, FilterState(facility_statuses=frozenset({status})))
        assert _ils_airports(result) == {"VIS"}, status


# ---------- categories excluded regardless of the category filter ----------


@pytest.fixture
def excludable_conn():
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A", city="X", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF"),
            Frequency(airport_id="AAA", freq_mhz=121.5, freq_category="EMERGENCY"),
            Frequency(airport_id="AAA", freq_mhz=245.0, freq_category="NDB"),
        ],
        ils=[],
    )
    return build_database(data)


def _categories(result):
    return {f.freq_category for f in result.frequencies}


def test_excluded_categories_are_dropped_with_no_category_filter(excludable_conn):
    """The case the option exists for: "no filter" means everything, so a
    caller that never offers Emergency or NDB still shipped them.
    """
    result = apply_filters(
        excludable_conn, FilterState(excluded_freq_categories=frozenset({"EMERGENCY", "NDB"}))
    )
    assert _categories(result) == {"CTAF"}


def test_excluded_categories_apply_alongside_a_category_filter(excludable_conn):
    result = apply_filters(
        excludable_conn,
        FilterState(
            freq_categories=frozenset({"CTAF", "EMERGENCY"}),
            excluded_freq_categories=frozenset({"EMERGENCY"}),
        ),
    )
    assert _categories(result) == {"CTAF"}


def test_unset_exclusion_keeps_every_category(excludable_conn):
    """The CLI leaves this unset and must keep its full reach."""
    result = apply_filters(excludable_conn, FilterState())
    assert _categories(result) == {"CTAF", "EMERGENCY", "NDB"}
