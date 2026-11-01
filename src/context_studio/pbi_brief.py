"""The living brief: what a leader reads first. Where the headline measures will land, and the few things that
moved out of their own pattern, ranked by how much of the measure they are, each with where it sits, since
when, whether it is growing or easing, and who has it. Worked out again from the data every time, as of the
last complete month (or any earlier one: what the brief said then).

    brief(m, as_of=None)   -> {as_of, data_through, landing: [...], claims: [...], settled: [...], months, ms}

A claim is a place (a measure narrowed to the values the investigation found, e.g. Returns at Leo, XBOX ONE)
whose month sits far outside its own last twelve months. Its status comes from the data alone: new this
month, growing / holding / easing against last month, or settled (back within its usual range after being
out of it last month). Nothing is said to have caused anything: a settled claim with an action on it says
when the action was taken, and leaves the reader to judge.

Targets (plan, budget) live in targets.yaml beside the model, set from the brief; without one a quarter is
compared with the same quarter a year before.

Each person's brief is their own (briefs.json beside the model, by name): the measures they follow, the slice
of the business they answer for (scope: {Store[Store]: [Leo, Fama]}), and which measures get a landing. All
of it, the landing, the claims and the map, is worked out inside that slice, and what they own comes first.
"""

from __future__ import annotations

import math
import time
from datetime import date
from pathlib import Path

import json

import yaml

from context_studio import pbi_team
from insurance_context.bistats import smooth, standing, surprise

Z = 3.0          # how far outside its own history a month must sit to be a claim
FLOOR = 0.005    # and how much of the measure's month it must be (half a percent), so trivia stay out
TOP = 5


def _month_name(m: str) -> str:
    return pbi_team._name(m)


def _quarter(m: str) -> tuple[str, list[str]]:
    y, mo = int(m[:4]), int(m[5:7])
    q = (mo - 1) // 3
    return f"{y}Q{q + 1}", [f"{y}-{3 * q + k + 1:02d}" for k in range(3)]


def prefs(folder: Path, who: str | None) -> dict:
    """A person's brief settings: measures (followed), headline (landings) and scope (their slice); {} if none."""
    p = folder / "briefs.json"
    if not who or not p.exists():
        return {}
    return (json.loads(p.read_text(encoding="utf-8")).get(who.strip().lower()) or {}).get("prefs") or {}


def save_prefs(folder: Path, who: str, new: dict) -> dict:
    with pbi_team._LOCK:
        p = folder / "briefs.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        data[who.strip().lower()] = {"who": who.strip(), "prefs": new, "at": time.time()}
        p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    _CACHE.clear()
    _MAP.clear()
    return new


def _series(kit, metric: str, period: str, where: dict | None = None) -> dict[str, float]:
    sql = kit.layer.compile([metric], [period], where=where or None, dialect=kit.wh.dialect, row_filters=kit.row_filters)
    return {str(r[0]): float(r[1] or 0) for r in kit.wh.query(sql, limit=5000)[1] if r[0] is not None}


def _last_day(kit, metric: str, period: str, month: str) -> str:
    day = period.rsplit(":", 1)[0] + ":day"
    rows = _series(kit, metric, day, {period: month})
    return max((d for d, v in rows.items() if v), default=f"{month}-01")[:10]


def _complete(month: str, last: str) -> bool:
    y, m = int(month[:4]), int(month[5:7])
    end = date(y + (m == 12), m % 12 + 1, 1).toordinal() - 1
    return date.fromisoformat(last).toordinal() >= end


def _money(layer, metric: str) -> bool:
    return any(c in (layer.metrics[metric].get("format") or "") for c in "$€£¥₹")


def _headline(m, mine: list[str] | None = None) -> list[str]:
    conf = _conf(m.folder)
    add = pbi_team._additive(m.layer)
    if mine:
        return [k for k in mine if k in m.layer.metrics][:3]
    if conf.get("headline"):
        return [k for k in conf["headline"] if k in m.layer.metrics][:3]
    money = [k for k in add if _money(m.layer, k)]
    return (money or add)[:2]


