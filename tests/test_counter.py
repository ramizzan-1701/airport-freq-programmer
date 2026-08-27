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
