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
from fastapi.testclient import TestClient

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
from afp.web import create_app
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


# ---------- the map's own window ----------
#
# The map is a separate document in a separate window. The main window
# is already dense, and a map squeezed above the results table read as
# one more thing competing for the same column.

MAP_JS = (STATIC_DIR / "map.js").read_text(encoding="utf-8")
STYLE_CSS = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
MAP_HTML = (STATIC_DIR / "map.html").read_text(encoding="utf-8")
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@pytest.fixture
def web_client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


def test_the_map_page_is_served(web_client):
    response = web_client.get("/map")
    assert response.status_code == 200
    assert "map-window" in response.text


def test_the_map_page_loads_the_map_and_nothing_else():
    """Its own document, so it gets its own scripts -- and the
    controller after the library it is built on.
    """
    for src in ("vendor/leaflet/leaflet.js", "map.js", "map-window.js"):
        assert src in MAP_HTML, f"{src} is not loaded by the map page"
    assert MAP_HTML.index("map.js") < MAP_HTML.index("map-window.js")


def test_the_main_page_no_longer_carries_the_map():
    """It does not draw one any more, so it should not be paying to
    parse a mapping library or reserving a panel for it.
    """
    assert "map-panel" not in INDEX_HTML, "the docked panel is back"
    # The tags, not the word: a comment saying why Leaflet is absent is
    # not Leaflet being present.
    loads = [
        line
        for line in INDEX_HTML.splitlines()
        if ("<script" in line or "<link" in line) and "leaflet" in line.lower()
    ]
    assert not loads, f"the main page still loads Leaflet: {loads}"
    assert "/static/map.js" not in INDEX_HTML


def test_the_docked_placement_is_gone_from_the_stylesheet():
    """Dead rules for an element nothing builds any more."""
    assert ".map-panel" not in STYLE_CSS
    assert ".map-grip" not in STYLE_CSS


def test_the_map_window_is_sized_by_the_window_not_a_grip():
    """A panel had to be resizable within a cap, because it shared a
    column with the results table. A window is resized by the OS, so
    the grip and its cap went with the panel -- but Leaflet still has
    to be told, because it caches its container's size.
    """
    assert "map-grip" not in MAP_JS
    assert "MAX_PANEL_SHARE" not in MAP_JS
    assert "ResizeObserver" in MAP_JS
    assert "invalidateSize" in MAP_JS


# ---------- the swatch, and removing a rule from its pill ----------

APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_a_rule_can_be_removed_from_its_map_chip():
    """The rail has carried a remove button all along; the map's chips
    did not, so a ring put down from the map had to be deleted from the
    other side of the screen.
    """
    assert 'className = "map-chip-remove"' in MAP_JS
    assert ".map-chip-remove {" in STYLE_CSS
    assert "hooks.removeRule(r.id)" in MAP_JS
    # The chip itself opens the rule, so the remove button has to keep
    # its click to itself or it reopens what it just deleted.
    chip = MAP_JS.split("function renderChips()", 1)[1].split("\n    }", 1)[0]
    assert "event.stopPropagation()" in chip


def test_the_map_chip_is_not_a_button_inside_a_button():
    """It carries a remove button, so it cannot be one itself: nesting
    them is invalid markup and the inner one stops being reachable by
    keyboard. It keeps the role and the key handling instead.
    """
    chip = MAP_JS.split("function renderChips()", 1)[1].split("\n    }", 1)[0]
    assert 'const chip = document.createElement("span");' in chip
    assert 'const chip = document.createElement("button")' not in chip
    assert 'chip.setAttribute("role", "button")' in chip
    assert "chip.tabIndex = 0" in chip
    assert 'chip.addEventListener("keydown"' in chip


def test_both_lists_mark_include_and_exclude_with_the_same_swatch():
    """The rail and the map show the same rules. One of them growing its
    own mark for include and exclude is how the two drift apart.
    """
    assert 'className = "radius-swatch"' in APP_JS  # the rail's rows
    assert 'className = "radius-swatch"' in MAP_JS  # the map's chips
    assert ".radius-swatch {" in STYLE_CSS
    assert '.radius-swatch[data-mode="exclude"]' in STYLE_CSS


