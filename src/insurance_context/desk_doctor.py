"""Where a measure fails: each step the desk takes for it, run one at a time, PASS or FAIL, and the first that failed.

    python -m insurance_context.desk_doctor <model folder> <measure key or label>
    python -m insurance_context.desk_doctor <model folder> <measure> --sql     also print the SQL of the scoped query

No language model is involved: what fails here fails for the agent too. It runs a handful of queries on the warehouse.
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from pathlib import Path


OTHERS = 8  # the other values of one scope filter step 8 tries


def _value(kit, layer, metric: str, where: dict, by: list | None = None) -> tuple[object, str]:
    """(the number, the SQL), or (an Exception, the SQL or ""). With `by`, the first row that has a number."""
    sql = ""
    try:
        resolved = kit.resolve(dict(where))[0]
        sql = layer.compile([metric], list(by or []), resolved, dialect=kit.wh.dialect, row_filters=kit.row_filters)
        rows = kit.wh.query(sql, limit=5)[1]
        return next((r[-1] for r in rows if r[-1] is not None), None), sql
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        return exc, sql


def _catalog(layer, t: str) -> str:
    parts = str((layer.tables.get(t) or {}).get("table") or "").split(".")
    return parts[0] if len(parts) == 3 else "(default)"


def _sql_tables(layer, sql: str) -> list[str]:
    """The model's tables a query reads: those whose warehouse name is in its SQL, quoted or not."""
    plain = re.sub(r"[`\"\[\]]", "", sql).lower()
    return sorted(t for t, i in layer.tables.items() if i.get("table") and re.search(
        r"(?<![\w.])" + re.escape(str(i["table"]).lower()) + r"(?![\w])", plain))


def _grain(layer, key: str) -> list[str]:
    try:
        return list((layer.needs(key) or {}).get("by") or [])
    except Exception:  # noqa: BLE001
        return []


def _said(v) -> str:
    if isinstance(v, Exception):
        return f"refused: {str(v)[:300]}"
    return "no number (NULL)" if v is None else f"{v}"


