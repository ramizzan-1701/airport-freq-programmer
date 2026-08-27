import pytest

from afp.custom_entries import (
    CustomGroupCapacityError,
    check_custom_group_capacity,
    split_recognized_and_custom,
)
from afp.selection import Entry


def _entry(tag_name: str, group: str) -> Entry:
    return Entry(tag_name=tag_name, freq_mhz=122.8, group=group, lat=1.0, lon=-1.0)


def test_split_separates_fixed_groups_from_custom():
    entries = [
        _entry("AAA-CTAF", "0-9"),
        _entry("BBB-CTAF", "A-E"),
        _entry("HOME-BASE", "PERSONAL"),
        _entry("CABIN-WX", "WX NOTES"),
    ]
    recognized, custom = split_recognized_and_custom(entries)
    assert {e.tag_name for e in recognized} == {"AAA-CTAF", "BBB-CTAF"}
    assert {e.tag_name for e in custom} == {"HOME-BASE", "CABIN-WX"}


def test_split_all_recognized_leaves_no_custom():
    entries = [_entry("AAA-CTAF", "0-9"), _entry("BBB-CTAF", "F-J")]
    recognized, custom = split_recognized_and_custom(entries)
    assert len(recognized) == 2
    assert custom == []


def test_capacity_allows_exactly_three_distinct_groups():
    custom = [_entry(f"E{i}", g) for i, g in enumerate(["A", "B", "C"])]
    check_custom_group_capacity(custom)  # should not raise


def test_capacity_blocks_more_than_three_distinct_groups():
    custom = [_entry(f"E{i}", g) for i, g in enumerate(["A", "B", "C", "D"])]
    with pytest.raises(CustomGroupCapacityError) as exc_info:
        check_custom_group_capacity(custom)
    err = exc_info.value
    assert err.found == 4
    assert err.available == 3
    assert set(err.groups) == {"A", "B", "C", "D"}


def test_capacity_ignores_entry_count_only_counts_distinct_groups():
    # 10 entries, but all in the same single group -- must not block.
    custom = [_entry(f"E{i}", "PERSONAL") for i in range(10)]
    check_custom_group_capacity(custom)  # should not raise


def test_first_time_import_with_zero_fixed_names_present_still_succeeds():
    """A first-time import, before the user has ever done the group-rename
    CTA, has zero matches against the 6 fixed names -- that's expected,
    not an error. Only >3 distinct *custom* groups blocks.
    """
    custom = [_entry("A1", "MISC"), _entry("A2", "BACKUP")]
    check_custom_group_capacity(custom)  # should not raise
