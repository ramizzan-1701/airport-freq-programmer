"""The README.txt shipped beside the downloaded executable.

Generated from afp.about at build time rather than written by hand, so
the interesting failures are all silent ones: a section that renders
empty because a constant was renamed, copy that arrives as mojibake, or
a platform's warning going to the wrong download.

Nothing here runs during a build failure -- CI writes this file and
uploads it without reading it -- so these are the only eyes on it.
"""

import sys
from pathlib import Path

import pytest

import afp
from afp import about

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "packaging"))
import make_readme  # noqa: E402


@pytest.fixture(params=["windows", "macos"])
def readme(request) -> str:
    return make_readme.build(request.param)


def flat(text: str) -> str:
    """Whitespace collapsed to single spaces.

    The readme is wrapped to 78 columns and its lists are indented, so
    no sentence longer than a line survives as a contiguous string.
    Comparing flattened text asks the question actually worth asking --
    are these words present, in this order -- without pinning where the
    wrapper happened to break them.
    """
    return " ".join(text.split())


# ---------- it says what the About screen says ----------


def test_the_readme_carries_the_intro_copy(readme):
    """The whole point of generating it: someone reading the download
    gets the same description as someone reading the About screen.
    """
    for paragraph in about.INTRO_PARAGRAPHS:
        assert flat(make_readme.ascii_fold(paragraph)) in flat(readme)


def test_the_readme_carries_every_feature(readme):
    for lead, rest in about.FEATURES:
        assert flat(make_readme.ascii_fold(f"{lead} {rest}")) in flat(readme)


def test_the_readme_carries_every_workflow_step(readme):
    for step in about.WORKFLOW_STEPS:
        wanted = flat(make_readme.ascii_fold(make_readme.strip_bold(step)))
        assert wanted in flat(readme), f"workflow step missing: {wanted!r}"


def test_the_readme_uses_the_same_section_headings_as_the_about_screen(readme):
    for title in ("WHAT IT DOES", "HOW IT DOES IT", "WORKFLOW"):
        assert title in readme


def test_the_readme_states_the_running_version(readme):
    assert afp.__version__ in readme
    assert afp.RELEASE_DATE in readme
    assert about.AUTHOR in readme
    assert about.CONTACT_EMAIL in readme


def test_the_bold_markers_do_not_survive(readme):
    """They drive the screen's renderer. In plain text they are noise."""
    assert "**" not in readme


def test_the_link_urls_are_spelled_out(readme):
    """Plain text cannot hide a URL behind a phrase, so a reader who
    wants the programming software has to be able to read the address.
    """
    for _phrase, url in about.INTRO_LINKS:
        assert url in readme


# ---------- it survives being opened by anything ----------


def test_the_readme_is_pure_ascii(readme):
    """The copy uses arrows and em dashes. This file is opened by
    whatever the reader has, on a machine whose encoding defaults are
    not knowable from here -- so it is folded rather than trusted.
    """
    assert readme.isascii(), [c for c in readme if ord(c) > 127]


def test_menu_arrows_become_a_plain_greater_than(readme):
    assert "Transfer > Read From Radio" in readme


def test_an_unmapped_character_fails_the_build(monkeypatch):
    """The fold is a fixed table, so copy introducing a new glyph would
    otherwise ship as whatever the reader's editor guessed. Better to
    break the build than to hear about it from someone holding the file.
    """
    monkeypatch.setattr(
        about, "AUTHOR", "Ryan ★ Ramirez", raising=True
    )
    monkeypatch.setattr(
        make_readme._appinfo, "author", lambda: "Ryan ★ Ramirez"
    )
    with pytest.raises(SystemExit, match="U\\+2605"):
        make_readme.build("windows")


# ---------- the part the About screen has no reason to carry ----------


def test_each_platform_explains_its_own_first_run_warning():
    """An unsigned download warns on first open, differently on each
    platform, and a reader who cannot get past it never sees the app at
    all. This is the reason to ship a readme rather than rely on About.
    """
    windows = make_readme.build("windows")
    macos = make_readme.build("macos")

    assert "SmartScreen" in windows and "Run anyway" in windows
    assert "Gatekeeper" in macos and "right-click" in macos

    # Not each other's: following the wrong instructions finds nothing.
    assert "Gatekeeper" not in windows
    assert "SmartScreen" not in macos


def test_each_platform_names_its_own_data_directory():
    assert "LOCALAPPDATA" in make_readme.build("windows")
    assert "Library/Application Support" in make_readme.build("macos")


def test_the_data_directory_matches_the_one_the_app_actually_uses():
    """Named so someone can find their downloaded cycles, or clear them.
    Pointing at the wrong folder is worse than saying nothing.
    """
    from afp.paths import APP_NAME

    for platform in ("windows", "macos"):
        assert APP_NAME in make_readme.build(platform)


def test_nothing_wraps_past_the_intended_width(readme):
    """Wrapped here rather than by the reader's window, so a line that
    escaped the wrapper would be the one that looks broken.

    The indented URL is exempt: breaking a URL across lines makes it
    uncopyable, which is worse than one long line.
    """
    urls = {url for _phrase, url in about.INTRO_LINKS}
    for line in readme.splitlines():
        if any(url in line for url in urls):
            continue
        assert len(line) <= make_readme.WIDTH, f"{len(line)} cols: {line!r}"
