"""Q&A over a Power BI model's captured metrics: agent tools whose numbers all come from SQL.

    kit = BIToolkit(BILayer.load(folder), Warehouse.open(folder, layer))
    Orchestrator(..., system_prompt=kit.system_prompt(), tools=kit.tools(), tool_registry=kit.registry())

The agent never writes SQL. It picks captured metrics, columns to group or filter by, and a
top N; the layer compiles that to SQL for the warehouse, and every answer carries the
definitions and the SQL that produced it. Filter values are matched to what the data holds
("external" -> "External") or the call fails with the valid values, so a typo cannot come
back as a zero that looks like an answer.

What a metric needs to be asked (list_metrics shows it as a hint after the definition):
  [needs by=Calendar[Date]:month or finer]   a period metric: group by the date at that grain
  [needs by=Store[Store]]                     a rank: group by the column it ranks
  [what-if return_rate: ...]                  reads a what-if parameter: compute(params={key: value})
A scope switch (DAX ISINSCOPE) answers with a different metric depending on the column grouped
by; its definition says which. Presentation measures (text for a visual) are listed as
display-only with the metric whose number they show. `metric_needs` reads BILayer.needs(key)
and falls back to time_by(key) plus the spec's rank and parameter shapes.

Limits: at most `max_rows` rows are fetched and shown (the warehouse stops reading past them), a
column's distinct values are read up to MAX_VALUES, and `top` above `max_top` is refused. After a
call, `last` holds the SQL that ran and the rows it returned, for the server's audit log.
"""

from __future__ import annotations

from .agent_runtime import RunContext  # tool signatures are resolved against module globals

import re
from contextvars import ContextVar
from decimal import Decimal

from .agent_tools import _markdown, _match_value
from .bilayer import BILayer, LayerError, Warehouse, parse_ref

# The conversation a tool call runs for: one toolkit serves every conversation on a model, so each
# result it keeps is tagged with whose question made it (see `results`), and a stream sends only its own.
ASKER: ContextVar[str | None] = ContextVar("asker", default=None)

MAX_ROWS = 200
MAX_VALUES = 10_000


def _fmt(v) -> str:
    if isinstance(v, Decimal):  # Databricks DECIMAL sums, DuckDB numeric literals
        v = int(v) if v == v.to_integral_value() and abs(v) >= 1000 else float(v)
    if isinstance(v, bool) or v is None:
        return "" if v is None else str(v)
    if isinstance(v, int):
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.4g}"
    return str(v)


def _num(v) -> str:
    return "?" if v is None else f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


_REFS = re.compile(r"\{\??([a-z0-9_]+)\}")
_PARAM = re.compile(r"\{\$([A-Za-z0-9_]+)\}")


def _children(m: dict) -> list[str]:
    """Metric keys a metric reads: formula placeholders, a rank's `of`, a scope switch's branches."""
    out = _REFS.findall(m.get("formula") or "")
    if m.get("rank"):
        out.append(m["rank"].get("of"))
    if m.get("iterate"):
        out += [m["iterate"].get("of"), m["iterate"].get("having")]
    for b in m.get("by_scope") or []:
        out.append(b.get("metric") or b.get("else"))
    return [k for k in out if k]


def _tree(layer: BILayer, key: str) -> list[str]:
    seen, todo = [], [key]
    while todo:
        k = todo.pop()
        if k in seen or k not in layer.metrics:
            continue
        seen.append(k)
        todo += _children(layer.metrics[k])
    return seen


def parameters(layer: BILayer) -> dict[str, dict]:
    """The model's what-if parameters: {key: {label, table, column, min, max, step, default}}."""
    return dict(layer.spec.get("parameters") or {})


def metric_needs(layer: BILayer, key: str) -> dict:
    """{"by": groupings the metric needs, "params": {key: {label, min, max, step, default}}}.
    BILayer.needs when the compiler has it; else time_by plus rank `over` and `{$param}` reads."""
    known = parameters(layer)
    if hasattr(layer, "needs"):
        try:
            got = layer.needs(key) or {}
        except LayerError:
            return {"by": [], "params": {}}
        return {"by": list(got.get("by") or []),
                "params": {p: {**known.get(p, {}), **(v or {})} for p, v in (got.get("params") or {}).items()}}
    try:
        by = list(layer.time_by(key))
    except Exception:  # noqa: BLE001 - a shape this compiler does not know yet
        by = []
    params: dict = {}
    for k in _tree(layer, key):
        m = layer.metrics[k]
        over = (m.get("rank") or {}).get("over")
        if over and over not in by:
            by.append(over)
        for p in _PARAM.findall(m.get("formula") or "") + _PARAM.findall(m.get("expr") or ""):
            params.setdefault(p, known.get(p, {}))
    return {"by": by, "params": params}


def param_range(p: str, info: dict) -> str:
    """"% Return Rate (return_rate): 0 to 0.2 step 0.01, default 0.05"; a choice: "one of 1, 2, 3"."""
    label = info.get("label")
    rng = (f"{_num(info.get('min'))} to {_num(info.get('max'))}" + (f" step {_num(info['step'])}" if info.get("step")
                                                                   else "")) if "min" in info else \
        f"one of {', '.join(repr(c) if isinstance(c, str) else _num(c) for c in info['choices'])} (which the report's " \
        "slicer picks)" if info.get("choices") \
        else "any value"
    default = f"default {_num(info['default'])}" if info.get("default") is not None else "no default: must be given"
    return f"{label + ' (' + p + ')' if label and label != p else p}: {rng}, {default}"


