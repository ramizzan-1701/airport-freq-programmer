from afp.counter import counter_status
from afp.export.profile import ExportProfile

PROFILE = ExportProfile(name="Test", max_tag_length=14, max_entries=400)


def test_green_when_comfortably_under_cap():
    status = counter_status(100, PROFILE)
    assert status.level == "green"
    assert not status.over_cap


def test_amber_when_within_ten_percent_of_cap():
    status = counter_status(360, PROFILE)  # 90% of 400
    assert status.level == "amber"
    assert not status.over_cap


def test_just_under_amber_threshold_is_green():
    status = counter_status(359, PROFILE)
    assert status.level == "green"


def test_red_when_at_cap_is_not_over():
    status = counter_status(400, PROFILE)
    assert status.level == "amber"
    assert not status.over_cap  # AT the cap, not over it


def test_red_when_over_cap():
    status = counter_status(401, PROFILE)
    assert status.level == "red"
    assert status.over_cap


# ---------- the rolling counter ----------
#
# The number travels to its new value rather than snapping, and the bar
# and colour travel with it. Almost all of this fails silently: a
# rebuilt element simply appears at its new value, and a tween that
# restarts from the wrong place just looks like a glitch.

from afp.counter import AMBER_THRESHOLD  # noqa: E402
from afp.web.app import STATIC_DIR  # noqa: E402

APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")


def test_the_amber_threshold_is_sent_rather_than_repeated_in_the_frontend():
    """The counter recolours from the value it is currently showing, not
    from the level the server computed for the destination -- so the
    frontend needs the threshold. Sent, not copied: a second constant
    would be free to drift from this one.
    """
    assert "amber_threshold: float" in (
        (STATIC_DIR.parent / "models.py").read_text(encoding="utf-8")
    )
    assert "amber_threshold=AMBER_THRESHOLD" in (
        (STATIC_DIR.parent / "app.py").read_text(encoding="utf-8")
    )
    assert str(AMBER_THRESHOLD) not in APP_JS, "the threshold is hard-coded in app.js"


def test_the_counter_is_built_once_and_updated_in_place():
    """Rebuilding it per query is what made animation impossible: a
    fresh element has no previous value to travel from, so every change
    simply appeared. renderCounter must not wipe its own contents.
    """
    body = APP_JS.split("function renderCounter(result) {", 1)[1].split("\n}", 1)[0]
    assert 'innerHTML = ""' not in body, "renderCounter wipes the counter again"
    assert "buildCounter(el)" in body


def test_the_roll_lands_exactly_on_its_target():
    """Eased interpolation overshoots and rounds; the last frame has to
    be the real number, not whatever the curve produced.
    """
    body = APP_JS.split("function countAt(", 1)[1].split("\n}", 1)[0]
    assert "elapsed >= duration) return to;" in body
    assert "elapsed <= 0) return from;" in body


def test_the_duration_scales_with_the_distance_travelled():
    """A fixed duration is wrong at both ends: 395 to 396 over a second
    reads as lag, and a jump across the whole cap in the same time reads
    as a flicker.
    """
    body = APP_JS.split("function countTweenDuration(", 1)[1].split("\n}", 1)[0]
    assert "Math.abs(to - from)" in body
    assert "COUNT_TWEEN.perEntryMs" in body
    for knob in ("minMs:", "maxMs:", "perEntryMs:"):
        assert knob in APP_JS, f"{knob} is not tunable in one place"


def test_a_new_result_mid_roll_picks_up_from_what_is_on_screen():
    """Restarting from the last result's count instead would send the
    number backwards before it ran forward again.
    """
    body = APP_JS.split("function renderCounter(result) {", 1)[1].split("\n}", 1)[0]
    assert "const from = shownCount;" in body
    assert "cancelAnimationFrame(countFrame)" in body


def test_the_colour_follows_the_number_rather_than_the_destination():
    """So the two always agree: the counter turns red as the digits
    cross the cap, not while they are still below it.
    """
    body = APP_JS.split("function paintCounter(value, result) {", 1)[1].split("\n}", 1)[0]
    assert "value > result.cap" in body
    assert "result.level" not in body, "paintCounter is reading the destination's level"


def test_reduced_motion_skips_the_roll():
    assert "prefers-reduced-motion: reduce" in APP_JS
    body = APP_JS.split("function renderCounter(result) {", 1)[1].split("\n}", 1)[0]
    assert "prefersReducedMotion()" in body
