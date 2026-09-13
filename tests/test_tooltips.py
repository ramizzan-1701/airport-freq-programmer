"""The two tooltip mechanisms and the line between them.

Hover targets are passive -- a breakdown chip whose short label hides the
full one, where the tip *is* the data. Click targets are the help icons,
which explain a control you are about to click for another reason.

None of this can be executed here, so these read the source for the
invariants that go wrong silently.
"""

from afp.web.app import STATIC_DIR

APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
STYLE_CSS = (STATIC_DIR / "style.css").read_text(encoding="utf-8")


def function_body(name: str) -> str:
    """Source of a top-level function, up to its closing brace."""
    start = APP_JS.index(f"function {name}(")
    return APP_JS[start : APP_JS.index("\n}", start)]


# ---------- the click icons ----------


def test_the_help_icon_is_a_real_button():
    """A styled span would be neither tabbable nor announced."""
    body = function_body("helpIcon")
    assert 'createElement("button")' in body
    assert 'button.type = "button"' in body, "inside a form-ish row, a bare button submits"
    assert "aria-label" in body
    assert "aria-expanded" in body


def test_the_help_icon_does_not_toggle_the_accordion():
    """It sits inside the group header, whose own click toggles the
    group and whose Enter/Space does the same. Left to bubble, asking
    what a filter does would collapse the filter.
    """
    body = function_body("helpIcon")
    click = body.split('addEventListener("click"', 1)[1].split("});", 1)[0]
    assert "stopPropagation" in click, "a click on the icon reaches the header"

    keydown = body.split('addEventListener("keydown"', 1)[1].split("});", 1)[0]
    assert "stopPropagation" in keydown, "Enter on the icon reaches the header"
    assert "Enter" in keydown and '" "' in keydown


def test_a_click_opened_tip_survives_the_pointer_leaving():
    """The whole reason these are click and not hover: the tip renders
    below its target, so moving down to read a long one used to fire
    mouseout and dismiss it before it could be read.
    """
    init = function_body("initTooltips")
    mouseout = init.split('addEventListener("mouseout"', 1)[1].split("});", 1)[0]
    assert "isClickTriggered(current)" in mouseout, (
        "mouseout still dismisses a pinned tip"
    )


def test_hover_and_focus_leave_click_targets_alone():
    """Otherwise the icon would show its tip on the way past, which is
    the behaviour these replaced.
    """
    init = function_body("initTooltips")
    for handler in ("mouseover", "focusin"):
        block = init.split(f'addEventListener("{handler}"', 1)[1].split("});", 1)[0]
        assert "isClickTriggered" in block, f"{handler} still fires for click targets"


def test_a_pinned_tip_can_be_dismissed_by_clicking_elsewhere():
    """It has no close button, so without this it would be stuck open
    until something else happened to hide it.
    """
    init = function_body("initTooltips")
    assert 'addEventListener("click"' in init
    click = init.split('addEventListener("click"', 1)[1].split("});", 1)[0]
    assert "hide()" in click


def test_escape_still_dismisses():
    init = function_body("initTooltips")
    assert 'event.key === "Escape"' in init


# ---------- the line between the two ----------


def test_the_breakdown_chips_stay_on_hover():
    """Twenty chips, each revealing a label that did not fit. An icon on
    every one would be clutter, and nobody clicks a chip.
    """
    chips = APP_JS.split("function renderBreakdown", 1)[1].split("\n}", 1)[0]
    assert 'setAttribute("data-tip"' in chips
    assert "data-tip-trigger" not in chips, "the chips became click targets"


def test_the_radius_centre_keeps_its_focus_tooltip():
    """It fires on focus, which is when it helps -- you are about to
    type into the field it explains.
    """
    assert "tip(centerInput, CENTER_TIP)" in APP_JS


def test_no_control_label_is_still_a_hover_target():
    """The three that explained a control were the complaint: hovering
    a title on the way to clicking it put the tip in the way.
    """
    hover_attachments = [
        line.strip()
        for line in APP_JS.splitlines()
        if line.strip().startswith("tip(") and "centerInput" not in line
    ]
    assert hover_attachments == [], f"still attaching hover tips: {hover_attachments}"


# ---------- placement ----------


def test_the_icon_sits_after_the_title_it_explains():
    body = function_body("accordionGroup")
    assert "titleEl.append(document.createTextNode(title))" in body
    assert "titleEl.appendChild(helpIcon(help, title))" in body
    assert body.index("createTextNode(title)") < body.index("helpIcon("), (
        "the icon renders before the title text"
    )


def test_the_non_site_icon_sits_outside_its_label():
    """A button inside a <label> activates the label's control, so an
    icon there would tick the checkbox it was explaining.
    """
    body = function_body("renderNonSiteToggle")
    assert "row.appendChild(helpIcon(" in body
    assert "label" not in body.split("helpIcon(", 1)[0].rsplit("row.appendChild", 1)[1]


def test_the_icon_has_a_resting_and_an_open_state():
    """Without the open state there is no indication which icon the
    visible tip belongs to."""
    assert ".help-dot {" in STYLE_CSS
    assert '.help-dot[aria-expanded="true"]' in STYLE_CSS
