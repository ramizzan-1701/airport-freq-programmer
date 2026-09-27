import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from afp.export.profile import ExportProfile
from afp.export.xml_writer import build_xml
from afp.nasr.scraper import Cycle
from afp.query.db import build_database
from afp.schema import Airport, Frequency, Ils, NormalizedData
from afp.selection import Entry
from afp.web.app import create_app
from afp.web.state import LoadedCycle

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_data() -> NormalizedData:
    return NormalizedData(
        airports=[
            Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True),
            Airport(id="BBB", name="B Field", city="SAN FRANCISCO", state="CA", lat=37.0, lon=-122.0, public_use=True),
            Airport(id="CCC", name="C Field", city="LAS VEGAS", state="NV", lat=36.0, lon=-115.0, public_use=True),
        ],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF", facility_status="NON_TOWERED"),
            Frequency(airport_id="AAA", freq_mhz=118.0, freq_category="TOWER", facility_status="TOWERED", tower_hours="24"),
            Frequency(airport_id="BBB", freq_mhz=126.0, freq_category="WEATHER_STATION", weather_subtype="ATIS", facility_status="TOWERED"),
            Frequency(airport_id="CCC", freq_mhz=122.9, freq_category="CTAF", facility_status="NON_TOWERED"),
        ],
        ils=[
            Ils(airport_id="AAA", runway_end_id="25L", freq_mhz=109.9, system_type="LS", component_status="OPERATIONAL IFR"),
        ],
    )


@pytest.fixture
def client(tmp_path, sample_data) -> TestClient:
    app = create_app(cache_dir=tmp_path)
    conn = build_database(sample_data)
    app.state.afp_state.loaded = LoadedCycle(cycle_date=date(2026, 8, 6), data=sample_data, conn=conn)
    return TestClient(app)


@pytest.fixture
def empty_client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


# ---------- status / cycle discovery / load ----------


def test_static_assets_disable_heuristic_caching(empty_client):
    """StaticFiles sends Last-Modified but no Cache-Control, which lets
    browsers silently serve a stale app.js after it changes between runs
    of the local dev server -- confirmed by hitting this in a real
    browser after a code update. Every /static/* and / response must
    force revalidation.
    """
    for path in ("/", "/static/app.js", "/static/style.css"):
        res = empty_client.get(path)
        assert res.headers["cache-control"] == "no-cache", path


def test_status_before_load_shows_nothing_loaded(empty_client):
    res = empty_client.get("/api/status")
    assert res.status_code == 200
    body = res.json()
    assert body["loaded_cycle"] is None
    assert body["available_cycles"] == []


_FIXTURE_CSV_NAMES = (
    ("APT_BASE.csv", "apt_base_sample.csv"),
    ("FRQ.csv", "frq_sample.csv"),
    ("ILS_BASE.csv", "ils_base_sample.csv"),
)


def _write_fixture_cycle(cache_dir: Path, cycle: str) -> None:
    cycle_dir = cache_dir / cycle
    cycle_dir.mkdir()
    for dest_name, src_name in _FIXTURE_CSV_NAMES:
        shutil.copy(FIXTURES / src_name, cycle_dir / dest_name)


def test_available_cycles_discovered_from_cache_dir(tmp_path):
    _write_fixture_cycle(tmp_path, "2026-08-06")

    client = TestClient(create_app(cache_dir=tmp_path))
    res = client.get("/api/status")
    assert res.json()["available_cycles"] == ["2026-08-06"]


def test_load_known_cycle_populates_status(tmp_path):
    _write_fixture_cycle(tmp_path, "2026-08-06")

    client = TestClient(create_app(cache_dir=tmp_path))
    res = client.post("/api/load", json={"cycle": "2026-08-06"})
    assert res.status_code == 200
    body = res.json()
    assert body["loaded_cycle"] == "2026-08-06"
    assert body["airport_count"] > 0


def test_load_unknown_cycle_returns_404(empty_client):
    res = empty_client.post("/api/load", json={"cycle": "1999-01-01"})
    assert res.status_code == 404


