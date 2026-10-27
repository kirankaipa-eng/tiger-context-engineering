"""A Power BI model's captured metrics, compiled to SQL over the warehouse tables behind it.

    import_pbix(pbix, out)   .pbix -> TMDL -> captured metrics (capture.yaml) [-> local stand-in]
    BILayer.load(out)        the captured layer
    layer.compile(...)       metrics by columns, filtered, top N -> SQL (sqlglot, any dialect)
    Warehouse.open(out)      Databricks when DATABRICKS_* is set, else the local stand-in

The Power BI model supplies the definitions and the table details: which tables, which
columns, how they relate. The data stays in the warehouse. Each Power BI table maps to a
warehouse table (read from its Power Query source when that is Databricks, else
<catalog>.<schema>.<table> for an owner to confirm) and each column to a warehouse column.
Joins follow the model's active relationships from the many side to the one side, the way
Power BI filters, so a question "by Product[Category]" reaches Product from Sales on its own.

A metric that reads a period (time intelligence: previous month, same period last year, year
to date) is answered at the grain of the question. A shift moves each row's date before it is
bucketed (DATEADD semantics, bounded to the date table's range), so "Net Sales PM by month"
puts December's sales in January; a to-date metric adds up, for each period asked for, the rows
from the start of its year, quarter or month (a running total). Both need the date column in the
question, and say how to ask when it is missing.

CALCULATE's filter modifiers are a base metric's `context`: ALL / REMOVEFILTERS (keep: none) take
the question's grouping and filters on a table (and every table its rows reach, DAX's expanded
table) or on one column out of that metric, ALLSELECTED (keep: filters) only the grouping, ALLEXCEPT
keeps the columns it names, and a month-level removal answers a month question at year. The metric's
SQL leaves those columns out of its GROUP BY / WHERE and joins the answer on the columns that remain,
so its value repeats across the removed ones. A grouping or filter on a table a metric's rows do not
reach leaves that metric alone the same way (DAX: a filter that does not reach a table has no effect
on it); one that no metric asked for reaches is refused. `latest` reads only the rows where a column
equals its max (or min) over the question's cell, plus an offset (VAR _y = MAX(T[C]) ... T[C] = _y - 1):
a cells query gives each cell's value, the metric is grouped by the column and joined on it.
A filter that compares rows with {@key} (a running total's Date <= the cell's max date) joins each
row to its cell's value of that metric (computed in a query of its own), so the rows it reads differ
per cell; a column the metric removes (ALL) does not tie a row to a cell, so the row counts in each.

Formulas combine metrics: {key} is the value with BLANK as 0 (also as a CASE branch's result), {?key}
the raw value, NULL when the metric has no rows (ISBLANK); {$key} a what-if parameter's value, from
compile(params=) or its default. A `rank` metric is rank() / dense_rank() OVER the answer's rows,
partitioned by the question's other groupings, before ORDER BY / LIMIT, among the rows the question's
filters leave (RANKX over ALL(T[C]) would also count values the filters remove). A `by_scope` metric
(SWITCH on ISINSCOPE) is resolved per question to the first branch whose column it groups by.
needs(key) says what a question must include for a metric to be answered at all.

SQL is written once (DuckDB syntax) and transpiled by sqlglot to the warehouse's dialect, so
the same definition runs on Databricks and on the local stand-in used for development.
"""

from __future__ import annotations

import os
import re
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import sqlglot
import yaml

from .daxcapture import capture
from .pbimodel import Model, read_pbix, read_pbix_data, read_tmdl, write_tmdl

CAPTURE = "capture.yaml"
GRAINS = ("year", "quarter", "month", "week", "day")
_COL = re.compile(r"\{('(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]\}")
_METRIC = re.compile(r"\{([a-z0-9_]+)\}")
_REF = re.compile(r"\{(?:\$([A-Za-z0-9_]+)|(\?)?([a-z0-9_]+))\}")  # {$param} | {?raw metric} | {metric}
_PARAM = re.compile(r"\{\$([A-Za-z0-9_]+)\}")
_SCALAR = re.compile(r"\{@([a-z0-9_]+)\}")  # in a filter: that metric's value in the question's cell
_RESHAPE = ("Table.AddColumn", "Table.NestedJoin", "Table.ExpandTableColumn", "Table.Group", "Table.RenameColumns",
            "Table.ReplaceValue", "Table.SelectRows", "Table.Pivot", "Table.Unpivot", "Table.Combine",
            "Table.TransformColumns", "Table.Distinct")
_STEP = {"day": ("DAY", 1), "month": ("MONTH", 1), "quarter": ("MONTH", 3), "year": ("YEAR", 1)}
_UNIT_KEY = {"year": "year({})", "quarter": "(year({0}) * 10 + quarter({0}))", "month": "strftime({}, '%Y-%m')"}
_AGG_SQL = {"sum": "sum({})", "avg": "avg({})", "min": "min({})", "max": "max({})", "count": "count({})",
            "count_distinct": "count(DISTINCT {})", "median": "median({})"}


class LayerError(ValueError):
    pass


def _unq(name: str) -> str:
    return name[1:-1].replace("''", "'") if name.startswith("'") else name


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "t"


def ref(table: str, column: str, grain: str | None = None) -> str:
    """How a column is named in questions and answers: Product[Category], Calendar[Date]:month."""
    t = table if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table) else f"'{table}'"
    return f"{t}[{column}]" + (f":{grain}" if grain else "")


def parse_ref(text: str) -> tuple[str, str, str | None]:
    m = re.fullmatch(r"\s*('(?:[^']|'')+'|[^'\[\]]+?)\s*\[([^\]]+)\](?::(\w+))?\s*", text)
    if not m:
        raise LayerError(f"{text!r} is not a column; write Table[Column], e.g. Product[Category]")
    grain = m.group(3)
    if grain and grain not in GRAINS:
        raise LayerError(f"unknown grain {grain!r}; use one of {', '.join(GRAINS)}")
    return _unq(m.group(1)), m.group(2), grain


# ------------------------------------------------------------------ import

