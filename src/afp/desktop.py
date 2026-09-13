"""Desktop entry point: the same FastAPI app the CLI serves, wrapped in
a native window instead of a browser tab.

This deliberately does not reuse afp.cli._run_serve. That function
blocks the main thread in uvicorn.run() and shells out to
webbrowser.open(); the desktop app needs the inverse -- uvicorn on a
background thread, with the GUI owning the main thread. That's not a
style preference: macOS requires its Cocoa/WebKit event loop to run on
thread 0, so pywebview.start() must be called from the main thread.
"""

from __future__ import annotations

import threading
import time

import uvicorn
import webview

from .paths import default_cache_dir
from .web import create_app

WINDOW_TITLE = "FTA-850 Frequency Manager"
_STARTUP_TIMEOUT_S = 30.0

# The size the UI was composed at. The layout tolerates larger -- the
# filter rail is fixed at 336px and the results panel absorbs the rest --
# but not smaller: the per-category breakdown shrinks with the window and
# reflows its chips into a single column, and the band grows tall enough
# to push the results table off screen entirely. Measured against the
# real 2026-09-03 cycle, 20 categories showing:
#
#   1180px  252px breakdown, 11 chip rows, 397px band, table visible
#   1140px  212px breakdown, 16 chip rows, 552px band, table visible
#   1100px  190px breakdown, 20 chip rows, 690px band, TABLE OFF SCREEN
#
# So the floor is the design size, not a token value below it: pywebview
# defaults min_size to (200, 100), and the 1000x700 this used to pass was
# already inside the broken range.
WINDOW_SIZE = (1180, 760)
WINDOW_MIN_SIZE = (1180, 760)

# pywebview defaults this to False, which silently swallows the download
# triggered by "Generate XML" -- the button appears to do nothing, and
# producing that file is the entire point of the app. In a browser tab
# this never comes up; it's specific to running inside a native webview.
webview.settings["ALLOW_DOWNLOADS"] = True

# Already the default, but pinned: the About screen links out to Yaesu,
# and if this ever flipped, that link would load the vendor's site inside
# the app window -- which has no address bar and no back button, so there
# would be no way back to the app short of restarting it.
webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True


class ServerStartupError(RuntimeError):
    """uvicorn never reached a bound-and-listening state."""


def _wait_for_port(server: uvicorn.Server, timeout_s: float = _STARTUP_TIMEOUT_S) -> int:
    """Block until uvicorn is actually listening, then report the port
    the OS assigned it.

    We bind to port 0 (see main) so two copies of the app can't collide
    on a hardcoded port, which means the real port isn't known until the
    socket exists. Opening the window before that point renders a
    connection error instead of the app, so this gate is load-bearing
    rather than defensive.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        # server.started flips before .servers is populated, so check both
        # rather than racing to read an empty list.
        if server.started and server.servers:
            sockets = server.servers[0].sockets
            if sockets:
                return sockets[0].getsockname()[1]
        time.sleep(0.05)
    raise ServerStartupError(
        f"the local server didn't start within {timeout_s:.0f}s -- "
        "nothing to display, so not opening a window"
    )


def main() -> int:
    app = create_app(cache_dir=default_cache_dir())
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    # Daemon so a hung server can never keep the process alive after the
    # window closes; the explicit should_exit below is the clean path.
    thread = threading.Thread(target=server.run, daemon=True, name="afp-uvicorn")
    thread.start()

    try:
        port = _wait_for_port(server)
    except ServerStartupError:
        server.should_exit = True
        raise

    webview.create_window(
        WINDOW_TITLE,
        f"http://127.0.0.1:{port}",
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
    )
    webview.start()  # blocks until the user closes the window

    server.should_exit = True
    thread.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
