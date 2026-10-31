"""Remembering where the user put the radius map window.

The map is a window of its own, which means the user arranges it --
beside the main window, on a second screen, resized to taste -- and
expects to find it there again. None of that can come from the page:
inside a webview there is no browser chrome, so JS reads the content box
as the whole window, and restoring that as the window size would shave
the title bar off on every launch. The frame numbers come from
pywebview's own resized/moved events, which is why this is tested here
rather than against the DOM.

The failure worth guarding is not a number that fails to round-trip. It
is a window that comes back somewhere the user cannot reach it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from afp import desktop
from afp.web import local_store
from afp.web.state import AppState


class FakeScreen:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self.x, self.y, self.width, self.height = x, y, width, height


LAPTOP = FakeScreen(0, 0, 1920, 1080)
# To the left of the laptop, which is where negative coordinates come
# from on a real desk.
SECOND = FakeScreen(-1920, 0, 1920, 1080)


@pytest.fixture
def state(tmp_path) -> AppState:
    return AppState(cache_dir=tmp_path)


@pytest.fixture
def map_window(state, monkeypatch) -> desktop.MapWindow:
    monkeypatch.setattr(desktop, "_screens", lambda: [LAPTOP])
    return desktop.MapWindow("http://127.0.0.1:1234", state)


# ---------- the store ----------


def test_placement_round_trips(tmp_path: Path):
    path = tmp_path / "map_window.json"
    local_store.save_map_window(path, {"width": 900, "height": 700, "x": 40, "y": 60, "open": True})
    assert local_store.load_map_window(path)["x"] == 40


def test_nothing_saved_yet_is_not_an_error(tmp_path: Path):
    assert local_store.load_map_window(tmp_path / "missing.json") is None


def test_a_corrupt_file_opens_the_window_at_its_default(tmp_path: Path):
    """Same choice as the filters and the groups: a file that cannot be
    read should cost the user their window position, not the app.
    """
    path = tmp_path / "map_window.json"
    path.write_text("{not json", encoding="utf-8")
    assert local_store.load_map_window(path) is None


def test_the_placement_is_not_written_into_the_saved_filters(state):
    """It is window state. Nothing about it changes the query, and
    filters.json is read by the rail in both windows.
    """
    state.save_map_window_placement({"width": 900, "height": 700, "open": True})
    assert state.map_window_path != state.filters_path
    assert not state.filters_path.exists()


# ---------- what comes back ----------


def test_the_window_opens_where_it_was(map_window, state):
    state.save_map_window_placement({"width": 1100, "height": 800, "x": 300, "y": 120, "open": True})
    assert map_window._restore_geometry() == {
        "width": 1100,
        "height": 800,
        "x": 300,
        "y": 120,
    }


def test_nothing_remembered_means_no_geometry_to_apply(map_window):
    """An empty dict so create_window falls back to the defaults in this
    module rather than to pywebview's.
    """
    assert map_window._restore_geometry() == {}


def test_a_size_below_the_floor_comes_back_at_the_floor(map_window, state):
    state.save_map_window_placement({"width": 100, "height": 80, "x": 10, "y": 10})
    geometry = map_window._restore_geometry()
    assert (geometry["width"], geometry["height"]) == desktop.MAP_WINDOW_MIN_SIZE


def test_a_position_on_a_screen_that_is_gone_is_dropped(map_window, state):
    """The case this exists for: the map lived on a second monitor, the
    monitor is no longer plugged in, and the saved coordinates are still
    perfectly valid. The window would open where nothing can be seen or
    dragged back, and the app would look like it had failed to open it.
    """
    state.save_map_window_placement(
        {"width": 900, "height": 700, "x": SECOND.x + 200, "y": 300, "open": True}
    )
    geometry = map_window._restore_geometry()
    assert geometry == {"width": 900, "height": 700}, "an unreachable position was kept"


def test_the_position_is_kept_when_that_screen_is_still_there(map_window, state, monkeypatch):
    monkeypatch.setattr(desktop, "_screens", lambda: [LAPTOP, SECOND])
    state.save_map_window_placement({"width": 900, "height": 700, "x": -1720, "y": 300})
    assert map_window._restore_geometry()["x"] == -1720


def test_a_window_sized_for_a_bigger_screen_is_cut_down(map_window, state, monkeypatch):
    """Saved on a 4K display, reopened on a laptop. The position is
    reachable, so it survives the check above -- but the window would
    run most of the way off the right edge.
    """
    monkeypatch.setattr(desktop, "_screens", lambda: [FakeScreen(0, 0, 1280, 800)])
    state.save_map_window_placement({"width": 3200, "height": 1800, "x": 20, "y": 20})
    geometry = map_window._restore_geometry()
    assert (geometry["width"], geometry["height"]) == (1280, 800)


def test_half_a_saved_record_is_not_trusted(map_window, state):
    """A size with no position is normal -- a window that was never
    moved. A position with no size is not something this writes, and
    guessing the missing half would put the window somewhere arbitrary.
    """
    state.save_map_window_placement({"x": 300, "y": 120, "open": True})
    assert map_window._restore_geometry() == {}


# ---------- what gets recorded ----------


def test_a_resize_and_a_move_build_one_frame(map_window):
    map_window._on_resized(1000, 760)
    map_window._on_moved(55, 66)
    assert map_window._frame == {"width": 1000, "height": 760, "x": 55, "y": 66}


def test_a_minimised_window_does_not_overwrite_the_position(map_window):
    """Windows reports (-32000, -32000) for a minimised window. Saved,
    it would put the map somewhere no screen covers on the next launch
    -- which the restore check would then throw away, losing a position
    that was perfectly good a moment earlier.
    """
    map_window._on_moved(55, 66)
    map_window._on_moved(-32000, -32000)
    assert map_window._frame["x"] == 55


def test_a_zero_sized_report_is_ignored(map_window):
    map_window._on_resized(900, 700)
    map_window._on_resized(0, 0)
    assert map_window._frame["width"] == 900


# ---------- open and closed across launches ----------


class FakeWindow:
    def __init__(self) -> None:
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


def test_closing_the_app_with_the_map_open_remembers_it_as_open(map_window, state, monkeypatch):
    """The state to remember is the one the user left, not the one the
    teardown produces. close_if_open destroys the window, which fires
    the same `closed` event the user's own close does -- so the open
    flag has to be written before the destroy, or quitting would always
    record it as closed and it would never come back.
    """
    window = FakeWindow()
    map_window._window = window
    monkeypatch.setattr(desktop.webview, "windows", [window])

    map_window.close_if_open()

    assert window.destroyed
    assert json.loads(state.map_window_path.read_text(encoding="utf-8"))["open"] is True


def test_the_user_closing_the_map_means_it_stays_closed(map_window, state):
    map_window._on_closed()
    assert json.loads(state.map_window_path.read_text(encoding="utf-8"))["open"] is False


def test_the_map_is_only_restored_if_it_was_open(map_window, state, monkeypatch):
    opened = []
    monkeypatch.setattr(desktop.MapWindow, "open_map_window", lambda self: opened.append(True))

    state.save_map_window_placement({"width": 900, "height": 700, "open": False})
    assert map_window.restore_map_window() is False

    state.save_map_window_placement({"width": 900, "height": 700, "open": True})
    map_window.restore_map_window()
    assert opened == [True]


def test_a_fresh_install_does_not_open_a_map_window(map_window):
    """Nothing saved at all -- the first launch. The map should not
    appear beside a window the user has not asked it to.
    """
    assert map_window.restore_map_window() is False


def test_the_page_can_reach_every_method_it_calls(map_window):
    """pywebview builds `pywebview.api` from the public, callable
    attributes it finds on the js_api object -- anything named with a
    leading underscore is skipped. So the privacy of a method here is
    not a style choice: it decides whether app.js can call it at all,
    and the failure is a button that silently does nothing.
    """
    import inspect

    for name in ("open_map_window", "restore_map_window", "close_map_window"):
        method = getattr(map_window, name)
        assert not name.startswith("_")
        assert inspect.signature(method).parameters == {}, (
            f"{name} takes arguments the page does not send"
        )
