import xml.etree.ElementTree as ET

import pytest

from afp.export.fta850l import FTA_850L
from afp.export.profile import ExportProfile
from afp.export.xml_writer import (
    ExportValidationError,
    build_xml,
    ungrouped_warning,
    validate,
)
from afp.selection import Entry

SNS_ATIS = Entry(
    tag_name="SNS-ATIS",
    freq_mhz=124.850,
    group="Local",
    lat=36.662783,
    lon=-121.606367,
)


def test_bom_precedes_xml_declaration():
    xml_bytes = build_xml([SNS_ATIS], FTA_850L)
    assert xml_bytes.startswith(b"\xef\xbb\xbf<?xml")


def test_matches_known_good_sns_atis_example():
    xml_bytes = build_xml([SNS_ATIS], FTA_850L)
    text = xml_bytes.decode("utf-8-sig")

    assert "<TAG_NAME>SNS-ATIS</TAG_NAME>" in text
    assert "<FREQUENCY>124.850</FREQUENCY>" in text
    assert "<LAT>36°39.767</LAT>" in text
    assert "<NS>N</NS>" in text
    assert "<LON>121°36.382</LON>" in text
    assert "<EW>W</EW>" in text
    assert "<SCAN_MEMORY>Off</SCAN_MEMORY>" in text
    assert "<SHIFT>Off</SHIFT>" in text


def test_frequency_always_has_three_decimals():
    entry = Entry(tag_name="X-CTAF", freq_mhz=122.9, group="G", lat=1.0, lon=-1.0)
    xml_bytes = build_xml([entry], FTA_850L)
    text = xml_bytes.decode("utf-8-sig")
    assert "<FREQUENCY>122.900</FREQUENCY>" in text
    assert "122.9<" not in text


def test_output_is_well_formed_xml():
    xml_bytes = build_xml([SNS_ATIS], FTA_850L)
    text = xml_bytes.decode("utf-8-sig")
    root = ET.fromstring(text)
    assert root.tag == "FILE"
    assert root.find("./MEMORY_BOOK/MEMORY_BOOK_GROUP/TAG_NAME").text == "SNS-ATIS"


def test_tag_name_over_max_length_is_rejected():
    profile = ExportProfile(name="Test", max_tag_length=14, max_entries=400)
    entry = Entry(tag_name="WAY-TOO-LONG-TAG-NAME", freq_mhz=122.9, group="G", lat=1.0, lon=-1.0)
    problems = validate([entry], profile)
    assert any("WAY-TOO-LONG-TAG-NAME" in p for p in problems)
    with pytest.raises(ExportValidationError):
        build_xml([entry], profile)


def test_duplicate_tag_names_within_one_group_are_rejected():
    profile = ExportProfile(name="Test", max_tag_length=14, max_entries=400)
    entries = [
        Entry(tag_name="SNS-CTAF", freq_mhz=122.8, group="G", lat=1.0, lon=-1.0),
        Entry(tag_name="SNS-CTAF", freq_mhz=122.9, group="G", lat=1.0, lon=-1.0),
    ]
    problems = validate(entries, profile)
    assert any("duplicate" in p.lower() for p in problems)
    with pytest.raises(ExportValidationError):
        build_xml(entries, profile)


def test_the_same_tag_in_two_different_groups_is_allowed():
    """A custom group is frequently a shortlist of frequencies the user
    already has in a generated group -- the whole point of spec §5 step 9
    not deduplicating custom entries. Rejecting that would make the file
    ungeneratable for anyone who keeps one.
    """
    profile = ExportProfile(name="Test", max_tag_length=14, max_entries=400)
    entries = [
        Entry(tag_name="SNS-CTAF", freq_mhz=122.8, group="P-T", lat=1.0, lon=-1.0),
        Entry(tag_name="SNS-CTAF", freq_mhz=122.8, group="Local", lat=1.0, lon=-1.0),
    ]
    assert validate(entries, profile) == []
    xml = build_xml(entries, profile)
    assert xml.count(b"<TAG_NAME>SNS-CTAF</TAG_NAME>") == 2
    assert b"<GROUP>P-T</GROUP>" in xml
    assert b"<GROUP>Local</GROUP>" in xml