def _conf(folder: Path) -> dict:
    p = folder / "brief.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def targets(folder: Path) -> dict:
    p = folder / "targets.yaml"
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def set_target(folder: Path, metric: str, quarter: str, value: float | None, who: str) -> dict:
    t = targets(folder)
    if value is None:
        t.get(metric, {}).pop(quarter, None)
    else:
        t.setdefault(metric, {})[quarter] = {"value": float(value), "by": who, "on": date.today().isoformat()}
    (folder / "targets.yaml").write_text(yaml.safe_dump(t, sort_keys=True), encoding="utf-8")
    return t


def _landing(m, kit, metric: str, period: str, months: list[str], as_of: str, scope: dict | None = None) -> dict:
    s = _series(kit, metric, period, scope)
    hist = [x for x in months if x <= as_of]
    values = [s.get(x, 0.0) for x in hist]
    q, qm = _quarter(as_of)
    done = [x for x in qm if x <= as_of]
    if len(done) == 3:  # the quarter is closed: say how it closed, and look at the next one
        closed = {"quarter": q, "actual": sum(s.get(x, 0.0) for x in qm)}
        y, n = int(q[:4]), int(q[-1])
        q, qm = (f"{y + 1}Q1", [f"{y + 1}-0{k}" for k in (1, 2, 3)]) if n == 4 else _quarter(f"{y}-{3 * n + 1:02d}")
        done = []
    else:
        closed = None
    ahead = len(qm) - len(done)
    f = smooth(values, ahead, 12) if len(values) >= 8 else None
    so_far = sum(s.get(x, 0.0) for x in done)
    land = so_far + sum(f["forecast"]) if f else None
    low = so_far + sum(f["low"]) if f else None
    high = so_far + sum(f["high"]) if f else None
    last_year = f"{int(q[:4]) - 1}{q[4:]}"
    ly = sum(s.get(f"{int(x[:4]) - 1}{x[4:]}", 0.0) for x in qm)
    tgt = (targets(m.folder).get(metric) or {}).get(q)
    return {"metric": metric, "label": m.layer.metrics[metric].get("label", metric),
            "format": m.layer.metrics[metric].get("format"), "quarter": q, "months": qm, "so_far": so_far,
            "months_done": len(done), "landing": land, "low": low, "high": high,
            "miss": None if not f or math.isnan(f["miss"]) else f["miss"], "method": f["method"] if f else None,
            "last_year": {"quarter": last_year, "value": ly} if ly else None, "target": tgt, "closed": closed,
            "history": [[x, s.get(x, 0.0)] for x in hist[-15:]],
            "ahead": [[x, v, lo, hi] for x, v, lo, hi in zip(qm[len(done):], *(f[k] for k in ("forecast", "low", "high")))]
            if f else []}


def _standing(series: dict[str, float], months: list[str], month: str) -> dict | None:
    if month not in months:
        return None
    i = months.index(month)
    past = [series.get(x, 0.0) for x in months[max(0, i - 12):i]]
    if len(past) < 6:
        return None
    return {**standing(past, series.get(month, 0.0)), "value": series.get(month, 0.0)}


def _out(st: dict | None, total: float) -> bool:
    return bool(st) and abs(st["z"]) >= Z and abs(st["excess"]) >= FLOOR * abs(total or 1)


_CACHE: dict[tuple, dict] = {}


