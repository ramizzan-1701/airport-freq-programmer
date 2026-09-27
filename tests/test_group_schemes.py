"""The two group-naming schemes, and the slot map that becomes <GROUPS>.

The radio has 9 memory-group slots. Six carry whichever scheme is
active; three are the user's own. A <GROUPS> block in the import XML
defines those names directly, which is what removed the app's old
requirement that the user rename six groups by hand in YCE-46 before
every import.

Almost nothing here fails loudly in production: a wrong group name still
imports, it just puts frequencies somewhere the pilot did not expect, or
nowhere at all.
"""

import pytest

from afp.classification import ALL_FREQ_CATEGORIES, NON_SELECTABLE_CATEGORIES
from afp.export.fta850l import FTA_850L
from afp.selection import (
    ALPHABETICAL,
    ALPHABETICAL_GROUP_NAMES,
    CATEGORY,
    CATEGORY_GROUP_NAMES,
    CUSTOM_SLOTS,
    PRESET_SLOTS,
    RESERVED_GROUP_NAMES,
    TOTAL_SLOTS,
    UNASSIGNED_GROUP,
    category_group_for,
    default_custom_group_name,
    group_for,
    group_slots,
    unreserved_group_name,
)


# ---------- the schemes themselves ----------


def test_both_schemes_define_exactly_the_preset_slots():
    for names in (ALPHABETICAL_GROUP_NAMES, CATEGORY_GROUP_NAMES):
        assert len(names) == PRESET_SLOTS
        assert len(set(names)) == PRESET_SLOTS, "a scheme repeats a name"


def test_the_slots_add_up_to_what_the_radio_has():
    assert PRESET_SLOTS + CUSTOM_SLOTS == TOTAL_SLOTS == FTA_850L.max_groups


def test_every_scheme_name_fits_the_radios_name_length():
    """Silently truncated on the radio otherwise, which turns two groups
    into one name and loses whichever lost the race.
    """
    for name in ALPHABETICAL_GROUP_NAMES + CATEGORY_GROUP_NAMES:
        assert len(name) <= FTA_850L.max_group_name_length, name


def test_the_two_schemes_do_not_share_a_name():
    """They are reserved as one set, so an overlap would not break
    anything -- but it would mean a group whose meaning depends on which
    scheme wrote it, which nothing downstream could untangle.
    """
    assert not set(ALPHABETICAL_GROUP_NAMES) & set(CATEGORY_GROUP_NAMES)


def test_reserved_names_are_both_schemes_plus_all():
    assert RESERVED_GROUP_NAMES == set(ALPHABETICAL_GROUP_NAMES) | set(
        CATEGORY_GROUP_NAMES
    ) | {UNASSIGNED_GROUP}


# ---------- the category mapping ----------


def test_every_category_that_can_reach_an_export_has_a_group():
    """A category with nowhere to go would land in OTHER by fallback,
    which is safe but wrong -- OTHER should mean "genuinely
    miscellaneous", not "we forgot this one".

    NDB and AIRSPACE_INFO are excluded deliberately: neither can produce
    an entry at all, NDB being outside the tunable band and AIRSPACE_INFO
    an annotation on another row rather than a frequency.
    """
    excluded = NON_SELECTABLE_CATEGORIES | {"NDB"}
    for code in ALL_FREQ_CATEGORIES:
        if code in excluded:
            continue
        assert category_group_for(code) in CATEGORY_GROUP_NAMES, code
        assert category_group_for(code) != "OTHER" or code in (
            "MIL_GOV_OPS",
            "EMERGENCY",
            "PROCEDURE_FIX",
            "OTHER",
        ), f"{code} fell through to OTHER"


def test_an_unknown_category_falls_back_rather_than_raising():
    """The mapping is consulted per entry during an export. A category
    added to classification.py later must not take the export down.
    """
    assert category_group_for("SOMETHING_NEW") == "OTHER"
    assert category_group_for("") == "OTHER"


def test_the_ils_pseudo_category_is_mapped():
    """ILS entries come from their own table and carry a category no
    frequency row ever has, so it is easy to miss.
    """
    assert category_group_for("ILS") == "VOR/ILS"


def test_an_airports_services_split_across_groups_under_the_category_scheme():
    """The stated trade-off, pinned so it is a decision rather than a
    surprise: organising by what a frequency does means one airport's
    frequencies stop being adjacent.
    """
    groups = {category_group_for(c) for c in ("CTAF", "TOWER", "WEATHER_STATION")}
    assert len(groups) == 3


# ---------- picking a scheme ----------


def test_the_alphabetical_scheme_keys_on_the_airport_id():
    assert group_for("TOWER", "HWD", ALPHABETICAL) == "F-J"
    assert group_for("WEATHER_STATION", "HWD", ALPHABETICAL) == "F-J"


