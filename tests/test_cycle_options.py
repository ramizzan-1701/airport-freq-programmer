"""What the download screen is told, and what it costs to keep.

Two things meet here. The screen has to say whether the data you are
about to work with is current, which it could never do before: the only
answer available to it was /api/check-update's `update_available`, and
that compares the published cycle against cycle_store.json's
last-processed watermark rather than against what is on disk or in use.
It reads false while you work on data two months old.

And the cache had no ceiling. Nothing deleted a cycle, ever, and each
one is about 29 MB -- the extracted CSVs plus the zips they came out of,
which are not cleaned up after extraction either. Measured at 84 MB and
86 MB on two installs before this existed.

The pruning tests are the ones worth being careful about: they are the
only code in this app that deletes a user's data.
"""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from afp.nasr.scraper import Cycle
from afp.web import create_app

FIXTURES = Path(__file__).parent / "fixtures"
_FIXTURE_CSV_NAMES = (
    ("APT_BASE.csv", "apt_base_sample.csv"),
    ("FRQ.csv", "frq_sample.csv"),
    ("ILS_BASE.csv", "ils_base_sample.csv"),
)


def write_cycle(cache_dir: Path, cycle: str, *, with_zips: bool = False) -> Path:
    cycle_dir = cache_dir / cycle
    cycle_dir.mkdir(parents=True, exist_ok=True)
    for dest_name, src_name in _FIXTURE_CSV_NAMES:
        shutil.copy(FIXTURES / src_name, cycle_dir / dest_name)
    if with_zips:
        # Half the weight of a real cycle, and never cleaned up after
        # extraction -- so a prune that left them would reclaim nothing
        # like what it appears to.
        for name in ("APT_CSV.zip", "FRQ_CSV.zip", "ILS_CSV.zip"):
            (cycle_dir / name).write_bytes(b"not really a zip")
    return cycle_dir


class StubSource:
    """Stands in for the live FAA scrape, the way test_web_api.py does."""

    def __init__(self, published: date | None):
        self._published = published

    def get_current_cycle(self):
        if self._published is None:
            raise RuntimeError("could not reach the FAA")
        return Cycle(self._published, f"https://example/{self._published}")

    def is_update_available(self, current=None) -> bool:
        # Only /api/check-update asks. /api/cycle-options deliberately
        # does not -- that is the watermark comparison this replaces.
        return False


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(cache_dir=tmp_path))


def options(client: TestClient, published: date | None) -> dict:
    client.app.state.afp_state.source = StubSource(published)
    response = client.get("/api/cycle-options")
    assert response.status_code == 200
    return response.json()


# ---------- which situation the screen is in ----------


def test_a_newer_published_cycle_is_an_update(client, tmp_path):
    write_cycle(tmp_path, "2026-09-03")

    body = options(client, date(2026, 10, 29))
    assert body["state"] == "update_available"
    assert body["published_cycle"] == "2026-10-29"
    assert body["newest_downloaded"] == "2026-09-03"


def test_having_the_published_cycle_is_being_current(client, tmp_path):
    """Measured against what is on disk, not against the last-processed
    watermark. The watermark says "have I ever downloaded this", which
    stays true while the thing you actually loaded is months old.
    """
    write_cycle(tmp_path, "2026-09-03")
    write_cycle(tmp_path, "2026-10-29")

    body = options(client, date(2026, 10, 29))
    assert body["state"] == "current"


def test_a_cycle_newer_than_the_published_one_is_still_current(client, tmp_path):
    """A preview cycle fetched early, or a clock that disagrees. The
    screen should not offer to fetch something already on disk.
    """
    write_cycle(tmp_path, "2026-10-29")
    write_cycle(tmp_path, "2026-11-26")

    body = options(client, date(2026, 10, 29))
    assert body["state"] == "current"
    assert body["newest_downloaded"] == "2026-11-26"