def brief(m, as_of: str | None = None, me: str | None = None) -> dict:
    """`me`'s brief as of `as_of` (default: the last complete month), inside their slice. The numbers are cached
    for the same month, settings and data (ten minutes at most); who owns what is read again every time."""
    pr = prefs(m.folder, me)
    key = (str(m.folder), as_of, json.dumps(pr, sort_keys=True))
    hit = _CACHE.get(key)
    if not hit or time.time() - hit["_at"] > 600:
        hit = _CACHE[key] = {**_brief(m, as_of, pr), "_at": time.time()}
    team = pbi_team.load(m.folder).get("findings", {})
    out = {k: v for k, v in hit.items() if k != "_at"}
    for c in out.get("claims", []) + out.get("settled", []):
        values = {w["value"] for w in c["where"]}
        mine = next((f for f in team.values() if f.get("metric") == c["metric"] and
                     {x["value"] for x in f.get("explain", {}).get("steps", [])} == values), None)
        c["finding"] = {k: mine.get(k) for k in ("id", "owner", "status", "comments")} if mine else None
        c["mine"] = bool(me and mine and (mine.get("owner") or "").strip().lower() == me.strip().lower())
    if out.get("claims"):  # what is theirs first, then by size
        out["claims"] = sorted(out["claims"], key=lambda c: (not c["mine"], -c["share"]))
    out["me"] = me
    for l in out.get("landing", []):
        l["target"] = (targets(m.folder).get(l["metric"]) or {}).get(l["quarter"])
    return out


def _brief(m, as_of: str | None = None, pr: dict | None = None) -> dict:
    started = time.monotonic()
    kit = m.kit()
    pr = pr or {}
    scope = {k: v for k, v in (pr.get("scope") or {}).items() if v}
    every = [k for k in pbi_team._additive(m.layer) if pbi_team._period(m.layer, k)]
    metrics = [k for k in (pr.get("measures") or []) if k in every] or every
    if not metrics:
        return {"error": "no measure here adds up by month, so there is nothing to brief"}
    first = metrics[0]
    period = pbi_team._period(m.layer, first)
    months = pbi_team._months(kit, first, period)
    last = _last_day(kit, first, period, months[-1])
    complete = [x for x in months if x < months[-1] or _complete(x, last)]
    as_of = as_of if as_of in complete else complete[-1]
    prev = pbi_team._shift(as_of, -1)

    landing = []
    for metric in _headline(m, pr.get("headline")):
        p = pbi_team._period(m.layer, metric)
        if p:
            landing.append(_landing(m, kit, metric, p, months, as_of, scope))

    # places that moved: what the investigation finds this month and last, each against a month and a year before
    places: dict[tuple, dict] = {}
    for metric in metrics:
        p = pbi_team._period(m.layer, metric)
        for now in (as_of, prev):
            for before in (pbi_team._shift(now, -1), pbi_team._shift(now, -12)):
                if before not in months:
                    continue
                if kit.explain(metric, p, now, before, scope or None).startswith("Error"):
                    continue
                inv = kit.results[-1].get("explain") if kit.results else None
                if not inv or inv.get("verdict") != "path":
                    continue
                path = tuple((s["by"], s["value"]) for s in inv["steps"])
                places.setdefault((metric, path), {"metric": metric, "period": p, "path": path, "explain": inv})

    claims, settled, seen = [], [], []
    totals: dict[str, dict[str, float]] = {}
    for (metric, path), c in places.items():
        p = c["period"]
        totals.setdefault(metric, _series(kit, metric, p, scope))
        s = _series(kit, metric, p, {**scope, **dict(path)})
        now, then = _standing(s, months, as_of), _standing(s, months, prev)
        out_now, out_then = _out(now, totals[metric].get(as_of, 0)), _out(then, totals[metric].get(prev, 0))
        if not (out_now or out_then):
            continue
        values = {v for _, v in path}
        since = as_of
        while out_now and _out(_standing(s, months, pbi_team._shift(since, -1)),
                               totals[metric].get(pbi_team._shift(since, -1), 0)):
            since = pbi_team._shift(since, -1)
        if out_now and not out_then:
            status = "new"
        elif out_now:
            r = now["excess"] / then["excess"] if then["excess"] else 1
            status = "growing" if r > 1.15 else "easing" if r < 0.85 else "holding"
        else:
            status = "settled"
        st = now if out_now else then
        claim = {"metric": metric, "label": m.layer.metrics[metric].get("label", metric),
                 "format": m.layer.metrics[metric].get("format"), "where": [{"by": b, "value": v} for b, v in path],
                 "status": status, "month": as_of if out_now else prev, "since": since, "value": st["value"],
                 "usual": st["usual"], "excess": st["excess"], "z": st["z"] if math.isfinite(st["z"]) else None,
                 "share": abs(st["excess"]) / abs(totals[metric].get(as_of if out_now else prev) or 1),
                 "now": now, "series": [[x, s.get(x, 0.0)] for x in months if x <= as_of][-13:],
                 "explain": c["explain"], "period": p}
        seen.append({"metric": metric, "values": values, "claim": claim, "now": out_now})
    # the same story told by several measures (Returns, Units Returned at Leo): kept once, in a money measure
    # if one tells it, naming the others
    kept = []
    for x in sorted(seen, key=lambda x: (not _money(m.layer, x["metric"]), -x["claim"]["share"])):
        twin = next((k for k in kept if k["now"] == x["now"] and (len(k["values"] & x["values"]) >= 2 or
                                                                  k["values"] <= x["values"] or x["values"] <= k["values"])), None)
        if twin is None:
            kept.append(x)
        elif x["metric"] != twin["metric"] and x["claim"]["label"] not in twin["claim"].setdefault("also", []):
            twin["claim"]["also"].append(x["claim"]["label"])
    for x in kept:
        (claims if x["now"] else settled).append(x["claim"])
    return {"as_of": as_of, "as_of_name": _month_name(as_of), "data_through": last, "complete": complete[-24:],
            "partial": months[-1] if months[-1] not in complete else None, "landing": landing,
            "claims": claims[:TOP], "more": max(0, len(claims) - TOP), "settled": settled[:3],
            "checked": len(metrics), "ms": int((time.monotonic() - started) * 1000),
            "prefs": {"measures": metrics if pr.get("measures") else [], "headline": [x["metric"] for x in landing],
                      "scope": scope},
            "choices": [{"metric": k, "label": m.layer.metrics[k].get("label", k)} for k in every],
            "slices": sorted(kit._splits(every[0], period), key=lambda d: d.lower())}