def main(argv=None) -> int:
    from . import desk_harness as dh
    from .bi_agent import BIToolkit
    from .bilayer import BILayer
    from .biwarehouse import Warehouse

    ap = argparse.ArgumentParser(description="Run the desk's steps for one measure and show where it fails.")
    ap.add_argument("folder")
    ap.add_argument("metric")
    ap.add_argument("--sql", action="store_true", help="print the SQL of the scoped query")
    ap.add_argument("--set", action="append", default=[], metavar="COLUMN=VALUE",
                    help="replace one owner scope value for this run, e.g. --set \"Table[Column]=value\"; may be repeated")
    ap.add_argument("--all", action="store_true", help="every captured measure, one query each: which answer, which are empty, "
                                                         "which are refused and why (pass any word as the measure)")
    ap.add_argument("--workers", type=int, default=4, help="with --all, measures run at once")
    a = ap.parse_args(argv)

    layer = BILayer.load(a.folder)
    kit = BIToolkit(layer, Warehouse.open(a.folder, layer))
    layer = kit.layer
    dh.install(kit, a.folder)
    h = kit.compute.__self__
    for x in a.set:
        c, _, v = x.partition("=")
        h.defaults[h._column(c.strip())] = int(v) if v.strip().isdigit() else v.strip()
        print(f"[doctor] owner scope for this run: {h._column(c.strip())} = {v.strip()}", flush=True)
    dh.SESSION.set("doctor")
    dh.QUESTION.set(f"what is {a.metric}")
    if a.all:
        return every_measure(kit, layer, h, a.folder, a.workers)
    failed: list[str] = []

    def step(n: int, name: str, ok: bool, said: str) -> None:
        print(f"{n}. {'PASS' if ok else 'FAIL'}  {name}\n      {said}", flush=True)
        if not ok:
            failed.append(f"step {n}, {name}: {said}")

    # 1. the measure
    by_label = {str(m.get("label") or k).lower(): k for k, m in layer.metrics.items()}
    key = a.metric if a.metric in layer.metrics else by_label.get(a.metric.lower())
    m = layer.metrics.get(key or "")
    step(1, "the measure is in the capture, captured", bool(m) and m.get("status") == "captured",
         f"{key}: {m.get('status')}" if m else f"no measure {a.metric!r}: check the key or label")
    if not m:
        return 1
    print(f"      warehouse: {kit.wh.describe()}")

    # 1b. the warehouse catalogs its tables are mapped to: one measure reading two catalogs is a mapping slip
    def catalog(t: str) -> str:
        parts = str((layer.tables.get(t) or {}).get("table") or "").split(".")
        return parts[0] if len(parts) == 3 else "(default)"

    every: dict[str, list[str]] = {}
    for t in layer.tables:
        every.setdefault(catalog(t), []).append(t)
    try:
        walk = list(dict.fromkeys([key, *layer.leaves(key)]))
    except Exception:  # noqa: BLE001 - a measure whose parts cannot be walked is refused at step 4
        walk = [key]
    named = set()
    for x in walk:
        mx = layer.metrics.get(x) or {}
        named |= {mx["table"]} if mx.get("table") else set()
        named |= {a or b for a, b in re.findall(r"'([^']+)'\[|\b([A-Za-z_][\w]*)\[", str(mx.get("dax") or ""))}
    mine = sorted(t for t in named if t in layer.tables)
    reach = {catalog(t) for t in mine}
    lines = ", ".join(f"{c}: {len(ts)} tables" for c, ts in sorted(every.items(), key=lambda x: -len(x[1])))
    odd = "" if len(every) < 2 else "; the fewer: " + "; ".join(f"{c}: {', '.join(ts[:8])}" for c, ts in
                                                                 sorted(every.items(), key=lambda x: len(x[1]))[:-1])
    step(1, "its tables are all in one warehouse catalog", len(reach) < 2,
         f"this measure reads {sorted(reach)} from {', '.join(mine) or 'no mapped table'}; the model maps {lines}{odd}")

    # 2. the owner's scope, with latest read from the warehouse
    t0 = time.monotonic()
    if not h.defaults:
        base = Path(a.folder)
        print(f"2. NOTE  no owner scope file: looked for {base / 'analysis_default.yaml'} and "
              f"{base / 'capture' / 'analysis_default.yaml'}", flush=True)
    try:
        pinned = h._pinned()
        ok = all(str(v).lower() != "latest" for v in pinned.values()) and len(pinned) == len(h.defaults)
        step(2, "the owner's scope is read and latest settled", ok or not h.defaults,
             f"{pinned or 'no owner scope'} ({time.monotonic() - t0:.1f}s)" if ok or not h.defaults else
             f"given {h.defaults}, settled only {pinned}: latest could not be read")
    except Exception as exc:  # noqa: BLE001
        pinned = {}
        step(2, "the owner's scope is read and latest settled", False, f"{exc}")

    # 3. which scope filters the measure reaches
    scope = h._scope_for([key], [], {}) if h.defaults else {}
    left_out = [c for c in pinned if c not in scope]
    step(3, "the scope filters this measure reaches", True,
         f"applied: {scope or 'none'}" + (f"; left out (its rows do not reach them): {left_out}" if left_out else ""))

    # 4. compiles and runs under the scope
    t0 = time.monotonic()
    v, sql = _value(kit, layer, key, scope)
    step(4, "compiles and returns a number under the scope", not isinstance(v, Exception) and v is not None,
         f"{_said(v)} ({time.monotonic() - t0:.1f}s)" + ("; no owner scope file was found, so no period was given: put "
                                                         "analysis_default.yaml back in the model folder" if isinstance(v, Exception)
                                                         and not h.defaults and "context" in str(v).lower() else ""))
    if sql:
        read = _sql_tables(layer, sql)
        cats = sorted({_catalog(layer, t) for t in read})
        step(4, "the tables its SQL reads are in one catalog", len(cats) < 2,
             "; ".join(f"{c}: {', '.join(t for t in read if _catalog(layer, t) == c)}" for c in cats) or "no mapped table named in the SQL")
    if a.sql and sql:
        print("      --- SQL ---\n" + "\n".join(f"      {x}" for x in sql.splitlines()) + "\n      --- end ---")

    # 5. without each scope filter: which one empties it
    if scope and (isinstance(v, Exception) or v is None):
        for c in scope:
            w, _ = _value(kit, layer, key, {x: y for x, y in scope.items() if x != c})
            good = not isinstance(w, Exception) and w is not None
            step(5, f"without {c} = {scope[c]}", good, _said(w) + ("   <-- this filter empties it" if good else ""))

    # 6. no scope at all
    w, _ = _value(kit, layer, key, {})
    step(6, "with no scope at all", not isinstance(w, Exception) and w is not None, _said(w))

    # 7. the period a question compares with
    asof = h._asof()
    if asof:
        col, now, before = asof
        w, _ = _value(kit, layer, key, {**scope, col: before})
        step(7, f"the comparison period ({col} = {before}, against {now})", not isinstance(w, Exception) and w is not None, _said(w))
        if isinstance(v, Exception) or v is None:
            found = h._with_data(key, col, now, {})
            step(7, "an earlier as-of with a number", bool(found), f"{col} = {found}" if found else "none of the earlier as-ofs has one")
    elif any(str(x).lower() == "latest" for x in h.defaults.values()):
        step(7, "a period a year before the latest, to compare with", False, "none: a why question cannot compare")

    # 8. the other values of each scope filter: which value would give a number
    if scope and (isinstance(v, Exception) or v is None):
        period = asof[0] if asof else None
        for c in scope:
            if c == period:
                continue
            try:
                held = [r[0] for r in kit.wh.query(kit._values_sql(c, {}), limit=OTHERS + 1)[1] if r[0] is not None]
            except Exception as exc:  # noqa: BLE001
                step(8, f"the other values of {c}", False, f"could not list them: {str(exc)[:200]}")
                continue
            tried = [(x, _value(kit, layer, key, {**scope, c: x})[0]) for x in held[:OTHERS] if str(x) != str(scope[c])]
            good = [f"{x}: {w}" for x, w in tried if not isinstance(w, Exception) and w is not None]
            step(8, f"{c} set to its other values ({', '.join(str(x) for x, _ in tried) or 'none'})", bool(good),
                 ("a number with " + "; ".join(good) + f"   <-- {c} = {scope[c]} is the value that empties it") if good
                 else "none of them gives a number either")

    print()
    print("FIRST FAILURE: " + failed[0] if failed else "ALL PASSED: this measure answers under the owner's scope.")
    return 1 if failed else 0


