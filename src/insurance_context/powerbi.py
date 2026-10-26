"""Power BI semantic models: harvest their measures, crosswalk them to governed metrics, and
generate governed metrics back as TMDL measures.

    harvest(source)          measures (name, table, DAX) from a PBIP folder, model.bim, .pbit or .pbix
    crosswalk(sl, measures)  each measure -> governed metric | presentation | gap, with why
    to_tmdl(sl, names)       governed metrics as DAX measures over the published views

The semantic layer stays the source of definitions. A BI model keeps its own measures; the
crosswalk records which governed metric each one is, so agents and reports can agree, and
which ones the context layer does not cover yet. Generation covers the common shapes
(sums, distinct counts, row counts, simple filters, ratios, sums of metrics); anything else
is reported as "keep in SQL" rather than translated badly.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from .pbimodel import read_tmdl
from .schema_utils import table_name
from .semantic import SemanticLayer


def harvest(source: str | Path) -> list[dict]:
    """Measures (name, table, DAX) in a Power BI semantic model, from any of the forms it is kept in:

        PBIP folder   .SemanticModel/definition/tables/*.tmdl (or model.bim in older projects)
        model.bim     the model as JSON (TMSL)
        .pbit         a template: its DataModelSchema is the same JSON
        .pbix         the compressed model inside, read with pbixray (pip install insurance-context[powerbi])
    """
    source = Path(source)
    if source.is_dir():
        if any(source.rglob("*.tmdl")):
            return _harvest_tmdl(source)
        bims = sorted(source.rglob("model.bim"))
        if bims:
            return _harvest_bim(json.loads(bims[0].read_text(encoding="utf-8-sig")))
        raise ValueError(f"{source}: no TMDL or model.bim in this folder; is it a PBIP project?")
    suffix = source.suffix.lower()
    if suffix == ".bim":
        return _harvest_bim(json.loads(source.read_text(encoding="utf-8-sig")))
    if suffix == ".pbit":
        return _harvest_bim(_pbit_schema(source))
    if suffix == ".pbix":
        return _harvest_pbix(source)
    raise ValueError(f"{source}: not a PBIP folder, model.bim, .pbit or .pbix")


def _harvest_bim(doc: dict) -> list[dict]:
    """Measures in a TMSL model (model.bim, or a template's DataModelSchema)."""
    model = doc.get("model", doc)
    out = []
    for t in model.get("tables", []):
        for ms in t.get("measures", []):
            expr = ms.get("expression", "")
            if isinstance(expr, list):  # TMSL splits long expressions into a list of lines
                expr = "\n".join(expr)
            out.append({"name": ms["name"], "table": t["name"], "dax": expr.strip()})
    return out


def _pbit_schema(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        if "DataModelSchema" not in z.namelist():
            raise ValueError(f"{path}: no DataModelSchema; the template has no model of its own")
        raw = z.read("DataModelSchema")
    # Power BI writes it as UTF-16 (usually without a BOM)
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-16-le" if raw[1:2] == b"\x00" else "utf-8-sig"
    return json.loads(raw.decode(enc))


def _harvest_pbix(path: Path) -> list[dict]:
    """A .pbix keeps its model as a compressed binary (DataModel); pbixray decodes it."""
    try:
        from pbixray import PBIXRay
    except ImportError:
        raise ValueError(f"{path}: reading a .pbix needs pbixray (pip install insurance-context[powerbi]). "
                         "Or save it from Power BI Desktop as a PBIP project with TMDL turned on, "
                         "and harvest that folder.") from None
    try:
        frame = PBIXRay(str(path)).dax_measures
    except Exception as e:  # live-connected reports have no model; pbixray raises its own error
        raise ValueError(f"{path}: could not read a model from this file ({type(e).__name__}: {e}). "
                         "A report connected live to a published model has none; harvest that model "
                         "instead.") from None
    return [{"name": r["Name"], "table": r["TableName"], "dax": (r["Expression"] or "").strip()}
            for r in frame.to_dict("records")]


def _harvest_tmdl(folder: Path) -> list[dict]:
    return [{"name": m.name, "table": t, "dax": m.expression} for t, m in read_tmdl(folder).measures]


# words in BI measure names -> words in governed metric labels
_SYNONYMS = {"k": "per 1000", "prof": "professional", "op": "outpatient", "ip": "inpatient",
             "total": "", "trend": "", "admit": "admits", "visit": "visits"}


def _words(text: str) -> set[str]:
    text = text.lower().replace("1,000", "1000").replace("/", " per ")
    words = []
    for w in re.findall(r"[a-z0-9]+", text):
        words += _SYNONYMS.get(w, w).split()
    return {w for w in words if w not in ("per", "the", "of")}


def _is_presentation(dax: str) -> bool:
    """Formatting or time-comparison wrappers around a quantity: text out, or YoY arithmetic."""
    return bool(re.search(r'FORMAT\(|"[▲▼]|& "|YoY|_y - 1', dax))


def crosswalk(sl: SemanticLayer, measures: list[dict], explicit: dict | None = None) -> list[dict]:
    """Classify each measure. `explicit` (measure name -> metric name or null) wins over matching."""
    explicit = explicit or {}
    available = {m["name"]: m for m in sl.catalog()}
    labels = {n: _words(m["label"]) | _words(n) for n, m in available.items()}
    rows = []
    for ms in measures:
        name = ms["name"]
        if name in explicit:
            target = explicit[name]
            status = "governed" if target in available else "gap"
            rows.append({**ms, "status": status, "metric": target if status == "governed" else None,
                         "why": "set by an owner" if target else "an owner marked it as not covered yet"})
            continue
        if _is_presentation(ms["dax"]):
            rows.append({**ms, "status": "presentation", "metric": None,
                         "why": "formats or compares a quantity over time; the quantity is what is governed"})
            continue
        best, score = None, 0.0
        # "Paid" disambiguates (paid per visit vs visits) when a governed label carries it; BI names
        # also use it loosely ("Paid PMPM" is total PMPM), so retry without it.
        for want in (_words(name), _words(name) - {"paid"}):
            for n, words in labels.items():
                overlap = len(want & words) / max(len(want | words), 1)
                if want and want <= words and overlap > score:
                    best, score = n, overlap
            if best:
                break
        if best:
            rows.append({**ms, "status": "governed", "metric": best,
                         "why": f"same quantity as {available[best]['label']}"})
        else:
            rows.append({**ms, "status": "gap", "metric": None,
                         "why": "no governed metric for this quantity yet"})
    return rows


# ------------------------------------------------------------------ TMDL generation

_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _wraps(text: str) -> bool:
    """Is the first "(" closed by the last ")" (so the pair wraps the whole text)?"""
    depth = 0
    for i, ch in enumerate(text):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0 and i < len(text) - 1:
            return False
    return True


def _dax_condition(sql: str, table: str) -> str | None:
    """`{a} = 'x' AND {b} IN ('y','z')` -> DAX filter arguments, or None when not this simple shape."""
    out = []
    for part in re.split(r"\s+AND\s+", sql.strip(), flags=re.I):
        part = part.strip()
        while part.startswith("(") and part.endswith(")") and part.count("(") == part.count(")") \
                and _wraps(part):
            part = part[1:-1].strip()
        m = re.fullmatch(r"\{(\w+)\}\s*(=|<>|!=)\s*'([^']*)'", part)
        if m:
            op = "=" if m.group(2) == "=" else "<>"
            out.append(f'\'{table}\'[{m.group(1)}] {op} "{m.group(3)}"')
            continue
        m = re.fullmatch(r"\{(\w+)\}\s+(NOT\s+)?IN\s*\(([^)]*)\)", part, flags=re.I)
        if m:
            vals = ", ".join(f'"{v.strip().strip(chr(39))}"' for v in m.group(3).split(","))
            expr = f"'{table}'[{m.group(1)}] IN {{{vals}}}"
            out.append(f"NOT ({expr})" if m.group(2) else expr)
            continue
        return None
    return ", ".join(out)


def _base_dax(m, table: str) -> str | None:
    measure = " ".join((m.measure or "").split())
    conds = []
    if m.filter:
        c = _dax_condition(" ".join(m.filter.split()), table)
        if c is None:
            return None
        conds.append(c)
    if m.as_of:
        if "." in m.as_of:
            return None
        conds.append(f"'{table}'[{m.as_of}] <= [Valuation Date]")
    agg = None
    if measure == "count(*)":
        agg = f"COUNTROWS('{table}')"
    elif re.fullmatch(r"sum\(\{(\w+)\}\)", measure):
        agg = f"SUM('{table}'[{_FIELD.search(measure).group(1)}])"
    elif re.fullmatch(r"count\(DISTINCT \{(\w+)\}\)", measure):
        agg = f"DISTINCTCOUNT('{table}'[{_FIELD.search(measure).group(1)}])"
    else:
        m2 = re.fullmatch(r"count\(DISTINCT CASE WHEN (.+) THEN \{(\w+)\} END\)", measure)
        if m2:
            c = _dax_condition(m2.group(1), table)
            if c is None:
                return None
            conds.append(c)
            agg = f"DISTINCTCOUNT('{table}'[{m2.group(2)}])"
    if agg is None:
        return None
    return f"CALCULATE({agg}, {', '.join(conds)})" if conds else agg


def dax(sl: SemanticLayer, name: str) -> str | None:
    """A governed metric as a DAX expression over the published views, or None if not a simple shape."""
    m = sl.metrics[name]
    if m.is_base:
        return _base_dax(m, table_name(m.entity))
    if m.ratio:
        num, den = (dax(sl, p) for p in m.ratio)
        if num is None or den is None:
            return None
        expr = f"DIVIDE({_measure_ref(sl, m.ratio[0])}, {_measure_ref(sl, m.ratio[1])})"
        return f"{expr} * {m.scale:g}" if m.scale else expr
    if m.sum_of:
        parts = [dax(sl, p) for p in m.sum_of]
        if any(p is None for p in parts):
            return None
        return " + ".join(_measure_ref(sl, p) for p in m.sum_of)
    return None


def _measure_ref(sl: SemanticLayer, name: str) -> str:
    return f"[{sl.metrics[name].label}]"


def to_tmdl(sl: SemanticLayer, names: list[str], table: str = "Governed Metrics") -> tuple[str, list[str]]:
    """TMDL for the named governed metrics and everything they are built from.

    Returns (tmdl, kept_in_sql): metrics whose shape does not translate are listed, not emitted."""
    order, kept = [], []

    def visit(n):
        if n in order:
            return
        m = sl.metrics[n]
        for p in (m.ratio or m.sum_of or []):
            visit(p)
        order.append(n)

    for n in names:
        visit(n)
    lines = [f"table '{table}'", "",
             "\t/// Generated from the governed semantic layer. Do not edit here: change the definition in",
             "\t/// Context Studio and regenerate.",
             "\tmeasure 'Valuation Date' = DATE(2026, 3, 31)", ""]
    for n in order:
        m = sl.metrics[n]
        expr = dax(sl, n)
        if expr is None:
            kept.append(n)
            continue
        fmt = {"currency": "\\$#,0", "percent": "0.0%", "count": "#,0", "number": "#,0.0"}.get(m.format)
        lines.append(f"\tmeasure '{m.label}' = {expr}")
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        if m.description:
            lines.append(f"\t\tdescription: {' '.join(m.description.split())}")
        lines.append(f"\t\tannotation GovernedMetric = {n}")
        lines.append("")
    return "\n".join(lines), kept
