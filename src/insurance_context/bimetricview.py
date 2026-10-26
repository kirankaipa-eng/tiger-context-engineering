"""Captured Power BI metrics as Databricks metric views (YAML specification 1.1), plus the context the
specification has no place for.

    export(layer, out) -> [Path, ...]      ictx pbi export <capture dir | model.pbix> --out DIR

One metric view per fact table the captured metrics aggregate (Sales, ...), with the model's
relationships as `joins` (many-to-one, nested for snowflakes) and every column those tables reach as
`fields`. Three files per view:

    <view>.yaml          the metric view: only the specification's keys, so `CREATE VIEW ... WITH
                         METRICS LANGUAGE YAML` takes it as is
    <view>.sql           that statement
    <view>.context.yaml  what the specification cannot carry, keyed by the same measure names: each
                         measure's Power BI name and DAX, how exact the translation is and how it must
                         be asked; the captured metrics the specification cannot express (ranks, scope
                         switches, ALL / ALLSELECTED, latest-period variables), with their captured
                         definitions so our engine still answers them; display-only measures and the
                         number each shows; what-if ranges; row-level security roles; and which field
                         is which Power BI column

How captured shapes become the specification's:

    aggregation + filters      SUM(x) FILTER (WHERE ...)                                   exact
    formula over metrics       COALESCE(MEASURE(a), 0) / NULLIF(COALESCE(MEASURE(b), 0), 0) exact
    year/quarter/month to date window: cumulative on the date, current on the year/...    documented pattern
    previous month/quarter/... window: current on that grain, offset -1 month/...         follows the spec
    DATEADD, SAMEPERIODLASTYEAR window: current on the month, offset -12 month             follows the spec
    what-if parameter          parameters: (name, data_type; the slider's range in context)
    rank, ISINSCOPE, ALL, ALLSELECTED, latest-period VAR      not in the view: context.yaml

"Follows the spec" means the window is built from the specification's documented offset and range
rules but has not been run on Databricks yet; the context file says so per measure.
"""

from __future__ import annotations

import re
from pathlib import Path

import sqlglot
import yaml

from .bilayer import BILayer, LayerError, parse_ref, ref, snake

SPEC = "1.1"
_AGG = {"sum": "SUM({})", "avg": "AVG({})", "min": "MIN({})", "max": "MAX({})", "count": "COUNT({})",
        "count_distinct": "COUNT(DISTINCT {})"}
_COL = re.compile(r"\{('(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_ ]*)\[([^\]]+)\]\}")
_REF = re.compile(r"\{([?$]?)([a-z0-9_]+)\}")
_GRAIN_FIELD = {"year": ("year", "DATE_TRUNC('YEAR', {})"), "quarter": ("quarter", "DATE_TRUNC('QUARTER', {})"),
                "month": ("month", "DATE_TRUNC('MONTH', {})")}
_NOT_IN_SPEC = {
    "rank": "ranks are not part of the metric view specification; our engine answers it (RANK() over the rows "
            "a question returns)",
    "by_scope": "the answer depends on which column the question groups by (ISINSCOPE); the specification has no "
                "per-grouping measures",
    "context": "ignores part of the question's grouping or filters (ALL / ALLSELECTED / REMOVEFILTERS); the "
               "specification has no equivalent outside a date window",
    "latest": "reads the latest period in the question's rows (a VAR over MAX); semiadditive windows come close "
              "but differ when the question groups by another column",
}


class _Skip(Exception):
    pass


def _q(name: str) -> str:
    return name if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) else "`" + name.replace("`", "``") + "`"


def _databricks(expr: str) -> str:
    """A DuckDB-syntax expression (how capture.yaml writes them) as Databricks SQL."""
    return sqlglot.transpile(expr, read="duckdb", write="databricks")[0]


