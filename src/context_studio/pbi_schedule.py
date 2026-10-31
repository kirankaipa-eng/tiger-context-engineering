"""Scheduled briefs: a person says in their own words what they want a brief on, and when, as a cron expression
("0 7 * * 1" is Mondays at 7:00). At each time the agent is asked, through the same /ask the Ask page uses, in a
conversation of its own, and what it said and computed is kept as that brief's edition. The page shows the latest
edition and the ones before it.

    schedules.json beside the model: {id: {id, owner, title, ask, cron, tz, paused, created, last_run, editions}}

Cron is the usual five fields (minute hour day-of-month month day-of-week) with *, lists, ranges, steps and the
names JAN-DEC and SUN-SAT, or one of @hourly @daily @weekly @monthly @yearly. When both day fields are
restricted, either may match, as in cron. Times are in the schedule's own time zone. A server that was down
when a brief was due writes one edition when it is back, not one per missed time.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KEEP = 12  # editions kept per brief
_LOCK = threading.Lock()
_RUNNING: set[str] = set()

MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
          "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
NAMES = {"month": {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)},
    "dow": {d: i for i, d in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}}
FIELDS = [("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("day of week", 0, 7)]


class CronError(ValueError):
    pass


class Cron:
    """A parsed five-field cron expression."""

    def __init__(self, text: str):
        self.text = " ".join(str(text or "").split())
        expr = MACROS.get(self.text.lower(), self.text)
        parts = expr.split()
        if len(parts) != 5:
            raise CronError("a cron expression has five fields: minute hour day-of-month month day-of-week "
                            "(e.g. 0 7 * * 1 for Mondays at 7:00)")
        sets = [self._field(p, *f) for p, f in zip(parts, FIELDS)]
        self.minutes, self.hours, self.days, self.months, dow = sets
        self.dows = {d % 7 for d in dow}  # 7 is Sunday too
        self.any_day, self.any_dow = parts[2] == "*", parts[4] == "*"

    @staticmethod
    def _field(text: str, name: str, lo: int, hi: int) -> set[int]:
        names = NAMES["month"] if name == "month" else NAMES["dow"] if name == "day of week" else {}
        out: set[int] = set()
        for item in text.split(","):
            m = re.fullmatch(r"(\*|[A-Za-z0-9]+(?:-[A-Za-z0-9]+)?)(?:/(\d+))?", item)
            if not m:
                raise CronError(f"{item!r} is not {'an' if name[0] in 'aeiouh' else 'a'} {name}")
            rng, step = m.group(1), int(m.group(2) or 1)
            if step < 1:
                raise CronError(f"a step in the {name} is at least 1")

            def num(v: str) -> int:
                n = names.get(v.upper()) if not v.isdigit() else int(v)
                if n is None or not lo <= n <= hi:
                    raise CronError(f"{v!r} is not {'an' if name[0] in 'aeiouh' else 'a'} {name} ({lo} to {hi})")
                return n
            if rng == "*":
                a, b = lo, hi
            elif "-" in rng:
                a, b = map(num, rng.split("-"))
                if a > b:
                    raise CronError(f"{rng!r}: a {name} range runs low to high")
            else:
                a = num(rng)
                b = hi if m.group(2) else a  # 5/15 is 5, 20, 35, 50
            out.update(range(a, b + 1, step))
        return out

    def _day(self, d: datetime) -> bool:
        if d.month not in self.months:
            return False
        dom, dow = d.day in self.days, (d.weekday() + 1) % 7 in self.dows
        if self.any_day and self.any_dow:
            return True
        if self.any_day:
            return dow
        if self.any_dow:
            return dom
        return dom or dow

    def next_after(self, after: datetime) -> datetime:
        """The first time strictly after `after` (aware) that the expression names, in after's time zone."""
        start = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.replace(hour=0, minute=0)
        for _ in range(366 * 5):  # 29 Feb on a Monday may be years away
            if self._day(day):
                for h in sorted(self.hours):
                    for mi in sorted(self.minutes):
                        t = day.replace(hour=h, minute=mi)
                        if t >= start:
                            return t
            day = (day + timedelta(days=1)).replace(hour=0, minute=0)
        raise CronError("that expression never comes round")