def test_check_update_reports_availability(empty_client, monkeypatch):
    app = empty_client.app

    class StubSource:
        def get_current_cycle(self):
            return Cycle(date(2026, 8, 6), "https://example/2026-08-06")

        def is_update_available(self, current=None):
            return True

    app.state.afp_state.source = StubSource()
    res = empty_client.get("/api/check-update")
    assert res.status_code == 200
    body = res.json()
    assert body["current_published_cycle"] == "2026-08-06"
    assert body["update_available"] is True


def test_check_update_failure_is_502_not_a_crash(empty_client):
    app = empty_client.app

    class FailingSource:
        def get_current_cycle(self):
            raise RuntimeError("offline")

    app.state.afp_state.source = FailingSource()
    res = empty_client.get("/api/check-update")
    assert res.status_code == 502


# ---------- filter options ----------


def test_filter_options_requires_loaded_data(empty_client):
    res = empty_client.get("/api/filter-options")
    assert res.status_code == 400


def test_filter_options_returns_distinct_values(client):
    res = client.get("/api/filter-options")
    assert res.status_code == 200
    body = res.json()
    states = {s["code"]: s["label"] for s in body["states"]}
    assert states == {"CA": "California", "NV": "Nevada"}
    freq_category_codes = {c["code"] for c in body["freq_categories"]}
    assert {"CTAF", "TOWER", "WEATHER_STATION"} <= freq_category_codes
    ctaf = next(c for c in body["freq_categories"] if c["code"] == "CTAF")
    assert ctaf["label"] == "CTAF"
    assert ctaf["default_view"] is True
    assert "OPERATIONAL IFR" in body["ils_component_statuses"]
    assert {"TOWERED", "NON_TOWERED"} <= {s["code"] for s in body["facility_statuses"]}
    ils_system_types = {s["code"]: s["label"] for s in body["ils_system_types"]}
    assert ils_system_types == {"LS": "ILS (Instrument Landing System)"}


def test_airspace_emergency_ndb_hidden_from_web_ui_but_still_filterable(tmp_path):
    """AIRSPACE_INFO/EMERGENCY/NDB are removed from the web UI's Frequency
    Category list entirely per user direction (inert filter, memorized
    frequency, and below-VHF respectively) -- but the underlying filter
    dimension is untouched, so the API/CLI can still filter by them
    directly even though filter_options() no longer advertises them.
    """
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF"),
            Frequency(airport_id="AAA", freq_mhz=120.9, freq_category="AIRSPACE_INFO", raw_freq_use="CLASS B"),
            Frequency(airport_id="AAA", freq_mhz=121.5, freq_category="EMERGENCY"),
            Frequency(airport_id="AAA", freq_mhz=0.396, freq_category="NDB"),
        ],
        ils=[],
    )
    app = create_app(cache_dir=tmp_path)
    conn = build_database(data)
    app.state.afp_state.loaded = LoadedCycle(cycle_date=date(2026, 8, 6), data=data, conn=conn)
    client = TestClient(app)

    freq_category_codes = {c["code"] for c in client.get("/api/filter-options").json()["freq_categories"]}
    assert freq_category_codes == {"CTAF"}
    assert not freq_category_codes & {"AIRSPACE_INFO", "EMERGENCY", "NDB"}

    # The filter dimension still works for all three -- none of them
    # errors or is rejected. What each yields differs, and for two of
    # them the answer is nothing, for unrelated reasons:
    #
    #   AIRSPACE_INFO is NON_SELECTABLE -- an annotation on another row,
    #     never an entry of its own.
    #   NDB sits at 190-535 kHz, far below the nav band, so the tunable
    #     band check drops it before selection. This used to yield an
    #     entry the radio could not have tuned.
    #   EMERGENCY is a real, in-band frequency and still filters through.
    expected = {
        "AIRSPACE_INFO": set(),
        "NDB": set(),
        "EMERGENCY": {"AAA-EMERG"},
    }
    for hidden_category, expected_tags in expected.items():
        res = client.post("/api/query", json={"freq_categories": [hidden_category]})
        assert res.status_code == 200
        assert {e["tag_name"] for e in res.json()["entries"]} == expected_tags, hidden_category


