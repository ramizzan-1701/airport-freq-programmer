from afp.selection import Entry
from afp.web.local_store import (
    load_custom_entries,
    save_custom_entries,
)

ENTRY = Entry(tag_name="HOME-BASE", freq_mhz=122.725, group="PERSONAL", lat=37.02, lon=-122.058)


def test_load_custom_entries_returns_empty_list_when_file_missing(tmp_path):
    assert load_custom_entries(tmp_path / "does_not_exist.json") == []


def test_custom_entries_save_then_load_round_trips(tmp_path):
    path = tmp_path / "nested" / "custom_entries.json"
    save_custom_entries(path, [ENTRY])
    assert load_custom_entries(path) == [ENTRY]


def test_custom_entries_save_overwrites_previous_value(tmp_path):
    path = tmp_path / "custom_entries.json"
    save_custom_entries(path, [ENTRY])
    save_custom_entries(path, [])
    assert load_custom_entries(path) == []