def describe(layer: BILayer, key: str) -> str:
    m = layer.metrics[key]
    if "formula" in m:
        return f"formula {m['formula']}"
    if m.get("rank"):
        r = m["rank"]
        return (f"rank of each {r['over']} by {r['of']} ({'highest' if r.get('order', 'desc') == 'desc' else 'lowest'}"
                f" = 1{', ties share a rank, no gaps' if r.get('ties') == 'dense' else ''})")
    if m.get("iterate"):
        it = m["iterate"]
        each = f"{it['of']} for each {', '.join(it['over'])}"
        if it["agg"] == "count":
            return f"count of {', '.join(it['over'])} values where {it['having']} holds"
        return (f"{ {'sum': 'sum', 'avg': 'average', 'min': 'smallest', 'max': 'largest'}[it['agg']] } of {each}"
                + (f" where {it['having']} holds" if it.get("having") else ""))
    if m.get("by_scope"):
        def on(b):
            return f"{'grouped or filtered' if b.get('filtered') else ''} by {b['column']}".strip()
        parts = [" and ".join([on(b), *map(on, b.get("also", []))]) + "".join(f", not {on(x)}" for x in b.get("unless", []))
                 + f": {b['metric']}" for b in m["by_scope"] if "column" in b]
        other = next((b["else"] for b in m["by_scope"] if "else" in b), None)
        return ("depends on the grouping: " + "; ".join(parts)
                + (f"; otherwise {other}" if other else "; otherwise not answered (group by one of those columns)"))
    if m.get("status") == "presentation":
        return f"text for display, shows {m['value']}" if m.get("value") else "text for display"
    what = f"{m['agg']}({m['expr']})" if m["expr"] != "*" else f"count of {m['table']} rows"
    what += f" where {' and '.join(m['filter'])}" if m.get("filter") else ""
    t = m.get("time")
    if t:
        what += (f", {t['to_date']} to date over {t['date']}" if t.get("to_date") else
                 f", {t['date']} shifted {t['shift']:+d} {t['unit']}")
    for c in m.get("context") or []:
        exc = f" except {', '.join(c['except'])}" if c.get("except") else ""
        if c.get("level"):
            what += f", ignoring the {c['level']} within {c['remove']} (by {c['level']} it gives the whole year)"
        elif c.get("keep") == "filters":
            what += f", ignoring the question's grouping by {c['remove']}{exc} (its filters still apply)"
        else:
            what += f", ignoring the question's grouping and filters on {c['remove']}{exc}"
    lt = m.get("latest")
    if lt:
        off = int(lt.get("offset") or 0)
        what += (f", only the {'latest' if lt.get('agg', 'max') == 'max' else 'earliest'} {lt['column']} in the "
                 f"question's rows" + (f" {off:+d}" if off else ""))
    return what


def needs(layer: BILayer, key: str) -> str:
    """How a metric must be asked for ("" for an ordinary metric): groupings, then what-if parameters."""
    n = metric_needs(layer, key)
    out = ""
    if n["by"]:
        exact = set()
        for x in _tree(layer, key):
            t = layer.metrics[x].get("time") or {}
            if t.get("grain"):
                exact.add(t["date"])
        shown = []
        for d in n["by"]:
            try:
                t, c, g = parse_ref(d)
            except LayerError:
                shown.append(d)
                continue
            dated = g and not any(parse_ref(e)[:2] == (t, c) for e in exact)
            shown.append(d + (" or finer" if dated else ""))
        out += f" [needs by={', '.join(shown)}]"
    if n["params"]:
        out += " [what-if " + "; ".join(param_range(p, i) for p, i in n["params"].items()) + "]"
    return out


def _shown_by(layer: BILayer) -> dict[str, list[str]]:
    """{metric key: labels of the display-only measures that show it}."""
    out: dict[str, list[str]] = {}
    for m in layer.metrics.values():
        if m.get("status") == "presentation" and m.get("value") and not m.get("hidden"):
            out.setdefault(m["value"], []).append(m.get("label") or m["value"])
    return out


