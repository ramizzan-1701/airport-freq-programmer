"""Filter selections persisting across sessions.

Held server-side beside the custom entries and the acknowledgment flags,
so the whole of the app's remembered state has one home and one lifetime.
"""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from afp.query.db import build_database
from afp.schema import Airport, Frequency, NormalizedData
from afp.web import create_app
from afp.web.local_store import load_filters, save_filters
from afp.web.state import LoadedCycle


@pytest.fixture
def empty_client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


@pytest.fixture
def client(tmp_path) -> TestClient:
    """A loaded cycle, for the one test that has to run a real query."""
    data = NormalizedData(
        airports=[
            Airport(id="AAA", name="A Field", city="LOS ANGELES", state="CA",
                    lat=34.0, lon=-118.0, public_use=True),
        ],
        frequencies=[
            Frequency(airport_id="AAA", freq_mhz=122.8, freq_category="CTAF",
                      facility_status="NON_TOWERED"),
        ],
        ils=[],
    )
    app = create_app(cache_dir=tmp_path)
    app.state.afp_state.loaded = LoadedCycle(
        cycle_date=date(2026, 8, 6), data=data, conn=build_database(data)
    )
    return TestClient(app)


# ---------- the round trip ----------


def test_nothing_saved_reads_back_as_no_filters(empty_client):
    """A fresh install has to return the rail's own starting state, not
    an error or a null the frontend then has to special-case.
    """
    body = empty_client.get("/api/filters").json()
    assert body["states"] is None
    assert body["freq_categories"] is None
    assert body["radius_filters"] == []
    assert body["mode"] == "smart"
    assert body["include_public"] is True
    assert body["include_private"] is False


def test_selections_survive_a_restart(tmp_path):
    """The actual feature: a second app instance over the same cache dir
    comes up with what the first one left.
    """
    first = TestClient(create_app(cache_dir=tmp_path))
    first.put(
        "/api/filters",
        json={
            "states": ["CA"],
            "freq_categories": ["CTAF", "TOWER"],
            "include_private": True,
            "mode": "raw",
        },
    )

    second = TestClient(create_app(cache_dir=tmp_path))
    body = second.get("/api/filters").json()
    assert body["states"] == ["CA"]
    assert body["freq_categories"] == ["CTAF", "TOWER"]
    assert body["include_private"] is True
    assert body["mode"] == "raw"


def test_saving_replaces_rather_than_merges(empty_client):
    """The rail sends its whole state every time, so a filter the user
    cleared has to actually go -- a merge would make clearing impossible.
    """
    empty_client.put("/api/filters", json={"states": ["CA"], "freq_categories": ["CTAF"]})
    empty_client.put("/api/filters", json={"states": ["NV"]})

    body = empty_client.get("/api/filters").json()
    assert body["states"] == ["NV"]
    assert body["freq_categories"] is None


def test_radius_filters_survive_with_their_shape_intact(empty_client):
    empty_client.put(
        "/api/filters",
        json={"radius_filters": [{"center": "LAX", "radius_nm": 60, "mode": "include"}]},
    )
    saved = empty_client.get("/api/filters").json()["radius_filters"]
    assert saved == [{"center": "LAX", "radius_nm": 60.0, "mode": "include"}]


def test_what_is_saved_is_something_the_query_accepts(client):
    """Saved filters are typed as FilterStateIn, the same shape
    /api/query takes. Restoring something the query then rejects would
    leave the rail unusable until the user found "Clear all".
    """
    client.put("/api/filters", json={"states": ["CA"], "freq_categories": ["CTAF"]})
    saved = client.get("/api/filters").json()

    res = client.post("/api/query", json=saved)
    assert res.status_code == 200


# ---------- surviving a bad file ----------


def test_a_corrupt_file_reads_back_as_no_filters(tmp_path):
    """Better to open with an empty rail than to refuse to open."""
    (tmp_path / "filters.json").write_text("{not json at all", encoding="utf-8")
    client = TestClient(create_app(cache_dir=tmp_path))
    assert client.get("/api/filters").json()["states"] is None


def test_a_file_from_an_incompatible_version_reads_back_as_no_filters(tmp_path):
    """A stored shape that no longer validates must not stop the rail
    loading -- the cost of starting clean is one set of filters.
    """
    (tmp_path / "filters.json").write_text(json.dumps({"mode": "sideways"}), encoding="utf-8")
    client = TestClient(create_app(cache_dir=tmp_path))
    assert client.get("/api/filters").json()["mode"] == "smart"


def test_a_file_holding_something_other_than_an_object_is_ignored(tmp_path):
    (tmp_path / "filters.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert load_filters(tmp_path / "filters.json") is None


def test_the_store_creates_its_directory(tmp_path):
    nested = tmp_path / "does" / "not" / "exist" / "filters.json"
    save_filters(nested, {"states": ["CA"]})
    assert load_filters(nested) == {"states": ["CA"]}


# ---------- the frontend's side of the contract ----------


def test_the_rail_restores_before_its_first_render():
    """Restoring after the first render would paint an empty rail and
    then repaint it, which reads as a flicker on every launch.
    """
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    init = app_js.split("async function initWorkspace()", 1)[1].split("\n}", 1)[0]
    assert init.index("restoreSavedFilters") < init.index("renderFilters"), (
        "the rail renders before its saved selections are applied"
    )


def test_the_rail_drops_a_radius_centre_the_cycle_cannot_resolve():
    """An unknown centre does not narrow the results -- it fails the
    whole query with a 400. Restored blindly, the rail would come up
    reading "Query failed" with nothing pointing at the cause.
    """
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "resolvableRadiusFilters" in app_js
    checker = app_js.split("async function resolvableRadiusFilters", 1)[1].split("\n}", 1)[0]
    assert "/api/resolve-center" in checker, (
        "restored radius centres are not checked against the loaded cycle"
    )


def test_every_filter_change_funnels_through_the_save():
    """runQuery is the one place every change reaches, including "Clear
    all", which calls it directly rather than through the debounce.
    """
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    run_query = app_js.split("async function runQuery()", 1)[1].split("\n}", 1)[0]
    assert "persistFilters(payload)" in run_query