def test_include_non_site_facilities_flows_through_the_api(tmp_path):
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=118.0, freq_category="TOWER", platform_type=None, facility_status="TOWERED"),
            Frequency(airport_id="AAA", freq_mhz=112.6, freq_category="VOR", platform_type=None, facility_status=None),
        ],
        ils=[],
    )
    app = create_app(cache_dir=tmp_path)
    conn = build_database(data)
    app.state.afp_state.loaded = LoadedCycle(cycle_date=date(2026, 8, 6), data=data, conn=conn)
    client = TestClient(app)

    without_flag = client.post("/api/query", json={"facility_statuses": ["TOWERED"]}).json()
    assert {e["tag_name"] for e in without_flag["entries"]} == {"AAA-CT"}

    with_flag = client.post(
        "/api/query", json={"facility_statuses": ["TOWERED"], "include_non_site_facilities": True}
    ).json()
    assert {e["tag_name"] for e in with_flag["entries"]} == {"AAA-CT", "AAA-VOR"}


def test_cities_scoped_by_state(client):
    res = client.get("/api/cities", params={"states": ["NV"]})
    assert res.json() == ["LAS VEGAS"]

    res = client.get("/api/cities")
    assert set(res.json()) == {"LAS VEGAS", "LOS ANGELES", "SAN FRANCISCO"}


# ---------- query ----------


def test_query_no_filters_returns_all_public_airports(client):
    res = client.post("/api/query", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] > 0
    assert body["level"] == "green"
    assert body["cap"] == 400
    assert not body["truncated"]


def test_query_state_filter_narrows_results(client):
    all_result = client.post("/api/query", json={}).json()
    ca_result = client.post("/api/query", json={"states": ["CA"]}).json()
    assert ca_result["count"] < all_result["count"]

    airport_ids = {e["airport_id"] for e in ca_result["entries"]}
    assert airport_ids <= {"AAA", "BBB"}


def test_ils_folded_into_frequency_category(client):
    """ILS is now a pseudo Frequency Category ("ILS") rather than a
    separate control. Narrowing Facility Status alone doesn't hide it
    (accepted tradeoff -- only Frequency Category governs ILS now);
    narrowing Frequency Category without "ILS" does.
    """
    res = client.post("/api/query", json={"facility_statuses": ["TOWERED"]})
    tags = {e["tag_name"] for e in res.json()["entries"]}
    assert "AAA-LOC25L" in tags

    res = client.post("/api/query", json={"freq_categories": ["WEATHER_STATION"]})
    tags = {e["tag_name"] for e in res.json()["entries"]}
    assert "AAA-LOC25L" not in tags

    res = client.post("/api/query", json={"freq_categories": ["WEATHER_STATION", "ILS"]})
    tags = {e["tag_name"] for e in res.json()["entries"]}
    assert "AAA-LOC25L" in tags

    res = client.post("/api/query", json={})  # unfiltered: ILS included by default
    tags = {e["tag_name"] for e in res.json()["entries"]}
    assert "AAA-LOC25L" in tags


def test_ils_appears_as_pseudo_category_in_filter_options(client):
    res = client.get("/api/filter-options")
    freq_categories = {c["code"]: c for c in res.json()["freq_categories"]}
    assert "ILS" in freq_categories
    assert freq_categories["ILS"]["default_view"] is True


def test_query_radius_filter_by_airport_id_center(client):
    res = client.post(
        "/api/query",
        json={"radius_filters": [{"center": "AAA", "radius_nm": 5, "mode": "include"}]},
    )
    body = res.json()
    assert {e["airport_id"] for e in body["entries"]} == {"AAA"}


