import zipfile

import pytest

from afp.nasr.downloader import extract_csv


def _make_zip(path, members: dict[str, str]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


def test_extract_csv_finds_file_regardless_of_internal_directory(tmp_path):
    zip_path = _make_zip(
        tmp_path / "APT_CSV.zip",
        {
            "56DySubscription/CSV_Data/APT_BASE.csv": "ARPT_ID\nSNS\n",
            "56DySubscription/CSV_Data/APT_RWY.csv": "ignored\n",
        },
    )
    dest_dir = tmp_path / "out"

    result = extract_csv(zip_path, "APT_BASE.csv", dest_dir)

    assert result == dest_dir / "APT_BASE.csv"
    assert result.read_text() == "ARPT_ID\nSNS\n"


def test_extract_csv_is_case_insensitive(tmp_path):
    zip_path = _make_zip(tmp_path / "FRQ_CSV.zip", {"frq.CSV": "SERVICED_FACILITY\n"})
    dest_dir = tmp_path / "out"

    result = extract_csv(zip_path, "FRQ.csv", dest_dir)

    assert result.exists()


def test_extract_csv_raises_clearly_when_missing(tmp_path):
    zip_path = _make_zip(tmp_path / "ILS_CSV.zip", {"OTHER_FILE.csv": "x\n"})
    dest_dir = tmp_path / "out"

    with pytest.raises(FileNotFoundError, match="ILS_BASE.csv"):
        extract_csv(zip_path, "ILS_BASE.csv", dest_dir)
