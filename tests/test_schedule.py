"""Scheduled briefs: the cron expressions a person writes, and when they come round."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from context_studio.pbi_schedule import Cron, CronError, due, upcoming

CT = ZoneInfo("America/Chicago")


def at(*a):
    return datetime(*a, tzinfo=CT)


def test_weekly_monday_seven():
    # Friday 30 Oct 2026, 18:00 -> Monday 2 Nov, 07:00
    assert Cron("0 7 * * 1").next_after(at(2026, 10, 30, 18, 0)) == at(2026, 11, 2, 7, 0)
    assert Cron("0 7 * * MON").next_after(at(2026, 11, 2, 7, 0)) == at(2026, 11, 9, 7, 0)


def test_weekdays_steps_lists_and_macros():
    assert Cron("30 8 * * 1-5").next_after(at(2026, 10, 31, 9)) == at(2026, 11, 2, 8, 30)  # Saturday -> Monday
    assert Cron("*/15 * * * *").next_after(at(2026, 10, 30, 18, 7)) == at(2026, 10, 30, 18, 15)
    assert Cron("0 9,17 * * *").next_after(at(2026, 10, 30, 10)) == at(2026, 10, 30, 17)
    assert Cron("@monthly").next_after(at(2026, 10, 30, 18)) == at(2026, 11, 1, 0, 0)
    assert Cron("0 0 * * 7").next_after(at(2026, 10, 30)) == at(2026, 11, 1)  # 7 is Sunday


def test_day_fields_either_matches_when_both_given():
    # the 15th, or any Monday: Monday 2 Nov comes before 15 Nov
    assert Cron("0 6 15 * 1").next_after(at(2026, 10, 30)) == at(2026, 11, 2, 6)


def test_leap_day_is_years_away():
    assert Cron("0 0 29 2 *").next_after(at(2026, 10, 30)) == at(2028, 2, 29)


@pytest.mark.parametrize("bad", ["", "0 7 * *", "61 * * * *", "0 25 * * *", "0 7 * * FUNDAY", "0 7 5-1 * *", "0 0 31 2 *"])
def test_bad_expressions_say_why(bad):
    with pytest.raises(CronError):
        Cron(bad).next_after(at(2026, 10, 30))


def test_upcoming_and_due():
    nxt = upcoming("0 7 * * 1", "America/Chicago", 2, at(2026, 10, 30, 18))
    assert nxt == ["2026-11-02T07:00:00-06:00", "2026-11-09T07:00:00-06:00"]
    s = {"id": "x", "cron": "0 7 * * 1", "tz": "America/Chicago", "created": at(2026, 10, 30).timestamp(), "last_run": None}
    assert not due(s, at(2026, 11, 2, 6, 59).timestamp())
    assert due(s, at(2026, 11, 2, 7, 0).timestamp())
    assert not due({**s, "last_run": at(2026, 11, 2, 7, 1).timestamp()}, at(2026, 11, 2, 9).timestamp())
    assert not due({**s, "paused": True}, at(2026, 11, 3).timestamp())
