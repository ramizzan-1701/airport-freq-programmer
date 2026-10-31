"""Keeping the main window and the map window agreeing.

The map opens in a window of its own, which is a separate JS context
with its own copy of the filters. Rather than have the two talk to each
other, each polls the server and the server is the one that is right.

The part worth testing is not that a value round-trips -- it is that a
window can tell its own writes from the other window's. Without that,
applying a change re-persists it, which bumps the revision, which the
other window applies and re-persists, and the two bounce a filter back
and forth for as long as the app is open.
"""

import pytest
from fastapi.testclient import TestClient

from afp.web import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


def sync(client: TestClient, filters_since: int = -1, ui_since: int = -1) -> dict:
    response = client.get(
        "/api/sync", params={"filters_since": filters_since, "ui_since": ui_since}
    )
    assert response.status_code == 200
    return response.json()


# ---------- revisions ----------


def test_a_caller_who_has_never_synced_can_ask_for_everything(client):
    """Revisions start at 0, so "I have never synced" cannot itself be
    0 -- asking from 0 would skip revision 0 and the window would open
    on nothing. The endpoint's defaults have to be older than that.
    """
    body = client.get("/api/sync").json()
    assert body["filters"] is not None
    assert body["ui"] is not None


def test_the_first_poll_is_given_everything(client):
    body = sync(client)
    assert body["filters_revision"] == 0
    assert body["filters"] is not None, "a caller who has never synced gets the state"
    assert body["ui"] is not None


def test_saving_filters_bumps_the_revision(client):
    before = sync(client)["filters_revision"]
    client.put("/api/filters", json={"states": ["CA"]})
    after = sync(client)["filters_revision"]
    assert after > before


def test_a_poll_that_is_already_current_carries_no_payload(client):
    """The common answer is "nothing happened", and both windows ask a
    few times a second. It costs two integers, not a filter state.
    """
    client.put("/api/filters", json={"states": ["CA"]})
    current = sync(client)

    body = sync(client, filters_since=current["filters_revision"], ui_since=current["ui_revision"])
    assert body["filters"] is None
    assert body["ui"] is None
    assert body["filters_source"] is None
    assert body["ui_source"] is None


def test_a_stale_caller_is_given_the_new_filters(client):
    stale = sync(client)["filters_revision"]
    client.put("/api/filters", json={"states": ["CA"], "cities": ["LOS ANGELES"]})

    body = sync(client, filters_since=stale)
    assert body["filters"]["states"] == ["CA"]
    assert body["filters"]["cities"] == ["LOS ANGELES"]


# ---------- telling your own writes from the other window's ----------


def test_a_write_is_attributed_to_the_client_that_made_it(client):
    stale = sync(client)["filters_revision"]
    client.put("/api/filters", json={"states": ["CA"]}, headers={"X-Client-Id": "main"})

    body = sync(client, filters_since=stale)
    assert body["filters_source"] == "main", (
        "without this a window cannot tell its own change from the other's, "
        "and the two re-persist each other's writes forever"
    )


def test_the_source_is_only_sent_with_the_change_it_describes(client):
    """A source left on a poll that carries no payload is a trap: it
    reads as "someone just wrote this" every time around the loop.
    """
    client.put("/api/filters", json={"states": ["CA"]}, headers={"X-Client-Id": "main"})
    current = sync(client)
    assert current["filters_source"] == "main"

    quiet = sync(client, filters_since=current["filters_revision"], ui_since=current["ui_revision"])
    assert quiet["filters_source"] is None


def test_the_latest_writer_wins_the_attribution(client):
    client.put("/api/filters", json={"states": ["CA"]}, headers={"X-Client-Id": "main"})
    client.put("/api/filters", json={"states": ["NV"]}, headers={"X-Client-Id": "map"})

    body = sync(client)
    assert body["filters_source"] == "map"
    assert body["filters"]["states"] == ["NV"]


def test_a_write_without_a_client_id_is_still_accepted(client):
    """Nothing about the sync layer should make an ordinary save fail --
    /api/filters predates it and other callers do not send the header.
    """
    response = client.put("/api/filters", json={"states": ["CA"]})
    assert response.status_code == 200
    assert sync(client)["filters_source"] is None


# ---------- the active rule ----------


def test_the_active_rule_is_shared_between_windows(client):
    stale = sync(client)["ui_revision"]
    client.put("/api/ui-state", json={"active_radius_id": "abc123"}, headers={"X-Client-Id": "map"})

    body = sync(client, ui_since=stale)
    assert body["ui"]["active_radius_id"] == "abc123"
    assert body["ui_source"] == "map"


def test_clearing_the_active_rule_is_a_change_like_any_other(client):
    """Deselecting has to travel. If only setting an id propagated, a
    rule closed in one window would stay open in the other.
    """
    client.put("/api/ui-state", json={"active_radius_id": "abc123"})
    seen = sync(client)["ui_revision"]

    client.put("/api/ui-state", json={"active_radius_id": None})
    body = sync(client, ui_since=seen)
    assert body["ui"] is not None, "clearing did not bump the revision"
    assert body["ui"]["active_radius_id"] is None


def test_the_active_rule_is_never_written_to_the_saved_filters(client):
    """It is UI state: which window has a rule open changes nothing
    about the query, and restoring it next session would reopen an
    editor nobody asked for.
    """
    client.put("/api/ui-state", json={"active_radius_id": "abc123"})

    saved = client.get("/api/filters").json()
    assert "active_radius_id" not in saved


def test_ui_state_and_filters_have_revisions_of_their_own(client):
    """Opening a rule must not make every window re-apply the filters,
    and a filter change must not reopen an editor.
    """
    start = sync(client)

    client.put("/api/ui-state", json={"active_radius_id": "abc123"})
    after_ui = sync(client, filters_since=start["filters_revision"], ui_since=start["ui_revision"])
    assert after_ui["ui"] is not None
    assert after_ui["filters"] is None, "opening a rule dragged the filters along"

    seen = sync(client)
    client.put("/api/filters", json={"states": ["CA"]})
    after_filters = sync(client, filters_since=seen["filters_revision"], ui_since=seen["ui_revision"])
    assert after_filters["filters"] is not None
    assert after_filters["ui"] is None, "a filter change dragged the UI state along"


def test_ui_state_merges_rather_than_replacing(client):
    """A window sets the one field it cares about. Anything else that
    comes to live here must survive a write that does not mention it.
    """
    client.put("/api/ui-state", json={"active_radius_id": "abc123"})
    client.put("/api/ui-state", json={})

    assert sync(client)["ui"]["active_radius_id"] == "abc123"