def test_the_category_scheme_ignores_the_airport_id():
    assert group_for("TOWER", "HWD", CATEGORY) == "TWR/GND"
    assert group_for("TOWER", "SFO", CATEGORY) == "TWR/GND"


def test_an_unknown_scheme_raises():
    with pytest.raises(ValueError, match="unknown group scheme"):
        group_for("TOWER", "HWD", "by-vibes")


# ---------- the slot map ----------


def test_the_preset_slots_come_first_and_in_scheme_order():
    slots = group_slots(CATEGORY)
    assert [slots[i] for i in range(PRESET_SLOTS)] == list(CATEGORY_GROUP_NAMES)


def test_unused_custom_slots_are_left_out_entirely():
    """Left out of <GROUPS> means left alone on the radio. Writing a name
    into a slot the app does not manage would rename whatever the user
    already had there.
    """
    slots = group_slots(ALPHABETICAL, ["Home", None, None])
    assert set(slots) == set(range(PRESET_SLOTS)) | {PRESET_SLOTS}
    assert slots[PRESET_SLOTS] == "Home"


def test_a_custom_slot_keeps_its_index_when_an_earlier_one_is_empty():
    """Slot identity, not list position. Derived from position, emptying
    the first custom group would slide the others down a slot and rename
    whatever was there.
    """
    slots = group_slots(ALPHABETICAL, [None, None, "Local"])
    assert slots[PRESET_SLOTS + 2] == "Local"
    assert PRESET_SLOTS not in slots and PRESET_SLOTS + 1 not in slots


def test_a_used_but_unnamed_custom_slot_takes_the_radios_own_default():
    """It has to be called something: an entry naming an undefined group
    still imports, but lands with no group at all.
    """
    slots = group_slots(ALPHABETICAL, ["", "  "])
    assert slots[6] == "GROUP7"
    assert slots[7] == "GROUP8"


def test_the_default_custom_names_are_numbered_the_way_the_radio_counts():
    """Slots are 0-8 in the file and 1-9 on the radio, so the third
    custom slot -- index 8 -- is the radio's GROUP9.
    """
    assert default_custom_group_name(6) == "GROUP7"
    assert default_custom_group_name(8) == "GROUP9"


def test_more_custom_names_than_slots_are_ignored():
    slots = group_slots(ALPHABETICAL, ["a", "b", "c", "d"])
    assert len(slots) == TOTAL_SLOTS
    assert max(slots) == TOTAL_SLOTS - 1


def test_a_full_slot_map_fits_the_radio():
    slots = group_slots(CATEGORY, ["One", "Two", "Three"])
    assert len(slots) == FTA_850L.max_groups
    assert sorted(slots) == list(range(FTA_850L.max_groups))


# ---------- naming a custom group ----------


def test_a_name_colliding_with_a_reserved_one_is_suffixed_not_replaced():
    """A user whose radio already has a group called WEATHER would
    otherwise have it absorbed into the app's managed set and its entries
    discarded as regenerable. Suffixing keeps their name recognisable.
    """
    assert unreserved_group_name("WEATHER") == "WEATHER1"
    assert unreserved_group_name("OTHER") == "OTHER1"
    assert unreserved_group_name("0-9") == "0-91"


def test_collision_checking_ignores_case():
    assert unreserved_group_name("weather") != "weather"
    assert unreserved_group_name("Weather") != "Weather"


def test_a_name_already_taken_by_another_custom_group_is_suffixed_too():
    assert unreserved_group_name("Home", taken=["Home"]) == "Home1"
    assert unreserved_group_name("WEATHER", taken=["WEATHER1"]) == "WEATHER2"


def test_an_ordinary_name_is_left_alone():
    assert unreserved_group_name("Home") == "Home"
    assert unreserved_group_name("  Local  ") == "Local"


def test_the_result_always_fits_the_radios_name_length():
    """Including when the suffix is what pushes it over -- the stem is
    trimmed to make room rather than the suffix being dropped, which
    would put the collision back.
    """
    cap = FTA_850L.max_group_name_length
    long_name = "ABCDEFGHIJKLMNOP"
    assert len(unreserved_group_name(long_name, max_length=cap)) <= cap
    crowded = unreserved_group_name(
        long_name[:cap], taken=[long_name[:cap]], max_length=cap
    )
    assert len(crowded) <= cap
    assert crowded != long_name[:cap]


def test_an_empty_name_becomes_something_typeable():
    assert unreserved_group_name("") == "GROUP"
    assert unreserved_group_name("   ") == "GROUP"


def test_all_is_never_handed_back_as_a_custom_name():
    assert unreserved_group_name(UNASSIGNED_GROUP) != UNASSIGNED_GROUP
