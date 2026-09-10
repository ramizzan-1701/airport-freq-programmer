"""The About screen: its once-only first run, the build facts it shows,
and the fact that its text is still the README's.
"""

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import afp
from afp import about
from afp.web import create_app
from afp.web.app import STATIC_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def empty_client(tmp_path) -> TestClient:
    """A fresh install: no cache dir contents, nothing acknowledged."""
    return TestClient(create_app(cache_dir=tmp_path))


@pytest.fixture
def readme() -> str:
    return (REPO_ROOT / "README.md").read_text(encoding="utf-8")


def _collapse(text: str) -> str:
    """README paragraphs are hard-wrapped at ~90 chars; the About screen
    renders them as single strings. Compare on the words, not the line
    breaks.
    """
    return " ".join(text.split())


# ---------- shown once, then remembered ----------


def test_a_fresh_install_has_not_acknowledged_the_about_screen(empty_client):
    """What makes the screen appear ahead of the load screen the first
    time the app is ever opened.
    """
    assert empty_client.get("/api/status").json()["about_acknowledged"] is False


def test_acknowledging_sticks(empty_client):
    assert empty_client.post("/api/about/acknowledge").json()["about_acknowledged"] is True
    assert empty_client.get("/api/status").json()["about_acknowledged"] is True


def test_acknowledgement_survives_a_restart(tmp_path):
    """Persisted beside the group-setup flag rather than in the browser,
    so neither restarting the app nor clearing the webview's own storage
    brings the screen back.
    """
    first = TestClient(create_app(cache_dir=tmp_path))
    first.post("/api/about/acknowledge")

    second = TestClient(create_app(cache_dir=tmp_path))
    assert second.get("/api/status").json()["about_acknowledged"] is True


def test_acknowledging_twice_is_harmless(empty_client):
    for _ in range(3):
        assert empty_client.post("/api/about/acknowledge").json()["about_acknowledged"] is True


def test_about_content_is_available_before_any_cycle_is_loaded(empty_client):
    """It is the first thing shown on a fresh install, when there is no
    NASR data at all -- so unlike most endpoints it must not require a
    loaded cycle.
    """
    assert empty_client.get("/api/about").status_code == 200


# ---------- build facts ----------


def test_about_reports_the_running_version_and_release_date(empty_client):
    body = empty_client.get("/api/about").json()
    assert body["app_version"] == afp.__version__
    assert body["release_date"] == afp.RELEASE_DATE


def test_release_date_is_a_real_date():
    """It renders straight onto the screen, so a typo would just ship."""
    date.fromisoformat(afp.RELEASE_DATE)


def test_pyproject_takes_its_version_from_the_package():
    """One number to change. setuptools reads afp.__version__ through its
    dynamic attr, so the packaged version cannot drift from the one the
    About screen shows.
    """
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in pyproject
    assert 'version = {attr = "afp.__version__"}' in pyproject


def test_the_creator_and_contact_address_are_shown(empty_client):
    body = empty_client.get("/api/about").json()
    assert body["author"] == "Ryan Ramirez"
    assert "@" in body["contact_email"]


# ---------- intro links ----------


def test_every_intro_link_phrase_actually_appears_in_the_intro():
    """A link is described by the phrase it wraps, so a typo in that
    phrase doesn't error -- it just silently renders no link at all.
    """
    joined = " ".join(about.INTRO_PARAGRAPHS)
    for phrase, _url in about.INTRO_LINKS:
        assert phrase in joined, f"no intro paragraph contains {phrase!r}, so it would not link"


def test_intro_links_are_absolute_urls():
    for _phrase, url in about.INTRO_LINKS:
        assert url.startswith("https://"), url


def test_the_yaesu_software_link_is_present(empty_client):
    body = empty_client.get("/api/about").json()
    links = {link["phrase"]: link["url"] for link in body["intro_links"]}
    assert "YCE-46 programming software" in links
    assert "yaesu.com" in links["YCE-46 programming software"]


def test_the_desktop_app_sends_external_links_to_the_system_browser():
    """The About link is the app's only outbound link. Opened inside the
    webview it would be a dead end -- no address bar, no back button.
    """
    import webview

    import afp.desktop  # noqa: F401  (importing applies the setting)

    assert webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] is True


# ---------- page 2: the workflow ----------


def test_the_workflow_is_served_in_order(empty_client):
    body = empty_client.get("/api/about").json()
    assert body["workflow_intro"].startswith("Proper workflow")
    assert body["workflow_steps"] == list(about.WORKFLOW_STEPS)
    assert len(body["workflow_steps"]) == 8


def test_every_workflow_steps_bold_markers_are_balanced():
    """The frontend renders these by splitting on "**" and bolding the
    odd-numbered pieces. An unpaired marker doesn't error -- it silently
    bolds the rest of the step.
    """
    for i, step in enumerate(about.WORKFLOW_STEPS, start=1):
        assert step.count("**") % 2 == 0, f"unbalanced bold markers in step {i}: {step!r}"


def test_the_workflow_names_buttons_that_actually_exist():
    """Steps 2 and 6 tell the reader to go and press something. If those
    buttons get renamed again, the instructions become a scavenger hunt
    -- so hold them against the markup rather than trusting memory.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    labels = {"Groups setup help", "Generate FTA-850 XML"}
    for label in labels:
        assert f">{label}<" in html, f"{label!r} is no longer a button in index.html"

    steps = " ".join(about.WORKFLOW_STEPS)
    for label in labels:
        assert label.upper() in steps.upper(), (
            f"the workflow no longer refers to the {label!r} button by its real name"
        )


def test_the_workflow_uses_the_arrow_glyph_not_an_ascii_arrow():
    """The app writes menu paths with the arrow character everywhere
    else; a stray "->" would read as a different convention.
    """
    for step in about.WORKFLOW_STEPS:
        assert "->" not in step, step


# ---------- the text is still the README's ----------
#
# The About screen exists to say what the README says. These fail if
# either side is edited alone.


def test_the_intro_is_the_readmes_intro(readme):
    body = readme.split("# Airport Frequency Programmer", 1)[1].split("## What it does", 1)[0]
    paragraphs = [_collapse(p) for p in body.strip().split("\n\n") if p.strip()]

    assert len(paragraphs) == 2, "README intro is no longer two paragraphs"
    # Bold markers are markdown, not content -- the screen renders the
    # radio name as plain text.
    expected = [p.replace("**", "") for p in paragraphs]
    assert list(about.INTRO_PARAGRAPHS) == expected, (
        "README intro and afp.about.INTRO_PARAGRAPHS have drifted apart"
    )


def test_the_feature_list_is_the_readmes_feature_list(readme):
    body = readme.split("## What it does", 1)[1].split("## Running it", 1)[0]
    bullets = [_collapse(b) for b in re.findall(r"^- (.+?)(?=\n- |\Z)", body.strip(), re.DOTALL | re.MULTILINE)]

    assert len(bullets) == 5, "README's What it does list changed length"

    expected = []
    for bullet in bullets:
        # Each bullet is "**Lead** rest" or "**Lead.** rest".
        match = re.match(r"\*\*(.+?)\*\*\s*(.+)", bullet, re.DOTALL)
        assert match, f"README bullet is not in **lead** rest form: {bullet!r}"
        expected.append((match.group(1), _collapse(match.group(2))))

    assert [tuple(f) for f in about.FEATURES] == expected, (
        "README's What it does list and afp.about.FEATURES have drifted apart"
    )
