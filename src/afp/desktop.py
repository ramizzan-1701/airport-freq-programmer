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
from .web.state import AppState

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

# The map window. Smaller floor than the main window: it holds a toolbar,
# a chip row and the map, none of which reflow the way the breakdown band
# does, so it stays usable far below the main window's size.
MAP_WINDOW_TITLE = "Radius Map"
MAP_WINDOW_SIZE = (900, 700)
MAP_WINDOW_MIN_SIZE = (520, 460)

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


def _screens() -> list:
    """The attached displays, as pywebview reports them.

    Wrapped rather than read inline because `webview.screens` is not a
    plain attribute: reading it starts the GUI toolkit if it is not
    running yet. That is exactly right inside the app and exactly wrong
    inside a test, which wants to say what the displays are without
    opening a window to find out.
    """
    return list(webview.screens)


class ServerStartupError(RuntimeError):
    """uvicorn never reached a bound-and-listening state."""


class MapWindow:
    """Opens and closes the radius map's own window, and remembers where
    the user put it.

    Exposed to the page as `pywebview.api`, so the rail's "Open map"
    button can ask for a real OS window rather than a panel inside the
    main one.

    Creating a window from a js_api call is the main-thread question
    flagged at the top of this module: the call arrives on pywebview's
    JS bridge thread, not thread 0. pywebview marshals create_window
    onto the GUI thread itself, so this is its problem and not ours --
    but it is the first place to look if macOS misbehaves.

    Placement lives here rather than in the page because the page cannot
    see it. Inside a webview there is no browser chrome, so JS reads the
    content box as the whole window: restoring that as the window size
    would lose the title bar's height on every launch and the window
    would creep smaller each session. pywebview's resized/moved events
    report the real frame, in logical pixels on both platforms.
    """

    def __init__(self, base_url: str, state: AppState) -> None:
        self._base_url = base_url
        self._state = state
        self._window = None
        # The last frame the OS reported, or None before the first event.
        # Kept in memory and written on open and close rather than on
        # every event: `moved` fires continuously through a drag.
        self._frame: dict | None = None
        # pywebview runs each event handler on a thread of its own, so a
        # move arriving mid-close would otherwise have two threads in
        # the same dict and the same file.
        self._lock = threading.Lock()

    # ---------- placement ----------

    def _record_frame(self, **frame: int) -> None:
        """Remember a size or a position the OS just reported.

        Filters the obviously unusable rather than everything unusable:
        a minimised window reports (-32000, -32000) on Windows, and
        persisting that would hide the window on the next launch. The
        full check happens on the way back out, where the screens are
        worth asking about.
        """
        if "width" in frame and min(frame["width"], frame["height"]) < 1:
            return
        if "x" in frame and min(frame["x"], frame["y"]) < -10_000:
            return
        with self._lock:
            self._frame = {**(self._frame or {}), **frame}

    def _save(self, *, is_open: bool) -> None:
        with self._lock:
            placement = {**(self._frame or {}), "open": is_open}
            try:
                self._state.save_map_window_placement(placement)
            except OSError:
                # Losing the remembered position is not worth failing
                # a window open or a quit over.
                pass

    def _restore_geometry(self) -> dict:
        """create_window kwargs for where the window was last.

        A saved position is only used if the window would come back
        somewhere the user can reach it. The case this exists for is a
        second monitor that is no longer plugged in: the position is
        still perfectly valid, and the window opens where nothing can
        be seen or dragged.
        """
        saved = self._state.load_map_window_placement() or {}
        try:
            width = int(saved["width"])
            height = int(saved["height"])
        except (KeyError, TypeError, ValueError):
            return {}

        width = max(width, MAP_WINDOW_MIN_SIZE[0])
        height = max(height, MAP_WINDOW_MIN_SIZE[1])

        try:
            x = int(saved["x"])
            y = int(saved["y"])
        except (KeyError, TypeError, ValueError):
            return {"width": width, "height": height}

        # Where the title bar would land. If that point is on a screen,
        # the window can be moved; if it is not, nothing about the
        # window can be reached, however valid the numbers look.
        grab = (x + 60, y + 20)
        for screen in _screens():
            on_x = screen.x <= grab[0] < screen.x + screen.width
            on_y = screen.y <= grab[1] < screen.y + screen.height
            if on_x and on_y:
                # A window sized for a screen that is no longer there
                # would open mostly past the edge of this one.
                return {
                    "width": min(width, screen.width),
                    "height": min(height, screen.height),
                    "x": x,
                    "y": y,
                }
        return {"width": width, "height": height}

    # ---------- the window ----------

    def open_map_window(self) -> bool:
        # A window the user has closed leaves its Python object behind,
        # so "is there one" cannot be answered by the reference alone.
        if self._window is not None and self._window in webview.windows:
            self._window.show()
            return True

        geometry = {
            "width": MAP_WINDOW_SIZE[0],
            "height": MAP_WINDOW_SIZE[1],
            **self._restore_geometry(),
        }
        with self._lock:
            self._frame = dict(geometry)
        self._window = webview.create_window(
            MAP_WINDOW_TITLE,
            f"{self._base_url}/map",
            min_size=MAP_WINDOW_MIN_SIZE,
            **geometry,
        )
        self._window.events.resized += self._on_resized
        self._window.events.moved += self._on_moved
        self._window.events.closed += self._on_closed
        self._save(is_open=True)
        return True

    def restore_map_window(self) -> bool:
        """Reopen the map if it was open when the app last closed.

        Called by the main window once a cycle is loaded, not at
        startup: the map has nothing to draw before then, and on a fresh
        install it would open over the About screen.
        """
        if (self._state.load_map_window_placement() or {}).get("open"):
            return self.open_map_window()
        return False

    def close_map_window(self) -> bool:
        window, self._window = self._window, None
        if window is not None and window in webview.windows:
            window.destroy()
        return True

    def close_if_open(self) -> None:
        """Called when the main window goes away.

        pywebview keeps the process alive while any window is open, so a
        map window left behind would outlive the app it belongs to and
        hold the server thread open with it.

        The map being open at this moment is the state to remember: the
        user left it open, so it comes back next launch. Written before
        the destroy, because destroying it fires `closed` -- which means
        "the user shut it" everywhere else.
        """
        if self._window is not None and self._window in webview.windows:
            self._save(is_open=True)
        self.close_map_window()

    # ---------- OS events ----------

    def _on_resized(self, width: int, height: int) -> None:
        self._record_frame(width=int(width), height=int(height))

    def _on_moved(self, x: int, y: int) -> None:
        self._record_frame(x=int(x), y=int(y))

    def _on_closed(self) -> None:
        self._window = None
        self._save(is_open=False)


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

    base_url = f"http://127.0.0.1:{port}"
    map_window = MapWindow(base_url, app.state.afp_state)
    window = webview.create_window(
        WINDOW_TITLE,
        base_url,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN_SIZE,
        js_api=map_window,
    )
    # Closing the main window closes the app, so the map goes with it.
    window.events.closing += map_window.close_if_open

    webview.start()  # blocks until the user closes the window

    server.should_exit = True
    thread.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
