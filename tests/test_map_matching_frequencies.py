"""What a dot on the map is worth, as opposed to where it is.

A status answers "where is this point": inside the rings, outside them,
filtered away by state. It has never answered "does this point give me
anything", and it cannot, because categories filter *frequencies* and
the status is computed from airports.

The two come apart further than you would guess. On real NASR data, a
60nm ring around SJC holds 42 selected airports whatever the category
filter says -- but under Tower-only, 28 of those 42 contribute nothing
at all, and they are drawn exactly like the 14 carrying the whole
result. Sizing a ring by counting green dots is then wrong by a factor
of three.

The map keeps drawing them as selected, because they are selected: they
passed every filter that decides a dot's colour. The hover is where the
difference gets told, and this is the number it tells it with.
"""

import pytest

from afp.classification import ILS_PSEUDO_CATEGORY
from afp.query import FilterState, RadiusFilter, apply_filters, build_database
from afp.query.query import SELECTED, classify_points, map_points
from afp.schema import Airport, Frequency, Ils, NormalizedData

LAX_LAT, LAX_LON = 34.0, -118.0


@pytest.fixture
def data() -> NormalizedData:
    """One towered field, one without a tower, and a VOR.

    TOWERLESS is the point of the fixture: it sits beside the towered
    one, so no ring can tell them apart, and under a Tower-only filter
    it is worth nothing while looking identical on the map.
    """
    return NormalizedData(
        airports=[
            Airport(id="TOWERED", name="Big Field", city="LOS ANGELES", state="CA",
                    lat=LAX_LAT, lon=LAX_LON, public_use=True),
            Airport(id="TOWERLESS", name="Small Field", city="LOS ANGELES", state="CA",
                    lat=34.02, lon=-118.02, public_use=True),
        ],
        frequencies=[
            Frequency(airport_id="TOWERED", freq_mhz=120.0, freq_category="TOWER"),
            Frequency(airport_id="TOWERED", freq_mhz=121.9, freq_category="GROUND"),
            Frequency(airport_id="TOWERED", freq_mhz=126.0, freq_category="WEATHER_STATION"),
            Frequency(airport_id="TOWERLESS", freq_mhz=122.8, freq_category="CTAF"),
            Frequency(airport_id="TOWERLESS", freq_mhz=122.9, freq_category="UNICOM"),
            Frequency(airport_id="VOR1", freq_mhz=117.0, freq_category="VOR",
                      lat=34.1, lon=-118.1, state="CA", city="LOS ANGELES"),
        ],
        ils=[
            Ils(airport_id="TOWERED", runway_end_id="25L", freq_mhz=111.1,
                system_type="ILS", component_status="OPERATIONAL IFR"),
        ],
    )


@pytest.fixture
def conn(data):
    return build_database(data)


def near_la(nm: float, mode: str = "include") -> RadiusFilter:
    return RadiusFilter(center_lat=LAX_LAT, center_lon=LAX_LON, radius_nm=nm, mode=mode)


# ---------- the gap the number exists to close ----------


def test_a_point_in_range_can_still_contribute_nothing(conn):
    """The case the whole feature is for. Both fields are inside the
    ring and both are drawn green; one of them is worthless.
    """
    filters = FilterState(
        radius_filters=(near_la(50),), freq_categories=frozenset({"TOWER"})
    )
    result = classify_points(conn, filters)

    assert result.statuses["TOWERLESS"] == SELECTED, "the dot is still selected"
    assert result.matching_frequencies.get("TOWERLESS", 0) == 0, (
        "a selected dot that contributes nothing has to be able to say so"
    )
    assert result.matching_frequencies["TOWERED"] == 1


def test_the_count_follows_the_category_filter(conn):
    counts = {
        label: classify_points(conn, FilterState(freq_categories=cats)).matching_frequencies
        for label, cats in {
            "none": None,
            "tower": frozenset({"TOWER"}),
            "ctaf": frozenset({"CTAF"}),
        }.items()
    }
    assert counts["tower"].get("TOWERLESS", 0) == 0
    assert counts["ctaf"].get("TOWERED", 0) == 0
    assert counts["ctaf"]["TOWERLESS"] == 1
    assert counts["none"]["TOWERLESS"] == 2


def test_the_count_ignores_where_the_point_is(conn):
    """Location is the status's job. Counting it here too would make a
    point that failed a ring read as a point whose frequencies were all
    filtered away, which is a different sentence and a wrong one.
    """
    everywhere = classify_points(conn, FilterState()).matching_frequencies
    excluded = classify_points(
        conn, FilterState(radius_filters=(near_la(50, "exclude"),))
    ).matching_frequencies
    assert excluded == everywhere


def test_a_facility_is_counted_like_an_airport(conn):
    result = classify_points(conn, FilterState(freq_categories=frozenset({"VOR"})))
    assert result.matching_frequencies["VOR1"] == 1
    assert result.matching_frequencies.get("TOWERED", 0) == 0


# ---------- parity with what the export actually keeps ----------


@pytest.mark.parametrize(
    "categories",
    [None, frozenset({"TOWER"}), frozenset({"CTAF", "UNICOM"}),
     frozenset({ILS_PSEUDO_CATEGORY}), frozenset({"VOR"})],
    ids=["all", "tower", "ctaf+unicom", "ils", "vor"],
)
def test_the_count_is_what_the_export_keeps_for_that_point(conn, categories):
    """The same parity the statuses are held to, for the same reason: a
    number beside a dot that disagrees with the file is worse than no
    number, because it is believed.

    No location filters, so every point survives and the only thing
    separating the two sides is the frequency-level filtering.
    """
    filters = FilterState(freq_categories=categories)
    result = apply_filters(conn, filters)

    expected: dict[str, int] = {}
    for frequency in result.frequencies:
        expected[frequency.airport_id] = expected.get(frequency.airport_id, 0) + 1
    for ils in result.ils:
        expected[ils.airport_id] = expected.get(ils.airport_id, 0) + 1

    counted = classify_points(conn, filters).matching_frequencies
    assert {k: v for k, v in counted.items() if v} == expected


# ---------- ILS, which lives in its own table ----------


def test_ils_counts_when_the_pseudo_category_is_on(conn):
    """Leaving it out would report "none pass the frequency filters" for
    every airport under an ILS-only selection -- the one case where the
    count matters most, and the one it would get exactly backwards.
    """
    filters = FilterState(freq_categories=frozenset({ILS_PSEUDO_CATEGORY}))
    assert classify_points(conn, filters).matching_frequencies["TOWERED"] == 1


def test_ils_does_not_count_when_the_pseudo_category_is_off(conn):
    filters = FilterState(freq_categories=frozenset({"GROUND"}))
    counts = classify_points(conn, filters).matching_frequencies
    assert counts["TOWERED"] == 1, "only the ground frequency"


def test_the_static_total_counts_the_same_things_as_the_matching_count(conn):
    """The hover prints the two side by side -- "4 freq" and then "1
    passes". Counting different universes would read as nonsense: an
    airport with 3 frequencies and 4 of them passing.
    """
    totals = {row[0]: row[8] for row in map_points(conn)}
    everything = classify_points(conn, FilterState()).matching_frequencies

    assert totals["TOWERED"] == 4, "3 frequencies plus one ILS"
    assert everything["TOWERED"] == totals["TOWERED"], (
        "the unfiltered count has to be able to reach the total, or the "
        "hover shows a leftover that no filter explains"
    )
