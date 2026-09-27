"""Choosing a group scheme, naming the user's three slots, and copying
frequencies into them.

The failures worth guarding here are the quiet ones: a scheme that
persists but is not applied, a custom group whose entries are stranded
under a name nothing declares, and a copy that silently overwrites an
entry already in the target.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from afp.query.db import build_database
from afp.schema import Airport, Frequency, NormalizedData
from afp.selection import ALPHABETICAL_GROUP_NAMES, CATEGORY_GROUP_NAMES, Entry
from afp.web import create_app
from afp.web.state import LoadedCycle

DATA = NormalizedData(
    airports=[
        Airport(id="HWD", name="Hayward", city="HAYWARD", state="CA",
                lat=37.6, lon=-122.1, public_use=True)
    ],
    frequencies=[
        Frequency(airport_id="HWD", freq_mhz=120.2, freq_category="TOWER", raw_freq_use="LCL/P"),
        Frequency(airport_id="HWD", freq_mhz=126.7, freq_category="WEATHER_STATION", raw_freq_use="ATIS"),
    ],
    ils=[],
)


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app(cache_dir=tmp_path)
    app.state.afp_state.loaded = LoadedCycle(
        cycle_date=date(2026, 9, 3), data=DATA, conn=build_database(DATA)
    )
    return TestClient(app)


def groups_of(client) -> set[str]:
    return {e["group"] for e in client.post("/api/query", json={}).json()["entries"]}


# ---------- the scheme ----------


def test_the_default_scheme_is_alphabetical(client):
    """What the app produced before there was a choice, so an existing
    install's exports do not change shape until someone changes this.
    """
    assert client.get("/api/groups").json()["scheme"] == "alphabetical"
    assert groups_of(client) <= set(ALPHABETICAL_GROUP_NAMES)


def test_choosing_a_scheme_regroups_the_results(client):
    """Persisting the choice without applying it would look like the
    control does nothing.
    """
    client.put("/api/groups/scheme", json={"scheme": "category"})
    assert groups_of(client) <= set(CATEGORY_GROUP_NAMES)


def test_the_scheme_survives_a_restart(client, tmp_path):
    client.put("/api/groups/scheme", json={"scheme": "category"})
    second = TestClient(create_app(cache_dir=tmp_path))
    assert second.get("/api/groups").json()["scheme"] == "category"


def test_an_unknown_scheme_is_refused(client):
    assert client.put("/api/groups/scheme", json={"scheme": "by-vibes"}).status_code == 422
    assert client.get("/api/groups").json()["scheme"] == "alphabetical"


def test_the_response_carries_both_schemes_names(client):
    """So the selector can describe each one without a second copy of
    the names living in the frontend.
    """
    body = client.get("/api/groups").json()
    assert body["schemes"]["alphabetical"] == list(ALPHABETICAL_GROUP_NAMES)
    assert body["schemes"]["category"] == list(CATEGORY_GROUP_NAMES)


# ---------- naming the user's slots ----------


def test_slots_start_unnamed_and_sit_after_the_presets(client):
    slots = client.get("/api/groups").json()["custom_slots"]
    assert [s["slot"] for s in slots] == [6, 7, 8]
    assert all(s["name"] is None for s in slots)


def test_naming_a_slot_sticks(client):
    client.put("/api/groups/custom/0", json={"name": "Home"})
    assert client.get("/api/groups").json()["custom_slots"][0]["name"] == "Home"


def test_a_reserved_name_is_suffixed_rather_than_refused(client):
    """A user whose radio already has a WEATHER group should not have it
    silently absorbed into the managed set.
    """
    body = client.put("/api/groups/custom/0", json={"name": "WEATHER"}).json()
    assert body["custom_slots"][0]["name"] == "WEATHER1"


def test_two_slots_cannot_take_the_same_name(client):
    """The radio would have no way to tell which slot an entry meant,
    and the export refuses duplicate names outright.
    """
    client.put("/api/groups/custom/0", json={"name": "Home"})
    body = client.put("/api/groups/custom/1", json={"name": "Home"}).json()
    names = [s["name"] for s in body["custom_slots"]]
    assert names[0] == "Home" and names[1] == "Home1"


def test_a_name_longer_than_the_radio_allows_is_trimmed(client):
    body = client.put("/api/groups/custom/0", json={"name": "A" * 40}).json()
    cap = body["max_group_name_length"]
    assert len(body["custom_slots"][0]["name"]) <= cap


def test_renaming_carries_the_entries_with_it(client):
    """Entries hold the group *name*, not the slot. Left behind they
    would be in a group nothing declares, and land ungrouped.
    """
    client.put("/api/groups/custom/0", json={"name": "Home"})
    entry = {"tag_name": "HWD-CT", "freq_mhz": 120.2, "group": "", "airport_id": "HWD",
             "airport_name": "Hayward", "city": "HAYWARD", "state": "CA",
             "lat": 37.6, "lon": -122.1, "category": "TOWER"}
    client.post("/api/groups/copy", json={"slot": 0, "entries": [entry]})

    client.put("/api/groups/custom/0", json={"name": "Local"})
    held = client.get("/api/custom-entries").json()["entries"]
    assert [e["group"] for e in held] == ["Local"]


def test_naming_a_slot_that_does_not_exist_is_a_404(client):
    assert client.put("/api/groups/custom/7", json={"name": "X"}).status_code == 404


# ---------- copying ----------


def _row(client, index: int = 0) -> dict:
    return client.post("/api/query", json={}).json()["entries"][index]


def test_copying_snapshots_the_entry_into_the_slot(client):
    row = _row(client)
    body = client.post("/api/groups/copy", json={"slot": 0, "entries": [row]}).json()

    assert body["copied"] == 1
    held = client.get("/api/custom-entries").json()["entries"]
    assert held[0]["tag_name"] == row["tag_name"]
    assert held[0]["freq_mhz"] == row["freq_mhz"]


def test_copying_leaves_the_original_where_it_was(client):
    """A copy, not a move."""
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    tags = [e["tag_name"] for e in client.post("/api/query", json={}).json()["entries"]]
    assert tags.count(row["tag_name"]) == 2


def test_an_unnamed_target_slot_gets_the_radios_own_default_name(client):
    """It has to be called something: an entry naming a group no slot
    declares still imports, but lands with no group at all.
    """
    body = client.post("/api/groups/copy", json={"slot": 1, "entries": [_row(client)]}).json()
    assert body["group"] == "GROUP8"


def test_a_duplicate_tag_is_skipped_and_reported(client):
    """Tag names must be unique within a group. One collision must not
    fail the whole copy, and must not pass silently either -- only the
    user can decide what to rename.
    """
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    body = client.post("/api/groups/copy", json={"slot": 0, "entries": [row]}).json()

    assert body["copied"] == 0
    assert body["skipped"] == [row["tag_name"]]


def test_a_collision_does_not_block_the_rest_of_the_copy(client):
    rows = client.post("/api/query", json={}).json()["entries"][:2]
    client.post("/api/groups/copy", json={"slot": 0, "entries": [rows[0]]})
    body = client.post("/api/groups/copy", json={"slot": 0, "entries": rows}).json()

    assert body["copied"] == 1
    assert body["skipped"] == [rows[0]["tag_name"]]


def test_the_same_tag_may_live_in_two_different_groups(client):
    """Uniqueness is per group, confirmed on the radio itself. A
    shortlist of frequencies the user already has elsewhere is the whole
    point of a custom group.
    """
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    body = client.post("/api/groups/copy", json={"slot": 1, "entries": [row]}).json()
    assert body["copied"] == 1


def test_copying_into_a_slot_that_does_not_exist_is_a_404(client):
    assert client.post("/api/groups/copy", json={"slot": 9, "entries": []}).status_code == 404


def test_slot_counts_reflect_what_is_in_them(client):
    client.post("/api/groups/copy", json={"slot": 0, "entries": [_row(client)]})
    slots = client.get("/api/groups").json()["custom_slots"]
    assert slots[0]["entry_count"] == 1
    assert slots[1]["entry_count"] == 0


# ---------- what reaches the file ----------


def test_the_export_declares_every_group_it_uses(client):
    """The point of the whole feature: nothing to rename by hand,
    because the file says what the groups are called.
    """
    client.put("/api/groups/scheme", json={"scheme": "category"})
    client.put("/api/groups/custom/0", json={"name": "Home"})
    client.post("/api/groups/copy", json={"slot": 0, "entries": [_row(client)]})

    xml = client.post("/api/generate", json={}).content.decode("utf-8-sig")
    block = xml[xml.index("<GROUPS>"): xml.index("</GROUPS>")]
    for name in CATEGORY_GROUP_NAMES:
        assert f">{name}<" in block
    assert '<GROUP index="6">Home</GROUP>' in block


def test_an_unused_slot_is_left_out_of_the_export(client):
    """Left out is what leaves it alone on the radio -- writing all nine
    every time would rename groups the app does not manage.
    """
    xml = client.post("/api/generate", json={}).content.decode("utf-8-sig")
    block = xml[xml.index("<GROUPS>"): xml.index("</GROUPS>")]
    assert 'index="6"' not in block


def test_existing_custom_entries_are_seated_on_first_run(tmp_path):
    """Upgrading from a version with no slots: entries imported then
    carry a group name but no slot, and a slot nothing sits in is not
    declared -- so every one of those entries would land ungrouped.
    """
    app = create_app(cache_dir=tmp_path)
    app.state.afp_state.custom_entries = [Entry("OLD-1", 122.8, "Legacy", 1.0, -1.0)]
    app.state.afp_state._restore_groups()

    slots = [s for s in _slots(app)]
    assert slots[0] == "Legacy"


def _slots(app) -> list:
    return app.state.afp_state.custom_slot_names


def test_a_copied_row_and_its_original_are_distinguishable(client):
    """Found by using the UI: a frequency copied into a custom group
    shares its tag name with the generated row it came from, because
    uniqueness is only per group. The table keyed its selection on the
    tag alone, so the two rows collided -- ticking either ticked both,
    four rows vanished from the selection map, and delete could resolve
    to the wrong one of the pair.

    The rows must therefore differ in something other than the tag.
    """
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    entries = client.post("/api/query", json={}).json()["entries"]

    pair = [e for e in entries if e["tag_name"] == row["tag_name"]]
    assert len(pair) == 2, "expected the copy and its original"
    assert {e["is_custom"] for e in pair} == {True, False}
    assert len({e["group"] for e in pair}) == 2
    # The custom one is addressable for deletion; the generated one is not.
    assert sorted(e["custom_index"] is None for e in pair) == [False, True]


def test_the_frontend_keys_rows_on_more_than_the_tag():
    """Pins the fix above where it actually lives."""
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function rowKey(e)" in app_js
    assert "e.is_custom ? `c:${e.custom_index}`" in app_js
    assert "dataset.tag" not in app_js, "a tag-keyed row lookup came back"