def _kind(v) -> str:
    if isinstance(v, Exception):
        return "refused"
    return "empty" if v is None else "answers"


def _shape(exc: Exception) -> str:
    """A refusal with its particulars taken out, so the same reason across measures is counted once."""
    return re.sub(r"\d+", "N", re.sub(r"'[^']*'", "'…'", str(exc).splitlines()[0] if str(exc) else type(exc).__name__))[:160]


def every_measure(kit, layer, h, folder, workers: int) -> int:
    """Each captured measure under the owner's scope, one query each. Written to doctor.json in the model folder as it goes
    (what happened to each, never a number), and summed up at the end: how many answer, and why the others do not."""
    import contextvars
    import json
    import os
    from concurrent.futures import ThreadPoolExecutor

    from . import grid as grids

    keys = [k for k, m in layer.metrics.items() if m.get("status") == "captured"]
    h._pinned()  # latest settled once, before the measures share it
    out = Path(folder) / "doctor.json"
    found: dict[str, dict] = {}
    lock = threading.Lock()
    bar = grids._Progress(len(keys), kit, quiet=False)
    last = [time.monotonic()]

    def save() -> None:
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(found, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, out)

    def one(k: str) -> None:
        t0 = time.monotonic()
        by = _grain(layer, k)
        try:
            scope = h._scope_for([k], by, {})
        except Exception:  # noqa: BLE001
            scope = dict(h._pinned())
        v, sql = _value(kit, layer, k, scope, by)
        read = _sql_tables(layer, sql) if sql else []
        e = {"result": _kind(v), "by": by, "catalogs": sorted({_catalog(layer, t) for t in read}), "tables": read,
             "secs": round(time.monotonic() - t0, 1)}
        if isinstance(v, Exception):
            e["reason"] = str(v)[:400]
        with lock:
            found[k] = e
            if time.monotonic() - last[0] > 10:
                save()
                last[0] = time.monotonic()
        bar.tick(time.monotonic() - t0)

    with ThreadPoolExecutor(max(1, workers)) as pool:
        list(pool.map(lambda k: contextvars.copy_context().run(one, k), keys))
    bar.end()
    save()

    counts = {r: sum(1 for e in found.values() if e["result"] == r) for r in ("answers", "empty", "refused")}
    print(f"\n{len(keys)} captured measures under the owner's scope: {counts['answers']} answer, {counts['empty']} empty, "
          f"{counts['refused']} refused ({len(layer.metrics) - len(keys)} not captured, not tried)")
    for r in ("empty", "answers"):
        by_cat: dict[str, int] = {}
        for e in found.values():
            if e["result"] == r:
                c = " + ".join(e["catalogs"]) or "no mapped table"
                by_cat[c] = by_cat.get(c, 0) + 1
        if by_cat:
            print(f"  {r}, by the catalogs their SQL reads: " + "; ".join(f"{c}: {n}" for c, n in sorted(by_cat.items(), key=lambda x: -x[1])))
    for r in ("empty", "answers"):
        tabs: dict[str, int] = {}
        for e in found.values():
            if e["result"] == r:
                for t in e["tables"]:
                    tabs[t] = tabs.get(t, 0) + 1
        share = {t: n for t, n in tabs.items()}
        if r == "empty" and share:
            only = [t for t in share if not any(t in e["tables"] for e in found.values() if e["result"] == "answers")]
            if only:
                print(f"  tables read only by empty measures, never by one that answers: {', '.join(sorted(only)[:15])}")
    reasons: dict[str, list[str]] = {}
    for k, e in found.items():
        if e["result"] == "refused":
            reasons.setdefault(_shape(Exception(e["reason"])), []).append(k)
    if reasons:
        print("  refused, by reason:")
        for why, ks in sorted(reasons.items(), key=lambda x: -len(x[1]))[:10]:
            print(f"    {len(ks):4d}  {why}\n          e.g. {', '.join(ks[:3])}")
    print(f"\nEach measure: {out}")
    return 0 if counts["answers"] == len(keys) else 1


if __name__ == "__main__":
    sys.exit(main())