# ------------------------------------------------------------------ the pattern map

_MAP: dict[tuple, dict] = {}
SHOW = 18     # months on the map's timeline
WIDE = (14, 8)  # values kept on the outer and inner level; the rest are one "Other" tile


def _levels(m, kit, metric: str, period: str, claim: dict | None, as_of: str, scope: dict) -> list[str]:
    """Two columns to map by: the top claim's first and last (Store, then Product), so the map and the claim
    tell one story; otherwise the two split columns with the fewest values that still split (2 to 40) inside
    the person's slice."""
    if claim:
        bys = list(dict.fromkeys([claim["where"][0]["by"], claim["where"][-1]["by"]]))
        if len(bys) == 2:
            return bys
    sized = []
    for d in kit._splits(metric, period):
        try:
            n = len(_series(kit, metric, d, {**scope, period: as_of}))
        except Exception:  # noqa: BLE001 - a column the warehouse cannot group by is skipped
            continue
        if 2 <= n <= 40:
            sized.append((n, d))
    sized.sort()
    picked = [d for _, d in sized[:2]]
    if claim and claim["where"][0]["by"] not in picked:
        picked = [claim["where"][0]["by"], *picked][:2]
    return picked


def pattern(m, metric: str | None = None, as_of: str | None = None, me: str | None = None) -> dict:
    """Every place a measure adds up from, two levels deep (a store, then a product in it), month by month:
    its value, and how far it sits from its own last twelve months (z). The timeline is the last SHOW
    months to `as_of`; each month's out-of-pattern places are those a claim would be made of."""
    b = brief(m, as_of, me)
    if b.get("error"):
        return b
    scope = b["prefs"]["scope"]
    as_of = b["as_of"]
    claim = b["claims"][0] if b["claims"] else None
    metric = metric if metric in m.layer.metrics else (claim["metric"] if claim else b["landing"][0]["metric"])
    key = (str(m.folder), metric, as_of, json.dumps(scope, sort_keys=True))
    if key in _MAP and time.time() - _MAP[key]["_at"] < 600:
        return {k: v for k, v in _MAP[key].items() if k != "_at"}
    kit = m.kit()
    period = pbi_team._period(m.layer, metric)
    if claim and claim["metric"] != metric:
        claim = next((c for c in b["claims"] if c["metric"] == metric), None)
    levels = _levels(m, kit, metric, period, claim, as_of, scope)
    if not levels:
        return {"error": f"{m.layer.metrics[metric].get('label', metric)} has no column to map it by here"}
    months = [x for x in pbi_team._months(kit, metric, period) if x <= as_of]
    shown = months[-SHOW:]
    reach = months[-(SHOW + 12):]
    sql = kit.layer.compile([metric], [*levels, period], where={**scope, period: reach}, dialect=kit.wh.dialect,
                            row_filters=kit.row_filters)
    cells: dict[tuple[str, str], dict[str, float]] = {}
    for r in kit.wh.query(sql, limit=200000)[1]:
        a, c, mo, v = (r[0], r[0], r[1], r[2]) if len(levels) == 1 else r  # one level: each value is its own tile
        cells.setdefault((str(a), str(c)), {})[str(mo)] = float(v or 0)
    total = lambda path: sum(sum(s.get(x, 0.0) for x in shown) for k, s in cells.items() if k[:len(path)] == path)  # noqa: E731
    outer = sorted({a for a, _ in cells}, key=lambda a: -total((a,)))
    keep = outer[:WIDE[0]]
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    for (a, c), s in cells.items():
        a2 = a if a in keep else "Other"
        inner = sorted({c2 for a3, c2 in cells if a3 == a}, key=lambda c2: -total((a, c2)))[:WIDE[1]] if a in keep else []
        c2 = c if c in inner else "Other"
        dest = grouped.setdefault((a2, c2), {})
        for mo, v in s.items():
            dest[mo] = dest.get(mo, 0.0) + v
    month_total = {mo: sum(s.get(mo, 0.0) for s in grouped.values()) for mo in shown}
    # many places watched over many months: lit only where chance would put none of them (5%, Bonferroni),
    # each judged against a prediction from its own last twelve months
    alpha = 0.05 / max(1, len(grouped) * len(shown))
    groups: dict[str, list] = {}
    for (a, c), s in grouped.items():
        vals = [s.get(mo, 0.0) for mo in reach]
        zs, out = [], []
        for mo in shown:
            i = reach.index(mo)
            past = vals[max(0, i - 12):i]
            st = standing(past, vals[i]) if len(past) >= 6 else None
            z = st["z"] if st else 0.0
            z = max(-9.0, min(9.0, z)) if math.isfinite(z) else math.copysign(9.0, z)
            zs.append(round(z, 2))
            out.append(bool(st) and surprise(past, vals[i]) < alpha and abs(st["excess"]) >= FLOOR * abs(month_total[mo] or 1))
        groups.setdefault(a, []).append({"name": c, "values": [s.get(mo, 0.0) for mo in shown], "z": zs, "out": out})
    order = [a for a in keep if a in groups] + (["Other"] if "Other" in groups else [])
    out = {"metric": metric, "label": m.layer.metrics[metric].get("label", metric),
           "format": m.layer.metrics[metric].get("format"), "levels": levels, "months": shown, "as_of": as_of,
           "groups": [{"name": a, "children": sorted(groups[a], key=lambda t: -sum(t["values"]))} for a in order],
           "disturbed": [sum(1 for g in groups.values() for t in g if t["out"][i]) for i in range(len(shown))],
           "alpha": alpha, "places": len(grouped),
           "claim": [w["value"] for w in claim["where"] if w["by"] in levels] if claim else None,
           "choices": [{"metric": x["metric"], "label": x["label"]} for x in b["landing"]]
           + [{"metric": c["metric"], "label": c["label"]} for c in b["claims"] if c["metric"] not in
              {x["metric"] for x in b["landing"]}]}
    _MAP[key] = {**out, "_at": time.time()}
    return out