def test_query_unknown_radius_center_returns_error(client):
    res = client.post(
        "/api/query",
        json={"radius_filters": [{"center": "ZZZ", "radius_nm": 5, "mode": "include"}]},
    )
    assert res.status_code == 400
    assert "ZZZ" in res.json()["detail"]


# ---------- radius centre validation ----------
#
# The rail checks the centre through this endpoint before adding the
# filter, so a typo complains on the field instead of failing the whole
# query and blanking every other filter's result.


def test_resolve_center_accepts_an_airport_id(client):
    res = client.get("/api/resolve-center", params={"center": "AAA"})
    assert res.status_code == 200
    body = res.json()
    assert body["center"] == "AAA"
    assert isinstance(body["lat"], float) and isinstance(body["lon"], float)


def test_resolve_center_is_case_and_space_insensitive(client):
    """Matches resolve_center's own normalisation -- the field is free
    text, so "  aaa " has to reach the same airport the query would, and
    come back in the canonical spelling the chip then displays.
    """
    res = client.get("/api/resolve-center", params={"center": "  aaa "})
    assert res.status_code == 200
    assert res.json()["center"] == "AAA"
    assert res.json()["lat"] == client.get(
        "/api/resolve-center", params={"center": "AAA"}
    ).json()["lat"]


def test_resolve_center_accepts_a_lat_lon_pair(client):
    res = client.get("/api/resolve-center", params={"center": "33.94,-118.41"})
    assert res.status_code == 200
    assert (res.json()["lat"], res.json()["lon"]) == (33.94, -118.41)
    # Not upper-cased -- there is no canonical form to impose on digits.
    assert res.json()["center"] == "33.94,-118.41"


def test_a_resolved_center_is_accepted_by_the_query(client):
    """The point of resolving first: whatever comes back must be a value
    /api/query will take, or the field would pass something the query
    then rejects.
    """
    center = client.get("/api/resolve-center", params={"center": "aaa"}).json()["center"]
    res = client.post(
        "/api/query",
        json={"radius_filters": [{"center": center, "radius_nm": 5, "mode": "include"}]},
    )
    assert res.status_code == 200
    assert {e["airport_id"] for e in res.json()["entries"]} == {"AAA"}


def test_resolve_center_rejects_an_unknown_airport_id(client):
    res = client.get("/api/resolve-center", params={"center": "ZZZ"})
    assert res.status_code == 404
    assert "ZZZ" in res.json()["detail"]


def test_resolve_center_rejects_a_malformed_lat_lon_pair(client):
    res = client.get("/api/resolve-center", params={"center": "33.94,west"})
    assert res.status_code == 400
    assert "lat,lon" in res.json()["detail"]


def test_resolve_center_rejects_blank_input(client):
    res = client.get("/api/resolve-center", params={"center": "   "})
    assert res.status_code == 400


def test_resolve_center_requires_loaded_data(empty_client):
    res = empty_client.get("/api/resolve-center", params={"center": "AAA"})
    assert res.status_code == 400


def test_query_raw_mode_differs_from_smart_mode(client):
    smart = client.post("/api/query", json={"mode": "smart"}).json()
    raw = client.post("/api/query", json={"mode": "raw"}).json()
    # AAA has both TOWER and CTAF; smart mode picks one, raw mode keeps both
    assert raw["count"] >= smart["count"]


def test_orphan_facility_shows_its_own_name_city_state_in_results(tmp_path):
    """A standalone VOR has no Airport record to look up name/city/state
    from -- the results table must fall back to the facility's own
    SERVICED_* data instead of showing blank.
    """
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF"),
            Frequency(
                airport_id="AVE", freq_mhz=117.1, freq_category="VOR", raw_freq_use="AVE VOR/DME",
                state="CA", city="AVENAL", name="AVENAL", lat=35.647, lon=-119.979,
            ),
        ],
        ils=[],
    )
    app = create_app(cache_dir=tmp_path)
    conn = build_database(data)
    app.state.afp_state.loaded = LoadedCycle(cycle_date=date(2026, 8, 6), data=data, conn=conn)
    client = TestClient(app)

    res = client.post("/api/query", json={})
    entries_by_tag = {e["tag_name"]: e for e in res.json()["entries"]}

    ave = entries_by_tag["AVE-VOR"]
    assert ave["airport_id"] == "AVE"
    assert ave["airport_name"] == "AVENAL"
    assert ave["city"] == "AVENAL"
    assert ave["state"] == "CA"