def test_entry_count_over_cap_is_rejected():
    profile = ExportProfile(name="Test", max_tag_length=14, max_entries=2)
    entries = [
        Entry(tag_name=f"AB{i}-CTAF"[:14], freq_mhz=122.8, group="G", lat=1.0, lon=-1.0)
        for i in range(3)
    ]
    problems = validate(entries, profile)
    assert any("exceeds" in p and "cap" in p for p in problems)
    with pytest.raises(ExportValidationError):
        build_xml(entries, profile)


def test_entry_count_at_cap_is_allowed():
    profile = ExportProfile(name="Test", max_tag_length=14, max_entries=2)
    entries = [
        Entry(tag_name=f"AB{i}-CTAF"[:14], freq_mhz=122.8, group="G", lat=1.0, lon=-1.0)
        for i in range(2)
    ]
    assert validate(entries, profile) == []
    build_xml(entries, profile)  # should not raise


def test_southern_western_hemisphere_formatting():
    entry = Entry(tag_name="X-CTAF", freq_mhz=122.9, group="G", lat=-33.5, lon=151.25)
    xml_bytes = build_xml([entry], FTA_850L)
    text = xml_bytes.decode("utf-8-sig")
    assert "<NS>S</NS>" in text
    assert "<EW>E</EW>" in text
    assert "<LAT>33°30.000</LAT>" in text
    assert "<LON>151°15.000</LON>" in text


# ---------- the <GROUPS> block ----------
#
# The radio's memory-group names, defined by the import file itself. This
# is what removed the old requirement that a user rename six groups by
# hand in YCE-46 before every import -- and it means a mistake here
# renames groups on someone's radio rather than merely failing.


def _entry(tag_name: str, group: str) -> Entry:
    return Entry(tag_name=tag_name, freq_mhz=122.8, group=group, lat=1.0, lon=-1.0)


def _groups_block(xml_bytes: bytes) -> list[tuple[int, str]]:
    root = ET.fromstring(xml_bytes.decode("utf-8-sig"))
    block = root.find("GROUPS")
    if block is None:
        return []
    return [(int(g.get("index")), g.text) for g in block.findall("GROUP")]


def test_no_groups_block_is_written_when_none_is_given():
    """Exactly what every earlier version produced. Callers that have no
    opinion about group names must not start renaming groups on the
    radio just because the writer learned how.
    """
    xml_bytes = build_xml([SNS_ATIS], FTA_850L)
    assert b"<GROUPS>" not in xml_bytes


def test_the_groups_block_carries_each_slot_and_name():
    xml_bytes = build_xml([_entry("A", "Home")], FTA_850L, {0: "0-9", 6: "Home"})
    assert _groups_block(xml_bytes) == [(0, "0-9"), (6, "Home")]


def test_the_groups_block_is_ordered_by_slot():
    """Written from a dict, which has insertion order, not slot order."""
    xml_bytes = build_xml([_entry("A", "Z")], FTA_850L, {8: "Z", 0: "A", 4: "M"})
    assert [slot for slot, _name in _groups_block(xml_bytes)] == [0, 4, 8]


def test_the_groups_block_precedes_the_memory_book():
    """The groups have to exist before the entries that name them."""
    xml = build_xml([_entry("A", "Home")], FTA_850L, {0: "Home"}).decode("utf-8-sig")
    assert xml.index("<GROUPS>") < xml.index("<MEMORY_BOOK>")


def test_a_slot_left_out_stays_out():
    """Left out of <GROUPS> is what leaves a slot alone on the radio.
    Writing all nine every time would rename groups the app does not
    manage.
    """
    block = _groups_block(build_xml([_entry("A", "Home")], FTA_850L, {0: "Home"}))
    assert [slot for slot, _ in block] == [0]


