"""Choosing a group scheme, naming the user's three slots, and copying
frequencies into them.

The failures worth guarding here are the quiet ones: a scheme that
persists but is not applied, a custom group whose entries are stranded
under a name nothing declares, and a copy that silently overwrites an
entry already in the target.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from afp.query.db import build_database
from afp.schema import Airport, Frequency, NormalizedData
from afp.selection import ALPHABETICAL_GROUP_NAMES, CATEGORY_GROUP_NAMES, Entry
from afp.web import create_app
from afp.web.state import LoadedCycle

DATA = NormalizedData(
    airports=[
        Airport(id="HWD", name="Hayward", city="HAYWARD", state="CA",
                lat=37.6, lon=-122.1, public_use=True)
    ],
    frequencies=[
        Frequency(airport_id="HWD", freq_mhz=120.2, freq_category="TOWER", raw_freq_use="LCL/P"),
        Frequency(airport_id="HWD", freq_mhz=126.7, freq_category="WEATHER_STATION", raw_freq_use="ATIS"),
    ],
    ils=[],
)


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app(cache_dir=tmp_path)
    app.state.afp_state.loaded = LoadedCycle(
        cycle_date=date(2026, 9, 3), data=DATA, conn=build_database(DATA)
    )
    return TestClient(app)


def groups_of(client) -> set[str]:
    return {e["group"] for e in client.post("/api/query", json={}).json()["entries"]}


# ---------- the scheme ----------


def test_the_default_scheme_is_alphabetical(client):
    """What the app produced before there was a choice, so an existing
    install's exports do not change shape until someone changes this.
    """
    assert client.get("/api/groups").json()["scheme"] == "alphabetical"
    assert groups_of(client) <= set(ALPHABETICAL_GROUP_NAMES)


def test_choosing_a_scheme_regroups_the_results(client):
    """Persisting the choice without applying it would look like the
    control does nothing.
    """
    client.put("/api/groups/scheme", json={"scheme": "category"})
    assert groups_of(client) <= set(CATEGORY_GROUP_NAMES)


def test_the_scheme_survives_a_restart(client, tmp_path):
    client.put("/api/groups/scheme", json={"scheme": "category"})
    second = TestClient(create_app(cache_dir=tmp_path))
    assert second.get("/api/groups").json()["scheme"] == "category"


def test_an_unknown_scheme_is_refused(client):
    assert client.put("/api/groups/scheme", json={"scheme": "by-vibes"}).status_code == 422
    assert client.get("/api/groups").json()["scheme"] == "alphabetical"


def test_the_response_carries_both_schemes_names(client):
    """So the selector can describe each one without a second copy of
    the names living in the frontend.
    """
    body = client.get("/api/groups").json()
    assert body["schemes"]["alphabetical"] == list(ALPHABETICAL_GROUP_NAMES)
    assert body["schemes"]["category"] == list(CATEGORY_GROUP_NAMES)


# ---------- naming the user's slots ----------


def test_slots_start_unnamed_and_sit_after_the_presets(client):
    slots = client.get("/api/groups").json()["custom_slots"]
    assert [s["slot"] for s in slots] == [6, 7, 8]
    assert all(s["name"] is None for s in slots)


def test_naming_a_slot_sticks(client):
    client.put("/api/groups/custom/0", json={"name": "Home"})
    assert client.get("/api/groups").json()["custom_slots"][0]["name"] == "Home"


def test_a_reserved_name_is_suffixed_rather_than_refused(client):
    """A user whose radio already has a WEATHER group should not have it
    silently absorbed into the managed set.
    """
    body = client.put("/api/groups/custom/0", json={"name": "WEATHER"}).json()
    assert body["custom_slots"][0]["name"] == "WEATHER1"


def test_two_slots_cannot_take_the_same_name(client):
    """The radio would have no way to tell which slot an entry meant,
    and the export refuses duplicate names outright.
    """
    client.put("/api/groups/custom/0", json={"name": "Home"})
    body = client.put("/api/groups/custom/1", json={"name": "Home"}).json()
    names = [s["name"] for s in body["custom_slots"]]
    assert names[0] == "Home" and names[1] == "Home1"


def test_a_name_longer_than_the_radio_allows_is_trimmed(client):
    body = client.put("/api/groups/custom/0", json={"name": "A" * 40}).json()
    cap = body["max_group_name_length"]
    assert len(body["custom_slots"][0]["name"]) <= cap


def test_renaming_carries_the_entries_with_it(client):
    """Entries hold the group *name*, not the slot. Left behind they
    would be in a group nothing declares, and land ungrouped.
    """
    client.put("/api/groups/custom/0", json={"name": "Home"})
    entry = {"tag_name": "HWD-CT", "freq_mhz": 120.2, "group": "", "airport_id": "HWD",
             "airport_name": "Hayward", "city": "HAYWARD", "state": "CA",
             "lat": 37.6, "lon": -122.1, "category": "TOWER"}
    client.post("/api/groups/copy", json={"slot": 0, "entries": [entry]})

    client.put("/api/groups/custom/0", json={"name": "Local"})
    held = client.get("/api/custom-entries").json()["entries"]
    assert [e["group"] for e in held] == ["Local"]


def test_naming_a_slot_that_does_not_exist_is_a_404(client):
    assert client.put("/api/groups/custom/7", json={"name": "X"}).status_code == 404


# ---------- copying ----------


def _row(client, index: int = 0) -> dict:
    return client.post("/api/query", json={}).json()["entries"][index]


def test_copying_snapshots_the_entry_into_the_slot(client):
    row = _row(client)
    body = client.post("/api/groups/copy", json={"slot": 0, "entries": [row]}).json()

    assert body["copied"] == 1
    held = client.get("/api/custom-entries").json()["entries"]
    assert held[0]["tag_name"] == row["tag_name"]
    assert held[0]["freq_mhz"] == row["freq_mhz"]


def test_copying_leaves_the_original_where_it_was(client):
    """A copy, not a move."""
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    tags = [e["tag_name"] for e in client.post("/api/query", json={}).json()["entries"]]
    assert tags.count(row["tag_name"]) == 2


def test_an_unnamed_target_slot_gets_the_radios_own_default_name(client):
    """It has to be called something: an entry naming a group no slot
    declares still imports, but lands with no group at all.
    """
    body = client.post("/api/groups/copy", json={"slot": 1, "entries": [_row(client)]}).json()
    assert body["group"] == "GROUP8"


def test_a_duplicate_tag_is_skipped_and_reported(client):
    """Tag names must be unique within a group. One collision must not
    fail the whole copy, and must not pass silently either -- only the
    user can decide what to rename.
    """
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    body = client.post("/api/groups/copy", json={"slot": 0, "entries": [row]}).json()

    assert body["copied"] == 0
    assert body["skipped"] == [row["tag_name"]]


def test_a_collision_does_not_block_the_rest_of_the_copy(client):
    rows = client.post("/api/query", json={}).json()["entries"][:2]
    client.post("/api/groups/copy", json={"slot": 0, "entries": [rows[0]]})
    body = client.post("/api/groups/copy", json={"slot": 0, "entries": rows}).json()

    assert body["copied"] == 1
    assert body["skipped"] == [rows[0]["tag_name"]]


def test_the_same_tag_may_live_in_two_different_groups(client):
    """Uniqueness is per group, confirmed on the radio itself. A
    shortlist of frequencies the user already has elsewhere is the whole
    point of a custom group.
    """
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    body = client.post("/api/groups/copy", json={"slot": 1, "entries": [row]}).json()
    assert body["copied"] == 1


def test_copying_into_a_slot_that_does_not_exist_is_a_404(client):
    assert client.post("/api/groups/copy", json={"slot": 9, "entries": []}).status_code == 404


def test_slot_counts_reflect_what_is_in_them(client):
    client.post("/api/groups/copy", json={"slot": 0, "entries": [_row(client)]})
    slots = client.get("/api/groups").json()["custom_slots"]
    assert slots[0]["entry_count"] == 1
    assert slots[1]["entry_count"] == 0


# ---------- what reaches the file ----------


def test_the_export_declares_every_group_it_uses(client):
    """The point of the whole feature: nothing to rename by hand,
    because the file says what the groups are called.
    """
    client.put("/api/groups/scheme", json={"scheme": "category"})
    client.put("/api/groups/custom/0", json={"name": "Home"})
    client.post("/api/groups/copy", json={"slot": 0, "entries": [_row(client)]})

    xml = client.post("/api/generate", json={}).content.decode("utf-8-sig")
    block = xml[xml.index("<GROUPS>"): xml.index("</GROUPS>")]
    for name in CATEGORY_GROUP_NAMES:
        assert f">{name}<" in block
    assert '<GROUP index="6">Home</GROUP>' in block


def test_an_unused_slot_is_left_out_of_the_export(client):
    """Left out is what leaves it alone on the radio -- writing all nine
    every time would rename groups the app does not manage.
    """
    xml = client.post("/api/generate", json={}).content.decode("utf-8-sig")
    block = xml[xml.index("<GROUPS>"): xml.index("</GROUPS>")]
    assert 'index="6"' not in block


def test_existing_custom_entries_are_seated_on_first_run(tmp_path):
    """Upgrading from a version with no slots: entries imported then
    carry a group name but no slot, and a slot nothing sits in is not
    declared -- so every one of those entries would land ungrouped.
    """
    app = create_app(cache_dir=tmp_path)
    app.state.afp_state.custom_entries = [Entry("OLD-1", 122.8, "Legacy", 1.0, -1.0)]
    app.state.afp_state._restore_groups()

    slots = [s for s in _slots(app)]
    assert slots[0] == "Legacy"


def _slots(app) -> list:
    return app.state.afp_state.custom_slot_names


def test_a_copied_row_and_its_original_are_distinguishable(client):
    """Found by using the UI: a frequency copied into a custom group
    shares its tag name with the generated row it came from, because
    uniqueness is only per group. The table keyed its selection on the
    tag alone, so the two rows collided -- ticking either ticked both,
    four rows vanished from the selection map, and delete could resolve
    to the wrong one of the pair.

    The rows must therefore differ in something other than the tag.
    """
    row = _row(client)
    client.post("/api/groups/copy", json={"slot": 0, "entries": [row]})
    entries = client.post("/api/query", json={}).json()["entries"]

    pair = [e for e in entries if e["tag_name"] == row["tag_name"]]
    assert len(pair) == 2, "expected the copy and its original"
    assert {e["is_custom"] for e in pair} == {True, False}
    assert len({e["group"] for e in pair}) == 2
    # The custom one is addressable for deletion; the generated one is not.
    assert sorted(e["custom_index"] is None for e in pair) == [False, True]


def test_the_frontend_keys_rows_on_more_than_the_tag():
    """Pins the fix above where it actually lives."""
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert "function rowKey(e)" in app_js
    assert "e.is_custom ? `c:${e.custom_index}`" in app_js
    assert "dataset.tag" not in app_js, "a tag-keyed row lookup came back"


# ---------- dialogs the app has to draw itself ----------


def _app_js() -> str:
    from afp.web.app import STATIC_DIR

    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_no_native_browser_dialogs_are_used():
    """window.confirm and window.alert do not work in this app.
    pywebview's Edge WebView2 backend suppresses native JS dialogs, so
    confirm() returned false immediately without anyone being asked --
    deleting a custom entry appeared to do nothing at all -- and alert()
    reported import failures to nobody.

    Neither failed loudly, which is why this is pinned rather than left
    to be noticed again.
    """
    import re

    # Comments stripped first: the explanation of why these are banned
    # names them, and matching prose would make this unfixable.
    code = " ".join(line.split("//", 1)[0] for line in _app_js().splitlines())
    # A method call such as askConfirm(...) is fine; the bare global is not.
    for name in ("confirm", "alert", "prompt"):
        hits = re.findall(rf"(?<![A-Za-z0-9_.]){name}\s*\(", code)
        assert not hits, f"{name}() is unusable in this app -- use askConfirm/showMessage"


def test_deleting_asks_before_it_destroys_anything():
    """The one action here that cannot be undone. Copy deliberately does
    not ask; this does.
    """
    js = _app_js()
    body = js.split("async function deleteCustomEntries(", 1)[1].split("\n}", 1)[0]
    assert "await askConfirm(" in body
    assert "if (!ok) return;" in body


def test_the_confirmation_does_not_focus_the_destructive_button():
    """Enter should not complete something irreversible the user has
    not read yet.
    """
    js = _app_js()
    body = js.split("function askConfirm(", 1)[1].split("\n}", 1)[0]
    assert "cancel.focus();" in body
    assert "go.focus()" not in body


def test_every_results_column_is_accounted_for_by_a_width_rule():
    """The column widths are keyed on nth-child, so adding a column
    shifts every rule after it one place to the right -- silently. That
    is how the selection column ended up 17% wide (Tag's share), Group
    took Airport's 35%, and City/State lost its rule entirely.

    Exactly one column is deliberately unsized: under table-layout fixed
    it absorbs the remainder, which lets the selection column be an
    exact pixel width without the percentages having to sum around it.
    """
    import re

    from afp.web.app import STATIC_DIR

    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    header = html.split('<table class="results-table">', 1)[1].split("</thead>", 1)[0]
    columns = len(re.findall(r"<th\b", header))

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    sized = {
        int(n)
        for n in re.findall(r"\.results-table th:nth-child\((\d+)\)", css)
    }
    missing = set(range(1, columns + 1)) - sized
    assert len(missing) == 1, (
        f"{columns} columns, width rules for {sorted(sized)} -- "
        f"expected exactly one unsized, got {sorted(missing)}"
    )
    assert max(sized) <= columns, "a width rule points past the last column"


def test_the_selection_column_is_narrow_and_left_aligned():
    """It holds one checkbox. Centred in a wide column it reads as
    belonging to neither the table edge nor the row it ticks.
    """
    from afp.web.app import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    rule = css.split(".col-select {", 1)[1].split("}", 1)[0]
    assert "text-align: left" in rule
    assert "text-align: center" not in rule


def test_the_groups_bar_is_two_rows_that_do_not_wrap():
    """What the app names and what you name are separate decisions.
    Wrapped into one flex row they ran together, and the second read as
    a continuation of the first.
    """
    from afp.web.app import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    bar = css.split(".groups-bar {", 1)[1].split("}", 1)[0]
    assert "flex-direction: column" in bar
    assert "flex-wrap: wrap" not in bar

    row = css.split(".groups-row {", 1)[1].split("}", 1)[0]
    assert "flex-wrap: nowrap" in row

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert app_js.count('className = "groups-row"') == 2


def test_only_the_name_preview_gives_way_when_space_is_short():
    """It previews names listed in full on the row below, so an ellipsis
    there costs nothing -- whereas a truncated label or a slot field the
    user cannot reach does.
    """
    from afp.web.app import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    preview = css.split(".groups-preview {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in preview

    for selector in (".groups-kicker {", ".slot-field {", ".seg {"):
        rule = css.split(selector, 1)[1].split("}", 1)[0]
        assert "flex: none" in rule, f"{selector} can be squeezed"


def _memo(tag: str, group: str) -> str:
    return (
        f"<MEMORY_BOOK_GROUP><TAG_NAME>{tag}</TAG_NAME>"
        "<FREQUENCY>122.800</FREQUENCY>"
        f"<GROUP>{group}</GROUP>"
        "<POSITION><LAT>37\u00b030.000</LAT><NS>N</NS>"
        "<LON>122\u00b06.000</LON><EW>W</EW></POSITION>"
        "<SCAN_MEMORY>Off</SCAN_MEMORY><SHIFT>Off</SHIFT></MEMORY_BOOK_GROUP>"
    )


def _book(*memos: str) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8" standalone="yes"?>'
        "<FILE><MEMORY_BOOK>" + "".join(memos) + "</MEMORY_BOOK></FILE>"
    )
    return b"\xef\xbb\xbf" + body.encode("utf-8")


def test_imported_groups_take_slots(client):
    """Found by importing in the running app. An import brings its own
    group names, and a name with no slot is never written into <GROUPS>
    -- so every one of those entries lands ungrouped on the radio while
    the export succeeds and the file looks right.
    """
    client.post("/api/custom-entries/import",
                content=_book(_memo("HOME-1", "Hangar"), _memo("TRIP-1", "Trips")))

    slots = client.get("/api/groups").json()["custom_slots"]
    assert [s["name"] for s in slots[:2]] == ["Hangar", "Trips"]
    assert [s["entry_count"] for s in slots[:2]] == [1, 1]


def test_imported_groups_reach_the_export(client):
    """The consequence the test above exists to prevent."""
    client.post("/api/custom-entries/import",
                content=_book(_memo("HOME-1", "Hangar")))

    xml = client.post("/api/generate", json={}).content.decode("utf-8-sig")
    block = xml[xml.index("<GROUPS>"): xml.index("</GROUPS>")]
    assert "<GROUP index=\"6\">Hangar</GROUP>" in block


def test_reimporting_does_not_shuffle_a_group_to_another_slot(client):
    """A group the user has been working with should stay where it is."""
    client.post("/api/custom-entries/import",
                content=_book(_memo("A", "Alpha"), _memo("B", "Bravo")))
    client.post("/api/custom-entries/import",
                content=_book(_memo("B", "Bravo"), _memo("C", "Charlie")))

    names = [s["name"] for s in client.get("/api/groups").json()["custom_slots"]]
    assert names[1] == "Bravo", "Bravo moved off its slot"
    assert "Charlie" in names


def test_an_import_that_keeps_nothing_leaves_no_slots_claimed(client):
    """A file made entirely of app-generated group names keeps nothing,
    so nothing should be seated either.
    """
    client.post("/api/custom-entries/import",
                content=_book(_memo("X", "0-9"), _memo("Y", "A-E")))
    slots = client.get("/api/groups").json()["custom_slots"]
    assert all(s["name"] is None for s in slots)


# ---------- amber means "yours" ----------


def _css() -> str:
    from afp.web.app import STATIC_DIR

    return (STATIC_DIR / "style.css").read_text(encoding="utf-8")


def test_the_custom_colour_is_its_own_hue():
    """--custom and --ok were the same green, which is how a custom
    group pill ended up wearing the colour that titles the app's own
    sections. Amber gives the user's groups a hue nothing else claims.
    """
    import re

    css = _css()
    tokens = dict(re.findall(r"--(\w+):\s*(#[0-9A-Fa-f]{6});", css))
    assert tokens["custom"] != tokens["ok"]
    assert tokens["custom"] != tokens["warn"]
    assert tokens["custom"] != tokens["acc"]


def test_custom_group_names_are_marked_in_the_results_table():
    """They rendered identically to the app's own group names, which is
    the one distinction that column exists to make.
    """
    from afp.web.app import STATIC_DIR

    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'pill.className = "group-pill" + (e.is_custom ? " custom" : "");' in app_js
    assert ".group-pill.custom { border-color: var(--custom); color: var(--custom); }" in _css()


def test_the_two_group_labels_wear_the_colours_they_name():
    """Your groups in amber, the app's in green -- so the amber in the
    Group column is traceable back to the row that owns it.
    """
    from afp.web.app import STATIC_DIR

    assert ".groups-kicker.yours { color: var(--custom); }" in _css()
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'yoursLabel.className = "groups-kicker yours"' in app_js
    assert 'appLabel.className = "groups-kicker"' in app_js


def test_the_update_badge_did_not_follow_the_custom_colour():
    """It announces a newer FAA cycle, which has nothing to do with the
    user's groups. It only ever looked right because --custom happened
    to be the same green as --ok.
    """
    rule = _css().split(".update-badge {", 1)[1].split("}", 1)[0]
    assert "var(--ok)" in rule
    assert "var(--custom)" not in rule


# ---------- shift-click range selection ----------


def _js() -> str:
    from afp.web.app import STATIC_DIR

    return (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_the_checkbox_listens_for_click_not_change():
    """A change event carries no shiftKey -- by the time it fires the
    modifier is gone, so a range could never be detected from one.
    """
    js = _js()
    assert 'check.addEventListener("click", (ev) => {' in js
    assert 'check.addEventListener("change"' not in js


def test_a_range_replaces_the_selection_rather_than_adding_to_it():
    """Explorer and Gmail replace. It also makes a mis-aimed range
    recoverable by shift-clicking elsewhere instead of unpicking rows.
    """
    body = _js().split("function selectRangeTo(key) {", 1)[1].split("\n}", 1)[0]
    assert "selectedTags = new Set(keys.slice(lo, hi + 1));" in body


def test_the_anchor_does_not_move_when_a_range_is_drawn():
    """So the same range can be resized by shift-clicking again, rather
    than each shift-click re-anchoring and halving the range.
    """
    body = _js().split("function selectRangeTo(key) {", 1)[1].split("\n}", 1)[0]
    assert "anchorKey =" not in body, "selectRangeTo moves the anchor"
    assert "anchorKey = key;" in _js().split("function toggleRow(", 1)[1].split("\n}", 1)[0]


def test_a_range_works_in_both_directions():
    """Clicking above the anchor is as ordinary as clicking below it."""
    body = _js().split("function selectRangeTo(key) {", 1)[1].split("\n}", 1)[0]
    assert "from <= to ? [from, to] : [to, from]" in body


def test_the_anchor_is_dropped_when_its_row_leaves_the_table(): 
    """Filtering or re-querying rebuilds the rows. An anchor pointing at
    one that is gone would make the next shift-click measure from
    nowhere -- indexOf returns -1, which without this would slice from
    the end of the list.
    """
    js = _js()
    assert "if (anchorKey !== null && !renderedByTag.has(anchorKey)) anchorKey = null;" in js
    body = js.split("function selectRangeTo(key) {", 1)[1].split("\n}", 1)[0]
    assert "if (from === -1 || to === -1)" in body


def test_select_all_leaves_no_anchor():
    """It did not come from a row, so there is nowhere for a following
    shift-click to measure from.
    """
    body = _js().split('document.getElementById("select-all-rows")', 1)[1].split("});", 1)[0]
    assert "anchorKey = null;" in body


def test_shift_clicking_does_not_drag_a_text_selection():
    """Native shift-click extends a text selection across every row it
    spans, which is both ugly and awkward to clear.
    """
    js = _js()
    assert 'check.addEventListener("mousedown", (ev) => {' in js
    assert "if (ev.shiftKey) ev.preventDefault();" in js

    from afp.web.app import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert ".col-select { user-select: none; }" in css


def test_the_two_group_rows_start_their_controls_at_the_same_place():
    """The name fields sit directly above the scheme toggle, so the
    labels have to occupy the same width -- "Your groups" renders wider
    than "App groups", which left the rows 9.5px out of line.
    """
    from afp.web.app import STATIC_DIR

    rule = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    rule = rule.split(".groups-kicker {", 1)[1].split("}", 1)[0]
    assert "min-width:" in rule


def test_the_help_icon_sits_after_the_fields_it_describes():
    """Between the label and the first field it also pushed that field
    out of line with the row below.
    """
    js = _js()
    row = js.split("function renderGroupsBar()", 1)[1].split("bar.append(yours, app);", 1)[0]
    assert row.index("yours.appendChild(wrap);") < row.index("yours.appendChild(helpIcon(")
    assert row.index("yours.appendChild(helpIcon(") < row.index('held.className = "groups-held"')


def test_every_help_icon_says_what_it_is_about():
    """helpIcon takes the subject for its aria-label. Two call sites
    omitted it, which rendered aria-label="About undefined" -- announced
    to a screen reader exactly like that.
    """
    import re

    js = _js()
    for call in re.finditer(r"helpIcon\(", js):
        depth, i = 0, call.end() - 1
        while i < len(js):
            if js[i] == "(":
                depth += 1
            elif js[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        args = js[call.end():i]
        if args.startswith("text, describes"):
            continue  # the definition itself
        assert args.count(",") >= 1, f"helpIcon call with no subject: {args[:60]!r}"