def test_orphan_facility_with_long_name_shows_full_name_but_short_tag_id(tmp_path):
    """A standalone facility whose SERVICED_FACILITY is a full place name
    ("MEDICINE BOW") gets a short, tag-safe airport_id ("MEDICB") for the
    generated tag -- but the results table's separate name/city/state
    columns must still show the full place name. This also regression-
    tests the results-table lookup itself: it recovers a facility's
    airport_id by splitting the generated tag_name on its first dash, so
    that lookup has to be keyed by the same short id select_entries used,
    not the raw (long) SERVICED_FACILITY value, or the row shows blank.
    """
    data = NormalizedData(
        airports=[Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA", lat=34.0, lon=-118.0, public_use=True)],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF"),
            Frequency(
                airport_id="MEDICINE BOW", freq_mhz=122.5, freq_category="RCAG", raw_freq_use="MEDICINE BOW RCAG",
                state="WY", city="MEDICINE BOW", name="MEDICINE BOW", lat=41.9, lon=-106.2,
            ),
        ],
        ils=[],
    )
    app = create_app(cache_dir=tmp_path)
    conn = build_database(data)
    app.state.afp_state.loaded = LoadedCycle(cycle_date=date(2026, 8, 6), data=data, conn=conn)
    client = TestClient(app)

    res = client.post("/api/query", json={})
    entries_by_tag = {e["tag_name"]: e for e in res.json()["entries"]}

    entry = entries_by_tag["MEDICB-RCAG"]
    assert entry["airport_id"] == "MEDICB"
    assert entry["airport_name"] == "MEDICINE BOW"
    assert entry["city"] == "MEDICINE BOW"
    assert entry["state"] == "WY"


# ---------- generate ----------


def test_generate_returns_downloadable_xml(client):
    res = client.post("/api/generate", json={"states": ["CA"]})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/xml")
    assert "attachment" in res.headers["content-disposition"]
    assert res.content.startswith(b"\xef\xbb\xbf<?xml")


def test_generate_with_colliding_tags_returns_422(tmp_path):
    # Raw mode deliberately does NOT disambiguate two ILS records for the
    # same runway end with different system types -- that's a genuine
    # data ambiguity (which one wins?), not a naming coincidence, so it
    # must surface as a hard validation failure rather than being resolved
    # silently.
    data = NormalizedData(
        airports=[Airport(id="ZZZ", name="Z", city="Z", state="CA", lat=1.0, lon=-1.0, public_use=True)],
        frequencies=[],
        ils=[
            Ils(airport_id="ZZZ", runway_end_id="31", freq_mhz=109.9, system_type="LC", component_status="OPERATIONAL RESTRICTED"),
            Ils(airport_id="ZZZ", runway_end_id="31", freq_mhz=109.9, system_type="LS", component_status="OPERATIONAL IFR"),
        ],
    )
    app = create_app(cache_dir=tmp_path)
    conn = build_database(data)
    app.state.afp_state.loaded = LoadedCycle(cycle_date=date(2026, 8, 6), data=data, conn=conn)
    client = TestClient(app)

    res = client.post("/api/generate", json={"mode": "raw"})
    assert res.status_code == 422
    problems = res.json()["detail"]["problems"]
    assert any("duplicate" in p.lower() for p in problems)


def test_generate_requires_loaded_data(empty_client):
    res = empty_client.post("/api/generate", json={})
    assert res.status_code == 400


# ---------- group setup ----------


def test_status_defaults_to_group_setup_not_acknowledged(empty_client):
    body = empty_client.get("/api/status").json()
    assert body["group_setup_acknowledged"] is False
    assert body["fixed_group_names"] == ["0-9", "A-E", "F-J", "K-O", "P-T", "U-Z"]


