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


def test_an_empty_type_filter_narrows_nothing():
    """Every other group in the rail treats an empty selection as no
    constraint. Type read it literally -- exclude public and exclude
    private -- which can only return an empty result, so clearing both
    boxes emptied the table and looked like the app had broken.
    """
    from afp.query.filters import FilterState

    assert FilterState(include_public=False, include_private=False).airport_use_scope == (
        True,
        True,
    )


def test_the_type_filter_still_narrows_when_one_side_is_chosen():
    """The fix must not collapse into "always everything"."""
    from afp.query.filters import FilterState

    assert FilterState(include_public=True, include_private=False).airport_use_scope == (
        True,
        False,
    )
    assert FilterState(include_public=False, include_private=True).airport_use_scope == (
        False,
        True,
    )


def test_the_type_summary_no_longer_promises_an_empty_result():
    """It said "Neither — no entries", which described behaviour the
    filter no longer has.
    """
    assert "no entries" not in APP_JS.split("typeSummary", 1)[1].split(";", 1)[0]


# ---------- editable radius rules ----------
#
# Step 2 of the radius map (docs/radius-map/BRIEF.md §3.2, §4.4). The
# rail is the manual path and the map will be the visual one; both edit
# the same list, so the rules need identity and one of them needs to be
# "active".


def test_every_rule_gets_an_id_when_it_is_created():
    """Without one, "the active rule" could only be an index, and
    deleting a rule above it would quietly move the edit to its
    neighbour.
    """
    add = APP_JS.split("async function addRadiusFilter()", 1)[1].split("\n  }", 1)[0]
    assert "id: newRadiusId()" in add


def test_rules_restored_from_an_older_saved_file_get_an_id_too():
    """Every filters.json written before this has none, and a rule
    without an id can never be the active one -- so it would be
    uneditable on the map while looking identical to one that is.
    """
    body = APP_JS.split("async function resolvableRadiusFilters(", 1)[1].split("\n}", 1)[0]
    assert "id: rf.id || newRadiusId()" in body


def test_the_active_rule_is_not_part_of_the_filter():
    """It changes nothing about what the export contains, so it has no
    business in filters.json -- and persisting it would restore someone
    into an open editor they never left.
    """
    payload = APP_JS.split("function buildFilterPayload()", 1)[1].split("\n}", 1)[0]
    assert "activeRadiusId" not in payload


def test_the_payload_keeps_the_id_but_not_the_cached_position():
    """The id travels so the server can hand it back on /api/map/status.
    lat/lon are a drawing convenience the filter has no opinion about,
    and sending them would imply it did.
    """
    payload = APP_JS.split("function buildFilterPayload()", 1)[1].split("\n}", 1)[0]
    radius = payload.split("radius_filters:", 1)[1].split("))", 1)[0]
    assert "id: rf.id" in radius
    assert "lat" not in radius and "lon" not in radius


def test_adding_from_the_typed_form_does_not_steal_the_edit():
    """Someone who has just filled the form in has already said what
    they wanted; opening an editor on top of that is an interruption.
    """
    add = APP_JS.split("async function addRadiusFilter()", 1)[1].split("\n  }", 1)[0]
    assert "activeRadiusId =" not in add


def test_removing_the_active_rule_leaves_nothing_active():
    """Otherwise the id points at a rule that no longer exists, and the
    next render looks for an editor it can never build.
    """
    body = APP_JS.split("function removeRadiusFilter(", 1)[1].split("\n}", 1)[0]
    # Through setActiveRadius rather than by assignment: the map window
    # is showing the same rule, and only hears about this through the server.
    assert "if (activeRadiusId === id) setActiveRadius(null);" in body


def test_a_rule_is_removed_by_id_rather_than_by_position():
    """The list is rebuilt from the model on every change, so an index
    captured when the chip was drawn can be stale by the time it is
    clicked.
    """
    body = APP_JS.split("function removeRadiusFilter(", 1)[1].split("\n}", 1)[0]
    assert "filter((r) => r.id !== id)" in body


def test_the_editor_does_not_rebuild_itself_as_the_user_types():
    """renderList replaces every chip. Calling it from an input handler
    would take the focused field out from under the user mid-edit, so
    the live handlers update the model and the label and stop there.
    """
    editor = APP_JS.split("function radiusEditor(", 1)[1].split("\n}\n", 1)[0]
    live = editor.split('nm.addEventListener("input"', 1)[1].split("});", 1)[0]
    assert "onChange()" not in live
    assert "scheduleQuery()" in live


def test_the_centre_is_only_checked_once_the_user_has_finished_typing():
    """Every keystroke of "LAX" is its own unresolvable centre. Checking
    on input would clear the rule three times before it could be right.
    """
    editor = APP_JS.split("function radiusEditor(", 1)[1].split("\n}\n", 1)[0]
    assert 'center.addEventListener("blur", commitCenter)' in editor
    assert 'center.addEventListener("input", clearError)' in editor


def test_an_unresolvable_centre_leaves_the_rule_as_it_was():
    """The rule keeps working on the centre it had, rather than being
    emptied by a typo.
    """
    commit = APP_JS.split("async function commitCenter()", 1)[1].split("\n  }", 1)[0]
    assert "center.value = rf.center" in commit


def test_the_open_editor_still_shows_what_it_is_editing(): 
    """The chip keeps its label and its remove button while open.
    Replacing the chip wholesale hid both -- the rule being edited no
    longer said what it was, and deleting it meant closing the editor
    first.
    """
    editor = APP_JS.split("function radiusEditor(", 1)[1].split("\n}\n", 1)[0]
    assert "radiusLabel(rf)" in editor
    assert "radius-remove" in editor
    # ...and the label follows the live edits, since nothing redraws it.
    assert editor.count("text.textContent = radiusLabel(rf)") >= 2
