"""The grid: every captured measure against every column it could be cut by, worked out once from the capture.

    python -m insurance_context.grid <model folder>                 write grid.yaml beside the capture, and report
    python -m insurance_context.grid <model folder> --show <metric> what one measure is made of and can be cut by

Captured measures were checked when they were captured, so what the grid adds is not whether a number is right but where it
can be asked: each pair is compiled, never run, under the owner's scope (analysis_default.yaml), so a model that demands its
reporting context refuses here and not in front of a reader. For each measure it keeps

    formula   what it is, in its parts: medical_paid / member_months
    parts     the measures the formula reads, and for a base one the column it adds up and the rows it keeps
    needs     the period grain or what-if values it must be given
    slices    the columns it can be cut by (a named set, shared by every measure that has the same)
    refused   the columns it cannot, grouped by the model's reason

The file carries the capture's fingerprint: a grid of another capture is not read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path

import yaml

from . import findable

NAME = "grid.yaml"
_BRACES = re.compile(r"\{([^{}]+)\}")
_NULLIF = re.compile(r"nullif\(([^()]+?), 0\)", re.IGNORECASE)


def fingerprint(folder) -> str:
    base = Path(folder)
    p = next((x for x in (base / "capture.yaml", base / "capture" / "capture.yaml") if x.exists()), None)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p else ""


def plain(text: str) -> str:
    """A captured formula as a reader writes it: ({a} / nullif({b}, 0)) is a / b."""
    s = _BRACES.sub(r"\1", str(text or ""))
    while (t := _NULLIF.sub(r"\1", s)) != s:
        s = t
    s = s.strip()
    while s.startswith("(") and s.endswith(")") and _closes_last(s):
        s = s[1:-1].strip()
    return s


def _closes_last(s: str) -> bool:
    depth = 0
    for i, ch in enumerate(s):
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if depth == 0 and i < len(s) - 1:
            return False
    return True


def _type(layer, col: str) -> str:
    t, c = col.split("[", 1)
    return str(((layer.tables.get(t.strip("'")) or {}).get("columns") or {}).get(c.rstrip("]"), {}).get("type") or "")


def _stand_in(layer, col: str, value):
    """A value to compile with: `latest` is settled only when a question runs, so a number of the column's type stands in."""
    if str(value).lower() != "latest":
        return value
    return 0 if re.search(r"int|decimal|double|number", _type(layer, col), re.IGNORECASE) else "latest"