def test_acknowledging_group_setup_persists(empty_client):
    res = empty_client.post("/api/group-setup/acknowledge")
    assert res.json()["group_setup_acknowledged"] is True

    status = empty_client.get("/api/status").json()
    assert status["group_setup_acknowledged"] is True


# ---------- custom entries ----------


# A permissive profile for building test fixtures that simulate an
# externally-produced radio export -- these aren't going through the app's
# own /api/generate cap enforcement, so they shouldn't be limited by it.
_NO_CAP_PROFILE = ExportProfile(name="test fixture", max_tag_length=64, max_entries=10_000)


def _custom_xml(entries: list[Entry]) -> bytes:
    return build_xml(entries, _NO_CAP_PROFILE)


def test_custom_entries_empty_by_default(empty_client):
    body = empty_client.get("/api/custom-entries").json()
    assert body == {"entries": [], "entry_count": 0, "group_count": 0}


def test_import_works_without_a_loaded_nasr_cycle(empty_client):
    xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=37.0, lon=-122.0)])
    res = empty_client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})
    assert res.status_code == 200
    assert res.json()["entry_count"] == 1


def test_import_splits_recognized_and_custom(empty_client):
    xml = _custom_xml([
        Entry(tag_name="AAA-CTAF", freq_mhz=122.8, group="0-9", lat=1.0, lon=-1.0),
        Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=37.0, lon=-122.0),
    ])
    res = empty_client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})
    body = res.json()
    assert body["entry_count"] == 1
    assert body["group_count"] == 1
    assert body["entries"][0]["tag_name"] == "HOME-BASE"


def test_import_over_capacity_is_blocked_with_real_group_names(empty_client):
    xml = _custom_xml([
        Entry(tag_name=f"E{i}", freq_mhz=122.8, group=g, lat=1.0, lon=-1.0)
        for i, g in enumerate(["A", "B", "C", "D"])
    ])
    res = empty_client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["found"] == 4
    assert detail["available"] == 3
    assert set(detail["groups"]) == {"A", "B", "C", "D"}


def test_import_over_capacity_leaves_existing_custom_entries_untouched(empty_client):
    good_xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    empty_client.post("/api/custom-entries/import", content=good_xml, headers={"Content-Type": "application/xml"})

    bad_xml = _custom_xml([
        Entry(tag_name=f"E{i}", freq_mhz=122.8, group=g, lat=1.0, lon=-1.0)
        for i, g in enumerate(["A", "B", "C", "D"])
    ])
    res = empty_client.post("/api/custom-entries/import", content=bad_xml, headers={"Content-Type": "application/xml"})
    assert res.status_code == 422

    body = empty_client.get("/api/custom-entries").json()
    assert body["entry_count"] == 1
    assert body["entries"][0]["tag_name"] == "HOME-BASE"


def test_import_malformed_xml_returns_400(empty_client):
    res = empty_client.post(
        "/api/custom-entries/import", content=b"not xml", headers={"Content-Type": "application/xml"}
    )
    assert res.status_code == 400


def test_reimport_replaces_previous_custom_set(empty_client):
    first = _custom_xml([Entry(tag_name="FIRST", freq_mhz=122.7, group="A", lat=1.0, lon=-1.0)])
    empty_client.post("/api/custom-entries/import", content=first, headers={"Content-Type": "application/xml"})

    second = _custom_xml([Entry(tag_name="SECOND", freq_mhz=122.8, group="B", lat=1.0, lon=-1.0)])
    empty_client.post("/api/custom-entries/import", content=second, headers={"Content-Type": "application/xml"})

    body = empty_client.get("/api/custom-entries").json()
    assert [e["tag_name"] for e in body["entries"]] == ["SECOND"]


