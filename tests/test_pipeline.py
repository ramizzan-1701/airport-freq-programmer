import xml.etree.ElementTree as ET

import pytest

from afp.export.fta850l import FTA_850L
from afp.export.xml_writer import ExportValidationError
from afp.pipeline import generate_xml


def test_end_to_end_smart_mode_generates_valid_importable_xml(faa_adapter):
    xml_bytes = generate_xml(faa_adapter, FTA_850L, mode="smart")

    assert xml_bytes.startswith(b"\xef\xbb\xbf")
    text = xml_bytes.decode("utf-8-sig")
    root = ET.fromstring(text)

    tags = {
        el.text
        for el in root.findall("./MEMORY_BOOK/MEMORY_BOOK_GROUP/TAG_NAME")
    }
    assert tags == {
        "SNS-ATIS", "SNS-CT", "SNS-VOR", "SNS-LOC31", "WVI-CTAF", "WVI-AWOS",
        "PXN-VOR",  # standalone VORTAC, no matching airport row
    }

    # private-use airport in the fixture set must not appear
    assert not any(t.startswith("PVT1") for t in tags)


def test_end_to_end_raw_mode_surfaces_ils_collision(faa_adapter):
    with pytest.raises(ExportValidationError):
        generate_xml(faa_adapter, FTA_850L, mode="raw")
