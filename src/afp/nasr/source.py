"""Ties scraping, downloading, and cycle tracking together: the FAA-source
equivalent of "give me the three CSVs for the current cycle," ready to
hand to afp.adapters.faa.FAAAdapter.

Downloading and rebuilding is only ever triggered by an explicit call to
fetch_current_cycle() -- nothing here runs automatically, since the user
may have manually-added entries in their current export they don't want
silently overwritten by a background refresh (spec §1, §5).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..progress import ProgressTracker
from . import cycle_store, downloader, scraper
from .scraper import Cycle, REQUIRED_CATEGORIES

_ZIP_FILENAMES = {"APT": "APT_CSV.zip", "FRQ": "FRQ_CSV.zip", "ILS": "ILS_CSV.zip"}
_CSV_FILENAMES = {"APT": "APT_BASE.csv", "FRQ": "FRQ.csv", "ILS": "ILS_BASE.csv"}


@dataclass(frozen=True)
class FetchResult:
    cycle: Cycle
    apt_base_csv: Path
    frq_csv: Path
    ils_base_csv: Path


class NASRSource:
    def __init__(self, cache_dir: Path, cycle_store_path: Path | None = None):
        self.cache_dir = Path(cache_dir)
        self.cycle_store_path = (
            Path(cycle_store_path)
            if cycle_store_path is not None
            else self.cache_dir / "cycle_store.json"
        )

    def get_current_cycle(self, today: date | None = None) -> Cycle:
        html = scraper.fetch(scraper.INDEX_URL)
        cycles = scraper.parse_index(html)
        return scraper.current_cycle(cycles, today or date.today())

    def is_update_available(self, current: Cycle | None = None) -> bool:
        current = current or self.get_current_cycle()
        last_processed = cycle_store.load_last_processed(self.cycle_store_path)
        return last_processed is None or current.effective_date > last_processed

    def fetch_current_cycle(
        self, today: date | None = None, progress: ProgressTracker | None = None
    ) -> FetchResult:
        # `today` is injectable for the same reason get_current_cycle's is:
        # which cycle counts as current depends on the date, so a test that
        # can't pin it silently changes meaning as real time passes.
        #
        # `progress` is optional so the CLI and the tests are unaffected;
        # when present it is also what makes the work cancellable, since
        # its callbacks raise.
        if progress:
            progress.begin("cycle")
        cycle = self.get_current_cycle(today=today)

        if progress:
            progress.begin("links")
        subpage_html = scraper.fetch(cycle.subpage_url)
        links = scraper.parse_cycle_csv_links(subpage_html)

        missing = [c for c in REQUIRED_CATEGORIES if c not in links]
        if missing:
            raise RuntimeError(
                f"could not find CSV zip link(s) for {missing} on "
                f"{cycle.subpage_url}"
            )

        cycle_dir = self.cache_dir / cycle.effective_date.isoformat()
        csv_paths: dict[str, Path] = {}
        for category in REQUIRED_CATEGORIES:
            # The step keys are the category in lower case, which is what
            # ties these three iterations to their own slices of the bar
            # -- APT is 8 MB of the 10 and has to be weighted as such.
            step = category.lower()
            if progress:
                progress.begin(step)
            zip_path = downloader.download_file(
                links[category],
                cycle_dir / _ZIP_FILENAMES[category],
                on_bytes=progress.bytes_received if progress else None,
            )
            if progress:
                progress.begin(f"{step}_extract")
            csv_paths[category] = downloader.extract_csv(
                zip_path, _CSV_FILENAMES[category], cycle_dir
            )

        return FetchResult(
            cycle=cycle,
            apt_base_csv=csv_paths["APT"],
            frq_csv=csv_paths["FRQ"],
            ils_base_csv=csv_paths["ILS"],
        )

    def mark_processed(self, cycle: Cycle) -> None:
        cycle_store.save_last_processed(self.cycle_store_path, cycle.effective_date)
