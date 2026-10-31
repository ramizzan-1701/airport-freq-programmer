"""Per-point status for the radius map.

The map sits beside the counter and claims to explain it. If the two
ever disagree, the map is not a helpful picture of a filter -- it is a
second opinion, and the user has no way to tell which one is lying.

So the invariant almost every test here rests on is parity: the points
classified SELECTED are exactly the airports apply_filters returns for
the same FilterState. The rest check that the *reasons* a point is out
are told apart correctly, which the filtered result set cannot say at
all -- it holds only survivors.
"""

import pytest

from afp.query import FilterState, RadiusFilter, apply_filters, build_database
from afp.query.query import (
    EXCLUDED,
    FILTERED,
    OUTSIDE,
    SCOPE,
    SELECTED,
    classify_points,
)
from afp.schema import Airport, Frequency, NormalizedData
from afp.web.app import STATIC_DIR

# Los Angeles and a strip beside it, San Francisco ~300nm north, Las
# Vegas ~200nm east in another state, and a private field next to LA.
LAX_LAT, LAX_LON = 34.0, -118.0
SFO_LAT, SFO_LON = 37.0, -122.0


@pytest.fixture
def data() -> NormalizedData:
    return NormalizedData(
        airports=[
            Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA",
                    lat=LAX_LAT, lon=LAX_LON, public_use=True),
            Airport(id="BBB", name="B Field", city="SAN FRANCISCO", state="CA",
                    lat=SFO_LAT, lon=SFO_LON, public_use=True),
            Airport(id="CCC", name="C Field", city="LAS VEGAS", state="NV",
                    lat=36.0, lon=-115.0, public_use=True),
            Airport(id="DDD", name="D Strip", city="LOS ANGELES", state="CA",
                    lat=34.05, lon=-118.05, public_use=False),
        ],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF"),
            Frequency(airport_id="BBB", freq_mhz=126.0, freq_category="WEATHER_STATION"),
            Frequency(airport_id="CCC", freq_mhz=122.9, freq_category="CTAF"),
            Frequency(airport_id="DDD", freq_mhz=123.0, freq_category="CTAF"),
            # Standalone facilities: not airports, so they carry their own
            # position. VOR1 has one; TRACON1 has none, which real NASR
            # data does for some TRACON/APCH_DEP rows.
            Frequency(airport_id="VOR1", freq_mhz=117.0, freq_category="VOR",
                      lat=34.1, lon=-118.1, state="CA", city="LOS ANGELES"),
            Frequency(airport_id="VOR1", freq_mhz=117.5, freq_category="VOR",
                      lat=34.1, lon=-118.1, state="CA", city="LOS ANGELES"),
            Frequency(airport_id="TRACON1", freq_mhz=124.3, freq_category="TRACON",
                      state="CA", city="LOS ANGELES"),
        ],
        ils=[],
    )


@pytest.fixture
def conn(data):
    return build_database(data)


def near_la(nm: float, mode: str = "include") -> RadiusFilter:
    return RadiusFilter(center_lat=LAX_LAT, center_lon=LAX_LON, radius_nm=nm, mode=mode)


def near_sf(nm: float, mode: str = "include") -> RadiusFilter:
    return RadiusFilter(center_lat=SFO_LAT, center_lon=SFO_LON, radius_nm=nm, mode=mode)


AIRPORT_IDS = {"AAA", "BBB", "CCC", "DDD"}


def ids_with(status: str, result) -> set[str]:
    return {i for i, s in result.statuses.items() if s == status}


def airports_with(status: str, result) -> set[str]:
    """Airports only. classify_points returns standalone facilities in
    the same mapping -- they share no ids with airports by construction,
    but apply_filters reports them separately, so the two parity tests
    below have to compare like with like.
    """
    return ids_with(status, result) & AIRPORT_IDS


# ---------- parity with the filter itself ----------

