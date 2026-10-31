"""Tests for the packaged-app entry point.

These cover the server-startup half of afp.desktop only. webview.start()
opens a real native window and blocks the main thread, so it can't run
under pytest -- that half is verified by launching the built app.
"""

import threading
import urllib.request
from pathlib import Path

import afp.desktop as afp_desktop

import pytest
import uvicorn

from afp.desktop import (
    WINDOW_MIN_SIZE,
    WINDOW_SIZE,
    ServerStartupError,
    _wait_for_port,
)
from afp.paths import APP_NAME, default_cache_dir
from afp.web import create_app


@pytest.fixture
def running_server(tmp_path):
    """A real uvicorn server on an OS-assigned port, as desktop.main
    starts it -- torn down even if the test body fails.
    """
    app = create_app(cache_dir=tmp_path)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def test_wait_for_port_returns_the_os_assigned_port(running_server):
    port = _wait_for_port(running_server)
    assert port > 0


def test_app_is_actually_reachable_on_the_reported_port(running_server):
    """The port isn't just a number off a socket -- the window will load
    it, so it has to actually serve the UI.
    """
    port = _wait_for_port(running_server)
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as response:
        assert response.status == 200
        assert "FTA-850 Frequency Manager" in response.read().decode()


def test_static_assets_are_served_on_the_reported_port(running_server):
    """Guards the frozen-bundle failure mode: if STATIC_DIR doesn't
    resolve, index.html still returns but every asset 404s.
    """
    port = _wait_for_port(running_server)
    for asset in ("app.js", "style.css"):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/static/{asset}", timeout=5) as response:
            assert response.status == 200
            assert len(response.read()) > 0


def test_wait_for_port_raises_rather_than_hanging_when_server_never_starts():
    """A never-started server must fail fast with a clear error instead
    of spinning forever -- otherwise a packaged app hangs with no window
    and no message.
    """
    config = uvicorn.Config(create_app(cache_dir="."), host="127.0.0.1", port=0)
    never_started = uvicorn.Server(config)  # .run() deliberately never called

    with pytest.raises(ServerStartupError):
        _wait_for_port(never_started, timeout_s=0.2)


def test_downloads_are_enabled():
    """pywebview defaults ALLOW_DOWNLOADS to False, which silently
    swallows the "Generate XML" download -- the one thing the app exists
    to produce. Importing afp.desktop must flip it.
    """
    import webview

    import afp.desktop  # noqa: F401  (import is what applies the setting)

    assert webview.settings["ALLOW_DOWNLOADS"] is True


def test_default_cache_dir_is_absolute_and_outside_the_checkout():
    """The whole point of paths.default_cache_dir: a double-clicked app
    has no meaningful cwd, so this must never be relative.
    """
    cache_dir = default_cache_dir()
    assert cache_dir.is_absolute()
    assert APP_NAME in str(cache_dir)


# ---------- window geometry ----------
#
# webview.start() can't run under pytest, so these assert the numbers
# handed to create_window rather than the window itself.


def test_the_data_directory_name_does_not_follow_the_app_name():
    """APP_NAME is a real path, not a label.

    The app was renamed to "FTA-850 Frequency Manager" and this stayed
    "AirportFreqProgrammer" on purpose. Finishing the rename here looks
    like tidying up a leftover, and instead points every existing install
    at an empty directory: downloaded NASR cycles gone, imported custom
    entries gone, the About and group-setup flags reset, and no error
    saying why. Changing it needs a migration, not an edit.
    """
    assert APP_NAME == "AirportFreqProgrammer"
    assert " " not in APP_NAME, "a path segment with spaces is a nuisance to script around"


def test_the_window_cannot_be_dragged_below_the_size_the_ui_was_composed_at():
    """Below 1180x760 the breakdown reflows its chips into one column and
    the band grows tall enough to push the results table off screen, so
    the floor is the design size rather than some smaller round number.
    """
    assert WINDOW_MIN_SIZE == (1180, 760)
    assert WINDOW_SIZE >= WINDOW_MIN_SIZE