def zone(tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise CronError(f"{tz!r} is not a time zone (e.g. America/Chicago)") from None


def upcoming(cron: str, tz: str | None, n: int = 3, after: datetime | None = None) -> list[str]:
    c, z = Cron(cron), zone(tz)
    t, out = (after or datetime.now(z)).astimezone(z), []
    for _ in range(n):
        t = c.next_after(t)
        out.append(t.isoformat())
    return out


# ------------------------------------------------------------------ storage

def _path(folder) -> Path:
    return Path(folder) / "schedules.json"


def load(folder) -> dict:
    p = _path(folder)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def update(folder, change) -> dict:
    with _LOCK:
        data = load(folder)
        change(data)
        _path(folder).write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        return data


def view(s: dict) -> dict:
    """A schedule as the page shows it: with when it runs next and whether it is running now."""
    try:
        nxt = None if s.get("paused") else upcoming(s["cron"], s.get("tz"), 1)[0]
    except CronError:
        nxt = None
    return {**s, "next_run": nxt, "running": s["id"] in _RUNNING}


def save(folder, owner: str, body: dict) -> dict:
    ask = str(body.get("ask") or "").strip()[:2000]
    if not ask:
        raise CronError("say what you want the brief on")
    cron, tz = " ".join(str(body.get("cron") or "").split()), str(body.get("tz") or "UTC")
    upcoming(cron, tz, 1)  # parses, or says why not
    sid = str(body.get("id") or "") or uuid.uuid4().hex[:10]
    title = str(body.get("title") or "").strip()[:80] or (ask if len(ask) <= 60 else ask[:57].rsplit(" ", 1)[0] + "…")

    def put(data):
        old = data.get(sid)
        if old and old["owner"].lower() != owner.lower():
            raise CronError("that brief is someone else's")
        data[sid] = {**(old or {"created": time.time(), "editions": [], "last_run": None}),
                     "id": sid, "owner": owner, "title": title, "ask": ask, "cron": cron, "tz": tz,
                     "paused": bool(body.get("paused"))}
    return view(update(folder, put)[sid])


def mine(folder, owner: str) -> list[dict]:
    return [view(s) for s in sorted(load(folder).values(), key=lambda s: -s["created"])
            if s["owner"].lower() == owner.lower()]


# ------------------------------------------------------------------ running

PROMPT = ("This is a scheduled brief for {owner}, \"{title}\". They want a brief on: {ask}\n"
          "Work from the latest complete month in the data. Lead with what changed and whether it is out of the "
          "ordinary, with the numbers, then what is behind it. Compute what you need; keep it to a few sentences.")


async def run(m, sid: str, ask_handler) -> dict:
    """One edition: the agent asked through /ask in a conversation of its own, its answer and results kept."""
    s = load(m.folder).get(sid)
    if s is None or sid in _RUNNING:
        return {}
    _RUNNING.add(sid)
    started = time.time()
    chunks, results, final, error = [], [], None, None
    try:
        resp = await ask_handler(m.id, {"message": PROMPT.format(**s), "who": s["owner"],
                                        "session_id": f"brief-{sid}-{int(started)}"})
        async for line in resp.body_iterator:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            d = ev.get("data") or {}
            if ev.get("type") == "chunk":
                chunks.append(d.get("text", "") if isinstance(d, dict) else str(d))
            elif ev.get("type") == "pbi_result":
                results.append(d)
            elif ev.get("type") == "result":
                final = d.get("answer") if isinstance(d, dict) else None
            elif ev.get("type") == "error":
                error = d.get("message") if isinstance(d, dict) else str(d)
    except Exception as exc:  # no LLM configured, the agent failing: the edition says so
        error = getattr(exc, "detail", None) or str(exc)
    finally:
        _RUNNING.discard(sid)
    edition = {"at": started, "seconds": round(time.time() - started, 1), "answer": (final or "".join(chunks)).strip(),
               "results": results[-6:], **({"error": error} if error else {})}

    def put(data):
        if sid in data:
            data[sid]["editions"] = [edition, *data[sid].get("editions", [])][:KEEP]
            data[sid]["last_run"] = started
    update(m.folder, put)
    return edition


def due(s: dict, now: float) -> bool:
    if s.get("paused") or s["id"] in _RUNNING:
        return False
    try:
        z = zone(s.get("tz"))
        since = datetime.fromtimestamp(s.get("last_run") or s["created"], z)
        return Cron(s["cron"]).next_after(since).timestamp() <= now
    except CronError:
        return False


async def loop(models, ask_handler, every: float = 20):
    """Every few seconds, each brief whose time has come since it last ran gets one edition."""
    while True:
        await asyncio.sleep(every)
        try:
            for m in list(models().values()):
                for s in list(load(m.folder).values()):
                    if due(s, time.time()):
                        await run(m, s["id"], ask_handler)
        except Exception as exc:  # a bad file or a failed run must not stop the others
            print(f"scheduled briefs: {exc}")


def install(app, models, model, ask_handler) -> None:
    """The endpoints, and the loop started with the app."""
    from fastapi import Body, HTTPException

    @app.on_event("startup")
    async def _start():
        app.state.briefs = asyncio.create_task(loop(models, ask_handler))

    def who(owner: str | None) -> str:
        owner = str(owner or "").strip()[:60]
        if not owner or owner.lower() == "me":
            raise HTTPException(400, "say who you are: the brief is yours")
        return owner

    @app.get("/api/pbi/{mid}/schedules")
    def schedules(mid: str, owner: str):
        return {"schedules": mine(model(mid).folder, who(owner))}

    @app.post("/api/pbi/{mid}/schedules/preview")
    def preview(mid: str, body: dict = Body(...)):
        """When an expression runs next, or why it is not one."""
        try:
            return {"ok": True, "next": upcoming(str(body.get("cron") or ""), body.get("tz"), 3)}
        except CronError as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/pbi/{mid}/schedules")
    def schedule_save(mid: str, body: dict = Body(...)):
        try:
            return save(model(mid).folder, who(body.get("owner")), body)
        except CronError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.delete("/api/pbi/{mid}/schedules/{sid}")
    def schedule_delete(mid: str, sid: str, owner: str):
        o = who(owner)

        def drop(data):
            if sid in data and data[sid]["owner"].lower() == o.lower():
                del data[sid]
        update(model(mid).folder, drop)
        return {"ok": True}

    @app.post("/api/pbi/{mid}/schedules/{sid}/run")
    async def schedule_run(mid: str, sid: str, body: dict = Body(default={})):
        """Write an edition now, without waiting for its time; the page polls until it lands."""
        m = model(mid)
        s = load(m.folder).get(sid)
        if s is None or s["owner"].lower() != who(body.get("owner")).lower():
            raise HTTPException(404, "no such brief")
        asyncio.get_running_loop().create_task(run(m, sid, ask_handler))
        await asyncio.sleep(0)
        return view({**s}) | {"running": True}
