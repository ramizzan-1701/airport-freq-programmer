"""The About screen: its once-only first run, the build facts it shows,
and the internal consistency of its copy.
"""

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


def test_the_version_is_written_down_in_exactly_one_place():
    """The number the About screen shows, the wheel metadata and the
    macOS bundle all have to agree, and the only way that stays true is
    if there is one literal to change.

    This caught real drift twice: py2app's plist carried its own copy and
    had to be edited alongside, so Finder's Get Info reported one version
    while the About screen reported another for as long as it took to
    notice.
    """
    version = afp.__version__
    written = []
    candidates = sorted((REPO_ROOT / "src").rglob("*.py"))
    candidates += sorted((REPO_ROOT / "packaging").rglob("*.py"))
    candidates += sorted((REPO_ROOT / "packaging").rglob("*.spec"))
    for path in candidates:
        if f'"{version}"' in path.read_text(encoding="utf-8"):
            written.append(path.relative_to(REPO_ROOT).as_posix())

    assert written == ["src/afp/__init__.py"], (
        f"{version!r} is hard-coded in more than one place: {written}"
    )


def test_the_mac_bundle_takes_its_version_from_the_package():
    setup_py = (REPO_ROOT / "packaging" / "py2app_setup.py").read_text(encoding="utf-8")
    assert '"CFBundleVersion": VERSION' in setup_py
    assert '"CFBundleShortVersionString": VERSION' in setup_py
    assert "VERSION = _appinfo.package_version()" in setup_py

    # Parsed, not imported: these scripts run before anything is
    # guaranteed importable, and importing the package to read one string
    # would run it.
    appinfo = (REPO_ROOT / "packaging" / "_appinfo.py").read_text(encoding="utf-8")
    assert "ast.parse" in appinfo


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


def test_the_workflow_intro_carries_a_link_phrase_too():
    """Page 2's lead line names the programming software as well, and is
    rendered through the same linker. It is a separate string from the
    intro paragraphs, so rewording it is the one edit that would leave
    page 1 linked and page 2 not -- silently, since an absent phrase just
    renders as plain text.
    """
    assert any(phrase in about.WORKFLOW_INTRO for phrase, _url in about.INTRO_LINKS), (
        "the workflow intro no longer contains any linkable phrase"
    )


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
    assert len(body["workflow_steps"]) == 7


def test_every_workflow_steps_bold_markers_are_balanced():
    """The frontend renders these by splitting on "**" and bolding the
    odd-numbered pieces. An unpaired marker doesn't error -- it silently
    bolds the rest of the step.
    """
    for i, step in enumerate(about.WORKFLOW_STEPS, start=1):
        assert step.count("**") % 2 == 0, f"unbalanced bold markers in step {i}: {step!r}"


def test_the_workflow_names_buttons_that_actually_exist():
    """A step telling the reader to press something becomes a scavenger
    hunt if that button is renamed, so hold the names against the markup
    rather than trusting memory.
    """
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    labels = {"Generate FTA-850 XML"}
    for label in labels:
        assert f">{label}<" in html, f"{label!r} is no longer a button in index.html"

    steps = " ".join(about.WORKFLOW_STEPS)
    for label in labels:
        assert label.upper() in steps.upper(), (
            f"the workflow no longer refers to the {label!r} button by its real name"
        )


def test_every_workflow_action_phrase_appears_in_a_step():
    """An action is found by the phrase it wraps, so a phrase that no
    longer occurs renders no link and raises nothing.
    """
    steps = " ".join(about.WORKFLOW_STEPS)
    for phrase, _action in about.WORKFLOW_ACTIONS:
        assert phrase in steps, f"no step contains {phrase!r}, so it would not link"


def test_workflow_action_phrases_are_not_inside_a_bold_run():
    """The renderer only scans unmarked runs for actions -- emphasis
    means "a menu in YCE-46", so a phrase buried in one would never be
    turned into a link.
    """
    for phrase, _action in about.WORKFLOW_ACTIONS:
        for step in about.WORKFLOW_STEPS:
            if phrase not in step:
                continue
            # Even indices are the runs outside the ** markers.
            unmarked = step.split("**")[::2]
            assert any(phrase in part for part in unmarked), (
                f"{phrase!r} only appears inside a bold run in: {step!r}"
            )


def test_the_frontend_knows_every_action_the_copy_names():
    """about.py names an action, app.js maps it to a function. A name
    with no handler silently renders as plain text.
    """
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    handlers = app_js.split("const ABOUT_ACTIONS = {", 1)[1].split("};", 1)[0]
    for _phrase, action in about.WORKFLOW_ACTIONS:
        assert f'"{action}"' in handlers, f"app.js has no handler for the {action!r} action"


def test_the_workflow_actions_reach_the_frontend(empty_client):
    """Empty since the app began declaring the group names in the export
    itself: the one action opened the group-setup instructions, and there
    is no setup left to do. The plumbing is still exercised so it does
    not rot before the copy wants it again.
    """
    body = empty_client.get("/api/about").json()
    assert body["workflow_actions"] == []


def test_the_about_modal_is_a_fixed_height():
    """The two pages differ by nearly 200px of content. Sized to fit,
    the box grew and shrank on a page turn and took the header and the
    footer's arrows with it -- the button moved out from under the
    pointer that had just clicked it.
    """
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "#about-modal .modal-box { height:" in css


def test_the_workflow_uses_the_arrow_glyph_not_an_ascii_arrow():
    """The app writes menu paths with the arrow character everywhere
    else; a stray "->" would read as a different convention.
    """
    for step in about.WORKFLOW_STEPS:
        assert "->" not in step, step