def _scope(folder) -> dict:
    base = Path(folder)
    p = next((x for x in (base / "analysis_default.yaml", base / "capture" / "analysis_default.yaml") if x.exists()), None)
    return ((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("filters") or {}) if p else {}


def _sliceable(layer, col: str, added: set[str]) -> bool:
    """A column a reader would cut by: not a key, a flag, a date (a date is a period, said by its grain), an amount, or a
    column a measure adds up."""
    name = col.split("[", 1)[-1].rstrip("]")
    kind = _type(layer, col).lower()
    return not findable.HELPER.search(name) and not findable.HELPER.search("_".join(findable.split(name))) \
        and "date" not in kind and not re.search(r"double|decimal|float|currency", kind) and col.replace("'", "") not in added


def _reason(exc: Exception) -> str:
    """The model's reason, short and without the column it was asked for, so one reason gathers its columns."""
    s = str(exc).split(";")[0].split(": rows of")[0]
    return re.sub(r"\s+", " ", s)[:160]


def _base(m: dict) -> str:
    agg, expr = str(m.get("agg") or ""), plain(m.get("expr") or "")
    said = f"{agg} of {expr}" if expr and expr != "*" else "count of rows" if agg else ""
    keep = [plain(f) for f in m.get("filter") or []]
    return said + (f" where {' and '.join(keep)}" if keep else "")


def _settled(kit, layer, raw: dict) -> dict:
    """The owner's scope with `latest` read from the warehouse, as the desk reads it: the last value of the column under the
    other defaults of its table. One small query a column."""
    from .desk_harness import _latest_of

    out = {}
    for c, v in raw.items():
        if str(v).lower() != "latest":
            out[c] = v
            continue
        table = c.split("[")[0]
        others = {x: y for x, y in raw.items() if x != c and x.split("[")[0] == table and str(y).lower() != "latest"}
        rows = kit.wh.query(kit._values_sql(c, kit.resolve(others)[0]), limit=10001)[1]
        out[c] = _latest_of([r[0] for r in rows if r[0] is not None])
    return out


def _asked_once(wh) -> None:
    """The warehouse asked each query once: a model that settles its context with queries asks the same ones for every
    measure. Counted and timed, so the progress line says what went to the warehouse and how long it was waited on."""
    inner, kept, lock = wh.query, {}, threading.Lock()
    wh.asked = wh.sent = 0
    wh.secs = 0.0

    def query(sql, limit=None):
        with lock:
            wh.asked += 1
            hit = kept.get((sql, limit))
        if hit is not None:
            return hit
        t0 = time.monotonic()
        got = inner(sql, limit=limit)
        with lock:
            wh.sent += 1
            wh.secs += time.monotonic() - t0
            kept[(sql, limit)] = got
        return got

    wh.query = query


class _Progress:
    """One line, redrawn: how far, how long, how long to go, and where the time goes."""

    def __init__(self, total: int, kit, quiet: bool):
        self.total, self.kit, self.quiet, self.began, self.done, self.busy = total, kit, quiet, time.monotonic(), 0, 0.0
        self.lock = threading.Lock()

    def tick(self, secs: float) -> None:
        with self.lock:
            self.done += 1
            self.busy += secs
            if self.quiet:
                return
            took = time.monotonic() - self.began
            left = took / self.done * (self.total - self.done)
            bar = "#" * int(30 * self.done / self.total)
            wh = getattr(self.kit, "wh", None) if self.kit else None
            db = (f" | warehouse: {wh.sent} sent, {wh.asked} asked, {100 * wh.secs / max(self.busy, 1e-9):.0f}% of the time"
                  if wh is not None and hasattr(wh, "secs") else "")
            print(f"\r[{bar:<30}] {self.done}/{self.total}  {_clock(took)}, about {_clock(left)} to go{db}   ", end="", flush=True)

    def end(self) -> None:
        if not self.quiet:
            print(flush=True)


def _clock(secs: float) -> str:
    secs = int(secs)
    return f"{secs // 3600}h{secs % 3600 // 60:02d}m" if secs >= 3600 else f"{secs // 60}m{secs % 60:02d}s"


def _write(path: Path, g: dict) -> None:
    """Whole or not at all: a reader never meets half a file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(g, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    os.replace(tmp, path)


def build(layer, folder, kit=None, out_path: Path | None = None, workers: int = 1, quiet: bool = True) -> dict:
    """The grid of a capture: compiled, never run. With the toolkit (`kit`), as the desk compiles: the scope's `latest` read
    from the warehouse, and the toolkit's resolve before each compile, which some models need to settle their reporting
    context. Without it, offline, with a stand-in for `latest`.

    With `out_path` it is written as it goes, every few measures, marked complete only at the end; a run stopped part way is
    picked up where it stopped when the capture and the scope are the same. `workers` measures are checked at once."""
    raw = _scope(folder)
    scope = _settled(kit, layer, raw) if kit else {c: _stand_in(layer, c, v) for c, v in raw.items()}
    seen: dict[str, dict] = {}
    lock = threading.Lock()

    def resolved(where: dict) -> dict:
        if not kit:
            return where
        key = repr(sorted(where.items(), key=str))
        with lock:
            hit = seen.get(key)
        if hit is None:
            hit = kit.resolve(dict(where))[0]
            with lock:
                seen[key] = hit
        return hit

    def compile(metrics, by, where):
        if kit:
            return layer.compile(metrics, by, resolved(where), dialect=kit.wh.dialect, row_filters=kit.row_filters)
        return layer.compile(metrics, by, where)

    metrics = dict(layer.captured())
    for m in list(metrics.values()):  # a part reaches further than the ratio it is part of: it gets its own row
        for r in _BRACES.findall(str(m.get("formula") or "")):
            if r in layer.metrics and r not in metrics:
                metrics[r] = {**layer.metrics[r], "part_of": True}
    out: dict = {"version": fingerprint(folder), "scope": {c: str(v) for c, v in scope.items()}, "complete": False,
                 "slices": {}, "metrics": {}}
    if out_path and out_path.exists():  # a run stopped part way, of this capture and this scope: what it found stands
        try:
            old = json.loads(out_path.read_text(encoding="utf-8"))
            if old.get("version") == out["version"] and old.get("scope") == out["scope"] and not old.get("complete"):
                out["slices"], out["metrics"] = old.get("slices") or {}, old.get("metrics") or {}
        except ValueError:
            pass
    sets = {tuple(cols): name for name, cols in out["slices"].items()}
    added = {x.replace("'", "") for m in layer.metrics.values() for x in _BRACES.findall(str(m.get("expr") or ""))}

    def one(k: str, m: dict) -> tuple[dict, tuple | None]:
        entry: dict = {"label": m.get("label") or k, **({"part": True} if m.get("part_of") else {})}
        if m.get("formula"):
            entry["formula"] = plain(m["formula"])
            every = layer.metrics  # a part may be a hidden helper the capture keeps for a formula
            entry["parts"] = {r: _base(every[r]) if every[r].get("agg") else plain(every[r].get("formula") or "")
                              for r in dict.fromkeys(_BRACES.findall(m["formula"])) if r in every}
        elif m.get("agg"):
            entry["formula"] = _base(m)
        given: list[str] = []  # the grain or column the measure cannot be asked without
        try:
            n = layer.needs(k)
            if n.get("by") or n.get("params"):
                entry["needs"] = {"by": list(n.get("by") or []), "params": list(n.get("params") or {})}
                given = list(n.get("by") or [])
        except Exception as exc:  # noqa: BLE001 - what it needs could not be read: said, and the grid goes on
            entry["needs"] = {"error": _reason(exc)}
        where = dict(scope)
        for _ in range(len(scope) + 1):  # a scope table the measure's rows never reach is no part of its question
            try:
                compile([k], given, where)
                break
            except Exception as exc:  # noqa: BLE001
                gone = next((c for c in where if f"{c.split('[')[0].strip(chr(39))} is not related" in str(exc)), None)
                if not gone:
                    entry["refused"] = {_reason(exc): ["(the measure itself)"]}
                    return entry, None
                where = {c: v for c, v in where.items() if c.split("[")[0] != gone.split("[")[0]}
        ok, refused = [], {}
        for col in (c for c in layer.columns_for([k]) if _sliceable(layer, c, added) and c not in given):
            try:
                compile([k], [col, *given], where)
                ok.append(col)
            except Exception as exc:  # noqa: BLE001 - the reason is the finding
                refused.setdefault(_reason(exc), []).append(col)
        if refused:
            entry["refused"] = refused
        return entry, tuple(ok)

    todo = [(k, m) for k, m in metrics.items() if k not in out["metrics"]]
    bar = _Progress(len(metrics), kit, quiet)
    bar.done = len(metrics) - len(todo)
    last = [time.monotonic()]

    def keep(k: str, entry: dict, cut: tuple | None) -> None:
        with lock:
            if cut is not None:
                if cut not in sets:
                    sets[cut] = f"S{len(sets) + 1}"
                    out["slices"][sets[cut]] = list(cut)
                entry["slices"] = sets[cut]
            out["metrics"][k] = entry
            if out_path and time.monotonic() - last[0] > 10:  # what it has found, readable while it works
                _write(out_path, out)
                last[0] = time.monotonic()

    def run(item):
        t0 = time.monotonic()
        entry, cut = one(*item)
        keep(item[0], entry, cut)
        bar.tick(time.monotonic() - t0)

    if workers > 1:
        with ThreadPoolExecutor(workers) as pool:
            list(pool.map(run, todo))
    else:
        for item in todo:
            run(item)
    bar.end()
    out["complete"] = True
    out["metrics"] = {k: out["metrics"][k] for k in metrics if k in out["metrics"]}  # the capture's order, however it finished
    if out_path:
        _write(out_path, out)
    return out


def load(folder, layer=None) -> dict | None:
    """The grid beside the capture, when it is this capture's."""
    base = Path(folder or ".")
    p = next((x for x in (base / NAME, base / "capture" / NAME) if x.exists()), None)
    if not p:
        return None
    text = p.read_text(encoding="utf-8")
    try:  # written as JSON, which is YAML too and reads many times faster; one written by hand is read as YAML
        g = json.loads(text)
    except ValueError:
        g = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader)) or {}
    return g if g.get("version") == fingerprint(base) else None