class _View:
    """One fact table's metric view: its joins, fields and measures."""

    def __init__(self, layer: BILayer, fact: str):
        self.layer, self.fact = layer, fact
        self.name = snake(f"{layer.model}_{fact}")
        self.dot = {fact: ""}               # table -> its join path in expressions ("" for the source)
        self.joins: list[dict] = []
        nodes = {fact: self.joins}
        names: set[str] = set()
        paths = layer.reachable(fact)
        for t in sorted(paths, key=lambda x: (len(paths[x]), x)):
            if t == fact or not self._relation(t):
                continue
            frm, mine, _, theirs = paths[t][-1]
            if frm not in nodes:            # its parent has no warehouse table
                continue
            n = snake(t)
            while n in names or n == "source":
                n += "_"
            names.add(n)
            parent = "source" if frm == fact else self.dot[frm].split(".")[-1]
            node = {"name": n, "source": self._relation(t),
                    "on": f"{parent}.{_q(self._phys(frm, mine))} = {n}.{_q(self._phys(t, theirs))}"}
            nodes[frm].append(node)
            nodes[t] = node.setdefault("joins", [])
            self.dot[t] = f"{self.dot[frm]}.{n}".lstrip(".")
        for node in self._walk(self.joins):
            if not node["joins"]:
                del node["joins"]
        self.fields: list[dict] = []
        self.field_of: dict[str, str] = {}  # "Store[Type]" -> field name
        self.measures: list[dict] = []
        self.params: list[dict] = []
        self.context: dict[str, dict] = {}
        self.not_in_view: dict[str, dict] = {}
        self._done: dict[str, bool] = {}
        self.why: dict[str, str] = {}       # every key not exported (parts too) -> its reason

    @staticmethod
    def _walk(joins):
        for j in joins:
            yield j
            yield from _View._walk(j.get("joins", []))

    def _relation(self, table: str) -> str | None:
        info = self.layer.tables.get(table) or {}
        return info.get("sql") or info.get("table")

    def _phys(self, table: str, column: str) -> str:
        return self.layer.tables[table]["columns"][column]["column"]

    def col(self, table: str, column: str) -> str:
        if table not in self.dot:
            raise _Skip(f"reads {ref(table, column)}, which rows of {self.fact} do not reach")
        info = (self.layer.tables.get(table) or {}).get("columns") or {}
        if column not in info:
            raise _Skip(f"reads {ref(table, column)}, which has no warehouse column")
        p = _q(info[column]["column"])
        return f"{self.dot[table]}.{p}" if self.dot[table] else p

    # ------------------------------------------------------------ fields

    def add_fields(self) -> None:
        used = set()
        for t in self.dot:
            for c, info in ((self.layer.tables.get(t) or {}).get("columns") or {}).items():
                n = snake(f"{t}_{c}")
                while n in used:
                    n += "_"
                used.add(n)
                self.fields.append({"name": n, "expr": self.col(t, c), "display_name": c[:255],
                                    "comment": f"Power BI column {ref(t, c)}"})
                self.field_of[ref(t, c)] = n
                if info.get("type") in ("dateTime", "date"):
                    for g, (suffix, fn) in _GRAIN_FIELD.items():
                        gn = f"{n}_{suffix}"
                        used.add(gn)
                        self.fields.append({"name": gn, "expr": fn.format(n), "display_name": f"{c} ({g})"[:255],
                                            "comment": f"Power BI column {ref(t, c, g)}"})
                        self.field_of[ref(t, c, g)] = gn

    def date_field(self, date_ref: str, grain: str | None = None) -> str:
        t, c, _ = parse_ref(date_ref)
        key = ref(t, c, grain)
        if key not in self.field_of:
            raise _Skip(f"its period reads {date_ref}, which rows of {self.fact} do not reach")
        return self.field_of[key]

    # ------------------------------------------------------------ measures

    def export(self, key: str) -> bool:
        """Add `key` (and the measures it is built on, first) to the view; False with the reason recorded."""
        if key in self._done:
            return self._done[key]
        self._done[key] = False             # a cycle reads as not exportable
        m = self.layer.metrics[key]
        try:
            for shape, why in _NOT_IN_SPEC.items():
                if m.get(shape):
                    raise _Skip(why)
            fidelity, window = "exact", None
            if "formula" in m:
                expr, fidelity = self._formula(m["formula"])
            else:
                if m["table"] != self.fact:
                    raise _Skip(f"aggregates {m['table']}, another fact table: it is in that table's view")
                expr, window, fidelity = self._leaf(m)
            spec = {"name": key, "expr": expr}
            label = m.get("label") or f"{self.layer.metrics.get(m.get('of'), {}).get('label', m.get('of'))} (part)"
            spec["display_name"] = label[:255]
            spec["comment"] = (f"Power BI measure {label}" + (f": {m['dax']}" if m.get("dax") else
                               f", part of {m.get('of')}") + f". See {self.name}.context.yaml")[:1000]
            if window:
                spec["window"] = window
            self.measures.append(spec)
            self.context[key] = {"power_bi": label, **({"dax": m["dax"]} if m.get("dax") else {}),
                                 "fidelity": fidelity}
            if m.get("hidden"):
                self.context[key]["part_of"] = m.get("of")
            self._done[key] = True
        except (_Skip, LayerError) as exc:
            self.why[key] = str(exc)
            if not m.get("hidden"):
                self.not_in_view[key] = {"power_bi": m.get("label", key), **({"dax": m["dax"]} if m.get("dax") else {}),
                                         "why": str(exc),
                                         "captured_as": {k: v for k, v in m.items() if k not in ("label", "status", "dax")}}
        return self._done[key]

    def _formula(self, formula: str) -> tuple[str, str]:
        fid = []

        def sub(x):
            kind, k = x.group(1), x.group(2)
            if kind == "$":
                return self._param(k)
            dep = self.layer.metrics.get(k)
            if dep is None:
                raise _Skip(f"built on {k}, which the capture does not define")
            if "formula" in dep and not _leaves(self.layer, k):  # a what-if value only: inline it
                return "(" + _REF.sub(sub, dep["formula"]) + ")"
            if not self.export(k):
                why = self.why.get(k, "")
                if dep.get("hidden") or why.startswith("built on"):
                    raise _Skip(why)  # a part's reason is the measure's own; a chain names its root once
                raise _Skip(f"built on {k}: {why}")
            fid.append(self.context[k]["fidelity"])
            return f"MEASURE({k})" if kind == "?" else f"COALESCE(MEASURE({k}), 0)"

        out = _databricks(_REF.sub(sub, formula))
        windows = [f for f in fid if f != "exact"]
        return out, "exact" if not windows else f"built on windowed measures: {windows[0]}"

    def _leaf(self, m: dict) -> tuple[str, list | None, str]:
        def render(text: str) -> str:
            text = _COL.sub(lambda x: self.col(x.group(1).strip("'").replace("''", "'"), x.group(2)), text)
            return _REF.sub(lambda x: self._param(x.group(2)) if x.group(1) == "$" else x.group(0), text)

        inner = "1" if m["expr"] == "*" else render(m["expr"])
        expr = _AGG[m["agg"]].format(inner)
        if m.get("filter"):
            expr += " FILTER (WHERE " + " AND ".join(f"({render(f)})" for f in m["filter"]) + ")"
        expr = _databricks(expr)
        t = m.get("time")
        if not t:
            return expr, None, "exact"
        date = self.date_field(t["date"])
        if "to_date" in t:  # the specification's documented year-to-date pattern, at that period
            u = t["to_date"]
            return expr, [{"order": date, "range": "cumulative", "semiadditive": "last"},
                          {"order": self.date_field(t["date"], u), "range": "current", "semiadditive": "last"}], \
                "documented pattern (cumulative on the date, current on the period); not yet run on Databricks"
        n, unit, grain = t["shift"], t["unit"], t.get("grain")
        if unit == "day":
            order, offset = date, f"{n} day"
        else:  # DATEADD / SAMEPERIODLASTYEAR have no grain: the month is the period the specification shifts
            g = grain or "month"
            order = self.date_field(t["date"], g)
            offset = f"{n} year" if unit == g == "year" else f"{n * {'month': 1, 'quarter': 3, 'year': 12}[unit]} month"
        return expr, [{"order": order, "range": "current", "offset": offset, "semiadditive": "last"}], \
            f"follows the specification's offset rules ({offset} on {order}); not yet run on Databricks"

    def _param(self, key: str) -> str:
        p = (self.layer.spec.get("parameters") or {}).get(key)
        if p is None:
            raise _Skip(f"reads the what-if parameter {key}, which the capture does not define")
        if not any(x["name"] == key for x in self.params):
            entry = {"name": key, "data_type": "double"}
            if p.get("default") is not None:
                entry["default"] = p["default"]
            self.params.append(entry)
        return key

    # ------------------------------------------------------------ output

    def view_yaml(self) -> dict:
        info = self.layer.spec.get("warehouse") or {}
        out = {"version": SPEC,
               "comment": f"Power BI model {self.layer.model}: measures over {self.fact}, captured from DAX. "
                          f"Context the specification has no place for: {self.name}.context.yaml",
               "source": self._relation(self.fact)}
        if self.params:  # parameters precede the defaulted ones: the specification requires it
            out["parameters"] = sorted(self.params, key=lambda p: "default" in p)
        if self.joins:
            out["joins"] = self.joins
        out["fields"] = self.fields
        out["measures"] = self.measures
        view = self.name if self.name.endswith("_metrics") else f"{self.name}_metrics"
        self.view_name = ".".join(x for x in (info.get("catalog"), info.get("schema"), view) if x)
        return out

    def context_yaml(self) -> dict:
        spec = self.layer.spec
        for k in self.context:
            try:
                needs = self.layer.needs(k) if not self.layer.metrics[k].get("hidden") else {}
            except LayerError:
                needs = {}
            if needs.get("by") or needs.get("params"):
                self.context[k]["ask"] = {x: v for x, v in needs.items() if v}
        shown = {k: {"power_bi": m.get("label", k), "shows": m.get("value")} if m.get("value") else
                 {"power_bi": m.get("label", k), "why": m.get("why")}
                 for k, m in spec["metrics"].items() if m.get("status") == "presentation"}
        out = {"metric_view": self.view_name, "specification": f"Databricks metric view YAML {SPEC}",
               "power_bi_model": spec.get("model"), "captured_from": spec.get("source") or spec.get("imported"),
               "measures": self.context}
        if self.not_in_view:
            out["not_in_view"] = self.not_in_view
        if shown:
            out["display_only"] = shown
        nc = {k: {"power_bi": m.get("label", k), "why": m.get("why")} for k, m in spec["metrics"].items()
              if m.get("status") == "not_captured"}
        if nc:
            out["not_captured"] = nc
        if spec.get("parameters"):
            out["what_if"] = spec["parameters"]
        if spec.get("security"):
            out["row_level_security"] = spec["security"]
        out["fields"] = self.field_of
        return out


