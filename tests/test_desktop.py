"""Tests for the packaged-app entry point.

These cover the server-startup half of afp.desktop only. webview.start()
opens a real native window and blocks the main thread, so it can't run
under pytest -- that half is verified by launching the built app.
"""

import threading
import urllib.request

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
        assert "Airport Frequency Programmer" in response.read().decode()


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