def _source(table, catalog: str, schema: str) -> dict:
    """Where a Power BI table's rows live in the warehouse, read from its Power Query when it can be."""
    m = table.source or ""
    out: dict = {}
    native = re.search(r'Value\.NativeQuery\([^,]+,\s*"((?:[^"]|"")*)"', m, re.S)
    if re.search(r"\bDatabricks(MultiCloud)?\.(Catalogs|Contents|Query)\s*\(", m):
        if native:
            out["sql"] = native.group(1).replace('""', '"')
        else:
            names = re.findall(r'\[Name\s*=\s*"([^"]+)"', m)
            if len(names) >= 3:
                out["table"] = ".".join(names[:3])
        out["from"] = "Databricks, read from Power Query"
    if not out.get("table") and not out.get("sql"):
        out["table"] = f"{catalog}.{schema}.{snake(table.name)}"
        out["from"] = "assumed; confirm the warehouse table"
    steps = sorted({s for s in _RESHAPE if s in m})
    if steps:
        out["reshaped_in_power_query"] = steps
    return out


_MSTR = r'"((?:[^"]|"")*)"'
_MSTEP = re.compile(r'Table\.(AddColumn|ExpandTableColumn|RenameColumns)\(\s*(?:#"(?:[^"]|"")*"|[^,()]+)\s*,\s*'
                    r'("(?:[^"]|"")*"\s*,\s*\{[^{}]*\}(?:\s*,\s*\{[^{}]*\})?|"(?:[^"]|"")*"|\{(?:[^{}]|\{[^{}]*\})*\})')


def _renames(m: str) -> dict[str, str]:
    """Columns a table's Power Query renames: {model name: warehouse name}. A column renamed after a
    step added it (Table.AddColumn, an expanded join) has no warehouse column of that name, so is left out."""
    origin: dict[str, str | None] = {}
    for step, arg in _MSTEP.findall(m):
        names = [x.replace('""', '"') for x in re.findall(_MSTR, arg)]
        if step == "AddColumn":
            origin[names[0]] = None
        elif step == "ExpandTableColumn":
            lists = re.findall(r"\{[^{}]*\}", arg)
            origin.update({x.replace('""', '"'): None for x in re.findall(_MSTR, lists[-1] if lists else "")})
        else:
            for old, new in zip(names[::2], names[1::2]):
                origin[new] = origin.pop(old, old)
    return {new: old for new, old in origin.items() if old is not None and old != new}


def import_pbix(pbix: str | Path, out: str | Path, catalog: str = "pilot", schema: str | None = None,
                standin: bool = True) -> dict:
    """The pipeline: .pbix -> TMDL folder -> captured metrics -> capture.yaml (+ a local stand-in)."""
    pbix, out = Path(pbix), Path(out)
    schema = schema or snake(pbix.stem)
    out.mkdir(parents=True, exist_ok=True)
    write_tmdl(read_pbix(pbix), out)
    model = read_tmdl(out)  # every later step reads the TMDL, as it would a PBIP from source control
    spec = build_spec(model, catalog, schema)
    spec["imported"] = {"from": pbix.name, "on": date.today().isoformat()}
    save(spec, out)
    if standin:
        build_standin(pbix, BILayer(spec), out / f"{catalog}.duckdb")
    return spec


def build_spec(model: Model, catalog: str, schema: str) -> dict:
    tables = {}
    for t in model.tables.values():
        cols = [c for c in t.columns if c.expression is None]
        if not cols:
            continue
        if t.source_kind == "calculated":
            tables[t.name] = {"source": None, "why": "calculated in Power BI (a what-if parameter or DAX table)"}
            continue
        renamed = _renames(t.source or "")
        columns = {}
        for c in cols:
            name = c.source_column or c.name
            columns[c.name] = {"column": renamed.get(name, name), "type": c.data_type,
                               **({"renamed_from": renamed[name]} if name in renamed else {})}
        tables[t.name] = {**_source(t, catalog, schema), "columns": columns}
    joins = [{"from": ref(r.from_table, r.from_column), "to": ref(r.to_table, r.to_column),
              "many_to_one": r.from_many and not r.to_many, "one_to_one": not r.from_many and not r.to_many,
              **({"inactive": True} if not r.active else {})}
             for r in model.relationships if r.from_table in tables and r.to_table in tables]
    from .bisecurity import capture_roles
    params: dict = {}
    spec = {"model": model.name, "warehouse": {"dialect": "databricks", "catalog": catalog, "schema": schema},
            "tables": tables, "joins": joins, "metrics": capture(model, params)}
    if params:
        spec["parameters"] = params
    roles = capture_roles(model)
    if roles:
        spec["security"] = {"roles": roles}
    return spec


def save(spec: dict, out: str | Path) -> Path:
    path = Path(out) / CAPTURE
    head = ("# A Power BI model's metrics captured for SQL. Generated from the TMDL beside this file;\n"
            "# owners confirm each table's warehouse source and may correct a captured definition.\n"
            "# status: captured (compiles to SQL) | presentation (formats a value) | not_captured (why says)\n")
    path.write_text(head + yaml.safe_dump(spec, sort_keys=False, width=120, allow_unicode=True))
    return path


def build_standin(pbix: str | Path, layer: "BILayer", path: str | Path) -> Path:
    """A DuckDB file named after the catalog, holding the model's imported rows under the warehouse
    names, so <catalog>.<schema>.<table> resolves locally exactly as it will on Databricks."""
    import duckdb
    path = Path(path)
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    try:
        for name, t in layer.tables.items():
            if not t.get("table"):
                continue
            parts = t["table"].split(".")
            if len(parts) != 3 or parts[0] != path.stem:
                continue
            frame = read_pbix_data(pbix, name)
            frame = frame[[c for c in t["columns"] if c in frame.columns]].rename(
                columns={c: v["column"] for c, v in t["columns"].items()})
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {_ident(parts[1])}")
            con.register("frame", frame)
            con.execute(f"CREATE TABLE {_ident(parts[1])}.{_ident(parts[2])} AS SELECT * FROM frame")
            con.unregister("frame")
    finally:
        con.close()
    return path


# ------------------------------------------------------------------ the layer

@dataclass
class _Scope:
    table: str
    joins: dict  # table -> (alias, sql)
    shift: tuple | None = None  # (table, column, shifted sql): a period metric's date, moved
    param: Callable[[str], str] | None = None  # renders {$key}, a what-if parameter's value


