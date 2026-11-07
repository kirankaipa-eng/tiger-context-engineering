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
import re
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


def build(layer, folder) -> dict:
    """The grid of a capture: compiled, never run."""
    scope = {c: _stand_in(layer, c, v) for c, v in _scope(folder).items()}
    metrics = dict(layer.captured())
    for m in list(metrics.values()):  # a part reaches further than the ratio it is part of: it gets its own row
        for r in _BRACES.findall(str(m.get("formula") or "")):
            if r in layer.metrics and r not in metrics:
                metrics[r] = {**layer.metrics[r], "part_of": True}
    out: dict = {"version": fingerprint(folder), "scope": dict(_scope(folder)), "slices": {}, "metrics": {}}
    sets: dict[tuple, str] = {}
    added = {x.replace("'", "") for m in layer.metrics.values() for x in _BRACES.findall(str(m.get("expr") or ""))}
    for k, m in metrics.items():
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
                layer.compile([k], given, where)
                break
            except Exception as exc:  # noqa: BLE001
                gone = next((c for c in where if f"{c.split('[')[0].strip(chr(39))} is not related" in str(exc)), None)
                if not gone:
                    entry["refused"] = {_reason(exc): ["(the measure itself)"]}
                    break
                where = {c: v for c, v in where.items() if c.split("[")[0] != gone.split("[")[0]}
        if "refused" in entry:
            out["metrics"][k] = entry
            continue
        ok, refused = [], {}
        for col in (c for c in layer.columns_for([k]) if _sliceable(layer, c, added) and c not in given):
            try:
                layer.compile([k], [col, *given], where)
                ok.append(col)
            except Exception as exc:  # noqa: BLE001 - the reason is the finding
                refused.setdefault(_reason(exc), []).append(col)
        key = tuple(ok)
        if key not in sets:
            sets[key] = f"S{len(sets) + 1}"
            out["slices"][sets[key]] = list(key)
        entry["slices"] = sets[key]
        if refused:
            entry["refused"] = refused
        out["metrics"][k] = entry
    return out


def load(folder, layer=None) -> dict | None:
    """The grid beside the capture, when it is this capture's."""
    base = Path(folder or ".")
    p = next((x for x in (base / NAME, base / "capture" / NAME) if x.exists()), None)
    if not p:
        return None
    g = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
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
    a = ap.parse_args(argv)
    layer = BILayer.load(a.folder)
    if a.show:
        print(show(load(a.folder) or build(layer, a.folder), a.show))
        return
    g = build(layer, a.folder)
    out = Path(a.folder) / NAME
    out.write_text(yaml.safe_dump(g, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")
    ms = g["metrics"].values()
    whole = sum(1 for e in ms if "slices" in e)
    cut = sum(len(slices(g, k)) for k in g["metrics"])
    print(f"{out}: {len(g['metrics'])} measures, {whole} answerable under the owner's scope, {len(g['slices'])} slice sets, "
          f"{cut} measure-column pairs that work")
    stuck = [k for k, e in g["metrics"].items() if "slices" not in e]
    if stuck:
        print(f"refused whole ({len(stuck)}): " + ", ".join(stuck[:20]) + (" ..." if len(stuck) > 20 else ""))


if __name__ == "__main__":
    main()
