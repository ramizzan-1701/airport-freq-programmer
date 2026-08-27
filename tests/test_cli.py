from datetime import date
from pathlib import Path

from afp import cli
from afp.nasr.scraper import Cycle
from afp.nasr.source import FetchResult


class _StubSource:
    def __init__(self, cache_dir):
        self.cache_dir = cache_dir
        self.marked = None

    def get_current_cycle(self, today=None):
        return Cycle(date(2026, 8, 6), "https://example/2026-08-06")

    def is_update_available(self, current=None):
        return True

    def fetch_current_cycle(self):
        return FetchResult(
            cycle=Cycle(date(2026, 8, 6), "https://example/2026-08-06"),
            apt_base_csv=Path("APT_BASE.csv"),
            frq_csv=Path("FRQ.csv"),
            ils_base_csv=Path("ILS_BASE.csv"),
        )

    def mark_processed(self, cycle):
        self.marked = cycle


def test_check_reports_update_available(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "NASRSource", _StubSource)
    rc = cli.main(["--cache-dir", str(tmp_path), "check"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "2026-08-06" in out
    assert "Update available" in out


def test_fetch_prints_extracted_paths(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "NASRSource", _StubSource)
    rc = cli.main(["--cache-dir", str(tmp_path), "fetch"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "APT_BASE.csv" in out
    assert "FRQ.csv" in out
    assert "ILS_BASE.csv" in out
    assert "Marked as processed" not in out


def test_fetch_mark_processed_flag(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "NASRSource", _StubSource)
    rc = cli.main(["--cache-dir", str(tmp_path), "fetch", "--mark-processed"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Marked as processed" in out