def test_remove_custom_entry_by_index(empty_client):
    xml = _custom_xml([
        Entry(tag_name="FIRST", freq_mhz=122.7, group="A", lat=1.0, lon=-1.0),
        Entry(tag_name="SECOND", freq_mhz=122.8, group="A", lat=1.0, lon=-1.0),
    ])
    empty_client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    res = empty_client.delete("/api/custom-entries/0")
    assert res.status_code == 200
    assert [e["tag_name"] for e in res.json()["entries"]] == ["SECOND"]


def test_remove_custom_entry_invalid_index_returns_404(empty_client):
    res = empty_client.delete("/api/custom-entries/0")
    assert res.status_code == 404


def test_clear_custom_entries(empty_client):
    xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    empty_client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    res = empty_client.post("/api/custom-entries/clear")
    assert res.json() == {"entries": [], "entry_count": 0, "group_count": 0}


def test_query_total_count_includes_custom_entries(client):
    baseline = client.post("/api/query", json={}).json()

    xml = _custom_xml([
        Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0),
        Entry(tag_name="CABIN-WX", freq_mhz=162.550, group="PERSONAL", lat=1.0, lon=-1.0),
    ])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    result = client.post("/api/query", json={}).json()
    assert result["count"] == baseline["count"]  # FAA-only count is unaffected
    assert result["custom_entry_count"] == 2
    assert result["total_count"] == baseline["count"] + 2


def test_query_total_count_can_push_level_to_red_even_when_faa_count_is_small(client):
    many_custom = _custom_xml([
        Entry(tag_name=f"C{i}", freq_mhz=122.8, group="PERSONAL", lat=1.0, lon=-1.0) for i in range(401)
    ])
    client.post("/api/custom-entries/import", content=many_custom, headers={"Content-Type": "application/xml"})

    result = client.post("/api/query", json={}).json()
    assert result["level"] == "red"
    assert result["total_count"] > 400


def test_query_zero_faa_matches_with_custom_entries_still_generatable_shape(client):
    """A custom-only export (0 FAA matches, some custom entries) must not
    be treated as an empty result -- verified at the data level here; the
    corresponding frontend button-enable logic is exercised manually.
    """
    xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    # a state with zero matching airports in the fixture data
    result = client.post("/api/query", json={"states": ["WY"]}).json()
    assert result["count"] == 0
    assert result["total_count"] == 1


def test_generate_merges_custom_entries_and_the_preview_lists_them_first(client):
    """The preview is a picture of the XML about to be written, so an
    entry that reaches the file has to reach the table.

    Order is the one place the two differ on purpose: the file appends
    custom entries, the table leads with them. The preview stops at
    MAX_DISPLAYED_ENTRIES, so trailing them behind every FAA row would
    push them past the cut in any broad query -- exactly when someone is
    checking their own entries survived.
    """
    xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    query_result = client.post("/api/query", json={}).json()
    tags = [e["tag_name"] for e in query_result["entries"]]
    assert tags[0] == "HOME-BASE", "custom entries lead the preview"
    assert len(query_result["entries"]) == query_result["total_count"]

    res = client.post("/api/generate", json={})
    assert res.status_code == 200
    assert b"<TAG_NAME>HOME-BASE</TAG_NAME>" in res.content
    assert b"<GROUP>PERSONAL</GROUP>" in res.content


def test_custom_entries_stay_visible_when_the_preview_is_truncated(client, monkeypatch):
    """The whole point of leading with them. Cap the page at two rows and
    the custom entry still has to be on it, however many FAA rows match.
    """
    from afp.web import app as web_app

    monkeypatch.setattr(web_app, "MAX_DISPLAYED_ENTRIES", 2)

    xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    body = client.post("/api/query", json={}).json()
    assert len(body["entries"]) == 2
    assert body["entries"][0]["tag_name"] == "HOME-BASE"
    assert body["truncated"] is True