PARITY_CASES = {
    "no filters at all": FilterState(),
    "one include ring": FilterState(radius_filters=(near_la(50),)),
    "two includes are OR'd": FilterState(radius_filters=(near_la(50), near_sf(50))),
    "include and exclude": FilterState(radius_filters=(near_la(400), near_sf(50, "exclude"))),
    "exclude only": FilterState(radius_filters=(near_sf(50, "exclude"),)),
    "a lat,lon centre": FilterState(
        radius_filters=(RadiusFilter(center_lat=34.02, center_lon=-118.02, radius_nm=20, mode="include"),)
    ),
    "state only": FilterState(states=frozenset({"CA"})),
    "state, city and a ring": FilterState(
        states=frozenset({"CA"}),
        cities=frozenset({"LOS ANGELES"}),
        radius_filters=(near_la(50),),
    ),
    "private airports included": FilterState(include_public=True, include_private=True),
    "private only": FilterState(include_public=False, include_private=True),
    "both use flags off means no constraint": FilterState(
        include_public=False, include_private=False
    ),
    "a ring that contains nothing": FilterState(radius_filters=(near_la(1),)),
}


@pytest.mark.parametrize("label", sorted(PARITY_CASES))
def test_the_selected_points_are_exactly_what_the_filter_returns(conn, label):
    """The one invariant the map rests on. Everything else here is about
    telling the reasons apart; this is about not contradicting the
    number the map sits next to.
    """
    filters = PARITY_CASES[label]
    expected = {a.id for a in apply_filters(conn, filters).airports}
    assert airports_with(SELECTED, classify_points(conn, filters)) == expected, label


@pytest.mark.parametrize("label", sorted(PARITY_CASES))
def test_positioned_facilities_match_the_filter_too(conn, label):
    """Standalone facilities are filtered by their own state/city/
    position and never by public/private. They have to track
    apply_filters as closely as airports do.
    """
    filters = PARITY_CASES[label]
    data = apply_filters(conn, filters)
    airport_ids = {a.id for a in data.airports}
    expected = {f.airport_id for f in data.frequencies if f.airport_id not in airport_ids}

    result = classify_points(conn, filters)
    selected_facilities = ids_with(SELECTED, result) - AIRPORT_IDS
    # TRACON1 has no position and is never classified, so it can only
    # appear on the apply_filters side -- and only when no include ring
    # is in force.
    assert selected_facilities == expected - {"TRACON1"}, label


# ---------- the reasons, which the result set cannot express ----------


def test_an_airport_outside_every_include_ring_is_outside_not_filtered(conn):
    result = classify_points(conn, FilterState(radius_filters=(near_la(50),)))
    assert result.statuses["BBB"] == OUTSIDE
    assert result.statuses["AAA"] == SELECTED


def test_an_airport_inside_an_exclude_ring_is_excluded(conn):
    """Distinct from outside: it was in range and then ruled out, which
    is the difference between "widen your ring" and "move your
    exclusion".
    """
    result = classify_points(conn, FilterState(radius_filters=(near_la(400), near_la(50, "exclude"))))
    assert result.statuses["AAA"] == EXCLUDED


def test_the_state_filter_decides_before_the_rings_do(conn):
    """Precedence. An airport in the wrong state that also happens to
    sit inside an exclude ring is filtered, not excluded -- it was never
    a candidate for the ring to rule out.
    """
    filters = FilterState(
        states=frozenset({"CA"}),
        radius_filters=(RadiusFilter(center_lat=36.0, center_lon=-115.0, radius_nm=50, mode="exclude"),),
    )
    assert classify_points(conn, filters).statuses["CCC"] == FILTERED


def test_an_airport_out_of_scope_is_never_drawn(conn):
    """Public/private is the one filter that removes a point from the
    map entirely rather than recolouring it.
    """
    result = classify_points(conn, FilterState(include_public=True, include_private=False))
    assert result.statuses["DDD"] == SCOPE
    assert result.statuses["AAA"] == SELECTED