def slices(grid: dict, key: str) -> list[str]:
    return list((grid.get("slices") or {}).get((grid.get("metrics") or {}).get(key, {}).get("slices"), []))


def show(grid: dict, key: str) -> str:
    """One measure as the grid has it, in lines a reader or a model can take in."""
    e = (grid.get("metrics") or {}).get(key)
    if e is None:
        return f"{key}: not in the grid"
    lines = [f"{key} \"{e.get('label')}\""]
    if e.get("formula"):
        lines.append(f"  is: {e['formula']}")
    for r, said in (e.get("parts") or {}).items():
        lines.append(f"    {r}: {said}" if said else f"    {r}")
        further = [c for c in slices(grid, r) if c not in slices(grid, key)]
        if further:
            lines.append(f"      {r} alone also cuts by: {', '.join(further[:12])}{' and more' if len(further) > 12 else ''}")
    if e.get("needs"):
        lines.append(f"  needs: {e['needs']}")
    if cut := slices(grid, key):
        lines.append(f"  can be cut by: {', '.join(cut)}")
    for why, cols in (e.get("refused") or {}).items():
        lines.append(f"  cannot be cut by {', '.join(cols[:8])}{' and more' if len(cols) > 8 else ''}: {why}")
    return "\n".join(lines)


def main(argv=None) -> None:
    from .bilayer import BILayer

    ap = argparse.ArgumentParser(description="Work out where each captured measure can be asked: compiled, never run.")
    ap.add_argument("folder")
    ap.add_argument("--show", help="show one measure from the grid, and write nothing")
    ap.add_argument("--offline", action="store_true", help="compile without the warehouse: `latest` is a stand-in, and a model "
                                                         "that settles its reporting context with queries refuses")
    ap.add_argument("--workers", type=int, default=4, help="measures checked at once (default 4); 1 for one at a time")
    ap.add_argument("--fresh", action="store_true", help="start again rather than pick up a run that stopped part way")
    a = ap.parse_args(argv)
    if a.show and (g := load(a.folder)):  # the grid alone: the capture is not read
        print(show(g, a.show))
        return
    layer = BILayer.load(a.folder)
    kit = None
    if not a.offline:
        from .bi_agent import BIToolkit
        from .biwarehouse import Warehouse

        kit = BIToolkit(layer, Warehouse.open(a.folder, layer))
        layer = kit.layer  # what the toolkit compiles with, as the desk does
        print(f"compiling as the desk does, on {kit.wh.describe()}", flush=True)
        _asked_once(kit.wh)
    out = Path(a.folder) / NAME
    if a.show:
        print(show(build(layer, a.folder, kit), a.show))
        return
    if a.fresh and out.exists():
        out.unlink()
    print(f"writing {out} as it goes; stop it any time and run it again to carry on", flush=True)
    g = build(layer, a.folder, kit, out_path=out, workers=max(1, a.workers), quiet=False)
    print(f"scope: {g['scope']}")
    ms = g["metrics"].values()
    whole = sum(1 for e in ms if "slices" in e)
    cut = sum(len(slices(g, k)) for k in g["metrics"])
    print(f"{out}: {len(g['metrics'])} measures, {whole} answerable under the owner's scope, {len(g['slices'])} slice sets, "
          f"{cut} measure-column pairs that work")
    stuck = [k for k, e in g["metrics"].items() if "slices" not in e]
    if stuck:
        why = Counter(next(iter(g["metrics"][k]["refused"])) for k in stuck)
        print(f"refused whole ({len(stuck)}), by reason:")
        for r, n in why.most_common(5):
            print(f"  {n}  {r}")


if __name__ == "__main__":
    main()
