"""What the built executables call themselves.

The filename someone is handed, and the metadata Explorer's Details tab
and Finder's Get Info read out of it. None of this is exercised by
running the app, and all of it is wrong silently: a blank Details tab
and a camel-cased filename both just look like someone else's software.
"""

import sys
from pathlib import Path

import afp

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGING = REPO_ROOT / "packaging"
SPEC = (PACKAGING / "afp.spec").read_text(encoding="utf-8")
PY2APP = (PACKAGING / "py2app_setup.py").read_text(encoding="utf-8")

sys.path.insert(0, str(PACKAGING))
import _appinfo  # noqa: E402


# ---------- the shared source ----------


def test_appinfo_reads_the_package_rather_than_repeating_it():
    assert _appinfo.package_version() == afp.__version__
    assert _appinfo.author() == "Ryan Ramirez"


def test_the_version_tuple_is_four_integers():
    """VERSIONINFO is a fixed struct, not a string -- three parts or a
    string would raise inside PyInstaller rather than here.
    """
    parts = _appinfo.version_tuple()
    assert len(parts) == 4
    assert all(isinstance(p, int) for p in parts)
    assert ".".join(str(p) for p in parts).startswith(afp.__version__)


def test_both_builds_read_their_identity_from_the_same_place():
    """A release shipping a .exe and a .app that disagree about their own
    name or version is the failure this prevents.
    """
    assert "import _appinfo" in SPEC
    assert "import _appinfo" in PY2APP


# ---------- the filename ----------


def test_the_executable_is_named_for_the_product_not_an_identifier():
    """Spaces, not camel case: this is the filename a pilot is handed and
    double-clicks.
    """
    assert _appinfo.APP_DISPLAY_NAME == "FTA-850 Frequency Manager"
    assert " " in _appinfo.APP_DISPLAY_NAME
    assert "name=_appinfo.APP_DISPLAY_NAME" in SPEC, (
        "the Windows executable is not named for the product"
    )


def test_the_mac_bundle_is_named_for_the_product_too():
    """py2app names the .app from CFBundleName, so this is the same
    decision expressed in the other build's vocabulary.
    """
    assert '"CFBundleName": _appinfo.APP_DISPLAY_NAME' in PY2APP
    assert '"CFBundleDisplayName": _appinfo.APP_DISPLAY_NAME' in PY2APP


def test_the_space_free_name_is_kept_for_things_nobody_reads():
    """Artifact names and InternalName. Distinct from the executable, and
    distinct again from afp.paths.APP_NAME, which is a directory holding
    user data and cannot be renamed without orphaning it.
    """
    from afp.paths import APP_NAME

    assert " " not in _appinfo.APP_FILE_NAME
    assert APP_NAME == "AirportFreqProgrammer", "the data directory moved"


# ---------- the Windows version resource ----------


def test_the_exe_declares_a_version_resource():
    """Without one, Explorer's Details tab is blank -- no product, no
    version, no author -- on a downloaded, unsigned binary.
    """
    assert "version=VERSION_RESOURCE" in SPEC
    assert "VSVersionInfo(" in SPEC


def test_the_version_resource_carries_the_fields_details_shows():
    for field in (
        "CompanyName",
        "FileDescription",
        "FileVersion",
        "InternalName",
        "OriginalFilename",
        "ProductName",
        "ProductVersion",
    ):
        assert f'StringStruct("{field}"' in SPEC, f"{field} missing from the version resource"


def test_the_version_resource_holds_no_literals_of_its_own():
    """Every string in it comes from _appinfo, so the resource cannot
    claim a different version or name than the app reports.
    """
    resource = SPEC.split("VERSION_RESOURCE = ", 1)[1].split("\n)", 1)[0]
    for line in resource.splitlines():
        if "StringStruct(" not in line:
            continue
        value = line.split(",", 1)[1]
        assert "_appinfo." in value, f"hard-coded value in the version resource: {line.strip()}"


def test_the_original_filename_matches_what_is_actually_built():
    """OriginalFilename is what the resource claims the file was called
    when built; naming the .exe one thing and declaring another is the
    sort of mismatch a scanner notices.
    """
    assert 'f"{_appinfo.APP_DISPLAY_NAME}.exe"' in SPEC