def test_with_both_use_flags_off_every_airport_is_drawn(conn):
    """Both off means no constraint, so the map must show everything --
    not nothing, which is what reading it literally would give.
    """
    result = classify_points(conn, FilterState(include_public=False, include_private=False))
    assert SCOPE not in result.statuses.values()


def test_every_airport_gets_exactly_one_status(conn):
    result = classify_points(conn, FilterState(radius_filters=(near_la(50),)))
    assert set(result.statuses) >= {"AAA", "BBB", "CCC", "DDD"}
    assert all(s in (SELECTED, EXCLUDED, OUTSIDE, FILTERED, SCOPE) for s in result.statuses.values())


# ---------- standalone facilities ----------


def test_a_positioned_facility_is_classified_like_an_airport(conn):
    result = classify_points(conn, FilterState(radius_filters=(near_la(50),)))
    assert result.statuses["VOR1"] == SELECTED

    far = classify_points(conn, FilterState(radius_filters=(near_sf(50),)))
    assert far.statuses["VOR1"] == OUTSIDE


def test_a_facility_is_counted_once_however_many_frequencies_it_reports(conn):
    """Its position rides along on every frequency row, so an
    unaggregated query would classify the same id once per frequency --
    and count it that many times in the legend.
    """
    result = classify_points(conn, FilterState())
    assert list(result.statuses).count("VOR1") == 1


def test_a_facility_with_no_position_is_counted_not_dropped(conn):
    """It cannot be drawn, but silently losing it would make the legend
    disagree with the export.
    """
    for filters in (
        FilterState(),
        FilterState(radius_filters=(near_la(50),)),
        FilterState(radius_filters=(near_la(50, "exclude"),)),
    ):
        result = classify_points(conn, filters)
        assert result.unplottable == 1
        assert "TRACON1" not in result.statuses


def test_a_facility_is_never_judged_on_public_private(conn):
    """FACILITY_USE_CODE lives in APT_BASE.csv and has no equivalent for
    standalone facilities, which is why the filter leaves them alone too.
    """
    result = classify_points(conn, FilterState(include_public=False, include_private=True))
    assert result.statuses["VOR1"] != SCOPE


# ---------- the docked panel's size limits ----------
#
# The map sits above the results table in the same column. The 1180x760
# window floor was measured with the table only just fitting, so a panel
# that can grow without limit pushes it off screen -- and nothing about
# that fails loudly.

MAP_JS = (STATIC_DIR / "map.js").read_text(encoding="utf-8")
STYLE_CSS = (STATIC_DIR / "style.css").read_text(encoding="utf-8")


def test_the_panel_is_resizable_by_a_drawn_grip():
    """Not CSS `resize: vertical`: its corner triangle is a fixed light
    grey that disappears against the map, and Leaflet's own panes paint
    over it, so the affordance was there but invisible and unusable.
    """
    assert 'className = "map-grip"' in MAP_JS
    assert ".map-grip {" in STYLE_CSS
    assert "resize: vertical" not in STYLE_CSS, "the invisible native resizer is back"


def test_the_grip_and_the_stylesheet_cap_the_panel_at_the_same_size():
    """The resting height is CSS and the drag limit is JS, so the two
    carry the same numbers in different units and can drift apart
    silently -- leaving a panel that snaps on first drag.
    """
    assert "const MIN_PANEL_PX = 240;" in MAP_JS
    assert "const MAX_PANEL_SHARE = 0.45;" in MAP_JS

    panel = STYLE_CSS.split(".map-panel {", 1)[1].split("}", 1)[0]
    assert "min-height: 240px;" in panel
    assert "max-height: 45%;" in panel


def test_leaflet_is_told_when_the_panel_is_resized():
    """The grip changes the container's height directly. Leaflet caches
    its size, so without this the new strip stays blank and every click
    lands on whatever point used to be under it.
    """
    assert "ResizeObserver" in MAP_JS
    assert "invalidateSize" in MAP_JS
