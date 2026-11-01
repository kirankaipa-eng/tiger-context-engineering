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


# ---- an edition that stalls

class _Model:
    def __init__(self, folder):
        self.id, self.folder = "m", folder


def _brief(folder, sid="b1"):
    from context_studio.pbi_schedule import update
    update(folder, lambda data: data.update({sid: {"id": sid, "owner": "Kiran", "title": "PMPM watch", "ask": "PMPM",
                                                   "cron": "0 * * * *", "tz": "UTC", "created": 0, "editions": []}}))


def test_a_stalled_edition_is_stopped_and_says_so(tmp_path):
    import asyncio

    from context_studio.pbi_schedule import _RUNNING, load, run

    async def stalls(mid, body):  # a model call that never answers
        await asyncio.sleep(3600)

    _brief(tmp_path)
    edition = asyncio.run(run(_Model(tmp_path), "b1", stalls, limit=0.05))
    assert "Stopped after" in edition["error"] and edition["seconds"] < 5
    assert "b1" not in _RUNNING  # the page stops showing it as writing
    assert load(tmp_path)["b1"]["editions"][0]["error"] == edition["error"]


def test_a_stalled_brief_does_not_hold_up_another(tmp_path):
    import asyncio
    import json

    from context_studio import pbi_schedule as ps

    _brief(tmp_path, "slow"); _brief(tmp_path, "quick")
    done = []

    class Resp:
        def __init__(self, sid):
            self.sid = sid

        @property
        async def body_iterator(self):
            if self.sid == "slow":
                await asyncio.sleep(3600)
            done.append(self.sid)
            yield json.dumps({"type": "result", "data": {"answer": "PMPM held steady."}})

    async def ask(mid, body):
        return Resp(body["session_id"].split("-")[1])

    async def go():
        task = asyncio.create_task(ps.loop(lambda: {"m": _Model(tmp_path)}, ask, every=0.01))
        for _ in range(200):
            await asyncio.sleep(0.01)
            if done:
                break
        task.cancel()
    asyncio.run(go())
    assert done == ["quick"]
    ps._RUNNING.clear()
