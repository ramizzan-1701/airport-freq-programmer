"""Progress reporting for the two operations that make the user wait.

Fetching a cycle is 10 MB of download and about a second and a half of
local work. On a slow connection that is a minute or more of a screen
that does not change, which reads as a hung app.

Lives here rather than in afp.web so afp.nasr can report progress
without importing the web layer -- the downloader takes a sink as a
parameter, and the CLI passes none.

WEIGHTS
-------
The steps are weighted by measured cost, not counted equally. Equal
weighting would jump the bar to 25%, crawl through one 8 MB download for
the next minute, then sprint through five more steps -- worse than no
bar at all, because it misleads during the only slow part.

No single weighting can be right at every connection speed: the
downloads are ~97% of the wall clock on a slow line and ~25% on a fast
one, while these weights are fixed. They are set for the slow case,
which is the one worth fixing. On a fast connection the bar pauses
briefly near the end instead, which is the harmless direction to err.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass


class Cancelled(Exception):
    """Raised out of a progress callback to abort the work reporting to
    it. Chosen over passing a separate `should_cancel` callable down
    every layer: the sink is already threaded through to the one loop
    that can stop promptly.
    """


@dataclass(frozen=True)
class Step:
    key: str
    weight: float
    label: str
    # True when the step can report its own sub-progress -- i.e. a
    # download, where Content-Length gives a real denominator.
    #
    # The unmeasurable ones are shown at their own midpoint rather than
    # at their start. Parsing is 70% of a cached load's bar and reports
    # nothing while it runs, so anchoring it at the start left the fill
    # sitting at a dead zero for the whole of it, which reads as hung --
    # the exact thing this bar exists to prevent. The midpoint claims
    # only what is true: we are somewhere inside this step.
    measurable: bool = False


# Relative weights, from measured sizes and timings: APT_CSV.zip is
# 8.0 MB, FRQ 1.3 MB, ILS 0.7 MB, and parse+build is a fixed ~1.5s. The
# three downloads hold 81% of the bar between them, split in proportion
# to their real sizes.
FETCH_STEPS: tuple[Step, ...] = (
    Step("cycle", 2, "Checking the FAA for the current cycle"),
    Step("links", 3, "Locating the data files"),
    Step("apt", 65, "Downloading airport data", measurable=True),
    Step("apt_extract", 2, "Extracting airport data"),
    Step("frq", 11, "Downloading frequency data", measurable=True),
    Step("frq_extract", 1, "Extracting frequency data"),
    Step("ils", 5, "Downloading navaid data", measurable=True),
    Step("ils_extract", 1, "Extracting navaid data"),
    Step("parse", 7, "Reading the data"),
    Step("build", 3, "Building the search index"),
)

# Loading an already-downloaded cycle is only the tail of the above --
# no network at all. Same two step keys, so the same reporting code in
# AppState.load_cycle serves both paths.
LOAD_STEPS: tuple[Step, ...] = (
    Step("parse", 70, "Reading the data"),
    Step("build", 30, "Building the search index"),
)


def _human_mb(count: int) -> str:
    return f"{count / 1_000_000:.1f} MB"


class ProgressTracker:
    """Where a running fetch writes, and the polling endpoint reads.

    Written from the worker thread and read from the thread serving the
    poll, so every field moves under one lock: a snapshot assembled from
    separate unlocked reads could pair a new fraction with a stale
    message, which is exactly the kind of flicker nobody reproduces.
    """

    def __init__(self, steps: tuple[Step, ...]):
        self._lock = threading.Lock()
        self._steps = steps
        self._total_weight = sum(step.weight for step in steps)
        self._index = -1
        self._within = 0.0
        self._detail = ""
        self._done = False
        self._error: str | None = None
        self._cancelled = False

    # ---------- written by the worker ----------

    def begin(self, key: str) -> None:
        """Move to the named step. Raises Cancelled if the work should
        stop, so callers get the check for free at every boundary.
        """
        with self._lock:
            self._raise_if_cancelled()
            self._index = next(
                i for i, step in enumerate(self._steps) if step.key == key
            )
            self._within = 0.0
            self._detail = ""

    def bytes_received(self, received: int, total: int | None) -> None:
        """Sub-progress inside the current step.

        `total` is None when the server sends no Content-Length. The step
        then holds at its start rather than inventing a denominator, and
        the byte count still ticks upward underneath -- which is the part
        that actually shows the app is alive.
        """
        with self._lock:
            self._raise_if_cancelled()
            if total:
                self._within = min(1.0, received / total)
                self._detail = f"{_human_mb(received)} of {_human_mb(total)}"
            else:
                self._detail = _human_mb(received)

    def finish(self) -> None:
        with self._lock:
            self._index = len(self._steps) - 1
            self._within = 1.0
            self._detail = ""
            self._done = True

    def fail(self, message: str) -> None:
        with self._lock:
            self._error = message

    # ---------- written by the request that cancels ----------

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def _raise_if_cancelled(self) -> None:
        # Caller holds the lock.
        if self._cancelled:
            raise Cancelled()

    # ---------- read by the poll ----------

    def snapshot(self) -> dict:
        with self._lock:
            if self._index < 0:
                fraction, label = 0.0, self._steps[0].label
            else:
                before = sum(s.weight for s in self._steps[: self._index])
                current = self._steps[self._index]
                # An unmeasurable step sits at its own midpoint: it has
                # no denominator to interpolate against, and its start
                # would show as no movement at all while it worked.
                within = self._within if current.measurable else 0.5
                fraction = (before + current.weight * within) / self._total_weight
                label = current.label
            return {
                "fraction": 1.0 if self._done else round(fraction, 4),
                "label": label,
                "detail": self._detail,
                "done": self._done,
                "error": self._error,
                "cancelled": self._cancelled,
            }