def test_the_swatch_is_drawn_in_the_ring_colours():
    """It stands for the circle on the map, so a reader can match the
    two at a glance. The colours are held twice -- once as CSS custom
    properties and once as literals map.js paints the rings with -- and
    nothing else would catch them drifting apart.
    """
    assert 'const OK = "#3FCB57";' in MAP_JS
    assert 'const WARN = "#E8407D";' in MAP_JS
    assert "--ok:       #3FCB57;" in STYLE_CSS
    assert "--warn:     #E8407D;" in STYLE_CSS

    swatch = STYLE_CSS.split(".radius-swatch {", 1)[1].split("}", 1)[0]
    assert "var(--ok)" in swatch, "include is not drawn in the include ring's colour"
    exclude = STYLE_CSS.split('.radius-swatch[data-mode="exclude"] {', 1)[1].split("}", 1)[0]
    assert "var(--warn)" in exclude


# ---------- what the map is allowed to draw over ----------


def _z_index(selector: str) -> int:
    """The z-index declared in the first rule for `selector`."""
    block = STYLE_CSS.split(selector + " {", 1)[1].split("}", 1)[0]
    for line in block.splitlines():
        if "z-index:" in line:
            return int(line.split("z-index:", 1)[1].strip().rstrip(";"))
    raise AssertionError(f"{selector} declares no z-index")


def test_a_modal_outranks_everything_the_app_draws_for_itself():
    """A dialog something else can cover is not a dialog. The tooltip
    and the row menu are the two other positioned layers, and nothing
    inside a modal is a tooltip target, so there is nothing of ours
    underneath that needed to show through.
    """
    modal = _z_index(".modal-backdrop")
    assert modal > _z_index("#tooltip")
    assert modal > _z_index(".row-menu")


# ---------- what the map says about filters it cannot draw ----------


def test_the_map_names_the_city_filter_and_the_use_scope():
    """Two filters change what is on the map without colouring anything.
    Cities tint a halo small enough to miss; the use scope removes points
    outright -- on a default nationwide map that is roughly 14,000
    airports simply not there, with nothing on screen to say why.
    """
    assert "renderBadges" in MAP_JS
    assert "City:" in MAP_JS
    assert "Public only" in MAP_JS and "Private only" in MAP_JS


def test_the_map_does_not_badge_the_frequency_categories():
    """Decided rather than forgotten. A badge names a filter and points
    at a mark; the category filter changes no dot on the map, so the
    badge would point at nothing. The hover carries that instead.
    """
    assert "categories" not in MAP_JS.split("function renderBadges")[1].split("function renderHint")[0]


def test_neither_use_flag_ticked_earns_no_badge():
    """Both off means no constraint, not an empty map -- the same
    reading airport_use_scope gives it. A badge there would announce a
    filter that is not filtering.
    """
    badges = MAP_JS.split("function renderBadges")[1].split("function renderHint")[0]
    assert "pub && !priv" in badges and "priv && !pub" in badges


def test_the_badges_clear_leaflet_s_zoom_control():
    """They sit top-left, which Leaflet already owns. Overlapping would
    put an unclickable label over the one control the map cannot do
    without.
    """
    block = STYLE_CSS.split(".map-badges")[1].split("}")[0]
    assert "left: 52px" in block, "the badges would sit under the zoom control"


def test_the_hover_says_when_a_point_contributes_nothing():
    """A dot's colour says where the point is. Nothing on the map says
    whether it is worth anything, and under a narrow category filter
    most selected dots are not.
    """
    assert "pass the frequency filters" in MAP_JS
    assert "None pass the frequency filters" in MAP_JS


def test_the_hover_does_not_call_them_your_filters():
    """With nothing selected in the rail the app still drops
    AIRSPACE_INFO, EMERGENCY and NDB, so a count can sit below the total
    without the user having filtered anything. The number is honest; the
    attribution would not be.
    """
    assert "pass your frequency filters" not in MAP_JS
