"""Tests for the packaged-app entry point.

These cover the server-startup half of afp.desktop only. webview.start()
opens a real native window and blocks the main thread, so it can't run
under pytest -- that half is verified by launching the built app.
"""

import threading
import urllib.request

import pytest
import uvicorn

from afp.desktop import ServerStartupError, _wait_for_port
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