def export(layer: BILayer, out: str | Path) -> list[Path]:
    """Every fact table's metric view, its CREATE statement and its context file under `out`."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    captured = layer.captured()
    facts = sorted({layer.metrics[leaf]["table"] for k in captured for leaf in _leaves(layer, k)})
    files, report = [], [f"# {layer.model}: Databricks metric views", ""]
    homed: set[str] = set()
    for fact in facts:  # metrics with no aggregation (a what-if value alone) are inlined where used
        v = _View(layer, fact)
        v.add_fields()
        for k in captured:
            leaves = _leaves(layer, k)
            if leaves and all(layer.metrics[leaf]["table"] == fact for leaf in leaves):
                v.export(k)
                homed.add(k)
        if not v.measures:  # the specification needs a measure; what could not be exported is in the report
            if v.not_in_view:
                report += [f"## {fact}: no metric view", ""] + [f"- `{k}`: {x['why']}" for k, x in v.not_in_view.items()] + [""]
            continue
        doc = v.view_yaml()
        text = yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120)
        yaml.safe_load(text)  # what we write must read back
        (out / f"{v.name}.yaml").write_text(text)
        (out / f"{v.name}.sql").write_text(f"CREATE OR REPLACE VIEW {v.view_name}\nWITH METRICS\nLANGUAGE YAML\nAS $$\n"
                                           f"{text}$$;\n")
        (out / f"{v.name}.context.yaml").write_text(
            "# Context for the metric view the Databricks specification has no place for. Keys match\n"
            f"# {v.name}.yaml's measure names; `not_in_view` metrics are answered by our engine.\n"
            + yaml.safe_dump(v.context_yaml(), sort_keys=False, allow_unicode=True, width=120))
        files += [out / f"{v.name}{s}" for s in (".yaml", ".sql", ".context.yaml")]
        exact = sum(1 for c in v.context.values() if c["fidelity"] == "exact" and "part_of" not in c)
        shown = sum(1 for k in v.context if not layer.metrics[k].get("hidden"))
        report += [f"## {v.view_name}", "", f"source `{v._relation(fact)}`, {len(v.joins)} top-level joins, "
                   f"{len(v.fields)} fields, {len(v.measures)} measures ({shown} Power BI measures, "
                   f"{len(v.measures) - shown} parts of them); {exact} exact, {shown - exact} with a date window "
                   f"to confirm on Databricks", ""]
        if v.not_in_view:
            report += ["Not in the view (in the context file, answered by our engine):", ""]
            report += [f"- `{k}`: {x['why']}" for k, x in v.not_in_view.items()] + [""]
    mixed = [k for k in captured if k not in homed and _leaves(layer, k)]
    if mixed:
        report += ["## Not exported", ""] + [f"- `{k}`: combines measures of more than one fact table; a metric "
                                             "view has one source" for k in mixed] + [""]
    (out / "export.md").write_text("\n".join(report))
    return files + [out / "export.md"]


def _leaves(layer: BILayer, key: str) -> list[str]:
    try:
        return layer.leaves(key)
    except LayerError:
        return []
