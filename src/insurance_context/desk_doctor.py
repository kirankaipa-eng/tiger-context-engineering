"""Where a measure fails: each step the desk takes for it, run one at a time, PASS or FAIL, and the first that failed.

    python -m insurance_context.desk_doctor <model folder> <measure key or label>
    python -m insurance_context.desk_doctor <model folder> <measure> --sql     also print the SQL of the scoped query

No language model is involved: what fails here fails for the agent too. It runs a handful of queries on the warehouse.
"""

from __future__ import annotations

import argparse
import sys
import time


def _value(kit, layer, metric: str, where: dict) -> tuple[object, str]:
    """(the number, the SQL), or (an Exception, the SQL or "")."""
    sql = ""
    try:
        resolved = kit.resolve(dict(where))[0]
        sql = layer.compile([metric], [], resolved, dialect=kit.wh.dialect, row_filters=kit.row_filters)
        rows = kit.wh.query(sql, limit=5)[1]
        return (rows[0][-1] if rows else None), sql
    except Exception as exc:  # noqa: BLE001 - the failure is the finding
        return exc, sql


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
    a = ap.parse_args(argv)

    layer = BILayer.load(a.folder)
    kit = BIToolkit(layer, Warehouse.open(a.folder, layer))
    layer = kit.layer
    dh.install(kit, a.folder)
    h = kit.compute.__self__
    dh.SESSION.set("doctor")
    dh.QUESTION.set(f"what is {a.metric}")
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
        mine = sorted({layer.metrics[x]["table"] for x in layer.leaves(key) if layer.metrics[x].get("table")})
    except Exception:  # noqa: BLE001 - a measure whose parts cannot be walked is refused at step 4
        mine = []
    reach = {catalog(t) for t in mine} | {catalog(c.split("[")[0].strip("'")) for c in (layer.columns_for([key]) if mine else [])}
    lines = ", ".join(f"{c}: {len(ts)} tables" for c, ts in sorted(every.items(), key=lambda x: -len(x[1])))
    odd = "" if len(every) < 2 else "; the fewer: " + "; ".join(f"{c}: {', '.join(ts[:8])}" for c, ts in
                                                                 sorted(every.items(), key=lambda x: len(x[1]))[:-1])
    step(1, "its tables are all in one warehouse catalog", len(reach) < 2,
         f"this measure reads {sorted(reach)}; the model maps {lines}{odd}")

    # 2. the owner's scope, with latest read from the warehouse
    t0 = time.monotonic()
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
         f"{_said(v)} ({time.monotonic() - t0:.1f}s)")
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

    print()
    print("FIRST FAILURE: " + failed[0] if failed else "ALL PASSED: this measure answers under the owner's scope.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
