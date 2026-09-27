import pytest

from afp.custom_entries import (
    CustomGroupCapacityError,
    check_custom_group_capacity,
    custom_group_names,
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


# ---------- ALL, and the two schemes ----------


def test_all_entries_are_preserved_rather_than_discarded():
    """Importing replaces the radio's whole memory book rather than
    merging into it, so an entry dropped here is deleted from the radio
    on the next export. Unassigned entries are still the user's data.
    """
    entries = [_entry("LOOSE", "ALL"), _entry("KEPT", "Home")]
    _recognized, custom = split_recognized_and_custom(entries)
    assert {e.tag_name for e in custom} == {"LOOSE", "KEPT"}


def test_all_does_not_consume_a_custom_slot():
    """It is the absence of a group, not a group, and has no slot."""
    entries = [_entry("A", "ALL"), _entry("B", "One"), _entry("C", "Two")]
    assert custom_group_names(entries) == ["One", "Two"]
    check_custom_group_capacity(entries)  # three groups only if ALL counted


def test_all_plus_three_real_groups_still_fits():
    entries = [
        _entry("A", "ALL"),
        _entry("B", "One"),
        _entry("C", "Two"),
        _entry("D", "Three"),
    ]
    check_custom_group_capacity(entries)


def test_both_schemes_names_are_recognized_not_custom():
    """The switching problem. A user who moves from alphabetical to
    category and re-imports would otherwise meet their own six previous
    group names as six brand-new custom groups -- over the 3-slot cap
    before they had added anything of their own.
    """
    entries = [_entry("A", "0-9"), _entry("B", "TWR/GND"), _entry("C", "Home")]
    recognized, custom = split_recognized_and_custom(entries)

    assert {e.tag_name for e in recognized} == {"A", "B"}
    assert {e.tag_name for e in custom} == {"C"}


def test_switching_schemes_does_not_overflow_the_custom_cap():
    from afp.selection import ALPHABETICAL_GROUP_NAMES, CATEGORY_GROUP_NAMES

    entries = [
        _entry(f"OLD{i}", name) for i, name in enumerate(ALPHABETICAL_GROUP_NAMES)
    ] + [
        _entry(f"NEW{i}", name) for i, name in enumerate(CATEGORY_GROUP_NAMES)
    ] + [_entry("MINE", "Home")]

    _recognized, custom = split_recognized_and_custom(entries)
    check_custom_group_capacity(custom)
    assert custom_group_names(custom) == ["Home"]


def test_a_fourth_named_custom_group_still_overflows():
    """The cap is real -- the ALL and scheme exemptions must not have
    quietly disabled it.
    """
    entries = [_entry(str(i), f"G{i}") for i in range(4)]
    with pytest.raises(CustomGroupCapacityError):
        check_custom_group_capacity(entries)