def test_the_window_minimum_is_above_pywebviews_own_default():
    """pywebview defaults min_size to (200, 100). Leaving that in place
    is what "abnormally small" looks like -- the guard only means
    anything if it is well clear of the default.
    """
    import inspect

    import webview

    default = inspect.signature(webview.create_window).parameters["min_size"].default
    assert default == (200, 100), "pywebview's default moved; re-check this guard"
    assert WINDOW_MIN_SIZE[0] > default[0]
    assert WINDOW_MIN_SIZE[1] > default[1]


def test_the_css_floor_sits_under_the_window_minimum():
    """The stylesheet stops the layout collapsing on any surface, the
    browser included. It has to sit *below* the window minimum: window
    chrome eats a few px off the window's own size, and a floor at or
    above it would put a scrollbar on the packaged app at its smallest
    allowed size.
    """
    import re

    from afp.web.app import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    app_rule = re.search(r"#app\s*\{(.*?)\}", css, re.DOTALL)
    assert app_rule, "#app rule not found"
    min_w = int(re.search(r"min-width:\s*(\d+)px", app_rule.group(1)).group(1))
    min_h = int(re.search(r"min-height:\s*(\d+)px", app_rule.group(1)).group(1))

    assert min_w < WINDOW_MIN_SIZE[0]
    assert min_h < WINDOW_MIN_SIZE[1]
    # Still close enough to the design width to hold the two-column
    # breakdown -- a floor of, say, 900 would "work" and look broken.
    assert min_w >= 1140


# ---------- the map's own window ----------


def test_the_page_is_given_a_way_to_open_the_map_window():
    """The rail's "Open map" button asks the shell for a real OS window.
    Without js_api the page has no pywebview.api at all, and the button
    silently falls through to the browser-mode path, which inside a
    native webview opens nothing.
    """
    source = (Path(afp_desktop.__file__)).read_text(encoding="utf-8")
    assert "js_api=map_window" in source
    assert "def open_map_window" in source


def test_closing_the_main_window_takes_the_map_with_it():
    """pywebview keeps the process alive while any window is open, so a
    map window left behind outlives the app it belongs to -- and holds
    the server thread open with it, since main() only shuts uvicorn down
    after webview.start() returns.
    """
    source = (Path(afp_desktop.__file__)).read_text(encoding="utf-8")
    assert "window.events.closing += map_window.close_if_open" in source


def test_a_second_open_reuses_the_window_rather_than_stacking_them():
    """"Open map" is not a toggle -- the user can close the window
    itself, which this side is never told about -- so pressing it again
    with one already open has to raise that one, not add another.
    """
    opened = []
    shown = []

    class FakeWindow:
        def show(self):
            shown.append(self)

        def destroy(self):
            pass

    fake_windows = []

    def fake_create_window(*args, **kwargs):
        window = FakeWindow()
        opened.append(kwargs.get("width"))
        fake_windows.append(window)
        return window

    import afp.desktop as desktop

    original_create, original_list = desktop.webview.create_window, desktop.webview.windows
    desktop.webview.create_window = fake_create_window
    desktop.webview.windows = fake_windows
    try:
        manager = desktop.MapWindow("http://127.0.0.1:1234")
        manager.open_map_window()
        manager.open_map_window()
        assert len(fake_windows) == 1, "a second press opened another window"
        assert len(shown) == 1, "the existing window was not raised"
    finally:
        desktop.webview.create_window = original_create
        desktop.webview.windows = original_list


def test_a_window_the_user_already_closed_is_opened_again():
    """Closing it leaves the Python object behind, so "is one open"
    cannot be answered from the reference alone -- only by asking
    pywebview what it still has.
    """
    import afp.desktop as desktop

    class FakeWindow:
        def show(self):
            raise AssertionError("showed a window the user had closed")

        def destroy(self):
            pass

    created = []
    live = []

    def fake_create_window(*args, **kwargs):
        window = FakeWindow()
        created.append(window)
        live.append(window)
        return window

    original_create, original_list = desktop.webview.create_window, desktop.webview.windows
    desktop.webview.create_window = fake_create_window
    desktop.webview.windows = live
    try:
        manager = desktop.MapWindow("http://127.0.0.1:1234")
        manager.open_map_window()
        live.clear()  # the user closed it
        manager.open_map_window()
        assert len(created) == 2
    finally:
        desktop.webview.create_window = original_create
        desktop.webview.windows = original_list