@dataclass
class _Q:
    """What a metric's final expression is rendered against: the question's grouping and the leaves' columns."""
    src: dict  # leaf key -> its column in the final FROM (m0.net_sales)
    by: list
    param: Callable[[str, str], str]


class BILayer:
    def __init__(self, spec: dict):
        self.spec = spec
        self.model = spec.get("model", "model")
        self.tables: dict[str, dict] = spec.get("tables") or {}
        self.metrics: dict[str, dict] = spec.get("metrics") or {}
        self.edges: dict[str, list[tuple[str, str, str]]] = {}  # table -> [(other, my column, its column)]
        for j in spec.get("joins") or []:
            if j.get("inactive"):
                continue
            (ft, fc, _), (tt, tc, _) = parse_ref(j["from"]), parse_ref(j["to"])
            if j.get("many_to_one") or j.get("one_to_one"):
                self.edges.setdefault(ft, []).append((tt, fc, tc))
            if j.get("one_to_one"):
                self.edges.setdefault(tt, []).append((ft, tc, fc))

    @classmethod
    def load(cls, folder: str | Path) -> "BILayer":
        path = Path(folder) / CAPTURE
        if not path.exists():
            raise LayerError(f"no {CAPTURE} in {folder} (nor in {Path(folder) / 'capture'}); run "
                             f"`ictx pbi import <file.pbix> --out {folder}` first")
        return cls(yaml.safe_load(path.read_text()))

    # ------------------------------------------------------------ what can be asked

    def captured(self) -> dict[str, dict]:
        return {k: v for k, v in self.metrics.items() if v.get("status") == "captured" and not v.get("hidden")}

    def leaves(self, key: str, seen: tuple = (), by: list[str] | None = None, units: bool = False) -> list[str]:
        """The base metrics (one aggregate over a table) a metric is computed from. With `by`, a scope
        switch follows the branch that question takes; without it, every branch. With `units`, an
        `iterate` metric is returned as itself (compile computes it in a query of its own)."""
        if key in seen:
            raise LayerError(f"metric cycle: {' -> '.join((*seen, key))}")
        m = self.metrics.get(key)
        if m is None or (m.get("status") not in ("captured", None) and not m.get("hidden")):
            known = ", ".join(self.captured())
            raise LayerError(f"no captured metric {key!r}" + (f" ({m.get('why')})" if m else "") + f". Captured: {known}")
        if units and "iterate" in m:
            self.leaves(key, seen, by)  # its own parts are checked (missing, cycles) all the same
            return [key]
        refs = self._refs(key, by)
        if refs is None:
            return [key]
        out = []
        for k in refs:
            out += [x for x in self.leaves(k, (*seen, key), by, units) if x not in out]
        return out

    def _refs(self, key: str, by: list[str] | None) -> list[str] | None:
        """The metrics a derived metric reads (None for a base metric)."""
        m = self.metrics[key]
        if "formula" in m:
            return [x.group(3) for x in _REF.finditer(m["formula"]) if x.group(3)]
        if "rank" in m:
            return [m["rank"]["of"]]
        if "iterate" in m:
            it = m["iterate"]
            return [it["of"], *([it["having"]] if it.get("having") else [])]
        if "by_scope" in m:
            if by is None:
                return [b.get("metric") or b.get("else") for b in m["by_scope"]]
            return [self._pick(key, by)]
        return None

    def _pick(self, key: str, by: list[str], strict: bool = True) -> str | None:
        """A scope switch's branch for a question: the first whose column is grouped by, else `else`."""
        cols = {parse_ref(d)[:2] for d in by}
        branches = [b for b in self.metrics[key]["by_scope"] if "column" in b]
        for b in branches:
            if parse_ref(b["column"])[:2] in cols:
                return b["metric"]
        other = next((b["else"] for b in self.metrics[key]["by_scope"] if "else" in b), None)
        if other is None and strict:
            raise LayerError(f"{self._label(key)} is defined per level of the grouping and has no value without "
                             f"one: group by {' or '.join(b['column'] for b in branches)}")
        return other

    def reachable(self, table: str) -> dict[str, list[tuple[str, str, str]]]:
        """Tables a row of `table` reaches through relationships, with the join path to each."""
        paths, queue = {table: []}, deque([table])
        while queue:
            t = queue.popleft()
            for other, mine, theirs in self.edges.get(t, []):
                if other not in paths:
                    paths[other] = paths[t] + [(t, mine, other, theirs)]
                    queue.append(other)
        return paths

    def columns_for(self, metrics: list[str]) -> list[str]:
        """Columns every one of these metrics can be grouped or filtered by."""
        tables = None
        for k in metrics:
            for leaf in self.leaves(k):
                r = set(self.reachable(self.metrics[leaf]["table"]))
                tables = r if tables is None else tables & r
        out = []
        for t in sorted(tables or []):
            for c, info in (self.tables.get(t, {}).get("columns") or {}).items():
                out.append(ref(t, c))
        return out

    # ------------------------------------------------------------ SQL

    def _column_sql(self, table: str, column: str, scope: _Scope) -> str:
        info = self.tables.get(table)
        if info is None or not info.get("columns") or column not in info["columns"]:
            raise LayerError(f"no column {ref(table, column)} in the warehouse mapping")
        if table == scope.table:
            alias = "b"
        else:
            paths = self.reachable(scope.table)
            if table not in paths:
                raise LayerError(f"{table} is not related to {scope.table} (rows of {scope.table} do not reach "
                                 f"{table} through the model's relationships)")
            alias = "b"
            for i, (frm, mine, to, theirs) in enumerate(paths[table]):
                if to not in scope.joins:
                    a = f"j{len(scope.joins) + 1}"
                    scope.joins[to] = (a, f"LEFT JOIN {self._relation(to)} {a} ON "
                                          f"{a}.{_ident(self._phys(to, theirs))} = {alias}.{_ident(self._phys(frm, mine))}")
                alias = scope.joins[to][0]
        return f"{alias}.{_ident(info['columns'][column]['column'])}"

    def _phys(self, table: str, column: str) -> str:
        return self.tables[table]["columns"][column]["column"]

    def _relation(self, table: str) -> str:
        info = self.tables.get(table) or {}
        if info.get("sql"):
            return f"({info['sql']})"
        if not info.get("table"):
            raise LayerError(f"{table} has no warehouse table: {info.get('why', 'not mapped')}")
        return ".".join(_ident(p) for p in info["table"].split("."))

    def _render(self, text: str, scope: _Scope) -> str:
        if scope.param:
            text = _PARAM.sub(lambda m: scope.param(m.group(1)), text)
        return _COL.sub(lambda m: self._column_sql(_unq(m.group(1)), m.group(2), scope), text)

    def _dim(self, dim: str, scope: _Scope) -> str:
        table, column, grain = parse_ref(dim)
        if scope.shift and scope.shift[:2] == (table, column):
            return _bucket(scope.shift[2], grain)
        return _bucket(self._column_sql(table, column, scope), grain)

    def _label(self, key: str) -> str:
        m = self.metrics[key]
        return m.get("label") or (self.metrics.get(m.get("of")) or {}).get("label") or key

    def _as_date(self, dim: str, table: str, date: str) -> str:
        """`dim` read as the period's date column when it is the column the metric's rows reach that
        date through (Sales[Date] for Calendar[Date] via Sales[Date] -> Calendar[Date]); else unchanged.
        Exact: rows that reach the date table carry the same value in both, and the rest are dropped."""
        t, c, g = parse_ref(dim)
        dt, dc, _ = parse_ref(date)
        path = self.reachable(table).get(dt) or []
        return ref(dt, dc, g) if path and path[-1] == (t, c, dt, dc) else dim

    def _time_check(self, time: dict, by: list[str], where: list[str], key: str) -> tuple[str, str]:
        """The question can answer this period metric: it names the date column at a grain that fits."""
        dt, dc, _ = parse_ref(time["date"])
        date, label, words = ref(dt, dc), self._label(key), _words(time)
        on_by = [parse_ref(d) for d in by]
        on_where = [parse_ref(d) for d in where]
        for t, c, _ in on_by + on_where:
            if t == dt and c != dc:
                raise LayerError(f"{label} reads {words} on {date}; group or filter by {date} with a grain "
                                 f"(e.g. {date}:month), not by {ref(t, c)}")
        gb = [g or "day" for t, c, g in on_by if (t, c) == (dt, dc)]
        gw = [g or "day" for t, c, g in on_where if (t, c) == (dt, dc)]
        if "to_date" in time:
            u = time["to_date"]
            if len(gb) != 1 or gb[0] == "week" or GRAINS.index(gb[0]) < GRAINS.index(u):
                finer = [g for g in GRAINS if g != "week" and GRAINS.index(g) >= GRAINS.index(u)]
                raise LayerError(f"{label} is a running total ({words}): group by {date} once, at "
                                 f"{' or '.join(finer)} (e.g. {date}:{'month' if u != 'month' else 'day'})")
            return dt, dc
        g = time.get("grain")
        if not gb and not gw:
            raise LayerError(f"{label} compares with {words}: group or filter by "
                             + (f"{date}:{g}" if g else f"{date} with a grain, e.g. {date}:month"))
        if g and (g not in (gb or gw) or any(GRAINS.index(x) > GRAINS.index(g) for x in gb + gw)):
            raise LayerError(f"{label} compares with {words}: group by {date}:{g}")
        return dt, dc

    def time_by(self, key: str) -> list[str]:
        """The date grouping a metric's periods need for it to be answered at all ([] when it reads none)."""
        return self._time_by(self.leaves(key))

    def _time_by(self, leaves: list[str]) -> list[str]:
        fixed, loose = {}, {}
        for leaf in leaves:
            t = self.metrics[leaf].get("time")
            if not t:
                continue
            if t.get("grain"):
                fixed.setdefault(t["date"], t["grain"])
            else:
                loose[t["date"]] = max(loose.get(t["date"], "year"), t.get("to_date") or t["unit"], key=GRAINS.index)
        return [f"{d}:{fixed.get(d) or loose[d]}" for d in {**loose, **fixed}]

    def needs(self, key: str) -> dict:
        """What a question must include for this metric to be answered at all:
        {"by": ["Calendar[Date]:month", "Store[Store]", ...], "params": {key: {label, min, max, ...}}}.
        `by` holds the period grain its time intelligence needs, the column a rank ranks over, and the
        first column of a scope switch that has no `else`; `params` the what-if parameters it reads."""
        self.leaves(key)  # a missing or uncaptured metric says so here
        by: list[str] = []
        params: list[str] = []
        for _ in range(len(self.metrics) + 2):  # a scope switch's branch depends on the grouping found so far
            leaves, extra, params = [], [], []
            self._walk(key, by, leaves, extra, params)
            new = list(by)
            for d in self._time_by(leaves) + extra:
                if parse_ref(d)[:2] not in {parse_ref(x)[:2] for x in new}:
                    new.append(d)
            if new == by:
                break
            by = new
        defs = self.spec.get("parameters") or {}
        return {"by": by, "params": {p: dict(defs.get(p) or {}) for p in params}}

    def _walk(self, key: str, by: list[str], leaves: list, extra: list, params: list) -> None:
        m = self.metrics[key]
        texts = [m.get("formula") or "", m.get("expr") or "", *(m.get("filter") or [])]
        params += [p for t in texts for p in _PARAM.findall(t) if p not in params]
        if "by_scope" in m:
            pick = self._pick(key, by, strict=False)
            if pick is None:
                first = next(b for b in m["by_scope"] if "column" in b)
                extra.append(first["column"])
                pick = first["metric"]
            refs = [pick]
        elif "rank" in m:
            extra.append(m["rank"]["over"])
            refs = [m["rank"]["of"]]
        elif "iterate" in m:
            refs = self._refs(key, by)
        else:
            refs = self._refs(key, by)
        if refs is None:
            if key not in leaves:
                leaves.append(key)
            for k in {x for f in m.get("filter") or [] for x in _SCALAR.findall(f)}:
                self._walk(k, by, leaves, extra, params)
            return
        for k in refs:
            self._walk(k, by, leaves, extra, params)

    def _params(self, given: dict | None) -> Callable[[str, str], str]:
        """Renders {$key}: the value asked for, else the parameter's default; refuses a missing one."""
        defs = self.spec.get("parameters") or {}
        given = dict(given or {})
        unknown = [k for k in given if k not in defs]
        if unknown:
            raise LayerError(f"no what-if parameter {', '.join(map(repr, unknown))}; "
                             + (f"the model's parameters are {', '.join(defs)}" if defs else "the model has none"))

        def value(k: str, who: str) -> str:
            p = defs.get(k) or {}
            v = given[k] if k in given else p.get("default")
            span = (f" between {p['min']} and {p['max']}" + (f" in steps of {p['step']}" if p.get("step") else "")
                    if p.get("min") is not None and p.get("max") is not None else "")
            ask = f"pass params={{'{k}': <a number{span}>}}"
            if v is None:
                what = f"{k} ({p['label']})" if p.get("label") else k
                raise LayerError(f"{who} reads the what-if parameter {what}, which has no default: {ask}")
            if isinstance(v, str):
                try:
                    v = float(v) if any(c in v for c in ".eE") else int(v)
                except ValueError:
                    raise LayerError(f"what-if parameter {k} must be a number, not {v!r}: {ask}") from None
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise LayerError(f"what-if parameter {k} must be a number, not {v!r}: {ask}")
            text = repr(float(v)) if isinstance(v, float) else str(v)
            return f"({text})" if v < 0 else text
        return value

    def _removal(self, m: dict, key: str):
        """What a base metric's `context` (ALL / ALLSELECTED / REMOVEFILTERS / ALLEXCEPT) and `latest` take
        out of the question: fate(dim, in_where) -> "keep" | "drop" | "year" (a date read at year level),
        and covers(table, column). None when the metric removes nothing."""
        label, rules = self._label(key), []
        for c in m.get("context") or []:
            target, keep, level = c.get("remove"), c.get("keep", "none"), c.get("level")
            if not target or keep not in ("none", "filters"):
                raise LayerError(f"{label}: a context entry needs remove: Table or Table[Column] and keep: none|filters")
            exc = {parse_ref(e)[:2] for e in c.get("except") or []}
            if "[" in target:
                tables, cols = set(), {parse_ref(target)[:2]}
            else:
                t = _unq(target.strip())
                if t not in self.tables:
                    raise LayerError(f"{label} removes the filters on {t}, which is not a table of the model")
                tables, cols = set(self.reachable(t)), set()  # DAX's expanded table: what its rows reach
            if level and (tables or level not in ("year", "quarter", "month")):
                raise LayerError(f"{label}: a level removal names a date column and level year, quarter or month")
            rules.append((tables, cols, exc, keep, level))
        lat = m.get("latest")
        lcol = parse_ref(lat["column"])[:2] if lat else None
        if not rules and not lcol:
            return None

        def covers(t: str, c: str) -> bool:
            return (t, c) == lcol or any((t, c) not in exc and ((t, c) in cols or t in tables)
                                         for tables, cols, exc, _, _ in rules)

        def fate(d: str, in_where: bool) -> str:
            t, c, g = parse_ref(d)
            if (t, c) == lcol:  # T[C] = MAX(T[C]) replaces the question's own grouping and filter on T[C]
                return "drop"
            out = "keep"
            for tables, cols, exc, keep, level in rules:
                if (t, c) in exc or not ((t, c) in cols or t in tables) or (in_where and keep == "filters"):
                    continue
                if not level:
                    return "drop"
                out = self._level(ref(t, c), g or "day", level, in_where, label)
                if out == "drop":
                    return out
            return out
        return fate, covers

    def _level(self, date: str, g: str, level: str, in_where: bool, label: str) -> str:
        """A question's date at grain `g`, for a metric that ignores the `level` of the date hierarchy."""
        if g == level:
            return "drop" if level == "year" else "year"
        if GRAINS.index(g) < GRAINS.index(level) or (level == "quarter" and g == "month") or in_where:
            return "keep"  # a coarser period is untouched; so is a filter finer than the level
        fits = [x for x in ("year", "quarter", "month") if GRAINS.index(x) <= GRAINS.index(level)]
        raise LayerError(f"{label} reads {date} without its {level} (the date hierarchy's {level} level is "
                         f"removed), so it has no answer by {g}: group by " + " or ".join(f"{date}:{x}" for x in fits))

    def compile(self, metrics: list[str], by: list[str] | None = None, where: dict | None = None,
                top: int | None = None, order_by: str | None = None, ascending: bool = False,
                dialect: str = "duckdb", row_filters: dict[str, str] | None = None,
                params: dict | None = None, raw: bool = False) -> str:
        """`row_filters` is row-level security: {table: condition over {Table[Column]} placeholders}.
        A table's condition narrows every metric whose rows reach that table (Power BI's RLS
        propagation, many side to one side); bisecurity.filters_for works them out per caller.
        `params` gives what-if parameters their values ({key: number}); a parameter not given takes
        its default. `raw` leaves a metric with no rows NULL (a value rows are compared with, maybe a date)."""
        by, where = list(by or []), dict(where or {})
        if not metrics:
            raise LayerError("name at least one metric")
        param = self._params(params)
        leaves = []
        for k in metrics:
            leaves += [x for x in self.leaves(k, by=by, units=True) if x not in leaves]
        iters = [k for k in leaves if "iterate" in self.metrics[k]]
        leaves = [k for k in leaves if k not in iters]
        groups: dict[tuple, list[str]] = {}
        for k in leaves:
            m = self.metrics[k]
            t = m.get("time")
            groups.setdefault((m["table"], tuple(m.get("filter") or []), tuple(sorted(t.items())) if t else None,
                               _frozen(m.get("context")), _frozen(m.get("latest"))), []).append(k)
        homes = sorted({g[0] for g in groups} | {self.metrics[x]["table"] for k in iters for x in self.leaves(k)})
        for d in [*by, *where]:  # a column no metric's rows reach is a question the model cannot answer
            t, c, _ = parse_ref(d)
            if t not in self.tables or c not in (self.tables[t].get("columns") or {}):
                raise LayerError(f"no column {ref(t, c)} in the warehouse mapping")
            if homes and t in self.tables and not any(t in self.reachable(h) for h in homes):
                raise LayerError(f"{t} is not related to {', '.join(homes)}: rows of {', '.join(homes)} do not reach "
                                 f"{t} through the model's relationships, so no metric asked for has a value per "
                                 f"{ref(t, c)}; group or filter by a column of a table they reach")
        ctes, src, keyed, joins, pieces = [], {}, [], [], []
        for i, ((table, filters, tkey, _, _), keys) in enumerate(groups.items()):
            m0, label = self.metrics[keys[0]], self._label(keys[0])
            scope = _Scope(table, {}, param=lambda p, w=label: param(p, w))
            time = dict(tkey) if tkey else None
            gby, gwhere = list(by), list(where.items())
            if time:  # the column the date relationship joins on is the period's date (equal on every row)
                gby = [self._as_date(d, table, time["date"]) for d in by]
                gwhere = [(self._as_date(d, table, time["date"]), v) for d, v in gwhere]
            scalars = sorted({x for f in filters for x in _SCALAR.findall(f)})
            if scalars and (time or m0.get("latest")):
                raise LayerError(f"{label} compares its rows with a value computed per cell and also reads a period "
                                 f"or the latest one; that combination is not supported")
            dt = self._time_check(time, gby, [d for d, _ in gwhere], keys[0]) if time else None
            on_date = lambda d: dt is not None and parse_ref(d)[:2] == dt  # noqa: E731
            removal = self._removal(m0, keys[0])
            if removal and dt and removal[1](*dt):
                raise LayerError(f"{label} reads {_words(time)} on {ref(*dt)} and also removes the question's "
                                 f"grouping or filter on it; that combination is not supported")

            reach = self.reachable(table)
            applies = lambda d: parse_ref(d)[0] in reach  # noqa: E731 - a filter that does not reach a table leaves it alone
            cell_n = [n for n, d in enumerate(by) if applies(d)]

            def fate(d: str, n: int, in_where: bool) -> str:
                if not applies(d):
                    return "drop"
                if not removal:
                    return "keep"
                f = removal[0](d, in_where)
                orig = (by[n] if not in_where else list(where)[n])
                return removal[0](orig, in_where) if f == "keep" and orig != d else f
            per = None  # to date: (grain asked for, the row's date)
            if time and "shift" in time:
                unit, n = _STEP[time["unit"]]
                scope.shift = (*dt, f"({self._column_sql(*dt, scope)} + INTERVAL '{-time['shift'] * n}' {unit})")
            elif time:
                per = (next(parse_ref(d)[2] for d in gby if on_date(d)), self._column_sql(*dt, scope))
                ctes.append(f"p{i} AS (\n  {self._periods(dt, time['to_date'], per[0], gwhere, on_date)}\n)")
            kept = []  # (n, this metric's column for by[n], the key set's value it joins on)
            for n, d in enumerate(gby):
                f = fate(d, n, False)
                if f == "year":
                    kept.append((n, _bucket(self._column_sql(*parse_ref(d)[:2], scope), "year"),
                                 f"CAST(substr(k.d{n}, 1, 4) AS INTEGER)"))
                elif f == "keep":
                    kept.append((n, "per.p" if per and on_date(d) else self._dim(d, scope), f"k.d{n}"))
            reshaped = len(kept) < len(by) or any(j != f"k.d{n}" for n, _, j in kept)
            dims = [f"{s} AS d{n}" for n, s, _ in kept]
            cell = None  # comparing rows with values computed per cell: each cell's values, joined to its rows
            if scalars:
                if any(j != f"k.d{n}" for n, _, j in kept):
                    raise LayerError(f"{label} compares its rows with a value computed per cell and reads a date "
                                     f"at another level; that combination is not supported")
                inner = self.compile(scalars, by=by, where=where, row_filters=row_filters, params=params, raw=True)
                ctes.append(f"v{i} AS (\n  {inner}\n)")
                cell = f"JOIN v{i} sv ON " + (" AND ".join(f"sv.{_ident(by[n])} IS NOT DISTINCT FROM {s}"
                                                          for n, s, _ in kept) or "TRUE")
                kept = [(n, None, f"k.d{n}") for n in range(len(by))]  # a removed column's rows count in every cell
                reshaped, dims = False, [f"sv.{_ident(d)} AS d{n}" for n, d in enumerate(by)]
            lat = m0.get("latest")
            if lat:
                if lat.get("agg", "max") not in ("max", "min"):
                    raise LayerError(f"{label}: latest.agg is max or min")
                lt, lc, _ = parse_ref(lat["column"])
                dims.append(f"{self._column_sql(lt, lc, scope)} AS lv")
            aggs = []
            for k in keys:
                m = self.metrics[k]
                expr = "*" if m["expr"] == "*" else self._render(m["expr"], scope)
                aggs.append(_AGG_SQL[m["agg"]].format(expr) + f" AS {k}")
                src[k] = f"m{i}.{k}"
            conds = [f"({self._render(_SCALAR.sub(lambda x: f'sv.{x.group(1)}', f), scope)})" for f in filters]
            conds += self._security(table, row_filters, scope)
            for n, (d, v) in enumerate(gwhere):
                if per and on_date(d):  # a running total's date filters chose its periods
                    continue
                f = fate(d, n, True)
                if f == "year":
                    conds.append(_condition(_bucket(self._column_sql(*parse_ref(d)[:2], scope), "year"), _year_of(v)))
                elif f == "keep":
                    conds.append(_condition(self._dim(d, scope), v))
            if scope.shift:  # no period beyond the date table: DAX shifts only to dates it holds
                rel, c = self._relation(dt[0]), _ident(self._phys(*dt))
                conds.append(f"{scope.shift[2]} BETWEEN (SELECT min({c}) FROM {rel}) AND (SELECT max({c}) FROM {rel})")
            sql = f"SELECT {', '.join(dims + aggs)}\n  FROM {self._relation(table)} b"
            sql += "".join(f"\n  {j}" for _, j in scope.joins.values())
            if cell:
                sql += f"\n  {cell}"
            if per:
                u = time["to_date"]
                sql += (f"\n  JOIN p{i} per ON per.u = {_UNIT_KEY[u].format(per[1])} "
                        f"AND per.p >= {_bucket(per[1], per[0])}")
            if conds:
                sql += "\n  WHERE " + "\n    AND ".join(conds)
            if dims:
                sql += f"\n  GROUP BY {', '.join(str(n + 1) for n in range(len(dims)))}"
            on = [f"m{i}.d{n} IS NOT DISTINCT FROM {j}" for n, _, j in kept]
            full = len(cell_n) == len(by)  # its rows reach every column asked for
            if lat or (by and reshaped and full):
                # the question's cells, before this metric's removals: the key set it adds, and for
                # `latest` the column's max (or min) in each cell, which picks the rows it reads
                cwhere = {d: v for d, v in where.items() if applies(d)}
                cby = [by[n] for n in cell_n]
                cells = self._cells(table, cby, cwhere, row_filters, lat, scope.param, cell_n)
                ctes.append(f"c{i} AS (\n  {cells}\n)")
                if by and full:
                    keyed.append(f"c{i}")
                if lat:
                    off = int(lat.get("offset") or 0)
                    on.append(f"m{i}.lv = c{i}.v" + (f" {'+' if off > 0 else '-'} {abs(off)}" if off else ""))
                    if by:
                        joins.append((f"c{i}", [f"c{i}.d{n} IS NOT DISTINCT FROM k.d{n}" for n in cell_n] or ["TRUE"]))
                    else:
                        pieces.append(f"c{i} LEFT JOIN m{i} ON {' AND '.join(on)}")
            else:
                if not reshaped:
                    keyed.append(f"m{i}")
                if not by:
                    pieces.append(f"m{i}")
            if by:
                joins.append((f"m{i}", on))
            ctes.append(f"m{i} AS (\n  {sql}\n)")
        for i, k in enumerate(iters):
            ctes.append(f"x{i} AS (\n  {self._iterate(k, by, where, row_filters, params)}\n)")
            src[k] = f"x{i}.{k}"
            keyed.append(f"x{i}")
            if by:
                joins.append((f"x{i}", [f"x{i}.d{n} IS NOT DISTINCT FROM k.d{n}" for n in range(len(by))]))
            else:
                pieces.append(f"x{i}")
        q = _Q(src, by, param)
        if not groups and not iters:  # only what-if parameters: nothing to read
            if by:
                raise LayerError(f"{', '.join(self._label(k) for k in metrics)} reads no rows (only what-if "
                                 f"parameters), so it has no value per {', '.join(by)}; ask it without by, or with a metric")
            sql = f"SELECT {', '.join(f'{self._expr(k, q, raw)} AS {k}' for k in metrics)}"
            return sql if dialect == "duckdb" else sqlglot.transpile(sql, read="duckdb", write=dialect, pretty=True)[0]
        if by and not keyed:
            raise LayerError(f"no metric asked for reaches every column of {', '.join(by)} (each metric's rows reach "
                             f"only some of them); ask for a metric whose rows reach them all, or group by fewer")
        if by:
            keys_sql = "\n  UNION\n  ".join(f"SELECT {', '.join(f'd{n}' for n in range(len(by)))} FROM {n}" for n in keyed)
            ctes.append(f"k AS (\n  {keys_sql}\n)")
            from_ = "k" + "".join(f"\nLEFT JOIN {n} ON {' AND '.join(on) or 'TRUE'}" for n, on in joins)
            select = [f"k.d{n} AS {_ident(d)}" for n, d in enumerate(by)]
        else:
            from_, select = " CROSS JOIN ".join(pieces), []
        select += [f"{self._expr(k, q, raw)} AS {k}" for k in metrics]
        sql = "WITH " + ",\n".join(ctes) + f"\nSELECT {', '.join(select)}\nFROM {from_}"
        if top or order_by:
            target = order_by or metrics[0]
            if target not in metrics:
                raise LayerError(f"order_by must be one of the requested metrics: {', '.join(metrics)}")
            sql += f"\nORDER BY {target} {'ASC' if ascending else 'DESC'} NULLS LAST"
        elif by:
            sql += f"\nORDER BY {', '.join(str(n + 1) for n in range(len(by)))}"
        if top:
            sql += f"\nLIMIT {int(top)}"
        return sql if dialect == "duckdb" else sqlglot.transpile(sql, read="duckdb", write=dialect, pretty=True)[0]

    def _iterate(self, key: str, by: list[str], where: dict, row_filters, params) -> str:
        """SUMX / AVERAGEX / MINX / MAXX / COUNTX / COUNTROWS(FILTER(...)) over the values of `over`:
        the metric `of` per cell of the question and value of `over` (DAX's context transition), then
        combined per cell; `having` keeps the values whose metric is non-zero (FILTER's condition)."""
        it, label = self.metrics[key]["iterate"], self._label(key)
        agg = it.get("agg", "sum")
        if agg not in ("sum", "avg", "min", "max", "count"):
            raise LayerError(f"{label}: iterate.agg is sum, avg, min, max or count")
        over = [d for d in it["over"] if parse_ref(d)[:2] not in {parse_ref(b)[:2] for b in by}]
        inner_by = [*by, *over]
        wanted = [it["of"], *([it["having"]] if it.get("having") else [])]
        inner = self.compile(wanted, by=inner_by, where=where, row_filters=row_filters, params=params)
        dims = [f"t.{_ident(d)} AS d{n}" for n, d in enumerate(by)]
        value = "*" if agg == "count" else f"t.{it['of']}"
        sql = f"SELECT {', '.join([*dims, _AGG_SQL[agg].format(value) + f' AS {key}'])}\n  FROM ({inner}) t"
        if it.get("having"):
            sql += f"\n  WHERE t.{it['having']} <> 0"
        if by:
            sql += f"\n  GROUP BY {', '.join(str(n + 1) for n in range(len(by)))}"
        return sql

    def _cells(self, table: str, by: list[str], where: dict, row_filters, lat: dict | None, param,
               names: list[int]) -> str:
        """The question's cells over `table`'s rows: its groupings under its filters (and security)."""
        scope = _Scope(table, {}, param=param)
        dims = [f"{self._dim(d, scope)} AS d{n}" for n, d in zip(names, by)]
        if lat:
            dims.append(f"{lat.get('agg', 'max')}({self._column_sql(*parse_ref(lat['column'])[:2], scope)}) AS v")
        conds = [_condition(self._dim(d, scope), v) for d, v in where.items()] + self._security(table, row_filters, scope)
        sql = f"SELECT {', '.join(dims)} FROM {self._relation(table)} b" + "".join(f" {j}" for _, j in scope.joins.values())
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        if by:
            sql += f" GROUP BY {', '.join(str(n + 1) for n in range(len(by)))}"
        return sql

    def _periods(self, dt: tuple[str, str], unit: str, grain: str | None, where: list, on_date) -> str:
        """The periods a running total is asked for: each bucket of the date table, with its year/quarter/month."""
        scope = _Scope(dt[0], {})
        c = self._column_sql(*dt, scope)
        conds = [_condition(self._dim(d, scope), v) for d, v in where if on_date(d)]
        return (f"SELECT DISTINCT {_bucket(c, grain)} AS p, {_UNIT_KEY[unit].format(c)} AS u "
                f"FROM {self._relation(dt[0])} b" + (f" WHERE {' AND '.join(conds)}" if conds else ""))

    def _expr(self, key: str, q: _Q, raw: bool = False, ranking: bool = False) -> str:
        """A metric's value on a row of the answer. {key} is coalesced to 0 (BLANK acts as 0 in DAX
        arithmetic); {?key} (raw) stays NULL when the metric has no rows, for ISBLANK."""
        m, label = self.metrics[key], self._label(key)
        if "by_scope" in m:
            return self._expr(self._pick(key, q.by), q, raw, ranking)
        if "rank" in m:
            r = m["rank"]
            over = parse_ref(r["over"])
            at = [n for n, d in enumerate(q.by) if parse_ref(d)[:2] == over[:2]]
            if not at:
                raise LayerError(f"{label} ranks each {ref(*over[:2])} by {self._label(r['of'])}: group by "
                                 f"{r['over']} (add it to by) to rank")
            if ranking:
                raise LayerError(f"{label} ranks by a value that is itself a rank; that is not supported")
            order, ties = (r.get("order") or "desc").lower(), (r.get("ties") or "skip").lower()
            if order not in ("asc", "desc") or ties not in ("skip", "dense"):
                raise LayerError(f"{label}: rank.order is asc or desc and rank.ties is skip or dense")
            part = [f"k.d{n}" for n in range(len(q.by)) if n not in at]
            value = self._expr(r["of"], q, ranking=True)
            return (f"{'dense_rank' if ties == 'dense' else 'rank'}() OVER ("
                    + (f"PARTITION BY {', '.join(part)} " if part else "") + f"ORDER BY {value} {order.upper()} NULLS LAST)")
        if "formula" not in m:
            return q.src[key] if raw else f"coalesce({q.src[key]}, 0)"

        def sub(x: re.Match) -> str:
            if x.group(1):
                return q.param(x.group(1), label)
            return self._expr(x.group(3), q, raw=bool(x.group(2)), ranking=ranking)
        body = "(" + _REF.sub(sub, m["formula"]) + ")"
        if raw:  # BLANK when every number it reads is blank
            cols = [q.src[x] for x in self.leaves(key, by=q.by, units=True)]
            if cols:
                return f"(CASE WHEN {' AND '.join(f'{c} IS NULL' for c in cols)} THEN NULL ELSE {body} END)"
        return body

    def _security(self, table: str, row_filters: dict[str, str] | None, scope: _Scope) -> list[str]:
        """Row-level security conditions that apply to rows of `table`: those on it and on every table
        its rows reach. A filter on a table they do not reach does not narrow them (as in Power BI)."""
        if not row_filters:
            return []
        reach = self.reachable(table)
        return [f"({self._render(cond, scope)})" for t, cond in row_filters.items() if t in reach]

    def values_sql(self, dim: str, limit: int = 500, row_filters: dict[str, str] | None = None,
                   where: dict | None = None) -> str:
        """Distinct values a column takes, to match what people type against what the data holds, or to
        list them: `where` narrows them by columns of its table or tables it reaches ({Table[Column]: value})."""
        table, _, _ = parse_ref(dim)
        scope = _Scope(table, {})
        expr = self._dim(dim, scope)
        conds = [f"{expr} IS NOT NULL"] + self._security(table, row_filters, scope)
        for d, v in (where or {}).items():
            if parse_ref(d)[0] not in self.reachable(table):
                raise LayerError(f"{parse_ref(d)[0]} is not related to {table}: list {dim} filtered by columns of "
                                 f"{table} or of tables it reaches, or ask a metric by {dim} with that filter")
            conds.append(_condition(self._dim(d, scope), v))
        joins = "".join(f" {j}" for _, j in scope.joins.values())
        return (f"SELECT DISTINCT {expr} AS v FROM {self._relation(table)} b{joins} WHERE {' AND '.join(conds)} "
                f"ORDER BY 1 LIMIT {limit}")