def test_all_is_refused_as_a_defined_group():
    """The radio's pseudo-group for unassigned entries, not a real one.
    Defining it would claim a slot for something that is not a group.
    """
    with pytest.raises(ExportValidationError, match="ALL"):
        build_xml([_entry("A", "ALL")], FTA_850L, {0: "ALL"})


def test_an_entry_may_still_name_all():
    """The other side of the same rule: ALL is a perfectly good value for
    an entry's GROUP -- it is how an entry stays unassigned.
    """
    xml_bytes = build_xml([_entry("A", "ALL")], FTA_850L, {0: "0-9"})
    assert b"<GROUP>ALL</GROUP>" in xml_bytes


# ---------- group validation ----------


def test_a_group_name_over_the_length_cap_is_refused():
    problems = validate([], FTA_850L, {0: "ELEVENCHARS"})
    assert any("11 chars" in p for p in problems)


def test_a_name_exactly_at_the_cap_is_allowed():
    assert validate([], FTA_850L, {0: "TENCHARSXX"}) == []


def test_more_groups_than_the_radio_has_slots_is_refused():
    slots = {i: f"G{i}" for i in range(FTA_850L.max_groups + 1)}
    problems = validate([], FTA_850L, slots)
    assert any("exceeds" in p and "cap of 9" in p for p in problems)


def test_exactly_nine_groups_is_allowed():
    assert validate([], FTA_850L, {i: f"G{i}" for i in range(9)}) == []


def test_two_slots_sharing_a_name_is_refused():
    """Leaves the radio no way to tell which slot an entry meant."""
    problems = validate([], FTA_850L, {0: "Home", 6: "Home"})
    assert any("defined on slots 0, 6" in p for p in problems)


def test_duplicate_names_are_caught_regardless_of_case():
    problems = validate([], FTA_850L, {0: "Home", 6: "HOME"})
    assert any("defined on slots" in p for p in problems)


def test_an_empty_group_name_is_refused():
    problems = validate([], FTA_850L, {6: "   "})
    assert any("empty name" in p for p in problems)


def test_group_problems_do_not_mask_entry_problems():
    """Both are reported together, so one export attempt surfaces
    everything wrong rather than one thing at a time.
    """
    long_tag = _entry("X" * (FTA_850L.max_tag_length + 1), "Home")
    problems = validate([long_tag], FTA_850L, {0: "ELEVENCHARS"})
    assert len(problems) == 2


# ---------- the undefined-group warning ----------


def test_an_entry_naming_an_undefined_group_does_not_block_the_export():
    """It still imports; it just lands unassigned. Refusing would be
    refusing the user's export over something the radio handles.
    """
    xml_bytes = build_xml([_entry("A", "Nowhere")], FTA_850L, {0: "0-9"})
    assert b"<GROUP>Nowhere</GROUP>" in xml_bytes


def test_the_warning_counts_entries_that_will_land_unassigned():
    entries = [_entry("A", "Nowhere"), _entry("B", "Nowhere"), _entry("C", "0-9")]
    warning = ungrouped_warning(entries, {0: "0-9"})
    assert "2 entries" in warning and "Nowhere" in warning


def test_one_stray_entry_reads_as_singular():
    warning = ungrouped_warning([_entry("A", "Nowhere")], {0: "0-9"})
    assert "1 entry will land" in warning


def test_entries_already_in_all_are_not_warned_about():
    """They are unassigned on purpose, not by accident."""
    assert ungrouped_warning([_entry("A", "ALL")], {0: "0-9"}) is None


def test_no_warning_when_every_group_is_defined():
    assert ungrouped_warning([_entry("A", "0-9")], {0: "0-9"}) is None


def test_the_warning_does_not_list_every_group_by_name():
    """A user with many stray groups gets a readable sentence, not a
    wall of names.
    """
    entries = [_entry(f"E{i}", f"G{i}") for i in range(10)]
    warning = ungrouped_warning(entries, {0: "0-9"})
    assert "and 7 more" in warning
