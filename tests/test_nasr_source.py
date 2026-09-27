from datetime import date
from pathlib import Path

import pytest

from afp.nasr import downloader as downloader_module
from afp.nasr import source as source_module
from afp.nasr.source import NASRSource

FIXTURES = Path(__file__).parent / "fixtures"
INDEX_HTML = (FIXTURES / "nasr_index_page.html").read_text(encoding="utf-8")
CYCLE_HTML = (FIXTURES / "nasr_cycle_page.html").read_text(encoding="utf-8")


@pytest.fixture
def patched_fetch(monkeypatch):
    def fake_fetch(url):
        return INDEX_HTML if url == source_module.scraper.INDEX_URL else CYCLE_HTML

    monkeypatch.setattr(source_module.scraper, "fetch", fake_fetch)


def test_get_current_cycle_uses_injected_today(patched_fetch, tmp_path):
    src = NASRSource(cache_dir=tmp_path)
    cycle = src.get_current_cycle(today=date(2026, 8, 16))
    assert cycle.effective_date == date(2026, 8, 6)


def test_is_update_available_true_when_nothing_processed_yet(patched_fetch, tmp_path):
    src = NASRSource(cache_dir=tmp_path)
    cycle = src.get_current_cycle(today=date(2026, 8, 16))
    assert src.is_update_available(current=cycle) is True


def test_is_update_available_false_once_marked_processed(patched_fetch, tmp_path):
    src = NASRSource(cache_dir=tmp_path)
    cycle = src.get_current_cycle(today=date(2026, 8, 16))
    src.mark_processed(cycle)
    assert src.is_update_available(current=cycle) is False


def test_is_update_available_true_when_newer_cycle_published(patched_fetch, tmp_path):
    src = NASRSource(cache_dir=tmp_path)
    old_cycle = src.get_current_cycle(today=date(2026, 7, 20))
    src.mark_processed(old_cycle)

    new_cycle = src.get_current_cycle(today=date(2026, 8, 16))
    assert src.is_update_available(current=new_cycle) is True


def test_fetch_current_cycle_downloads_and_extracts_each_category(
    patched_fetch, monkeypatch, tmp_path
):
    calls = {"downloaded": [], "extracted": []}

    # on_bytes mirrors the real signature: fetch_current_cycle passes a
    # progress sink through when it has one, and None otherwise.
    def fake_download_file(url, dest_path, on_bytes=None):
        calls["downloaded"].append((url, dest_path))
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(b"")
        return dest_path

    def fake_extract_csv(zip_path, filename, dest_dir):
        calls["extracted"].append((zip_path, filename, dest_dir))
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / filename
        out.write_text("stub\n")
        return out

    monkeypatch.setattr(downloader_module, "download_file", fake_download_file)
    monkeypatch.setattr(downloader_module, "extract_csv", fake_extract_csv)

    src = NASRSource(cache_dir=tmp_path)
    # Pinned like every other test here: without it this asserted against
    # whatever cycle the fixture page considered current on the day the
    # suite happened to run, and started failing on its own once real time
    # moved past the next cycle's effective date.
    result = src.fetch_current_cycle(today=date(2026, 8, 16))

    assert result.cycle.effective_date == date(2026, 8, 6)
    assert result.apt_base_csv.name == "APT_BASE.csv"
    assert result.frq_csv.name == "FRQ.csv"
    assert result.ils_base_csv.name == "ILS_BASE.csv"
    assert {c[1] for c in calls["extracted"]} == {"APT_BASE.csv", "FRQ.csv", "ILS_BASE.csv"}
    assert len(calls["downloaded"]) == 3
