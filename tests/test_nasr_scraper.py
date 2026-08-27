from datetime import date
from pathlib import Path

from afp.nasr.scraper import current_cycle, parse_cycle_csv_links, parse_index

FIXTURES = Path(__file__).parent / "fixtures"
INDEX_HTML = (FIXTURES / "nasr_index_page.html").read_text(encoding="utf-8")
CYCLE_HTML = (FIXTURES / "nasr_cycle_page.html").read_text(encoding="utf-8")


def test_parse_index_finds_cycles_including_current_and_preview():
    cycles = parse_index(INDEX_HTML)
    dates = {c.effective_date for c in cycles}

    assert date(2026, 8, 6) in dates
    assert date(2026, 9, 3) in dates  # preview cycle, still in the future
    assert date(2026, 7, 9) in dates  # an archived cycle
    # sorted oldest first
    assert dates == {c.effective_date for c in cycles}
    assert [c.effective_date for c in cycles] == sorted(c.effective_date for c in cycles)


def test_parse_index_builds_subpage_url_from_scraped_date():
    cycles = parse_index(INDEX_HTML)
    aug_cycle = next(c for c in cycles if c.effective_date == date(2026, 8, 6))
    assert aug_cycle.subpage_url.endswith("/NASR_Subscription/2026-08-06")


def test_current_cycle_ignores_future_preview_cycle():
    cycles = parse_index(INDEX_HTML)
    today = date(2026, 8, 16)
    picked = current_cycle(cycles, today)
    assert picked.effective_date == date(2026, 8, 6)


def test_current_cycle_advances_once_preview_becomes_effective():
    cycles = parse_index(INDEX_HTML)
    today = date(2026, 9, 10)
    picked = current_cycle(cycles, today)
    assert picked.effective_date == date(2026, 9, 3)


def test_current_cycle_raises_if_nothing_published_yet():
    cycles = parse_index(INDEX_HTML)
    with_only_far_future = [c for c in cycles]
    try:
        current_cycle(with_only_far_future, date(1999, 1, 1))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_parse_cycle_csv_links_finds_apt_frq_ils():
    links = parse_cycle_csv_links(CYCLE_HTML)
    assert set(links.keys()) == {"APT", "FRQ", "ILS"}
    assert links["APT"].endswith("_APT_CSV.zip")
    assert links["FRQ"].endswith("_FRQ_CSV.zip")
    assert links["ILS"].endswith("_ILS_CSV.zip")
    assert all(url.startswith("https://") for url in links.values())