def test_truncated_counts_the_custom_entries_too(client, monkeypatch):
    """truncated once measured the FAA rows alone while the table listed
    both, so a page that stopped short could still report nothing cut.
    """
    from afp.web import app as web_app

    baseline = client.post("/api/query", json={}).json()
    faa_count = baseline["count"]

    # A page exactly the size of the FAA result: without the custom
    # entries nothing is cut, with them the last one falls off.
    monkeypatch.setattr(web_app, "MAX_DISPLAYED_ENTRIES", faa_count)
    assert client.post("/api/query", json={}).json()["truncated"] is False

    xml = _custom_xml([Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    body = client.post("/api/query", json={}).json()
    assert body["total_count"] == faa_count + 1
    assert len(body["entries"]) == faa_count
    assert body["truncated"] is True, "a row fell off the page but truncated said otherwise"


def test_preview_flags_custom_rows_and_leaves_their_airport_fields_empty(client):
    """The tag prefix of a hand-added entry is whatever the user typed
    into the radio. Looking it up would caption the row with a real
    airport's name and city that the entry never came from.
    """
    xml = _custom_xml([Entry(tag_name="AAA-MINE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0)])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    entries = client.post("/api/query", json={}).json()["entries"]
    custom = [e for e in entries if e["tag_name"] == "AAA-MINE"]
    assert len(custom) == 1
    row = custom[0]
    assert row["is_custom"] is True
    # "AAA" is a real airport in the fixture data -- the row must not
    # borrow its name.
    assert (row["airport_id"], row["airport_name"], row["city"], row["state"]) == ("", "", "", "")

    faa = [e for e in entries if not e["is_custom"]]
    assert faa and all(e["airport_id"] for e in faa), "FAA rows keep their airport"


def test_category_counts_include_a_custom_bucket_and_still_sum_to_the_total(client):
    xml = _custom_xml([
        Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=1.0, lon=-1.0),
        Entry(tag_name="CABIN-WX", freq_mhz=162.550, group="PERSONAL", lat=1.0, lon=-1.0),
    ])
    client.post("/api/custom-entries/import", content=xml, headers={"Content-Type": "application/xml"})

    body = client.post("/api/query", json={}).json()
    by_code = {c["code"]: c for c in body["category_counts"]}
    assert by_code["CUSTOM"]["count"] == 2
    assert by_code["CUSTOM"]["short_label"] == "Custom"
    assert body["category_counts"][-1]["code"] == "CUSTOM", "pinned last, not sorted by size"
    assert sum(c["count"] for c in body["category_counts"]) == body["total_count"]


def test_query_returns_short_and_full_category_labels(client):
    """The breakdown chips show the short form and carry the full label
    as their tooltip, so both have to come back from one request.
    """
    body = client.post("/api/query", json={"mode": "smart"}).json()
    by_code = {c["code"]: c for c in body["category_counts"]}
    assert by_code, "expected at least one category in the breakdown"

    for entry in by_code.values():
        assert entry["label"], "full label is the tooltip text"
        assert entry["short_label"], "short label is what renders"

    # A category whose full name is long enough to have needed shortening
    if "RCAG" in by_code:
        assert by_code["RCAG"]["short_label"] == "RCAG"
        assert by_code["RCAG"]["label"] == "Remote Comm Relay"


def test_category_counts_sum_to_the_reported_count(client):
    """The breakdown describes the whole result set, not the truncated
    preview page -- so its counts must add up. With no custom entries
    held, `count` and `total_count` are the same number.
    """
    body = client.post("/api/query", json={"mode": "smart"}).json()
    assert body["count"] == body["total_count"]
    assert sum(c["count"] for c in body["category_counts"]) == body["count"]


def test_web_query_excludes_categories_hidden_from_the_filter_list(client):
    """Emergency and NDB aren't offered in the web UI's category list, so
    an unfiltered web query must not return them either -- otherwise the
    export carries entries the user was never able to deselect, including
    NDB, which the radio can't tune.
    """
    from afp.classification import HIDDEN_FROM_WEB_UI_CATEGORIES

    body = client.post("/api/query", json={"mode": "smart"}).json()
    returned = {c["code"] for c in body["category_counts"]}
    assert not (returned & HIDDEN_FROM_WEB_UI_CATEGORIES), returned & HIDDEN_FROM_WEB_UI_CATEGORIES
