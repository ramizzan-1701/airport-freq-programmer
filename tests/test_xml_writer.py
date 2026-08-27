import xml.etree.ElementTree as ET

import pytest

from afp.export.fta850l import FTA_850L
from afp.export.profile import ExportProfile
from afp.export.xml_writer import ExportValidationError, build_xml, validate
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


def test_duplicate_tag_names_are_rejected():
    profile = ExportProfile(name="Test", max_tag_length=14, max_entries=400)
    entries = [
        Entry(tag_name="SNS-CTAF", freq_mhz=122.8, group="G", lat=1.0, lon=-1.0),
        Entry(tag_name="SNS-CTAF", freq_mhz=122.9, group="G", lat=1.0, lon=-1.0),
    ]
    problems = validate(entries, profile)
    assert any("duplicate" in p.lower() for p in problems)
    with pytest.raises(ExportValidationError):
        build_xml(entries, profile)


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