def test_an_unreachable_faa_is_a_state_not_an_error(client, tmp_path):
    """This screen is the first thing every launch shows, so it has to
    render something. With data on disk you can work offline; being
    unable to reach the FAA is a sentence to print, not a 502 to
    swallow the way /api/check-update's caller has to.
    """
    write_cycle(tmp_path, "2026-09-03")

    body = options(client, None)
    assert body["state"] == "offline"
    assert body["published_cycle"] is None
    assert body["newest_downloaded"] == "2026-09-03"


def test_nothing_downloaded_is_empty_even_when_offline(client):
    """A first run with no connection. "offline" would offer a fallback
    that does not exist; there is only one thing to say, which is that
    there is nothing here yet.
    """
    assert options(client, None)["state"] == "empty"
    assert options(client, date(2026, 10, 29))["state"] == "empty"


# ---------- the cycle to fall back to ----------


def test_one_cycle_leaves_nothing_to_fall_back_to(client, tmp_path):
    write_cycle(tmp_path, "2026-10-29")
    assert options(client, date(2026, 10, 29))["previous_downloaded"] is None


def test_the_fallback_is_the_second_newest(client, tmp_path):
    for cycle in ("2026-08-06", "2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle)

    body = options(client, date(2026, 10, 29))
    assert body["newest_downloaded"] == "2026-10-29"
    assert body["previous_downloaded"] == "2026-09-03"


# ---------- pruning ----------


def test_pruning_keeps_the_newest_two(tmp_path):
    """Two rather than one because the newest is not always the one you
    want: if the FAA recalls data, the cycle before it is the thing to
    fall back to -- and it cannot be re-downloaded, because fetch only
    ever gets what is currently published.
    """
    for cycle in ("2026-07-09", "2026-08-06", "2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle)
    state = create_app(cache_dir=tmp_path).state.afp_state

    removed = state.prune_cycles()

    assert state.available_cycles() == [date(2026, 10, 29), date(2026, 9, 3)]
    assert sorted(removed) == [date(2026, 7, 9), date(2026, 8, 6)]


def test_pruning_takes_the_zips_with_it(tmp_path):
    """The zips are never cleaned up after extraction, so they are half
    the weight of a cycle. A prune that left them behind would report
    reclaiming far more than it did.
    """
    for cycle in ("2026-08-06", "2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle, with_zips=True)
    state = create_app(cache_dir=tmp_path).state.afp_state

    state.prune_cycles()

    assert not (tmp_path / "2026-08-06").exists(), "the whole directory should be gone"


def test_pruning_never_deletes_the_loaded_cycle(tmp_path):
    """Nothing would actually break -- the database is built in memory
    from the CSVs and never reads them again -- but a rule with no
    exceptions is easier to be sure of than one with a footnote.
    """
    for cycle in ("2026-08-06", "2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle)
    state = create_app(cache_dir=tmp_path).state.afp_state
    state.load_cycle(date(2026, 8, 6))

    state.prune_cycles()

    assert date(2026, 8, 6) in state.available_cycles()


def test_pruning_leaves_a_small_cache_alone(tmp_path):
    for cycle in ("2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle)
    state = create_app(cache_dir=tmp_path).state.afp_state

    assert state.prune_cycles() == []
    assert len(state.available_cycles()) == 2


def test_loading_a_cycle_does_not_prune(tmp_path):
    """Pruning rides on a fetch, where something new has just been
    proven good. A load proves nothing new -- and loading the fallback
    to check it is exactly when you would least like the others deleted.
    """
    for cycle in ("2026-07-09", "2026-08-06", "2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle)
    client = TestClient(create_app(cache_dir=tmp_path))

    assert client.post("/api/load", json={"cycle": "2026-09-03"}).status_code == 200
    assert len(client.app.state.afp_state.available_cycles()) == 4


def test_an_undeletable_cycle_does_not_fail_the_fetch(tmp_path, monkeypatch):
    """The data is downloaded and loaded by the time this runs. Leaving
    a directory behind costs the disk it was already using; raising here
    would throw away a fetch that worked.
    """
    for cycle in ("2026-08-06", "2026-09-03", "2026-10-29"):
        write_cycle(tmp_path, cycle)
    state = create_app(cache_dir=tmp_path).state.afp_state

    def refuse(path):
        raise OSError("file in use")

    monkeypatch.setattr("afp.web.state.shutil.rmtree", refuse)

    assert state.prune_cycles() == []
    assert len(state.available_cycles()) == 3


# ---------- pruning on the real fetch path ----------


class FetchingSource(StubSource):
    """A fetch that writes the cycle instead of downloading it.

    The endpoint is the thing under test -- that it prunes, and prunes
    after the new cycle is loaded rather than before. Pulling 29 MB from
    the FAA to find that out would make this test slow, flaky and
    dependent on what the FAA happens to be publishing today.
    """

    def __init__(self, published: date, cache_dir: Path):
        super().__init__(published)
        self.cache_dir = cache_dir
        self.marked = None

    def fetch_current_cycle(self, progress=None):
        from afp.nasr.source import FetchResult

        cycle_dir = write_cycle(self.cache_dir, self._published.isoformat(), with_zips=True)
        return FetchResult(
            cycle=Cycle(self._published, "https://example/new"),
            apt_base_csv=cycle_dir / "APT_BASE.csv",
            frq_csv=cycle_dir / "FRQ.csv",
            ils_base_csv=cycle_dir / "ILS_BASE.csv",
        )

    def mark_processed(self, cycle):
        self.marked = cycle.effective_date


def test_a_fetch_prunes_down_to_two(tmp_path):
    for cycle in ("2026-07-09", "2026-08-06", "2026-09-03"):
        write_cycle(tmp_path, cycle)
    client = TestClient(create_app(cache_dir=tmp_path))
    client.app.state.afp_state.source = FetchingSource(date(2026, 10, 29), tmp_path)

    response = client.post("/api/fetch")
    assert response.status_code == 200
    assert response.json()["loaded_cycle"] == "2026-10-29"

    state = client.app.state.afp_state
    assert state.available_cycles() == [date(2026, 10, 29), date(2026, 9, 3)]


def test_the_fetched_cycle_is_loaded_before_anything_is_deleted(tmp_path):
    """Order is the whole safety argument. Pruning first would delete a
    working cycle to make room for one that might fail to parse, and the
    app would come back with nothing.
    """
    for cycle in ("2026-08-06", "2026-09-03"):
        write_cycle(tmp_path, cycle)
    client = TestClient(create_app(cache_dir=tmp_path))
    state = client.app.state.afp_state
    state.source = FetchingSource(date(2026, 10, 29), tmp_path)

    seen = {}
    real_prune = state.prune_cycles
    state.prune_cycles = lambda keep=2: seen.setdefault(
        "loaded_when_pruned", state.loaded.cycle_date if state.loaded else None
    ) or real_prune(keep)

    client.post("/api/fetch")
    assert seen["loaded_when_pruned"] == date(2026, 10, 29)


def test_a_failed_fetch_deletes_nothing(tmp_path):
    """The except branches re-raise before the prune is reached. Worth
    pinning: a prune that ran in a finally would throw away good cycles
    every time the FAA was unreachable.
    """
    for cycle in ("2026-07-09", "2026-08-06", "2026-09-03"):
        write_cycle(tmp_path, cycle)
    client = TestClient(create_app(cache_dir=tmp_path), raise_server_exceptions=False)

    class BrokenSource(StubSource):
        def fetch_current_cycle(self, progress=None):
            raise RuntimeError("the FAA went away")

    client.app.state.afp_state.source = BrokenSource(date(2026, 10, 29))

    client.post("/api/fetch")
    assert len(client.app.state.afp_state.available_cycles()) == 3


# ---------- the old endpoint is unchanged ----------


def test_check_update_still_answers_its_own_question(client, tmp_path):
    """`afp check` and its tests read this. The new screen does not, and
    the two means of "is there an update" must not be merged: this one
    is measured against the last-processed watermark on purpose.
    """
    write_cycle(tmp_path, "2026-10-29")
    client.app.state.afp_state.source = StubSource(date(2026, 10, 29))

    response = client.get("/api/check-update")
    assert response.status_code == 200
    assert set(response.json()) == {"current_published_cycle", "update_available"}
