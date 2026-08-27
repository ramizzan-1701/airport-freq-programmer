import pytest

from afp.export.fta850l import FTA_850L
from afp.export.xml_reader import XmlParseError, parse_memory_book_xml
from afp.export.xml_writer import build_xml
from afp.selection import Entry

SNS_ATIS = Entry(
    tag_name="SNS-ATIS",
    freq_mhz=124.850,
    group="Local",
    lat=36.662783,
    lon=-121.606367,
)

SOUTH_WEST_ENTRY = Entry(tag_name="X-CTAF", freq_mhz=122.9, group="G", lat=-33.5, lon=151.25)


def test_round_trips_a_single_entry():
    xml_bytes = build_xml([SNS_ATIS], FTA_850L)
    parsed = parse_memory_book_xml(xml_bytes)
    assert len(parsed) == 1
    entry = parsed[0]
    assert entry.tag_name == "SNS-ATIS"
    assert entry.freq_mhz == pytest.approx(124.850)
    assert entry.group == "Local"
    # DD°MM.mmm rounds to 3 decimal minutes on the way out -- not bit-exact.
    assert entry.lat == pytest.approx(36.662783, abs=1e-4)
    assert entry.lon == pytest.approx(-121.606367, abs=1e-4)
    assert entry.scan_memory == "Off"
    assert entry.shift == "Off"


def test_round_trips_southern_western_hemisphere():
    xml_bytes = build_xml([SOUTH_WEST_ENTRY], FTA_850L)
    parsed = parse_memory_book_xml(xml_bytes)
    assert parsed[0].lat == pytest.approx(-33.5, abs=1e-4)
    assert parsed[0].lon == pytest.approx(151.25, abs=1e-4)


def test_round_trips_multiple_entries_preserving_order():
    entries = [
        Entry(tag_name="AAA-CTAF", freq_mhz=122.8, group="0-9", lat=1.0, lon=-1.0),
        Entry(tag_name="BBB-CTAF", freq_mhz=122.9, group="PERSONAL", lat=2.0, lon=-2.0),
    ]
    parsed = parse_memory_book_xml(build_xml(entries, FTA_850L))
    assert [e.tag_name for e in parsed] == ["AAA-CTAF", "BBB-CTAF"]
    assert [e.group for e in parsed] == ["0-9", "PERSONAL"]


def test_malformed_xml_raises_xml_parse_error():
    with pytest.raises(XmlParseError):
        parse_memory_book_xml(b"not xml at all")


def test_missing_memory_book_element_raises():
    with pytest.raises(XmlParseError):
        parse_memory_book_xml(b'<?xml version="1.0"?><FILE></FILE>')


def test_missing_required_field_raises_not_crashes():
    xml = (
        b'<?xml version="1.0"?><FILE><MEMORY_BOOK><MEMORY_BOOK_GROUP>'
        b"<TAG_NAME>X-CTAF</TAG_NAME><FREQUENCY>122.800</FREQUENCY>"
        b"</MEMORY_BOOK_GROUP></MEMORY_BOOK></FILE>"
    )
    with pytest.raises(XmlParseError):
        parse_memory_book_xml(xml)


def test_no_memory_book_group_elements_returns_empty_list():
    xml = b'<?xml version="1.0"?><FILE><MEMORY_BOOK></MEMORY_BOOK></FILE>'
    assert parse_memory_book_xml(xml) == []
