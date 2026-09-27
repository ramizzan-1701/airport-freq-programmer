"""Progress reporting for a fetch, and cancelling one.

The thing being prevented is a screen that does not change for a minute
on a slow connection, so the assertions are about movement: that the bar
advances while bytes arrive, that it advances *proportionally* to them,
and that the byte counter underneath moves even when the server declines
to say how big the file is.
"""

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from afp.progress import (
    FETCH_STEPS,
    LOAD_STEPS,
    Cancelled,
    ProgressTracker,
)
from afp.web import create_app


@pytest.fixture
def tracker() -> ProgressTracker:
    return ProgressTracker(FETCH_STEPS)


# ---------- the weighting ----------


def test_the_downloads_hold_most_of_the_bar():
    """The whole design decision in one assertion. 10 MB of download
    against ~1.5s of local work means anything else dominating the bar
    would make it misreport the slow case it exists for.
    """
    total = sum(step.weight for step in FETCH_STEPS)
    downloads = sum(s.weight for s in FETCH_STEPS if s.key in {"apt", "frq", "ils"})
    assert downloads / total > 0.75


def test_the_download_weights_track_the_real_file_sizes():
    """APT_CSV.zip is 8.0 MB, FRQ 1.3, ILS 0.7. A bar that gave them
    equal thirds would stall through the first and race the other two.
    """
    weights = {s.key: s.weight for s in FETCH_STEPS}
    assert weights["apt"] > weights["frq"] > weights["ils"]
    assert weights["apt"] > 4 * weights["frq"]


def test_every_step_carries_a_label_for_the_text_underneath():
    for step in FETCH_STEPS + LOAD_STEPS:
        assert step.label and not step.label.endswith("."), step


def test_both_step_lists_share_the_keys_load_cycle_reports():
    """AppState.load_cycle reports into whichever tracker it is handed:
    the fetch's, as its tail, or a load's, as the whole of it. A key
    missing from either list would raise StopIteration mid-load.
    """
    for key in ("parse", "build"):
        assert any(s.key == key for s in FETCH_STEPS)
        assert any(s.key == key for s in LOAD_STEPS)


# ---------- movement ----------


def test_the_fraction_only_ever_increases(tracker):
    seen = [tracker.snapshot()["fraction"]]
    for step in FETCH_STEPS:
        tracker.begin(step.key)
        seen.append(tracker.snapshot()["fraction"])
    assert seen == sorted(seen), seen
    assert seen[0] == 0.0


def test_bytes_move_the_bar_within_a_step(tracker):
    tracker.begin("apt")
    at_start = tracker.snapshot()["fraction"]
    tracker.bytes_received(4_000_000, 8_000_000)
    halfway = tracker.snapshot()["fraction"]
    tracker.bytes_received(8_000_000, 8_000_000)
    at_end = tracker.snapshot()["fraction"]

    assert at_start < halfway < at_end
    # Half the bytes should be half of that step's slice, not some
    # arbitrary animation.
    assert halfway == pytest.approx((at_start + at_end) / 2, abs=1e-3)


def test_the_byte_counter_reads_in_megabytes(tracker):
    tracker.begin("apt")
    tracker.bytes_received(3_200_000, 8_000_000)
    assert tracker.snapshot()["detail"] == "3.2 MB of 8.0 MB"


def test_a_missing_content_length_still_counts_upward(tracker):
    """Some servers send none. Inventing a denominator would animate a
    bar against a number we do not have; the count still proves life.
    """
    tracker.begin("apt")
    at_start = tracker.snapshot()["fraction"]
    tracker.bytes_received(3_200_000, None)
    snap = tracker.snapshot()

    assert snap["detail"] == "3.2 MB"
    # Held exactly where the step began -- not nudged along by a
    # denominator we were never given.
    assert snap["fraction"] == at_start


def test_an_unmeasurable_step_sits_at_its_own_midpoint():
    """Parsing is 70% of a cached load and reports nothing while it
    runs. Anchored at its start the fill stayed at a dead zero for the
    whole of it, which reads as hung -- the thing the bar exists to
    prevent. The midpoint claims only that we are inside the step.
    """
    load = ProgressTracker(LOAD_STEPS)
    load.begin("parse")
    assert load.snapshot()["fraction"] == pytest.approx(0.35)
    load.begin("build")
    assert load.snapshot()["fraction"] == pytest.approx(0.85)


def test_a_measurable_step_still_begins_at_its_start():
    """The midpoint rule must not apply to downloads: they have a real
    denominator, and starting one halfway would then run backwards as
    the first chunks arrived.
    """
    seen = []
    t = ProgressTracker(FETCH_STEPS)
    for step in FETCH_STEPS:
        t.begin(step.key)
        seen.append(t.snapshot()["fraction"])
        if step.measurable:
            for done in (0.01, 0.5, 1.0):
                t.bytes_received(int(8_000_000 * done), 8_000_000)
                seen.append(t.snapshot()["fraction"])
    assert seen == sorted(seen), "the bar runs backwards somewhere"


def test_finishing_pins_the_bar_to_full(tracker):
    tracker.begin("apt")
    tracker.finish()
    snap = tracker.snapshot()
    assert snap["fraction"] == 1.0
    assert snap["done"] is True


def test_a_failure_is_readable_from_the_snapshot(tracker):
    """The last poll can land after the POST has already returned its
    error. Finding a stale in-progress snapshot there would leave the
    bar frozen part-way with nothing said.
    """
    tracker.begin("apt")
    tracker.fail("connection reset")
    assert tracker.snapshot()["error"] == "connection reset"