def _bucket(c: str, grain: str | None) -> str:
    return {None: c, "year": f"year({c})", "quarter": f"(CAST(year({c}) AS VARCHAR) || '-Q' || CAST(quarter({c}) AS VARCHAR))",
            "month": f"strftime({c}, '%Y-%m')", "week": f"CAST(date_trunc('week', {c}) AS DATE)",
            "day": f"CAST({c} AS DATE)"}[grain]


def _words(t: dict) -> str:
    if "to_date" in t:
        return f"{t['to_date']} to date"
    n, u = t["shift"], t["unit"]
    if t.get("grain") and abs(n) == 1:
        return f"the {'previous' if n < 0 else 'next'} {u}"
    return f"the period {abs(n)} {u}{'s' if abs(n) != 1 else ''} {'earlier' if n < 0 else 'later'}"


def _frozen(x) -> str | None:
    return json.dumps(x, sort_keys=True) if x else None


def _year_of(value):
    """A month or quarter filter's value ('2025-03', '2025-Q1') read as its year, for a metric answered at year."""
    if isinstance(value, (list, tuple, set)):
        return sorted({_year_of(v) for v in value})
    m = re.match(r"\s*(\d{4})", str(value))
    if not m:
        raise LayerError(f"{value!r} is not a period of a year; write it as 2025-03 (month) or 2025-Q1 (quarter)")
    return int(m.group(1))


def _condition(expr: str, value) -> str:
    def lit(v):
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    if isinstance(value, (list, tuple, set)):
        return f"{expr} IN ({', '.join(lit(v) for v in value)})"
    if value is None:
        return f"{expr} IS NULL"
    return f"{expr} = {lit(value)}"


from .biwarehouse import Warehouse  # noqa: E402,F401 - re-exported; it lives in its own module