class BIToolkit:
    def __init__(self, layer: BILayer, warehouse: Warehouse, row_filters: dict[str, str] | None = None,
                 max_rows: int = MAX_ROWS, max_top: int | None = None):
        """`row_filters`: the caller's row-level security (bisecurity.filters_for); every query honours it."""
        self.layer, self.wh, self.row_filters = layer, warehouse, row_filters
        self.max_rows, self.max_top, self.last = max_rows, max_top, {}
        self.on_query = None  # called with {sql, ms} after each warehouse query (`ask --trace`)
        self.results: list[dict] = []  # each compute / explore that ran, with its rows (a screen charts them)
        self._seq = 0

    def _query(self, sql: str, limit: int) -> tuple[list[str], list[tuple]]:
        import time
        self.last = {"sql": sql, "rows": None}
        t = time.monotonic()
        try:
            cols, rows = self.wh.query(sql, limit=limit)
        finally:
            self.last["ms"] = int((time.monotonic() - t) * 1000)
            if self.on_query:
                self.on_query(self.last)
        self.last["rows"] = len(rows)
        return cols, rows

    def _keep(self, last: dict) -> None:
        self._seq += 1
        self.results = [*self.results[-49:], {**last, "seq": self._seq, "asker": ASKER.get()}]

    def list_metrics(self) -> str:
        shows = _shown_by(self.layer)
        lines = [f"- {k} ({m['label']}): {describe(self.layer, k)}{needs(self.layer, k)}"
                 + (f" (shown as text by {', '.join(shows[k])})" if k in shows else "")
                 for k, m in self.layer.captured().items()]
        shown = [m for m in self.layer.metrics.values() if not m.get("hidden") and m.get("status") == "presentation"]
        display = [f"- {m['label']}: shows {m['value']} as text" if m.get("value")
                   else f"- {m['label']}: {m.get('why') or 'text or a constant for display, no number behind it'}"
                   for m in shown]
        other = [f"- {m['label']}: {m.get('why', m.get('status'))}" for m in self.layer.metrics.values()
                 if not m.get("hidden") and m.get("status") not in ("captured", "presentation")]
        out = "Captured metrics (compute these):\n" + "\n".join(lines)
        params = parameters(self.layer)
        if params:
            out += ("\n\nWhat-if parameters (compute params={key: value}; the default when not given):\n"
                    + "\n".join(f"- {param_range(p, i)}" for p, i in params.items()))
        if display:
            out += ("\n\nDisplay-only measures (text for a visual; compute the metric they show):\n"
                    + "\n".join(display))
        if other:
            out += "\n\nPower BI measures not captured, and why:\n" + "\n".join(other)
        return out

    def list_columns(self, metrics: list[str] | None = None) -> str:
        try:
            # without metrics, every column some metric reaches: what all of them share can be nothing (a what-if
            # metric reaches no table), and an empty list reads as "the model has no such column"
            cols = self.layer.columns_for(metrics) if metrics else list(dict.fromkeys(
                c for k in self.layer.captured() for c in self.layer.columns_for([k])))
        except LayerError as exc:
            return f"Error: {exc}"
        dates = [c for c in cols if self._type(c) == "dateTime"]
        return ("Columns to group or filter by (Table[Column]):\n" + ", ".join(cols)
                + (f"\n\nDate columns also take a grain suffix (:year :quarter :month :week :day), e.g. "
                   f"{dates[0]}:month" if dates else ""))

    def _type(self, column: str) -> str | None:
        t, c, _ = parse_ref(column)
        return (((self.layer.tables.get(t) or {}).get("columns") or {}).get(c) or {}).get("type")

    def column_values(self, column: str, filters: dict | None = None) -> str:
        try:
            where, notes = self.resolve(filters)
            _, rows = self._query(self._values_sql(column, where), MAX_VALUES + 1)
        except Exception as exc:  # noqa: BLE001 - report it to the agent, do not end the turn
            return f"Error: {exc}"
        vals = [r[0] for r in rows]
        many = f"more than {MAX_VALUES:,}" if len(vals) > MAX_VALUES else str(len(vals))
        shown = 500 if filters else 100
        head = f"{column}" + (f" where {', '.join(f'{k} = {v!r}' for k, v in where.items())}" if where else "")
        return (f"{head} has {many} values: " + ", ".join(map(str, vals[:shown])) + (" ..." if len(vals) > shown else "")
                + (f"\n(Matched: {'; '.join(notes)})" if notes else ""))

    def _values_sql(self, column: str, where: dict | None = None) -> str:
        sql = self.layer.values_sql(column, row_filters=self.row_filters, where=where, limit=MAX_VALUES + 1)
        if self.wh.dialect != "duckdb":
            import sqlglot
            sql = sqlglot.transpile(sql, read="duckdb", write=self.wh.dialect)[0]
        return sql

    def resolve(self, filters: dict | None) -> tuple[dict, list[str]]:
        out, notes = {}, []
        for column, value in (filters or {}).items():
            wanted = value if isinstance(value, (list, tuple, set)) else [value]
            if not any(isinstance(v, str) for v in wanted):
                out[column] = value
                continue
            _, rows = self._query(self._values_sql(column), MAX_VALUES + 1)
            known = [r[0] for r in rows]
            got = []
            for v in wanted:
                if not isinstance(v, str) or v in known:
                    got.append(v)
                    continue
                hit = _match_value(v, known)
                if hit is None:  # a word of a longer label ("online" -> "Online Store"), when only one has it
                    words = [k for k in known if isinstance(k, str) and v.lower() in k.lower().split()]
                    hit = words[0] if len(words) == 1 else None
                if hit is None:
                    shown = ", ".join(map(str, known[:40])) + (" ..." if len(known) > 40 else "")
                    raise LayerError(f"no {column} value matches {v!r}. Values in the data: {shown}")
                got.append(hit)
                notes.append(f"read {column}={v!r} as {hit!r}")
            out[column] = got if isinstance(value, (list, tuple, set)) else got[0]
        return out, notes

    def resolve_params(self, metrics: list[str], given: dict | None) -> tuple[dict, list[str]]:
        """What-if values by parameter key (a label is accepted too), checked against the slider's range;
        and a note per parameter the metrics read, saying which value is used."""
        known = parameters(self.layer)
        for k in metrics:
            if k in self.layer.metrics:
                for p, info in metric_needs(self.layer, k)["params"].items():
                    known.setdefault(p, info)
        out = {}
        for name, value in (given or {}).items():
            key = name if name in known else next(
                (p for p, i in known.items() if str(i.get("label", "")).strip().lower() == str(name).strip().lower()),
                None)
            if key is None:
                listed = "; ".join(param_range(p, i) for p, i in known.items()) or "none"
                raise LayerError(f"no what-if parameter {name!r}. Parameters: {listed}")
            info = known[key]
            text = [c for c in info.get("choices") or [] if isinstance(c, str)]
            if text:  # a slicer of text: the value as the report spells it
                hit = next((c for c in text if c.lower() == str(value).strip().lower()), None)
                if hit is None:
                    raise LayerError(f"{param_range(key, info)}; {value!r} is not one of them")
                out[key] = hit
                continue
            try:
                v = float(value) if not isinstance(value, (int, float)) or isinstance(value, bool) else value
            except (TypeError, ValueError):
                raise LayerError(f"{param_range(key, info)}; {value!r} is not a number") from None
            lo, hi = info.get("min"), info.get("max")
            if lo is not None and hi is not None and not (min(lo, hi) - 1e-9 <= v <= max(lo, hi) + 1e-9):
                raise LayerError(f"{param_range(key, info)}; {_num(v)} is outside the range the report offers")
            if info.get("choices"):
                if v not in info["choices"]:
                    raise LayerError(f"{param_range(key, info)}; {_num(v)} is not one of them")
                v = next(c for c in info["choices"] if c == v)  # 2.0 asked is the slicer's 2
            out[key] = v
        notes = []
        used = {p: i for k in metrics if k in self.layer.metrics for p, i in metric_needs(self.layer, k)["params"].items()}
        for p, info in used.items():
            label = info.get("label") or p
            if p in out:
                notes.append(f"{label} = {_num(out[p])} (as asked)")
            elif info.get("default") is not None:
                notes.append(f"{label} = {_num(info['default'])} (the report's default; pass params={{{p!r}: ...}} "
                             "to change it)")
        return out, notes

    def compute(self, metrics: list[str], by: list[str] | None = None, filters: dict | None = None,
                top: int | None = None, order_by: str | None = None, ascending: bool = False,
                params: dict | None = None) -> str:
        if top is not None and self.max_top and top > self.max_top:
            return f"Error: top is capped at {self.max_top}; ask for fewer rows or narrow with filters."
        try:
            params, what_if = self.resolve_params(metrics, params)
            filters, notes = self.resolve(filters)
            extra = {"params": params} if params else {}  # a compiler without what-if never sees the argument
            sql = self.layer.compile(metrics, by, filters, top, order_by, ascending, dialect=self.wh.dialect,
                                     row_filters=self.row_filters, **extra)
            cols, rows = self._query(sql, self.max_rows + 1)
        except Exception as exc:  # noqa: BLE001 - a bad column or a warehouse error goes back to the agent
            return f"Error: {exc}"
        shown = rows[:self.max_rows]
        self.last.update(kind="compute", columns=cols, shown=shown, metrics=list(metrics), by=list(by or []),
                         filters=dict(filters or {}))
        self._keep(self.last)
        table = _markdown(cols, [[_fmt(v) if c in metrics else str(v) for c, v in zip(cols, r)] for r in shown])
        out = f"Data: {self.wh.describe()}\n\n{table}"
        if len(rows) > self.max_rows:
            out += f"\n(more than {self.max_rows} rows; first {self.max_rows} shown. Narrow it with filters or top.)"
        if notes:
            out += "\nFilters: " + "; ".join(notes) + "."
        if what_if:
            out += "\nWhat-if: " + "; ".join(what_if) + "."
        if not rows or all(not v for r in rows for c, v in zip(cols, r) if c in metrics):
            out += "\nNo data matches, so the result is empty or zero. Do not report it as the answer; check filters."
        try:
            leaves = {x for k in metrics for x in self.layer.leaves(k)} | set(metrics)
        except Exception:  # noqa: BLE001 - definitions are a courtesy; the numbers already ran
            leaves = set(metrics)
        leaves |= {x for k in metrics for x in _tree(self.layer, k)}
        out += "\n\nDefinitions (captured from the Power BI measures; for your reasoning, not to repeat unless asked):\n" + "\n".join(
            f"- {k}: {describe(self.layer, k)}" for k in metrics) + \
            "".join(f"\n  - {k}: {describe(self.layer, k)}" for k in sorted(leaves - set(metrics)))
        return out + f"\n\nSQL ({self.wh.dialect}):\n{sql}"

    def _splits(self, metric: str, period: str) -> list[str]:
        """Columns a change in the metric can be broken down by: text columns of the tables its rows reach,
        not the period's own (calendar) table, ids, images, sort keys or colours; one column per name, from
        the table named like it (Product[Product]) or else the narrowest."""
        dated = {parse_ref(period)[0]}
        skip = re.compile(r"(?i)(^id$|id$|image|sort|colou?r|url|latitude|longitude)")
        best: dict[str, tuple[tuple, str]] = {}
        for d in self.layer.columns_for([metric]):
            t, c, _ = parse_ref(d)
            cols = self.layer.tables.get(t, {}).get("columns") or {}
            if t in dated or skip.search(c) or (cols.get(c, {}).get("type") or "string").lower() != "string":
                continue
            rank = (0 if t.lower() == c.lower() else 1, len(cols))
            if c.lower() not in best or rank < best[c.lower()][0]:
                best[c.lower()] = (rank, d)
        return [d for _, d in best.values()]

    def explain(self, metric: str, period: str, now: str, before: str, filters: dict | None = None,
                depth: int = 3) -> str:
        """Why `metric` changed between two periods of `period` (a date column with its grain, e.g.
        Calendar[Date]:month): at each level, split the change by every candidate column, keep the column
        where one value carries the most of it, narrow to that value, and go again. Deterministic; every
        number is a query the warehouse ran, and each chosen split is kept as an exhibit."""
        if metric not in self.layer.metrics:
            return f"Error: no metric {metric!r}; list_metrics gives the keys"
        try:
            where, notes = self.resolve(filters)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        label = self.layer.metrics[metric].get("label", metric)

        def by(cols: list[str], w: dict) -> tuple[str, list[str], list[tuple]]:
            sql = self.layer.compile([metric], [*cols, period], {**w, period: [now, before]},
                                     dialect=self.wh.dialect, row_filters=self.row_filters)
            c, r = self.wh.query(sql, limit=2000)
            return sql, c, r

        def change(rows, n: int) -> dict:  # value -> (before, now), n: how many leading columns name it
            out: dict = {}
            for r in rows:
                k = r[:n] if n else ()
                b, a = out.get(k, (0.0, 0.0))
                v = float(r[n + 1] or 0)
                out[k] = (b + v, a) if str(r[n]) == before else (b, a + v)
            return out

        try:
            sql, cols, rows = by([], where)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        b0, a0 = change(rows, 0).get((), (0.0, 0.0))
        total = a0 - b0
        lines = [f"{label}: {b0:,.0f} in {before}, {a0:,.0f} in {now}, a change of {'+' if total >= 0 else ''}"
                 f"{total:,.0f}" + (f" ({total / b0:+.1%})" if b0 else "") + "."]
        if notes:
            lines.append("Filters: " + "; ".join(notes) + ".")
        if not total:
            return "\n".join(lines + ["Nothing changed, so there is nothing to explain."])
        def swing(w2: dict) -> float:
            """The median change from one period to the next over the year up to `now`, under `w2`."""
            try:
                rows2 = self.wh.query(self.layer.compile([metric], [period], w2, dialect=self.wh.dialect,
                                                         row_filters=self.row_filters), limit=2000)[1]
            except Exception:  # noqa: BLE001 - without history there is no usual swing
                return 0.0
            vals = [float(r[1] or 0) for r in sorted(rows2, key=lambda r: str(r[0])) if str(r[0]) <= now][-13:]
            out = sorted(abs(b / a - 1) for a, b in zip(vals, vals[1:]) if a)
            return out[len(out) // 2] if out else 0.0

        try:  # every period of the metric, so a part with no rows in a period counts it as 0
            periods = sorted({str(r[0]) for r in self.wh.query(self.layer.compile(
                [metric], [period], where, dialect=self.wh.dialect, row_filters=self.row_filters), limit=5000)[1]})
        except Exception:  # noqa: BLE001
            periods = []
        past = [p for p in periods if p < before][-12:]

        def unusual(w2: dict, pb: float, pa: float) -> bool:
            """Whether a part's change is out of the ordinary for it: at least 4 standard deviations of its
            own values over the 12 periods before `before` (history the change cannot have touched). Strict
            on purpose: searching many columns finds something that looks unusual by chance, and a driver
            made of noise is worse than none."""
            if len(past) < 6:
                return True  # too little history to test; the disproportion alone decides
            try:
                rows2 = self.wh.query(self.layer.compile([metric], [period], {**w2, period: past},
                                                         dialect=self.wh.dialect, row_filters=self.row_filters),
                                      limit=2000)[1]
            except Exception:  # noqa: BLE001
                return False
            got = {str(r[0]): float(r[1] or 0) for r in rows2}
            hist = [got.get(p, 0.0) for p in past]
            mu = sum(hist) / len(hist)
            sd = (sum((x - mu) ** 2 for x in hist) / len(hist)) ** 0.5
            return abs(pa - pb) / max(sd, 0.05 * abs(mu), 1e-9) >= 4

        usual = swing(where or {})
        quiet = bool(b0 and usual and abs(total / b0) < 1.5 * usual)
        if quiet:
            lines.append(f"The total is within its usual swing (the median change from one "
                         f"{period.rsplit(':', 1)[-1]} to the next over the last year was {usual:.1%}).")
        n0 = lambda x: f"{x:,.0f}"  # noqa: E731
        path, used, w, whole = [], set(), dict(where or {}), total
        steps: list[dict] = []  # the investigation as data, for the screen
        verdict = "quiet" if quiet else "spread"
        distinct: dict[str, int] = {}

        def grain(d: str) -> int:  # how many values a column has in the model (finer = more)
            if d not in distinct:
                try:
                    distinct[d] = len(self.wh.query(self.layer.compile([metric], [d], dialect=self.wh.dialect,
                                                                       row_filters=self.row_filters), limit=5000)[1])
                except Exception:  # noqa: BLE001
                    distinct[d] = 0
            return distinct[d]

        for level in range(depth):
            found, ordinary, single = [], [], []
            for d in self._splits(metric, period):
                if d in used or parse_ref(d)[:2] in {parse_ref(k)[:2] for k in w}:
                    continue
                try:
                    q, c, r = by([d], w)
                except Exception:  # noqa: BLE001 - a column this metric cannot be split by is skipped
                    continue
                parts = change(r, 1)
                if len(parts) < 2:
                    # one value here: a finer name for the step before (Category XBOX -> Product XBOX ONE) says
                    # more; an attribute of it (Price Range) does not
                    if parts and path and parse_ref(d)[0] == parse_ref(path[-1][0])[0] and \
                            grain(d) > grain(path[-1][0]) and str(next(iter(parts))[0]) != str(path[-1][1]):
                        single.append((grain(d), d, next(iter(parts.items())), q))
                    continue
                # each value's part of the movement the same way as the whole (net changes can offset, so
                # not of the net change), against its part of the base: what moved out of proportion
                sign = 1 if whole > 0 else -1
                moving = sum(max(0.0, (pa - pb) * sign) for pb, pa in parts.values()) or 1.0
                base = sum(pb for pb, _ in parts.values())
                ref = {k: pb / base for k, (pb, _) in parts.items()} if base >= 0.25 * abs(whole) else None
                wide_ref = ref is None
                if ref is None:  # this scope barely existed before: compare with everyone, before the change
                    try:
                        wide = {k: pb for k, (pb, _) in change(by([d], where or {})[2], 1).items()}
                    except Exception:  # noqa: BLE001
                        continue
                    ref = {k: wide.get(k, 0.0) / (sum(wide.values()) or 1.0) for k in parts}
                scored = [((pa - pb) * sign / moving - ref[k], (pa - pb) * sign / moving, ref[k], k[0], pb, pa)
                          for k, (pb, pa) in parts.items()]
                excess, carries, held, v, pb, pa = max(scored)
                if not unusual({**w, d: v}, pb, pa):  # its own ordinary variation, not a driver
                    ordinary.append(d)
                    continue
                rest = sum(x[5] - x[4] for x in scored if x[3] != v)
                found.append((excess, carries, held, d, v, pb, pa, rest, q, c, r, len(parts), wide_ref))
            if single and (not found or max(x[0] for x in found) < 0.2):  # nothing splits further; a finer name
                _, d, ((v,), (pb, pa)), q = max(single)
                steps.append({"by": d, "value": str(v), "before": pb, "after": pa, "carries": 1.0, "held": 0.0,
                              "held_wide": False, "values": [[str(v), pb, pa]], "n": 1, "rest": 0.0, "sql": q,
                              "finer": True, "tested": [{"by": d, "value": str(v), "status": "driver"}]})
                lines.append(f"Level {level + 1}, by {d}: all of it is {v} ({n0(pb)} to {n0(pa)}), the finer name "
                             f"for {path[-1][1]}.")
                path.append((d, v))
                used.add(d)
                w = {**w, d: v}
                continue
            if not found:
                if not path:
                    lines.append("No part of it broke from its own pattern either. There is no driver to find; say "
                                 "so rather than offering one." if quiet else "No part of it broke from its own "
                                 "pattern; the change is spread across ordinary variation.")
                break
            if quiet and not path:
                lines.append("But underneath, one part broke from its own pattern:")
            top = max(x[0] for x in found)  # within a hair of the best, the more specific column says more
            excess, carries, held, d, v, pb, pa, rest, q, c, r, n, held_wide = max(
                (x for x in found if x[0] >= top - 0.03), key=lambda x: (x[11], x[0]))
            if excess < 0.2:
                lines.append(f"Level {level + 1}: nothing stands out; the most out-of-proportion split, {d}, has {v} "
                             f"carrying {carries:.0%} of the movement against {held:.0%} of the base. The change is "
                             "spread evenly from here.")
                break
            values = sorted(([str(k[0]), xb, xa] for k, (xb, xa) in change(r, 1).items()),
                            key=lambda x: -abs(x[2] - x[1]))
            steps.append({"by": d, "value": str(v), "before": pb, "after": pa, "carries": carries, "held": held,
                          "held_wide": held_wide, "values": values[:12], "n": n, "rest": rest, "sql": q,
                          "tested": [{"by": x[3], "value": str(x[4]), "carries": x[1], "held": x[2],
                                      "status": "driver" if x[3] == d else "also" if x[0] >= 0.2 else "even"}
                                     for x in sorted(found, key=lambda x: -x[0])]
                          + [{"by": o, "status": "ordinary"} for o in ordinary]})
            verdict = "path"
            others = [x for x in sorted(found, key=lambda x: -x[0]) if x[3] != d][:2]
            up = "increases" if whole > 0 else "decreases"
            lines.append(f"Level {level + 1}, by {d} ({n} values): {v} went from {n0(pb)} to {n0(pa)} "
                         f"({'+' if pa >= pb else ''}{n0(pa - pb)}). It carries {carries:.0%} of all the {up} while "
                         f"it was {held:.0%} of the base{' (everyone, before)' if held_wide else ''}; the other {n - 1} together moved {'+' if rest >= 0 else ''}"
                         f"{n0(rest)}." + (" Also out of proportion: " + "; ".join(
                             f"{x[3]} = {x[4]} ({x[1]:.0%} of the {up}, {x[2]:.0%} of the base)" for x in others
                             if x[0] >= 0.2) + "." if any(x[0] >= 0.2 for x in others) else ""))
            path.append((d, v))
            used.add(d)
            w = {**w, d: v}
            whole = pa - pb
        if path:
            lines.append("Where it comes from: " + " → ".join(f"{d} = {v}" for d, v in path) + ".")
        lines.append("The screen shows this investigation as one exhibit (the path, what moved at each step, what "
                     "was ruled out, the rows). Answer in two or three plain sentences: no tables, no lists, no headings, and "
                     "do not repeat the path's numbers.")
        lines.append(f"Data: {self.wh.describe()}")
        self.last = {"kind": "explain", "sql": steps[-1]["sql"] if steps else sql, "columns": [], "shown": [],
                     "metrics": [metric], "by": [], "filters": dict(where or {}),
                     "explain": {"metric": metric, "label": label, "period": period, "before": before, "now": now,
                                 "total_before": b0, "total_after": a0, "usual": usual, "quiet": quiet,
                                 "verdict": verdict, "steps": steps}}
        self._keep(self.last)
        return "\n".join(lines)

    def explore(self, columns: list[str] | None = None, aggregates: list[str] | None = None,
                filters: dict | None = None, conditions: list[str] | None = None, order_by: list[str] | None = None,
                descending: bool = False, limit: int | None = None, distinct: bool = False,
                table: str | None = None) -> str:
        """Rows of the model's tables (not its metrics): the columns asked for, filtered, optionally summed
        or counted by them; see BILayer.explore_sql."""
        limit = min(int(limit or 50), self.max_rows)
        try:
            where, notes = self.resolve(filters)
            sql = self.layer.explore_sql(columns, aggregates, where, conditions, order_by, descending, limit + 1,
                                         distinct, table, row_filters=self.row_filters, dialect=self.wh.dialect)
            cols, rows = self._query(sql, limit + 1)
        except Exception as exc:  # noqa: BLE001 - a bad column or condition goes back to the agent
            return f"Error: {exc}"
        aggs = set(aggregates or [])
        self.last.update(kind="explore", columns=cols, shown=rows[:limit], metrics=list(aggregates or []),
                         by=list(columns or []), filters=dict(where or {}), conditions=list(conditions or []))
        self._keep(self.last)
        table_md = _markdown(cols, [[_fmt(v) if c in aggs else ("" if v is None else str(v)) for c, v in zip(cols, r)]
                                    for r in rows[:limit]])
        out = f"Data: {self.wh.describe()} (rows of the model's tables, not a Power BI measure)\n\n{table_md}"
        out += (f"\n(more than {limit} rows; first {limit} shown. Narrow it or raise limit, up to {self.max_rows}.)"
                if len(rows) > limit else f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")
        if notes:
            out += "\nFilters: " + "; ".join(notes) + "."
        return out + f"\n\nSQL ({self.wh.dialect}):\n{sql}"

    def system_prompt(self) -> str:
        return f"""You answer business questions about the Power BI model "{self.layer.model}".
Numbers come only from the compute tool, which runs the model's captured metrics as SQL on
{self.wh.describe()}.

How to work:
1. Call list_metrics first in a conversation, and list_columns when you need a column.
2. For any number call compute with metric keys, `by` columns written Table[Column] (date columns
   take :year/:quarter/:month/:week/:day), `filters` as {{Table[Column]: value or [values]}}, and
   `top` for "top/bottom N" (ascending=true for bottom). Never estimate or do arithmetic the tool
   could do; for a comparison between periods, compute by month or year and compare those rows.
   To LIST the values of one column ("which drugs are type T", "what plans are there") call
   column_values with `filters`; do not invent a metric for a question that asks for no number.
   For rows and ad-hoc questions no captured metric answers ("claims over $10,000 in March",
   "members in county X with their plan", "count of claims by place of service") call explore:
   `columns`, `conditions` like "Claims[Paid] > 10000" or "T[C] in ('a','b')", `filters`
   ({{Table[Column]: value}}, loosely matched), `aggregates` like "sum(T[C])" / "count(*)" grouped by
   the columns, `order_by`, `limit`. Prefer compute when a captured metric is the number asked for
   (it carries the report's definition); say when a number is a raw aggregate from explore.
3. Hints after a metric in list_metrics say how it must be asked:
   - [needs by=...]: put those columns in `by`. A period metric needs the date at that grain;
     a rank needs the column it ranks (e.g. Store[Store]).
   - Ranks: rank 1 is the highest unless the definition says lowest. "Top 3 stores" is the base
     metric by Store[Store] with top=3 (it can include the rank metric too); "where does North
     rank" is the rank metric by Store[Store], filtered to North. A rank counts only the rows
     the question's filters leave, so filtering changes the ranks.
   - "depends on the grouping": a scope switch returns a different metric depending on which
     column is in `by`; group by the column that matches the question (it says which).
   - [what-if ...]: the metric reads a what-if parameter (a slider in the report). For "what if
     the return rate were 10%" pass params={{key: 0.1}} within the range shown; without params
     the default is used. Always say which value the answer used (compute reports it).
4. If someone asks for a Power BI measure that is not captured, say so in one line with the
   reason list_metrics gives, then answer with the nearest captured metric if there is one. A
   display-only measure is text for a visual (a label, an arrow): compute the metric it shows.
5. When a question is ambiguous but has a sensible default, pick it, state the assumption in one
   line, and answer. Ask only if there is no sensible default.
6. Write for a business reader: lead with the answer in one or two plain sentences, name at most three
   numbers, and write months as people say them ("September 2026", not 2026-09). The screen already shows
   the chart and the table, so do not repeat them as a list. Do not show metric keys, definitions, formulas
   or SQL, and do not mention the data source, unless asked.
7. For "why did X change", "what drove", "what's behind the jump", or a message that names
   @Investigator, call explain with the two periods
   (default: the latest month against the same month a year before, or the month before if the
   question says so). Report the path it found, level by level, with its numbers, and say plainly
   where the change stops being concentrated. Do not add causes the data did not show.
Data is {f"a local stand-in ({self.wh.about}); say so only if the user asks where numbers come from" if self.wh.kind == "local" else "live from Databricks"}."""

    def tools(self) -> list:
        kit = self

        async def list_metrics(ctx: RunContext) -> str:
            """List the captured metrics (keys, labels, definitions, what each needs: by columns, what-if
            parameters), the what-if parameters, display-only measures and the measures not captured, with why."""
            return kit.list_metrics()

        async def list_columns(ctx: RunContext, metrics: list[str] | None = None) -> str:
            """Columns the given metrics (default: all) can be grouped or filtered by, as Table[Column]."""
            return kit.list_columns(metrics)

        async def column_values(ctx: RunContext, column: str,
                                filters: dict[str, str | int | float | list[str]] | None = None) -> str:
            """Distinct values of a column (Table[Column], optionally with a date grain like :month), narrowed
            by `filters` on its table or tables it reaches. Use it to LIST things ("which drugs are type T"),
            and compute only when a number is asked for."""
            return kit.column_values(column, filters)

        async def compute(ctx: RunContext, metrics: list[str], by: list[str] | None = None,
                          filters: dict[str, str | int | float | list[str]] | None = None,
                          top: int | None = None, order_by: str | None = None, ascending: bool = False,
                          params: dict[str, float] | None = None) -> str:
            """Compute captured metrics grouped `by` columns (Table[Column], dates may add :month etc.),
            filtered ({Table[Column]: value or [values]}; values may be typed loosely), optionally the
            `top` N rows ordered by `order_by` (default the first metric; ascending=true for bottom N),
            with what-if `params` ({parameter key: value}; the report's default when left out)."""
            return kit.compute(metrics, by, filters, top, order_by, ascending, params)

        async def explore(ctx: RunContext, columns: list[str] | None = None, aggregates: list[str] | None = None,
                          filters: dict[str, str | int | float | list[str]] | None = None,
                          conditions: list[str] | None = None, order_by: list[str] | None = None,
                          descending: bool = False, limit: int | None = None, distinct: bool = False,
                          table: str | None = None) -> str:
            """Ad-hoc rows of the model's tables, for questions no captured metric answers: `columns`
            (Table[Column], dates may add :month etc.), `conditions` ("T[C] > 100", "T[C] in ('a', 'b')",
            "T[C] between 1 and 9", "T[C] like '%x%'", "T[C] is null"), `filters` ({Table[Column]: value or
            [values]}, loosely matched), `aggregates` ("sum(T[C])", "avg(T[C])", "count(*)",
            "count_distinct(T[C])") grouped by the columns, `order_by` (columns or aggregates as written,
            `descending`), `limit` (default 50), `distinct`, and `table` when several tables' rows could be read."""
            return kit.explore(columns, aggregates, filters, conditions, order_by, descending, limit, distinct, table)

        async def explain(ctx: RunContext, metric: str, period: str, now: str, before: str,
                          filters: dict[str, str | int | float | list[str]] | None = None) -> str:
            """Why a metric changed between two periods ("why are returns up in September", "what drove the
            drop"): `period` is the date column with its grain (e.g. Calendar[Date]:month), `now` and `before`
            two of its values (e.g. "2026-09", "2026-06"), `filters` as for compute. It splits the change by
            every column, narrows to the value that carries most of it, and repeats up to three levels; each
            step becomes an exhibit."""
            return kit.explain(metric, period, now, before, filters)

        return [list_metrics, list_columns, column_values, compute, explore, explain]

    def registry(self) -> dict:
        return {fn.__name__: (fn, (fn.__doc__ or "").strip().split("\n")[0]) for fn in self.tools()}
