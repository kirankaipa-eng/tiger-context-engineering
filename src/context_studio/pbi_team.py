"""The team space over a Power BI model: findings the agents went looking for, that the team works on together.

    scout(m)            every measure that adds up its rows, the latest month against the month before and the
                        same month a year before, investigated (BIToolkit.explain); what broke from its own pattern
                        becomes a finding, and the Skeptic tries to knock it down before anyone sees it
    store               team.json beside the model: findings with their comments, owner and status, so a link to a
                        finding opens the same finding, with its thread, for whoever has the link

The Skeptic's checks are deterministic, each a query that ran: the month is complete (days with rows), the change
is many rows rather than one large one, spread over the month rather than booked at once, and a month-on-month
jump did not happen in the same month a year earlier (the season). A finding that fails one is kept, marked, so the team sees the doubt rather than a hidden filter.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import threading
import time
from pathlib import Path

from insurance_context.bilayer import parse_ref

_LOCK = threading.Lock()
_MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def _name(month: str) -> str:
    """2026-09 -> September 2026, as a business reader says it."""
    y, m = month.split("-")
    return f"{_MONTH[int(m) - 1]} {y}"


def _store(folder: Path) -> Path:
    return folder / "team.json"


def load(folder: Path) -> dict:
    p = _store(folder)
    if not p.exists():
        return {"findings": {}, "scouted": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save(folder: Path, data: dict) -> None:
    p = _store(folder)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)


def update(folder: Path, fn) -> dict:
    """Read, change and write the team store under a lock (several people at once)."""
    with _LOCK:
        data = load(folder)
        fn(data)
        save(folder, data)
        return data


def _additive(layer) -> list[str]:
    """Measures that add up their rows (a sum or a count, no period, latest or removed context): the ones a
    change can be split into parts of."""
    out = []
    for k, m in layer.metrics.items():
        if m.get("hidden") or m.get("status", "captured") != "captured" or not m.get("agg") or m.get("formula"):
            continue
        if any(m.get(x) for x in ("time", "latest", "context", "rank", "by_scope", "iterate")):
            continue
        out.append(k)
    return out


def _period(layer, metric: str) -> str | None:
    """The month grain of the calendar the measure's rows reach: the narrowest table with a date column."""
    reach = set(layer.reachable(layer.metrics[metric]["table"]))
    best = None
    for t, info in layer.tables.items():
        cols = info.get("columns") or {}
        dates = [c for c, i in cols.items() if (i.get("type") or "").lower().startswith("date")]
        if t in reach and dates and (best is None or len(cols) < best[0]):
            best = (len(cols), f"{t}[{dates[0]}]:month" if re.fullmatch(r"\w+", t) else f"'{t}'[{dates[0]}]:month")
    return best[1] if best else None


def _shift(month: str, n: int) -> str:
    y, m = map(int, month.split("-"))
    k = y * 12 + (m - 1) + n
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def _months(kit, metric: str, period: str) -> list[str]:
    sql = kit.layer.compile([metric], [period], dialect=kit.wh.dialect, row_filters=kit.row_filters)
    return sorted(str(r[0]) for r in kit.wh.query(sql, limit=5000)[1] if r[0] is not None)


