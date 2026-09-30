"""The filter rail's structure and section titles.

Small things, all of which fail silently: a missing rule draws no line,
a lower-case title looks like a typo rather than an error, and a hover
that lights half a group reads as two controls instead of one.
"""

import re

from afp.web.app import STATIC_DIR

APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
CSS = (STATIC_DIR / "style.css").read_text(encoding="utf-8")


def test_every_group_title_is_title_case():
    """They sit in one column down the rail, so one lower-cased word
    reads as a mistake in the app rather than a style choice.

    Small words inside a title stay lower case where they are genuinely
    part of a name, which is why this checks the first letter of the
    leading word rather than every word.
    """
    titles = re.findall(r'title: "([^"]+)"', APP_JS)
    assert titles, "no group titles found -- has the rail changed shape?"
    for title in titles:
        for word in title.split():
            if word[0].isalpha() and word.lower() not in ("of", "and", "the", "in"):
                assert word[0].isupper(), f"{title!r} is not title case"


def test_the_blocks_outside_the_accordion_draw_their_own_separator():
    """Geographic Radius and the non-site toggle are not accordion
    groups, so they never picked up the rule that draws the line between
    one group and the next -- leaving Geographic Radius running straight
    into City with nothing between them.
    """
    for selector in (".radius-block", ".non-site-block"):
        rule = CSS.split(f"{selector} {{", 1)[1].split("}", 1)[0]
        assert "border-top: 1px solid var(--line2)" in rule, selector


def test_hovering_a_group_lights_the_whole_group():
    """With a group open, hovering lit the header and left the options
    below it unlit, which read as two separate things rather than one
    section.
    """
    assert ".filter-group-label:hover" not in CSS
    assert ".filter-group:hover" in CSS
    hover = CSS.split(".filter-group:hover", 1)[1].split("}", 1)[0]
    assert "var(--hover)" in hover


def test_the_non_site_toggle_comes_after_the_groups_it_qualifies():
    """It exempts rows from Site Type *and* Facility Status, so it stays
    outside both rather than buried in one -- at the foot of the
    section, after the two it applies to.
    """
    body = APP_JS.split("// --- Facility ---", 1)[1].split("filters-filler", 1)[0]
    assert body.index('key: "siteType"') < body.index("renderNonSiteToggle(")
    assert body.index('key: "facilityStatus"') < body.index("renderNonSiteToggle(")