# ---------- cancelling ----------


def test_cancelling_raises_out_of_the_next_callback(tracker):
    tracker.begin("apt")
    tracker.cancel()
    with pytest.raises(Cancelled):
        tracker.bytes_received(1, 2)


def test_cancelling_also_stops_at_the_next_step_boundary(tracker):
    """Belt and braces: a cancel arriving between downloads, when no
    chunk callback is due, should still not start the next one.
    """
    tracker.cancel()
    with pytest.raises(Cancelled):
        tracker.begin("frq")


def test_the_snapshot_says_it_was_cancelled(tracker):
    tracker.cancel()
    assert tracker.snapshot()["cancelled"] is True


# ---------- the endpoints ----------


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


def test_progress_is_safe_to_poll_before_anything_runs(client):
    """The frontend starts polling the moment the button is pressed,
    which can beat the POST to the server.
    """
    body = client.get("/api/fetch/progress").json()
    assert body["fraction"] == 0.0
    assert body["done"] is False


def test_cancelling_nothing_is_a_404_not_a_crash(client):
    assert client.post("/api/fetch/cancel").status_code == 404


def test_a_download_reports_bytes_as_they_arrive(tmp_path, monkeypatch):
    """The downloader's own contract: a callback per chunk, carrying the
    running total and the declared size. Everything the bar does during
    the slow minute rests on this.
    """
    from afp.nasr import downloader

    payload = b"x" * (1 << 18)  # four chunks at the 64 KB chunk size
    monkeypatch.setattr(
        downloader.requests, "get", lambda *a, **k: _FakeResponse(payload)
    )

    seen: list[tuple[int, int | None]] = []
    downloader.download_file("http://x/f.zip", tmp_path / "f.zip", on_bytes=lambda *a: seen.append(a))

    assert len(seen) > 1, "only one callback -- the bar would not move"
    assert [n for n, _total in seen] == sorted(n for n, _total in seen)
    assert seen[-1] == (len(payload), len(payload))


def test_a_cancelled_download_leaves_no_partial_file(tmp_path, monkeypatch):
    """A truncated zip on disk fails later, at extraction, where the
    error explains nothing. A later attempt re-downloads from the start
    regardless, so there is nothing to keep.
    """
    from afp.nasr import downloader

    monkeypatch.setattr(
        downloader.requests, "get", lambda *a, **k: _FakeResponse(b"y" * (1 << 18))
    )

    dest = tmp_path / "f.zip"
    def stop(_received, _total):
        raise Cancelled()

    with pytest.raises(Cancelled):
        downloader.download_file("http://x/f.zip", dest, on_bytes=stop)
    assert not dest.exists()


def test_a_failed_download_leaves_no_partial_file(tmp_path, monkeypatch):
    from afp.nasr import downloader

    # Larger than the 64 KB chunk size, and failing partway, so some
    # bytes really have reached the file before the connection drops.
    monkeypatch.setattr(
        downloader.requests,
        "get",
        lambda *a, **k: _FakeResponse(b"z" * (1 << 18), fail_at=1 << 17),
    )
    dest = tmp_path / "f.zip"
    with pytest.raises(OSError):
        downloader.download_file("http://x/f.zip", dest)
    assert not dest.exists()


def test_the_tracker_survives_being_read_while_written(tracker):
    """Written from the thread doing the fetch, read from the thread
    serving the poll. A snapshot built from separate unlocked reads
    could pair a new fraction with a stale message.
    """
    tracker.begin("apt")
    stop = threading.Event()

    def write():
        n = 0
        while not stop.is_set():
            n = (n + 100_000) % 8_000_000
            tracker.bytes_received(n, 8_000_000)

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    try:
        for _ in range(2000):
            snap = tracker.snapshot()
            assert 0.0 <= snap["fraction"] <= 1.0
            assert snap["detail"].endswith("8.0 MB")
    finally:
        stop.set()
        writer.join(timeout=5)


class _FakeResponse:
    """Enough of requests' streaming response for the downloader."""

    def __init__(self, payload: bytes, fail_at: int | None = None):
        self._payload = payload
        self._fail_at = fail_at
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size: int):
        for start in range(0, len(self._payload), chunk_size):
            if self._fail_at is not None and start >= self._fail_at:
                raise OSError("connection reset")
            yield self._payload[start : start + chunk_size]


def test_a_finished_tracker_outlives_the_run_that_made_it():
    """begin_progress replaces rather than clears, because the last poll
    lands after the work ends and has to find the outcome there.

    The cost is that the first poll of the *next* run can arrive before
    its POST has installed a new tracker, and come back holding the
    previous run's completed snapshot -- which flashed the bar to 100%
    for one tick. The frontend guard against that is pinned below.
    """
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    poll = app_js.split("const tick = async () => {", 1)[1].split("};", 1)[0]
    assert "if (!p.done) started = true;" in poll, (
        "the poll believes a snapshot that was already finished"
    )


def test_the_load_path_offers_no_cancel_button():
    """Loading a cached cycle is about a second and a half of local work
    with nothing interruptible in it. A button that cannot be pressed in
    time is worse than no button.
    """
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    body = app_js.split("async function loadCycle(", 1)[1].split("\nasync function", 1)[0]
    assert "cancellable: false" in body


def test_the_downloader_stays_independent_of_the_web_layer():
    """The sink is a parameter, not a global. afp.nasr importing the web
    layer would make the CLI depend on it too.
    """
    source = Path("src/afp/nasr/downloader.py").read_text(encoding="utf-8")
    assert "afp.web" not in source and "from ..web" not in source