def skeptic(kit, inv: dict) -> list[dict]:
    """Try to knock the finding down. Each check: {check, passed, said}."""
    period, now, before, metric = inv["period"], inv["now"], inv["before"], inv["metric"]
    path = {s["by"]: s["value"] for s in inv["steps"]}
    last = inv["steps"][-1]
    out = []
    # 1. the month is complete: days with rows against the days in it
    try:
        day = re.sub(r":month$", ":day", period)
        sql = kit.layer.explore_sql([day], None, {period: now}, None, None, False, 40, True, None,
                                    row_filters=kit.row_filters, dialect=kit.wh.dialect)
        have, (y, mo) = len(kit.wh.query(sql, limit=40)[1]), map(int, now.split("-"))
        days = calendar.monthrange(y, mo)[1]
        out.append({"check": "complete", "passed": have >= days - 2,
                    "said": f"{_name(now)} is complete: data for all {days} days is in" if have >= days - 2 else
                    f"{_name(now)} may not be complete yet: data is in for only {have} of {days} days, so some of "
                    "the change may just be late data"})
    except Exception as exc:  # noqa: BLE001
        out.append({"check": "complete", "passed": None, "said": f"we could not check whether the month is complete ({exc})"})
    # 2. many rows, not one large one
    try:
        r = kit.layer.rows_behind(metric, where={**path, period: now}, limit=5000, row_filters=kit.row_filters,
                                  dialect=kit.wh.dialect)
        leaf = kit.layer.metrics[r["leaves"][0]["key"]] if r.get("leaves") else kit.layer.metrics[metric]
        col = re.search(r"\{([^}]+)\}", leaf.get("expr") or "")
        n_now = kit.wh.query(r["total_sql"])[1][0][0]
        cols, rows = kit.wh.query(r["sql"], limit=5000)
        delta = abs(last["after"] - last["before"]) or 1
        if col and col.group(1) in cols:
            i = cols.index(col.group(1))
            big = max((abs(float(x[i] or 0)) for x in rows), default=0.0)
            share = big / delta
            out.append({"check": "many_rows", "passed": share < 0.25,
                        "said": f"it is {n_now:,} separate transactions, and no single one is more than {max(share, 0.01):.0%} of it"
                        if share < 0.25 else f"one transaction is {share:.0%} of it: worth checking that one before acting"})
        else:
            out.append({"check": "many_rows", "passed": n_now >= 10,
                        "said": f"it is {n_now:,} separate transactions" if n_now >= 10 else
                        f"it is only {n_now} transactions, so treat it with care"})
        # 3. spread over the month, not booked at once: the days those rows fall on
        dated = [i for i, c in enumerate(cols) if ":" not in c and c in r.get("columns", []) and
                 (kit.layer.tables.get(parse_ref(c)[0], {}).get("columns", {}).get(parse_ref(c)[1], {}).get("type")
                  or "").lower().startswith("date")]
        if dated and len(rows) < 5000:
            on = len({str(x[dated[0]])[:10] for x in rows if x[dated[0]] is not None})
            y, mo = map(int, now.split("-"))
            days = calendar.monthrange(y, mo)[1]
            ok = on >= min(10, days // 2)
            out.append({"check": "spread", "passed": ok,
                        "said": f"they happened across {on} different days, not in one batch" if ok else
                        f"they happened on only {on} days: it may be one batch entered at once"})
    except Exception as exc:  # noqa: BLE001
        out.append({"check": "many_rows", "passed": None, "said": f"we could not read the transactions behind it ({exc})"})
    # 4. not the season: a month-on-month jump that also happened a year earlier
    if _shift(now, -12) == before:
        out.append({"check": "season", "passed": True,
                    "said": f"it is compared with {_name(before)}, the same time last year, so it is not seasonal"})
    else:
        try:
            a, b = _shift(now, -12), _shift(before, -12)
            sql = kit.layer.compile([metric], [period], {**path, period: [a, b]}, dialect=kit.wh.dialect,
                                    row_filters=kit.row_filters)
            got = {str(x[0]): float(x[1] or 0) for x in kit.wh.query(sql, limit=10)[1]}
            then = got.get(a, 0.0) - got.get(b, 0.0)
            change = last["after"] - last["before"]
            same = then * change > 0 and abs(then) >= 0.5 * abs(change)
            out.append({"check": "season", "passed": not same,
                        "said": f"the same thing happened at this time last year ({then:+,.0f}), so it may be seasonal"
                        if same else "it did not happen at this time last year, so it is not seasonal"})
        except Exception as exc:  # noqa: BLE001
            out.append({"check": "season", "passed": None, "said": f"we could not compare with last year ({exc})"})
    return out


def scout(m) -> dict:
    """Investigate every additive measure, latest month against the month before and a year before; keep what
    broke from its own pattern, once per place it sits, with the Skeptic's checks. Returns the findings and what
    was checked."""
    kit = m.kit()
    started, checked, found = time.monotonic(), [], {}
    for metric in _additive(m.layer):
        period = _period(m.layer, metric)
        if not period:
            continue
        try:
            months = _months(kit, metric, period)
        except Exception:  # noqa: BLE001 - a measure the warehouse cannot answer is skipped
            continue
        if len(months) < 14:
            continue
        now = months[-1]
        for before in (_shift(now, -1), _shift(now, -12)):
            if before not in months:
                continue
            out = kit.explain(metric, period, now, before)
            inv = (kit.results[-1] or {}).get("explain") if kit.results else None
            checked.append({"metric": metric, "label": m.layer.metrics[metric].get("label", metric),
                            "now": now, "before": before, "verdict": inv["verdict"] if inv else "error"})
            if not inv or inv["verdict"] != "path" or out.startswith("Error"):
                continue
            where = frozenset(s["value"] for s in inv["steps"])
            impact = abs(inv["steps"][-1]["after"] - inv["steps"][-1]["before"])
            # the same story in another measure (Returns and Units Returned): one finding, the first measure's,
            # that names the others
            prior = next((f for (w2, n2, b2), f in found.items() if (n2, b2) == (now, before) and
                          (where <= w2 or w2 <= where or len(where & w2) >= 2)), None)
            if prior is not None:
                prior.setdefault("also", []).append(inv["label"])
                continue
            fid = hashlib.sha1(f"{metric}|{'|'.join(sorted(where))}|{now}|{before}".encode()).hexdigest()[:10]
            found[(where, now, before)] = {"id": fid, "metric": metric, "label": inv["label"], "now": now,
                                           "before": before, "impact": impact, "explain": inv, "also": [],
                                           "source": "scout"}
    for f in found.values():
        f["checks"] = skeptic(kit, f["explain"])
    ms = int((time.monotonic() - started) * 1000)

    def merge(data):
        old = data.get("findings", {})
        keep = {k: v for k, v in old.items() if v.get("source") != "scout" or v.get("comments") or v.get("owner")}
        for f in found.values():
            prev = old.get(f["id"], {})
            keep[f["id"]] = {**f, "comments": prev.get("comments", []), "owner": prev.get("owner"),
                             "status": prev.get("status", "new"), "created": prev.get("created", time.time())}
        data["findings"] = keep
        data["scouted"] = {"at": time.time(), "ms": ms, "checked": checked,
                           "measures": len({c["metric"] for c in checked})}
    return update(m.folder, merge)


def ranked(data: dict) -> list[dict]:
    order = {"new": 0, "claimed": 1, "acted": 2, "resolved": 3}
    return sorted(data.get("findings", {}).values(),
                  key=lambda f: (order.get(f.get("status", "new"), 0),
                                 -sum(1 for c in f.get("checks", []) if c.get("passed")), -f.get("impact", 0)))


def touches(f: dict, period: str | None = None) -> list[str]:
    """The columns a finding narrows to, for display."""
    return [parse_ref(s["by"])[1] for s in f["explain"]["steps"]]
