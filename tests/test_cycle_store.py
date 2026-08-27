from datetime import date

from afp.nasr.cycle_store import load_last_processed, save_last_processed


def test_load_returns_none_when_file_missing(tmp_path):
    assert load_last_processed(tmp_path / "does_not_exist.json") is None


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "nested" / "cycle_store.json"
    save_last_processed(path, date(2026, 7, 9))

    assert load_last_processed(path) == date(2026, 7, 9)


def test_save_overwrites_previous_value(tmp_path):
    path = tmp_path / "cycle_store.json"
    save_last_processed(path, date(2026, 7, 9))
    save_last_processed(path, date(2026, 8, 6))

    assert load_last_processed(path) == date(2026, 8, 6)
