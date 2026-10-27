"""Capture a Power BI model's DAX measures as metric definitions that compile to SQL.

    capture(model, parameters={})  {key: definition} for every measure, captured or not, with why;
                                   fills `parameters` with the what-if parameters the metrics read

A measure is captured when it is an aggregation over a table's columns with fixed filters,
or arithmetic over such measures. That is most of what a business model holds:

    CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")       base: sum over Sales, filtered
    SUMX(FILTER(Sales, Sales[Unit] > 1), Sales[Unit] * RELATED(Product[Price]))
    DIVIDE([Net Sales], [Units Sold])                           formula over other metrics
    CALCULATE([Net Sales], Product[Category] = "Bikes")         the filter reaches every part

Time intelligence over a date column is captured as a period the metric reads, which the SQL
layer applies at the grain of the question:

    CALCULATE([Net Sales], PREVIOUSMONTH('Calendar'[Date]))     time: shift -1 month, at month grain
    CALCULATE([Net Sales], SAMEPERIODLASTYEAR('Calendar'[Date])) time: shift -1 year, any grain
    TOTALYTD([Net Sales], 'Calendar'[Date])                    time: to_date year (running total)

PREVIOUS/NEXT DAY|MONTH|QUARTER|YEAR, PARALLELPERIOD (shifts that need that grain), DATEADD and
SAMEPERIODLASTYEAR (shifts at any grain), DATESYTD/QTD/MTD and TOTALYTD/QTD/MTD (to date) are
captured; a fiscal year end, two periods on one metric, DATESINPERIOD and DATESBETWEEN are not.

What a measure reads of the visual is captured as what the question must say:

    CALCULATE([Net Sales], ALL(Store[Store]))                   context: remove Store[Store], keep none
    CALCULATE(SUM(Metrics[X]), ALLSELECTED(Metrics))            context: remove Metrics, keep filters
    CALCULATE([Net Sales], ALL('Calendar'[Date].[Month]))       context: ... level month (year stays)
    VAR _y = MAX(Metrics[Year]) RETURN CALCULATE(..., Metrics[Year] = _y - 1)   latest: max, offset -1
    CALCULATE([Paid], FILTER(ALL(Dim_Date), Dim_Date[Date] <= MAX(Dim_Date[Date])))
                                                                filter: Date <= {@part}, the cell's max date
    VAR _avg = [Avg Paid] RETURN CALCULATE([Paid], Claims[Paid] > _avg)    filter: Paid > {@avg_paid}
    IF([Store Top N] < 4, [Net Sales], 0)                       CASE WHEN ... END in the formula
    SELECTEDVALUE('% Return Rate'[% Return Rate])               {$pct_return_rate}: a what-if parameter
    RANKX(ALL(Store[Store]), [Net Sales], , DESC)               rank: over Store[Store], of net_sales
    SWITCH(TRUE(), ISINSCOPE(Product[Product]), ..., ...)       by_scope: the branch the grouping picks

A filter may compare rows with a value computed from the model ({@key}: that metric in the question's
cell): a VAR of the measure and a plain aggregation are computed once, around the filter, as in DAX. A
measure written inside the condition is computed again for each row tested (context transition), so it
is captured only when those rows do not reach what it reads (or it removes them with ALL); else refused.

ALL / REMOVEFILTERS / ALLSELECTED / ALLEXCEPT are captured as CALCULATE filter modifiers; IF and
SWITCH as CASE over comparisons of metrics, ISBLANK as {?key} IS NULL (the raw value); a measure
written with its table ('% Return Rate'[% Return Rate Value]) is a measure reference. A text measure
is `presentation` and names the number it shows (`value`), captured as `<key>_value` when that is an
expression; for ABS(x) next to a branch on the sign of x (arrows), the value is x.

Anything that would change what such a part means is refused with the reason rather than guessed:
a filter from outside on a column a part itself removes (DAX would drop it), filters around a
`latest`, rank or scope switch, a period next to a removal of its date column, ALL() of everything,
date levels other than month, ranks over anything but ALL(T[C]), VALUE of text (a sort key).

DAX is parsed, not pattern-matched: a small recursive-descent parser covers the expression
language; anything outside the captured subset raises a reason, never a wrong translation.
Semantic differences that are deliberate and documented: a CALCULATE filter is ANDed with the
question's filters (DAX would replace a filter on the same column); text comparison is exact (DAX
compares text case-insensitively); a rank over ALL(T[C]) ranks among the rows the question's filters
leave; BLANK reads as 0 in arithmetic and comparisons ({key}), so IF(..., [m], 0) shows 0, not blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pbimodel import Model

AGGS = {"MEDIAN": "median", "SUM": "sum", "AVERAGE": "avg", "MIN": "min", "MAX": "max", "COUNT": "count", "COUNTA": "count",
        "DISTINCTCOUNT": "count_distinct", "DISTINCTCOUNTNOBLANK": "count_distinct"}
ITERATORS = {"SUMX": "sum", "AVERAGEX": "avg", "MINX": "min", "MAXX": "max", "COUNTX": "count"}
_TIME = {"PREVIOUSMONTH", "PREVIOUSYEAR", "PREVIOUSQUARTER", "PREVIOUSDAY", "NEXTMONTH", "NEXTYEAR", "NEXTQUARTER",
         "SAMEPERIODLASTYEAR", "DATEADD", "DATESYTD", "DATESMTD", "DATESQTD", "TOTALYTD", "TOTALMTD", "TOTALQTD",
         "PARALLELPERIOD", "DATESINPERIOD", "DATESBETWEEN", "OPENINGBALANCEMONTH", "CLOSINGBALANCEMONTH"}
_SHIFT = {"PREVIOUSDAY": (-1, "day"), "PREVIOUSMONTH": (-1, "month"), "PREVIOUSQUARTER": (-1, "quarter"),
          "PREVIOUSYEAR": (-1, "year"), "NEXTDAY": (1, "day"), "NEXTMONTH": (1, "month"), "NEXTQUARTER": (1, "quarter"),
          "NEXTYEAR": (1, "year")}
_TODATE = {"DATESYTD": "year", "DATESQTD": "quarter", "DATESMTD": "month"}
_TOTAL = {"TOTALYTD": "DATESYTD", "TOTALQTD": "DATESQTD", "TOTALMTD": "DATESMTD"}
_UNITS = ("DAY", "MONTH", "QUARTER", "YEAR")
_CONTEXT = {"ALL", "ALLSELECTED", "ALLEXCEPT", "ALLNOBLANKROW", "REMOVEFILTERS", "USERELATIONSHIP", "CROSSFILTER",
            "ISINSCOPE", "HASONEVALUE", "HASONEFILTER", "SELECTEDVALUE", "VALUES", "ISFILTERED", "ISCROSSFILTERED",
            "FILTERS", "EARLIER", "LOOKUPVALUE"}
_RANK = {"RANKX", "TOPN", "RANK", "ROWNUMBER", "WINDOW", "OFFSET"}
_PRESENT = {"FORMAT", "CONCATENATE", "CONCATENATEX", "UNICHAR", "COMBINEVALUES"}
_COND = {"IF", "SWITCH", "IFERROR", "COALESCE"}


class Unsupported(Exception):
    def __init__(self, kind: str, why: str):
        super().__init__(why)
        self.kind, self.why = kind, why


# ------------------------------------------------------------------ tokens and parse

_TOKEN = re.compile(r"""
    (?P<ws>\s+|//[^\n]*|--[^\n]*|/\*.*?\*/)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<col>(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)?\[[^\]]+\])
  | (?P<tbl>'(?:[^']|'')+')
  | (?P<id>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op>&&|\|\||<=|>=|<>|==|[-+*/^&=<>(),{}.])
""", re.S | re.X)


def tokenize(text: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(text):
        m = _TOKEN.match(text, i)
        if not m:
            raise Unsupported("syntax", f"cannot read DAX near {text[i:i + 20]!r}")
        i = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group()))
    return out


def _unq(name: str) -> str:
    return name[1:-1].replace("''", "'") if name.startswith("'") else name


class _Parser:
    """DAX expressions as tuples: (num v) (str v) (col table col) (ref name) (table name)
    (call FN args) (bin op l r) (neg x) (not x) (set items) (blank) (datevar table col level)."""

    def __init__(self, text: str):
        self.toks, self.i = tokenize(text), 0
        self.vars: list[dict] = [{}]
        self.depth = 0  # inside how many function calls
        self.outer: list = []  # values of the measure's own VARs that read the model (computed once, up front)

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("eof", "")

    def take(self, value: str | None = None):
        t = self.peek()
        if value is not None and t[1].upper() != value.upper():
            raise Unsupported("syntax", f"expected {value!r}, found {t[1]!r}")
        self.i += 1
        return t

    def parse(self):
        node = self.expr()
        if self.peek()[0] != "eof":
            raise Unsupported("syntax", f"unexpected {self.peek()[1]!r}")
        return node

    def expr(self):
        if self.peek()[0] == "id" and self.peek()[1].upper() == "VAR":
            scope = {}
            self.vars.append(scope)
            while self.peek()[0] == "id" and self.peek()[1].upper() == "VAR":
                self.take()
                name = self.take()[1]
                self.take("=")
                scope[name] = self.expr()
                if self.depth == 0 and _scalarish(scope[name]):
                    self.outer.append(scope[name])
            self.take("RETURN")
            body = self.expr()
            self.vars.pop()
            return body
        return self.binary(0)

    _LEVELS = [("||",), ("&&",), ("=", "==", "<>", "<", ">", "<=", ">=", "IN"), ("&",), ("+", "-"), ("*", "/"), ("^",)]

    def binary(self, level):
        if level == len(self._LEVELS):
            return self.unary()
        left = self.binary(level + 1)
        while True:
            t = self.peek()
            op = t[1].upper() if t[0] in ("op", "id") else None
            if op not in self._LEVELS[level]:
                return left
            self.take()
            left = ("bin", "=" if op == "==" else op, left, self.binary(level + 1))

    def unary(self):
        t = self.peek()
        if t == ("op", "-"):
            self.take()
            return ("neg", self.unary())
        if t == ("op", "+"):
            self.take()
            return self.unary()
        if t[0] == "id" and t[1].upper() == "NOT" and self.peek(1) != ("op", "("):
            self.take()
            return ("not", self.unary())
        return self.postfix(self.primary())

    def postfix(self, node):
        if self.peek() == ("op", "."):
            self.take()
            level = self.take()
            if node[0] != "col" or level[0] != "col" or not level[1].startswith("["):
                raise Unsupported("syntax", "a '.' other than a date variation (Column.[Month])")
            return ("datevar", node[1], node[2], level[1][1:-1].lower())
        return node

    def primary(self):
        kind, text = self.take()
        if kind == "num":
            return ("num", float(text) if "." in text or "e" in text.lower() else int(text))
        if kind == "str":
            return ("str", text[1:-1].replace('""', '"'))
        if kind == "col":
            table, col = text.split("[", 1)
            return ("col", _unq(table), col[:-1]) if table else ("ref", col[:-1])
        if kind == "tbl":
            return ("table", _unq(text))
        if kind == "op" and text == "(":
            node = self.expr()
            self.take(")")
            return node
        if kind == "op" and text == "{":
            items = [self.expr()]
            while self.peek() == ("op", ","):
                self.take()
                items.append(self.expr())
            self.take("}")
            return ("set", items)
        if kind == "id":
            for scope in reversed(self.vars):
                if text in scope:
                    return scope[text]
            if self.peek() == ("op", "("):
                self.take()
                args = []
                self.depth += 1
                if self.peek() != ("op", ")"):
                    while True:
                        args.append(None if self.peek() in (("op", ","), ("op", ")")) else self.expr())
                        if self.peek() != ("op", ","):
                            break
                        self.take()
                self.depth -= 1
                self.take(")")
                fn = text.upper()
                if fn in ("TRUE", "FALSE"):
                    return ("bool", fn == "TRUE")
                if fn == "BLANK":
                    return ("blank",)
                return ("call", fn, args)
            if text.upper() in ("TRUE", "FALSE"):
                return ("bool", text.upper() == "TRUE")
            return ("table", text)
        raise Unsupported("syntax", f"unexpected {text!r}")


def parse(text: str):
    return _Parser(text).parse()


_TABLE_FNS = {"FILTER", "ALL", "ALLSELECTED", "ALLEXCEPT", "REMOVEFILTERS", "VALUES", "DISTINCT", "SUMMARIZE",
              "SUMMARIZECOLUMNS", "ADDCOLUMNS", "SELECTCOLUMNS", "TOPN", "CALCULATETABLE", "DATESBETWEEN",
              "DATESINPERIOD", "GENERATESERIES", "UNION", "EXCEPT", "INTERSECT", "CROSSJOIN", "TREATAS"}
_READS = {"CALCULATE", "COUNTROWS", *AGGS, *ITERATORS}


def _scalarish(node) -> bool:
    """A VAR's value that reads the model as a number ([M], MAX(T[C]), CALCULATE(...)), not a table."""
    if not isinstance(node, tuple) or not node:
        return False
    if node[0] == "call" and node[1] in _TABLE_FNS:
        return False
    if node[0] == "ref" or (node[0] == "call" and node[1] in _READS):
        return True
    return any(_scalarish(x) for x in node[1:] if isinstance(x, tuple)) or \
        any(_scalarish(x) for x in node[1:] if isinstance(x, list) for x in x)


# ------------------------------------------------------------------ capture

def key_of(label: str) -> str:
    k = re.sub(r"[^a-z0-9]+", "_", label.lower().replace("%", " pct ")).strip("_") or "metric"
    return f"m_{k}" if k[0].isdigit() else k


def col(table: str, column: str) -> str:
    """Placeholder for a model column inside a captured definition: {Sales[Amount]}."""
    t = table if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table) else "'" + table.replace("'", "''") + "'"
    return "{" + f"{t}[{column}]" + "}"


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


@dataclass
class _Ctx:
    model: Model
    keys: dict[str, str]          # measure name -> metric key
    homes: dict[str, str]         # measure name -> table it is defined on
    out: dict[str, dict]
    pending: set
    params: dict = field(default_factory=dict)   # what-if parameters the measures read
    values: dict = field(default_factory=dict)   # presentation key -> its `<key>_value` metric
    outer: dict = field(default_factory=dict)    # id -> a measure's own VAR value (computed once, up front)
    filter: tuple | None = None   # (owner, [(table, column | None)]): in a filter condition over those rows
    iterating: bool = False       # inside an iterator's per-value expression


_REF = re.compile(r"\{(\??)([a-z0-9_]+)\}")
_SCALAR = re.compile(r"\{@([a-z0-9_]+)\}")  # in a filter: that metric's value in the question's cell
_COLREF = re.compile(r"\{('(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]\}")


def capture(model: Model, parameters: dict | None = None) -> dict[str, dict]:
    """Every measure in the model as a metric definition, captured or with the reason it is not.
    `parameters`, when given, is filled with the what-if parameters the captured metrics read."""
    keys, homes = {}, {}
    for table, m in model.measures:
        k = key_of(m.name)
        while k in keys.values():
            k += "_2"
        keys[m.name], homes[m.name] = k, table
    ctx = _Ctx(model, keys, homes, {}, set())
    for _, m in model.measures:
        _measure(ctx, m.name)
    order = []
    for _, m in model.measures:
        order += [keys[m.name]] + ([ctx.values[keys[m.name]]] if keys[m.name] in ctx.values else [])
    used: set[str] = set()  # hidden parts that a captured metric still refers to
    todo = [ctx.out[k] for k in order if ctx.out[k]["status"] == "captured"]
    while todo:
        for ref in _refs(todo.pop()):
            if ref not in used and ctx.out.get(ref, {}).get("hidden"):
                used.add(ref)
                todo.append(ctx.out[ref])
    out = {k: ctx.out[k] for k in order + [k for k in ctx.out if k in used]}
    if parameters is not None:
        read = {p for v in out.values() if v.get("status", "captured") == "captured"
                for p in re.findall(r"\{\$([a-z0-9_]+)\}", v.get("formula", ""))}
        parameters.update({k: v for k, v in ctx.params.items() if k in read})
    return out


def _refs(spec: dict) -> list[str]:
    """The metric keys a definition reads: formula placeholders, a rank's metric, scope branches."""
    out = [m.group(2) for m in _REF.finditer(spec.get("formula", ""))]
    if "rank" in spec:
        out.append(spec["rank"]["of"])
    if "iterate" in spec:
        out += [spec["iterate"]["of"], *([spec["iterate"]["having"]] if spec["iterate"].get("having") else [])]
    out += [k for f in spec.get("filter") or [] for k in _SCALAR.findall(f)]
    return out + [b.get("metric") or b.get("else") for b in spec.get("by_scope", [])]


def _measure(ctx: _Ctx, name: str) -> dict:
    key = ctx.keys[name]
    if key in ctx.out:
        return ctx.out[key]
    if name in ctx.pending:
        raise Unsupported("cycle", f"measures refer to each other in a cycle through {name}")
    ctx.pending.add(name)
    expr = next(m.expression for _, m in ctx.model.measures if m.name == name)
    spec = {"label": name, "dax": " ".join(expr.split())}
    saved, ctx.filter, ctx.iterating = (ctx.filter, ctx.iterating), None, False  # its own context, not its user's
    try:
        p = _Parser(expr)
        node = p.parse()
        ctx.outer.update({id(v): v for v in p.outer})
        if _is_text(node):
            spec.update(status="presentation", why="formats a value as text for display; the value is the metric")
            _shows(ctx, node, key, spec)
            if node[0] == "str" and "value" not in spec:
                spec["why"] = "a display constant (text or an image link); it measures nothing"
        else:
            _finish(ctx, spec, _value(ctx, node, key))
            spec["status"] = "captured"
    except Unsupported as u:
        spec.update(status="presentation" if u.kind == "presentation" else "not_captured", why=u.why)
        if u.kind != "presentation":
            spec["gap"] = u.kind  # what kind of DAX stopped it: syntax, function, context, period, ...
    finally:
        ctx.pending.discard(name)
        ctx.filter, ctx.iterating = saved
    ctx.out[key] = {k: spec[k] for k in ("label", "status", *[x for x in spec if x not in ("label", "status")])}
    return ctx.out[key]


def _finish(ctx: _Ctx, spec: dict, formula: str) -> None:
    """A metric's definition from its formula: a single hidden part becomes the metric itself."""
    inner = re.fullmatch(r"\{([a-z0-9_]+)\}", formula)
    if inner and ctx.out.get(inner.group(1), {}).get("hidden"):
        base = ctx.out.pop(inner.group(1))
        spec.update({k: v for k, v in base.items() if k not in ("hidden", "of")})
    else:
        spec["formula"] = formula


def _results(node) -> list | None:
    """The branches an IF or SWITCH returns (None for an omitted else), or None for anything else."""
    if node[0] != "call" or node[1] not in ("IF", "SWITCH"):
        return None
    if node[1] == "IF":
        return list(node[2][1:3]) + ([None] if len(node[2]) < 3 else [])
    pairs = node[2][1:]
    return pairs[1::2] + ([pairs[-1]] if len(pairs) % 2 else [None])


def _is_text(node) -> bool:
    if node[0] == "str":
        return True
    if node[0] == "call" and node[1] in _PRESENT:
        return True
    branches = _results(node)
    if branches is not None:
        return any(b is not None and _is_text(b) for b in branches) and \
            all(b is None or b[0] == "blank" or _is_text(b) for b in branches)
    return node[0] == "bin" and node[1] == "&"


def _shown(node) -> list:
    """The numbers a text measure displays: what it formats, or joins into the text as it is."""
    if node is None or node[0] in ("str", "blank"):
        return []
    if node[0] == "call" and node[1] == "FORMAT":
        return [node[2][0]]
    if node[0] == "call" and node[1] in ("CONCATENATE", "COMBINEVALUES"):
        return [x for a in node[2] for x in _shown(a)]
    if node[0] == "call" and node[1] in ("UNICHAR", "CONCATENATEX"):
        return []
    if node[0] == "bin" and node[1] == "&":
        return _shown(node[2]) + _shown(node[3])
    branches = _results(node)
    if branches is not None:
        return [x for b in branches for x in _shown(b)]
    return [node]


def _signed(node, x) -> bool:
    """Whether the expression branches on the sign of x (x < 0, x >= 0, ...): an arrow beside ABS(x)."""
    if not isinstance(node, tuple):
        return isinstance(node, list) and any(_signed(n, x) for n in node)
    if node[0] == "bin" and node[1] in ("<", ">", "<=", ">=") and \
            {node[2] == x, node[3] == x} == {True, False} and ("num", 0) in (node[2], node[3]):
        return True
    return any(_signed(n, x) for n in node[1:] if isinstance(n, (tuple, list)))


def _shows(ctx: _Ctx, node, key: str, spec: dict) -> None:
    """Name the number a presentation measure displays (`value`), capturing it as `<key>_value` when it
    is an expression rather than a measure."""
    nums = []
    for x in _shown(node):
        if x not in nums:
            nums.append(x)
    if not nums:
        return
    if len(nums) > 1:
        spec["why"] += "; it shows several numbers, so no single value is named"
        return
    x = nums[0]
    if x[0] == "call" and x[1] == "ABS" and x[2] and _signed(node, x[2][0]):
        x = x[2][0]
    vkey = key + "_value"
    while vkey in ctx.keys.values() or vkey in ctx.out:
        vkey += "_2"
    try:
        formula = _value(ctx, x, vkey)
    except Unsupported as u:
        spec["why"] += f"; the number it shows is not captured: {u.why}"
        return
    inner = re.fullmatch(r"\{([a-z0-9_]+)\}", formula)
    if inner and not ctx.out.get(inner.group(1), {}).get("hidden"):
        spec["value"] = inner.group(1)
        return
    vspec = {"label": f"{spec['label']} (value)", "status": "captured"}
    _finish(ctx, vspec, formula)
    ctx.out[vkey], ctx.values[key], spec["value"] = vspec, vkey, vkey


def _why(fn: str) -> Unsupported:
    if fn in (*_SHIFT, *_TODATE, "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD"):
        return Unsupported("period", f"uses {fn} other than as a CALCULATE filter, which is not captured")
    if fn in _TIME:
        return Unsupported("period", f"uses {fn}, a period function not captured; ask for the metric by month or "
                                     "year and compare")
    if fn in _RANK:
        return Unsupported("ranking", f"ranks within the visual ({fn}) other than RANKX(ALL(T[C]), measure); ask "
                                      "for the top or bottom N by the metric")
    if fn in _CONTEXT:
        return Unsupported("context", f"depends on what the visual filters or shows ({fn})")
    if fn in _COND:
        return Unsupported("conditional", f"branches on the visual's values ({fn})")
    if fn in _PRESENT:
        return Unsupported("presentation", f"formats a value as text ({fn})")
    return Unsupported("function", f"uses {fn}, which is not captured yet")


_SAME = ("table", "agg", "expr", "filter", "context", "latest", "time", "formula", "rank", "by_scope", "iterate")


def _hidden(ctx: _Ctx, owner: str, spec: dict) -> str:
    for k, v in ctx.out.items():  # the same aggregation used twice in one measure is one metric
        if v.get("hidden") and v.get("of") == owner and {x: v.get(x) for x in _SAME} == {x: spec.get(x) for x in _SAME}:
            return k
    n = 1
    while f"{owner}__{n}" in ctx.out:
        n += 1
    ctx.out[f"{owner}__{n}"] = {**spec, "hidden": True, "of": owner}
    return f"{owner}__{n}"


def _part(ctx: _Ctx, node, owner: str) -> str:
    """An expression as one metric key: the measure it names, or a hidden metric of the owner."""
    formula = _value(ctx, node, owner)
    inner = re.fullmatch(r"\{([a-z0-9_]+)\}", formula)
    return inner.group(1) if inner else _hidden(ctx, owner, {"formula": formula})


def _measure_ref(ctx: _Ctx, node):
    """'Table'[Measure]: a measure written with its table, read as a measure reference."""
    if node[0] == "col" and node[2] in ctx.keys:
        t = ctx.model.tables.get(node[1])
        if t is not None and t.column(node[2]) is None:
            if ctx.homes[node[2]] != node[1]:
                raise Unsupported("reference", f"refers to {node[1]}[{node[2]}], but [{node[2]}] is a measure of "
                                               f"{ctx.homes[node[2]]}")
            return ("ref", node[2])
    return node


def _value(ctx: _Ctx, node, owner: str) -> str:
    """A measure-level expression as a formula over metric keys: '{net_sales} / nullif({units}, 0)'."""
    node = _measure_ref(ctx, node)
    kind = node[0]
    if kind == "num":
        return repr(node[1])
    if kind == "blank":
        return "NULL"
    if kind == "neg":
        return f"(-{_value(ctx, node[1], owner)})"
    if kind == "ref":
        name = node[1]
        if name not in ctx.keys:
            raise Unsupported("reference", f"refers to [{name}], which is not a measure (a column without its table?)")
        dep = _measure(ctx, name)
        if dep["status"] != "captured":
            raise Unsupported("dependency", f"built on [{name}], which is not captured: {dep.get('why', '')}")
        return "{" + ctx.keys[name] + "}"
    if kind == "bin":
        op = node[1]
        if op not in ("+", "-", "*", "/"):
            if op == "&":
                raise Unsupported("presentation", "joins text for display")
            raise Unsupported("conditional", f"returns TRUE/FALSE ({op}) rather than a number")
        a, b = _value(ctx, node[2], owner), _value(ctx, node[3], owner)
        return f"({a} {op} nullif({b}, 0))" if op == "/" else f"({a} {op} {b})"
    if kind == "col":
        raise Unsupported("context", f"reads {node[1]}[{node[2]}] outside an aggregation (the visual's current value)")
    if kind == "datevar":
        raise Unsupported("context", f"uses a date variation ({node[1]}[{node[2]}].[{node[3].title()}]) other than "
                                     "inside ALL")
    if kind == "call":
        fn, args = node[1], node[2]
        if fn == "DIVIDE":
            a, b = _value(ctx, args[0], owner), _value(ctx, args[1], owner)
            q = f"({a} / nullif({b}, 0))"
            return f"coalesce({q}, {_value(ctx, args[2], owner)})" if len(args) > 2 and args[2] is not None else q
        if fn in ("ROUND", "ROUNDDOWN", "ROUNDUP", "INT", "TRUNC", "ABS"):
            x = _value(ctx, args[0], owner)
            digits = _value(ctx, args[1], owner) if len(args) > 1 and args[1] is not None else "0"
            return {"ROUND": f"round({x}, {digits})", "ABS": f"abs({x})", "INT": f"floor({x})",
                    "ROUNDDOWN": _toward_zero(x, digits), "TRUNC": _toward_zero(x, digits),
                    "ROUNDUP": f"(sign({x}) * ceil(abs({x}) * power(10, {digits})) / power(10, {digits}))"}[fn]
        if fn == "CALCULATE":
            return _calculate(ctx, args[0], [a for a in args[1:] if a is not None], owner)
        if fn in _TOTAL:
            if len(args) < 2 or args[0] is None:
                raise Unsupported("syntax", f"{fn} needs an expression and a date column")
            if len(args) > 3 or (len(args) == 3 and args[2] is not None and args[2][0] == "str"):
                raise Unsupported("period", f"{fn} with a fiscal year end is not captured; ask for the metric by "
                                            "month and add up")
            rest = [a for a in args[2:3] if a is not None]
            return _calculate(ctx, args[0], [("call", _TOTAL[fn], [args[1]]), *rest], owner)
        if fn == "IF":
            if len(args) < 2 or args[0] is None or args[1] is None:
                raise Unsupported("syntax", "IF needs a condition and a result")
            other = _value(ctx, args[2], owner) if len(args) > 2 and args[2] is not None else "NULL"
            return f"CASE WHEN {_test(ctx, args[0], owner)} THEN {_value(ctx, args[1], owner)} ELSE {other} END"
        if fn == "SWITCH":
            return _switch(ctx, args, owner)
        if fn == "RANKX":
            return "{" + _hidden(ctx, owner, {"rank": _rank(ctx, args, owner)}) + "}"
        if fn == "SELECTEDVALUE":
            return _parameter(ctx, args)
        if fn == "VALUE" and args and args[0] is not None and _is_text(args[0]):
            raise Unsupported("sort_key", "turns text glued together (VALUE of CONCATENATE) back into a number: a "
                                          "sort key for a visual, which measures nothing; sort by its parts instead")
        it = _iterate(ctx, node, owner)
        if it is not None:
            return it
        base = _aggregation(ctx, node, owner)
        if base is not None:
            return "{" + _hidden(ctx, owner, base) + "}"
        raise _why(fn)
    if kind in ("str", "bool"):
        raise Unsupported("presentation", "returns a constant for display")
    raise Unsupported("syntax", f"cannot capture {kind}")


_COMPARE = ("=", "<>", "<", ">", "<=", ">=")


def _number(ctx: _Ctx, node, owner: str) -> str:
    """One side of a measure-level comparison: a number, never text."""
    if node is None or node[0] in ("str", "bool"):
        raise Unsupported("conditional", "compares text or TRUE/FALSE at the measure level")
    if node[0] == "blank":
        raise Unsupported("conditional", "compares with BLANK() at the measure level; ISBLANK is captured")
    return _value(ctx, node, owner)


def _test(ctx: _Ctx, node, owner: str) -> str:
    """A measure-level condition as SQL over metric keys; ISBLANK([m]) reads the raw value {?m}."""
    node = _measure_ref(ctx, node)
    kind = node[0]
    if kind == "bool":
        return "TRUE" if node[1] else "FALSE"
    if kind == "not" or (kind == "call" and node[1] == "NOT"):
        return f"(NOT {_test(ctx, node[1] if kind == 'not' else node[2][0], owner)})"
    if kind == "bin" and node[1] in ("&&", "||"):
        return f"({_test(ctx, node[2], owner)} {'AND' if node[1] == '&&' else 'OR'} {_test(ctx, node[3], owner)})"
    if kind == "call" and node[1] in ("AND", "OR") and len(node[2]) == 2:
        return f"({_test(ctx, node[2][0], owner)} {node[1]} {_test(ctx, node[2][1], owner)})"
    if kind == "bin" and node[1] in _COMPARE:
        return f"({_number(ctx, node[2], owner)} {node[1]} {_number(ctx, node[3], owner)})"
    if kind == "call" and node[1] == "ISBLANK" and node[2] and node[2][0] is not None:
        inner = re.fullmatch(r"\{([a-z0-9_]+)\}", _value(ctx, node[2][0], owner))
        if not inner:
            raise Unsupported("conditional", "ISBLANK of an expression; ISBLANK of a measure is captured")
        return "({?" + inner.group(1) + "} IS NULL)"
    if kind == "call" and node[1] in _CONTEXT:
        raise _why(node[1])
    raise Unsupported("conditional", f"branches on something other than a comparison ({kind})")


def _switch(ctx: _Ctx, args: list, owner: str) -> str:
    """SWITCH(TRUE(), ISINSCOPE(c), e, ..., else) as a scope switch; any other SWITCH as CASE."""
    if len(args) < 3 or args[0] is None:
        raise Unsupported("syntax", "SWITCH needs a value and at least one value/result pair")
    pairs = args[1:]
    tests, other = list(zip(pairs[0::2], pairs[1::2])), pairs[-1] if len(pairs) % 2 else None
    scoped = [t is not None and t[0] == "call" and t[1] == "ISINSCOPE" for t, _ in tests]
    if args[0] == ("bool", True) and any(scoped):
        if not all(scoped):
            raise Unsupported("context", "mixes ISINSCOPE with other conditions in one SWITCH")
        branches = []
        for t, r in tests:
            c = t[2][0] if t[2] else None
            if c is None or c[0] != "col":
                raise Unsupported("context", "ISINSCOPE of something other than a column")
            _column(ctx, c)
            branches.append({"column": col(c[1], c[2])[1:-1], "metric": _part(ctx, r, owner)})
        if other is not None:
            branches.append({"else": _part(ctx, other, owner)})
        return "{" + _hidden(ctx, owner, {"by_scope": branches}) + "}"
    whens = []
    for t, r in tests:
        if t is None or r is None:
            raise Unsupported("syntax", "SWITCH with an empty value or result")
        cond = _test(ctx, t, owner) if args[0] == ("bool", True) else \
            f"({_number(ctx, args[0], owner)} = {_number(ctx, t, owner)})"
        whens.append(f"WHEN {cond} THEN {_value(ctx, r, owner)}")
    return f"CASE {' '.join(whens)} ELSE {_value(ctx, other, owner) if other is not None else 'NULL'} END"


def _rank(ctx: _Ctx, args: list, owner: str) -> dict:
    """RANKX(ALL(T[C]), expr [, , DESC|ASC [, Skip|Dense]]) as a rank over that column."""
    over = args[0] if args else None
    if over is None or over[0] != "call" or over[1] != "ALL" or len(over[2]) != 1 or over[2][0][0] != "col":
        raise Unsupported("ranking", "ranks over something other than ALL(Table[Column]); ask for the top or bottom "
                                     "N by the metric")
    _column(ctx, over[2][0])
    if len(args) < 2 or args[1] is None:
        raise Unsupported("syntax", "RANKX needs an expression to rank by")
    if len(args) > 2 and args[2] is not None:
        raise Unsupported("ranking", "RANKX of a value other than the row's own (its third argument)")
    order = args[3] if len(args) > 3 else None
    order = {None: "desc", ("table", "DESC"): "desc", ("table", "ASC"): "asc", ("num", 0): "desc",
             ("num", 1): "asc"}.get(order if order is None or order[0] == "num" else ("table", order[1].upper()))
    ties = args[4] if len(args) > 4 else None
    ties = {None: "skip", "SKIP": "skip", "DENSE": "dense"}.get(None if ties is None else
                                                                ties[1].upper() if ties[0] == "table" else "?")
    if order is None or ties is None:
        raise Unsupported("ranking", "RANKX with an order other than DESC/ASC or ties other than Skip/Dense")
    c = over[2][0]
    return {"over": col(c[1], c[2])[1:-1], "of": _part(ctx, args[1], owner), "order": order, "ties": ties}


def _parameter(ctx: _Ctx, args: list) -> str:
    """SELECTEDVALUE of a what-if parameter's column (a GENERATESERIES table) as {$key}."""
    c = args[0] if args else None
    if c is None or c[0] != "col":
        raise Unsupported("context", "SELECTEDVALUE of something other than a column")
    t = ctx.model.tables.get(c[1])
    if t is None or t.column(c[2]) is None:
        raise Unsupported("reference", f"no column {c[1]}[{c[2]}] in the model")
    series = None
    if t.source_kind == "calculated" and t.column(c[2]).expression is None:
        try:
            series = parse(t.source or "")
        except Unsupported:
            pass
    if series is None or series[0] != "call" or series[1] != "GENERATESERIES" or not 2 <= len(series[2]) <= 3:
        raise Unsupported("context", f"reads the value the visual selects in {c[1]}[{c[2]}] (SELECTEDVALUE), which "
                                     "is not a what-if parameter")
    nums = [_const(a) for a in series[2]] + ([1] if len(series[2]) == 2 else [])
    alt = _const(args[1]) if len(args) > 1 and args[1] is not None and args[1][0] != "blank" else None
    if None in nums or (len(args) > 1 and args[1] is not None and args[1][0] != "blank" and alt is None):
        raise Unsupported("parameter", f"{c[1]} is a series or a default that is not a plain number")
    key = key_of(t.name)
    spec = {"label": t.name, "table": t.name, "column": c[2], "min": nums[0], "max": nums[1], "step": nums[2],
            "default": alt}
    have = ctx.params.setdefault(key, spec)
    if have != spec:
        raise Unsupported("parameter", f"reads {t.name} with a different default ({alt}) than another measure "
                                       f"({have['default']})")
    return "{$" + key + "}"


def _const(node):
    if node is not None and node[0] == "neg" and node[1][0] == "num":
        return -node[1][1]
    return node[1] if node is not None and node[0] == "num" else None


def _toward_zero(x: str, digits: str) -> str:
    """ROUNDDOWN/TRUNC: toward zero, in a form every dialect keeps (Databricks has no trunc(x, n))."""
    if digits in ("0", "0.0"):
        return f"trunc({x})"
    return f"(sign({x}) * floor(abs({x}) * power(10, {digits})) / power(10, {digits}))"


def _calculate(ctx: _Ctx, expr, args: list, owner: str) -> str:
    """CALCULATE(expr, filters...): row conditions, filter modifiers (ALL...), a latest period
    (T[C] = MAX(T[C]) + n) and at most one period (time intelligence)."""
    filters, periods, context, latest = [], [], [], []
    for a in list(args):
        if a[0] == "call" and a[1] == "FILTER" and len(a[2]) == 2 and None not in a[2] and \
                a[2][0][0] == "call" and a[2][0][1] in ("ALL", "REMOVEFILTERS"):
            context += [e for e in (_modifier(ctx, a[2][0]) or []) if e not in context]
            filters.append(_filtered(ctx, owner, _rows_of(ctx, a[2][0]), a[2][1]))
            continue
        m = _modifier(ctx, a)
        if m is not None:
            context += [e for e in m if e not in context]
            continue
        n = _latest(ctx, a)
        if n is not None:
            latest.append(n)
            continue
        t = _period(ctx, a)
        if t is not None:
            periods.append(t)
        else:
            f = _condition(ctx, a, owner)
            if f:
                filters.append(f)
    if len(periods) > 1:
        raise Unsupported("period", "applies two period functions in one CALCULATE; capture each as its own measure")
    if len(latest) > 1:
        raise Unsupported("context", "filters on two latest periods (= MAX(...)) in one CALCULATE")
    return _with_filters(ctx, _value(ctx, expr, owner), filters, owner, periods[0] if periods else None, context,
                         latest[0] if latest else None)


def _modifier(ctx: _Ctx, node) -> list[dict] | None:
    """ALL / REMOVEFILTERS / ALLSELECTED / ALLEXCEPT as the filters a leaf is computed without."""
    if node[0] != "call" or node[1] not in ("ALL", "REMOVEFILTERS", "ALLSELECTED", "ALLEXCEPT"):
        return None
    fn, args = node[1], [a for a in node[2] if a is not None]
    keep = "filters" if fn == "ALLSELECTED" else "none"
    if not args:
        raise Unsupported("context", f"{fn}() removes every filter in the model, which is not captured")
    if args[0][0] == "table":
        table = args[0][1]
        if table not in ctx.model.tables:
            raise Unsupported("reference", f"no table {table!r} in the model")
        if ctx.model.tables[table].source_kind == "calculated":
            raise Unsupported("parameter", f"{fn}({table}): a what-if parameter or calculated table")
        if fn != "ALLEXCEPT":
            if len(args) > 1:
                raise Unsupported("syntax", f"{fn} of a table and more")
            return [{"remove": table, "keep": keep}]
        for c in args[1:]:
            if c[0] != "col":
                raise Unsupported("syntax", "ALLEXCEPT keeps something other than a column")
            _column(ctx, c)
        return [{"remove": table, "keep": keep, "except": [col(c[1], c[2])[1:-1] for c in args[1:]]}]
    if fn == "ALLEXCEPT":
        raise Unsupported("syntax", "ALLEXCEPT of something other than a table")
    out = []
    for c in args:
        if c[0] not in ("col", "datevar"):
            raise _why(fn)
        _column(ctx, c[:3])
        entry = {"remove": col(c[1], c[2])[1:-1], "keep": keep}
        if c[0] == "datevar":
            _dates(ctx, fn, ("col", c[1], c[2]))
            if c[3] != "month":
                raise Unsupported("context", f"{fn} over the date hierarchy's {c[3].title()} level; only Month is "
                                             "captured")
            entry["level"] = "month"
        out.append(entry)
    return out


def _latest(ctx: _Ctx, node) -> dict | None:
    """T[C] = MAX(T[C]) [+/- n] (often VAR _y = MAX(T[C]) ... T[C] = _y - 1): rows of the latest value."""
    if node[0] != "bin" or node[1] != "=":
        return None
    for c, other in ((node[2], node[3]), (node[3], node[2])):
        offset = 0
        if other[0] == "bin" and other[1] in ("+", "-") and _const(other[3]) is not None:
            offset = _const(other[3]) * (1 if other[1] == "+" else -1)
            other = other[2]
        if c[0] == "col" and other[0] == "call" and other[1] in ("MAX", "MIN") and other[2] and other[2][0] == c:
            if not isinstance(offset, int):
                raise Unsupported("context", "a latest period offset by something other than a whole number")
            _column(ctx, c)
            return {"column": col(c[1], c[2])[1:-1], "agg": other[1].lower(), "offset": offset}
    return None


def _columns_of(filters: list[str]) -> set[tuple[str, str]]:
    return {(_unq(t), c) for f in filters for t, c in _COLREF.findall(f)}


def _split(name: str) -> tuple[str, str] | None:
    m = re.fullmatch(r"('(?:[^']|'')+'|[^\[]+)\[(.+)\]", name)
    return (_unq(m.group(1)), m.group(2)) if m else None


def _reach(ctx: _Ctx, table: str) -> set[str]:
    """A table and every table it reaches many-to-one (DAX's expanded table)."""
    seen, todo = {table}, [table]
    while todo:
        t = todo.pop()
        for r in ctx.model.relationships:
            if r.active and r.from_table == t and r.from_many and not r.to_many and r.to_table not in seen:
                seen.add(r.to_table)
                todo.append(r.to_table)
    return seen


def _removed(ctx: _Ctx, context: list[dict], table: str, column: str) -> bool:
    """Whether a leaf's context removes filters on Table[Column]."""
    name = col(table, column)[1:-1]
    for e in context:
        if _split(e["remove"]) is not None:
            if e["remove"] == name:
                return True
        elif table in _reach(ctx, e["remove"]) and name not in e.get("except", []):
            return True
    return False


def _with_filters(ctx: _Ctx, formula: str, filters: list[str], owner: str, time: dict | None = None,
                  context: list[dict] | None = None, latest: dict | None = None) -> str:
    """The formula with every metric it uses narrowed by the filters, computed without the context's
    filters, at the latest period and read over the period (CALCULATE semantics)."""
    context = context or []
    if not filters and not time and not context and not latest:
        return formula
    if "{$" in formula:
        params = {p["table"] for p in ctx.params.values()}
        if any(t in params for t, _ in _columns_of(filters)) or \
                any((_split(e["remove"]) or (e["remove"],))[0] in params for e in context):
            raise Unsupported("parameter", "filters a what-if parameter's table around a measure that reads it")

    def label(spec):
        return spec.get("label") or next(n for n, k in ctx.keys.items() if k == spec["of"])

    def narrow(m):
        raw, spec = m.group(1), ctx.out[m.group(2)]
        if "rank" in spec or "by_scope" in spec:
            raise Unsupported("context", f"applies CALCULATE filters to [{label(spec)}], a rank or scope switch; "
                                         "ask for it with the filters instead")
        if "formula" in spec:
            inner = _with_filters(ctx, spec["formula"], filters, owner, time, context, latest)
            return "{?" + _hidden(ctx, owner, {"formula": inner}) + "}" if raw else "(" + inner + ")"
        if spec.get("latest"):
            raise Unsupported("context", f"applies CALCULATE filters to [{label(spec)}], which reads the latest "
                                         f"{spec['latest']['column']}; the filters would change which one is latest")
        old = list(spec.get("filter") or [])
        if any(_SCALAR.search(f) for f in old):
            if time or latest:
                raise Unsupported("context", f"applies a period or the latest one to [{label(spec)}], which compares "
                                             "rows with a value it computes; not captured")

            def outer(x):  # the value it compares with is computed under the same filters (DAX's outer context)
                f = _with_filters(ctx, "{" + x.group(1) + "}", filters, owner, None, context, None)
                inner = re.fullmatch(r"\{([a-z0-9_]+)\}", f)
                return "{@" + (inner.group(1) if inner else _hidden(ctx, owner, {"formula": f})) + "}"
            old = [_SCALAR.sub(outer, f) for f in old]
        if any(_SCALAR.search(f) for f in filters) and (time or latest or spec.get("time")):
            raise Unsupported("context", "compares rows with a computed value and reads a period or the latest one "
                                         "in one CALCULATE, which is not captured")
        have = list(spec.get("context") or [])
        outside = _columns_of(filters) | ({_split(latest["column"])} if latest else set()) | \
            ({_split(time["date"])} if time else set())
        hit = next((tc for tc in outside if _removed(ctx, have, *tc)), None)
        if hit:
            raise Unsupported("context", f"filters [{label(spec)}] on {col(*hit)[1:-1]}, which it computes without "
                                         "(ALL); DAX would drop that filter")
        base = {x: spec[x] for x in ("table", "agg", "expr") if x in spec}
        reach = _reach(ctx, spec["table"])  # a filter on a table these rows do not reach leaves them alone
        here = [f for f in filters if all(t in reach for t, _ in _columns_of([f]))]
        if old or here:
            base["filter"] = old + [f for f in here if f not in old]
        if have or context:
            base["context"] = have + [e for e in context if e not in have]
        if latest:
            base["latest"] = latest
        if spec.get("time") and time:
            raise Unsupported("period", f"applies a period to [{label(spec)}], "
                                        "which already reads one; two periods on one metric are not captured")
        if spec.get("time") or time:
            base["time"] = spec.get("time") or time
            if base.get("context") and _removed(ctx, base["context"], *_split(base["time"]["date"])):
                raise Unsupported("period", f"reads a period of {base['time']['date']} and computes without that "
                                            "date's filters (ALL) in one metric, which is not captured")
        return ("{?" if raw else "{") + _hidden(ctx, owner, base) + "}"

    return _REF.sub(narrow, formula)


def _period(ctx: _Ctx, node) -> dict | None:
    """A time-intelligence table argument as the period a metric reads, or None if it is not one.
    {date, shift, unit, grain}: rows dated `shift` units away (grain: the question must be at that grain);
    {date, to_date}: rows from the start of the year/quarter/month up to the period asked for."""
    if node[0] == "call" and node[1] == "KEEPFILTERS":
        return _period(ctx, node[2][0])
    if node[0] != "call" or node[1] not in (*_SHIFT, *_TODATE, "SAMEPERIODLASTYEAR", "DATEADD", "PARALLELPERIOD"):
        return None
    fn, args = node[1], node[2]
    date = _dates(ctx, fn, args[0] if args else None)
    if fn in _SHIFT:
        n, unit = _SHIFT[fn]
        return {"date": date, "shift": n, "unit": unit, "grain": unit}
    if fn == "SAMEPERIODLASTYEAR":
        return {"date": date, "shift": -1, "unit": "year", "grain": None}
    if fn in _TODATE:
        if len(args) > 1 and args[1] is not None:
            raise Unsupported("period", f"{fn} with a fiscal year end is not captured; ask for the metric by month "
                                        "and add up")
        return {"date": date, "to_date": _TODATE[fn]}
    if len(args) != 3 or args[1] is None or args[2] is None:
        raise Unsupported("syntax", f"{fn} needs a date column, a number of intervals and an interval")
    n = args[1]
    if n[0] == "neg" and n[1][0] == "num":
        n = ("num", -n[1][1])
    if n[0] != "num" or not isinstance(n[1], int):
        raise Unsupported("period", f"{fn} by something other than a whole number of intervals")
    if args[2][0] != "table" or args[2][1].upper() not in _UNITS:
        raise Unsupported("period", f"{fn} interval must be DAY, MONTH, QUARTER or YEAR")
    unit = args[2][1].lower()
    return {"date": date, "shift": n[1], "unit": unit, "grain": unit if fn == "PARALLELPERIOD" else None}


def _dates(ctx: _Ctx, fn: str, node) -> str:
    if node is None or node[0] != "col":
        raise Unsupported("period", f"{fn} over something other than a date column ('Calendar'[Date])")
    _column(ctx, node)
    if ctx.model.tables[node[1]].column(node[2]).data_type not in ("dateTime", "date"):
        raise Unsupported("period", f"{fn} over {node[1]}[{node[2]}], which is not a date column")
    return col(node[1], node[2])[1:-1]


def _table_arg(ctx: _Ctx, node, owner: str | None = None) -> tuple[str, list[str]]:
    """A table expression an aggregation iterates: a table, or FILTER(table, condition)."""
    if node[0] == "table":
        if node[1] not in ctx.model.tables:
            raise Unsupported("reference", f"no table {node[1]!r} in the model")
        return node[1], []
    if node[0] == "call" and node[1] == "FILTER":
        table, filters = _table_arg(ctx, node[2][0], owner)
        return table, filters + [_filtered(ctx, owner, [(table, None)], node[2][1])]
    if node[0] == "call":
        raise _why(node[1])
    raise Unsupported("syntax", "iterates something other than a table")


def _aggregation(ctx: _Ctx, node, owner: str | None = None) -> dict | None:
    fn, args = node[1], node[2]
    if fn in AGGS:
        c = args[0]
        if c is None or c[0] != "col":
            raise Unsupported("syntax", f"{fn} of something other than a column")
        _column(ctx, c)
        return {"table": c[1], "agg": AGGS[fn], "expr": col(c[1], c[2])}
    if fn == "COUNTROWS":
        a = args[0]
        if a[0] == "call" and a[1] in ("DISTINCT", "VALUES") and a[2] and a[2][0][0] == "col":
            c = a[2][0]
            _column(ctx, c)
            return {"table": c[1], "agg": "count_distinct", "expr": col(c[1], c[2])}
        table, filters = _table_arg(ctx, a, owner)
        return {"table": table, "agg": "count", "expr": "*", **({"filter": filters} if filters else {})}
    if fn in ITERATORS:
        table, filters = _table_arg(ctx, args[0], owner)
        return {"table": table, "agg": ITERATORS[fn], "expr": _filtered(ctx, None, None, args[1]),
                **({"filter": filters} if filters else {})}
    return None


def _has_measure(ctx: _Ctx, node) -> bool:
    """The expression reads a measure (directly, or through CALCULATE): evaluated per row, it needs
    the row's context (DAX's context transition), which a plain row expression does not have."""
    if isinstance(node, tuple):
        if node and node[0] == "ref":
            return True
        if node and node[0] == "call" and node[1] == "CALCULATE":
            return True
        if node and node[0] == "col" and _measure_ref(ctx, node)[0] == "ref":
            return True
        return any(_has_measure(ctx, x) for x in node[1:])
    if isinstance(node, list):
        return any(_has_measure(ctx, x) for x in node)
    return False


def _over(ctx: _Ctx, node) -> list[str]:
    """What an iterator steps through, as the columns that identify one step: VALUES(T[C]) or
    DISTINCT(T[C]) -> T[C]; a table on the one side of a relationship -> its key; another table ->
    its columns (each distinct row)."""
    if node[0] == "call" and node[1] in ("VALUES", "DISTINCT") and node[2] and node[2][0] is not None:
        a = node[2][0]
        if a[0] == "col":
            _column(ctx, a)
            return [col(a[1], a[2])[1:-1]]
        node = a
    if node[0] != "table":
        raise _why(node[1]) if node[0] == "call" else Unsupported("syntax", "iterates something other than a table")
    t = ctx.model.tables.get(node[1])
    if t is None:
        raise Unsupported("reference", f"no table {node[1]!r} in the model")
    keys = sorted({r.to_column for r in ctx.model.relationships if r.to_table == t.name and r.active})
    names = keys if len(keys) == 1 else [c.name for c in t.columns if c.expression is None]
    for c in names:
        _column(ctx, ("col", t.name, c))
    return [col(t.name, c)[1:-1] for c in names]


def _iterate(ctx: _Ctx, node, owner: str) -> str | None:
    """SUMX / AVERAGEX / MINX / MAXX / COUNTX over VALUES(T[C]) or a table, of an expression that reads
    measures; COUNTROWS(FILTER(VALUES(T[C]), <measure condition>)): the measure per value, then
    combined (`iterate`). None for a plain row iterator, which _aggregation captures."""
    fn, args = node[1], node[2]
    if fn in ITERATORS and len(args) >= 2 and args[0] is not None and args[1] is not None:
        table, expr = args[0], args[1]
    elif fn == "COUNTROWS" and args and args[0] is not None:
        table, expr = args[0], None
    else:
        return None
    cond = None
    if table[0] == "call" and table[1] == "FILTER" and len(table[2]) == 2 and None not in table[2]:
        base, test = table[2]
        if _has_measure(ctx, test):
            table, cond = base, test
        elif base[0] == "call" and base[1] in ("VALUES", "DISTINCT"):  # a condition on the values themselves
            if expr is None:
                return None
            table, expr = base, ("call", "CALCULATE", [expr, test])
    grouped = table[0] == "call" and table[1] in ("VALUES", "DISTINCT")
    if cond is None and not (expr is not None and _has_measure(ctx, expr)) and not (grouped and expr is not None):
        return None
    if _has_outer(ctx, [expr, cond]):
        raise Unsupported("context", f"{fn} reads a VAR computed once before it, inside its per-value expression; "
                                     "not captured")
    over = _over(ctx, table)
    spec = {"agg": "count" if fn == "COUNTROWS" else ITERATORS[fn], "over": over}
    was, ctx.iterating = ctx.iterating, True
    try:
        if cond is not None:
            spec["having"] = _hidden(ctx, owner, {"formula": f"CASE WHEN {_test(ctx, cond, owner)} THEN 1 ELSE 0 END"})
        spec["of"] = _part(ctx, expr, owner) if expr is not None else spec["having"]
    finally:
        ctx.iterating = was
    if fn == "SUMX" and cond is None and table[0] == "table":  # row by row over the table its rows are in
        leaf = ctx.out.get(spec["of"], {})
        if leaf.get("table") == table[1] and leaf.get("agg") in ("sum", "count") and not any(
                leaf.get(x) for x in ("context", "latest", "time")):
            return "{" + spec["of"] + "}"
    return "{" + _hidden(ctx, owner, {"iterate": spec}) + "}"


def _column(ctx: _Ctx, c) -> None:
    t = ctx.model.tables.get(c[1])
    if t is None:
        raise Unsupported("reference", f"no table {c[1]!r} in the model")
    column = t.column(c[2])
    if column is None:
        raise Unsupported("reference", f"no column {c[1]}[{c[2]}] in the model")
    if t.source_kind == "calculated":
        raise Unsupported("parameter", f"uses {c[1]}, a what-if parameter or calculated table that only "
                                       "exists in Power BI")
    if column.expression is not None:
        raise Unsupported("calculated_column", f"uses {c[1]}[{c[2]}], a calculated column that only exists in "
                                               "Power BI")


def _condition(ctx: _Ctx, node, owner: str | None = None) -> str | None:
    """A CALCULATE filter argument as a row condition."""
    if node[0] == "call" and node[1] == "KEEPFILTERS":
        return _condition(ctx, node[2][0], owner)
    if node[0] == "call" and node[1] == "FILTER":
        table, filters = _table_arg(ctx, node, owner)
        return " AND ".join(f"({f})" for f in filters)
    if node[0] == "call" and node[1] not in ("NOT", "AND", "OR", "ISBLANK"):
        raise _why(node[1])
    return _filtered(ctx, owner, _row_cols(ctx, node), node)


def _filtered(ctx: _Ctx, owner: str | None, rows: list | None, node) -> str:
    """A filter condition over `rows` ([(table, column | None)], what DAX iterates to test it) as SQL; with
    an owner it may compare with a value computed in the question's cell ({@key}). None: a row expression."""
    saved, ctx.filter = ctx.filter, (owner, rows) if owner else None
    try:
        return _row(ctx, node)
    finally:
        ctx.filter = saved


def _rows_of(ctx: _Ctx, node) -> list:
    """What FILTER's table argument iterates: ALL(T) / T -> T's rows; ALL(T[C]) / VALUES(T[C]) -> T[C]."""
    if node[0] == "table":
        return [(node[1], None)]
    if node[0] == "call" and node[1] == "FILTER" and node[2]:
        return _rows_of(ctx, node[2][0])
    if node[0] == "call":
        return [x for a in node[2] if a is not None for x in
                ([(a[1], None)] if a[0] == "table" else [(a[1], a[2])] if a[0] in ("col", "datevar") else [])]
    return []


def _row_cols(ctx: _Ctx, node, out: list | None = None) -> list:
    """The columns a filter condition tests row by row (not those inside a value it computes)."""
    out = [] if out is None else out
    if isinstance(node, list):
        for x in node:
            _row_cols(ctx, x, out)
        return out
    if not isinstance(node, tuple) or not node or id(node) in ctx.outer:
        return out
    if node[0] == "col":
        if _measure_ref(ctx, node)[0] == "col" and (node[1], node[2]) not in out:
            out.append((node[1], node[2]))
        return out
    if node[0] == "ref" or (node[0] == "call" and node[1] in _READS):
        return out
    for x in node[1:]:
        if isinstance(x, (tuple, list)):
            _row_cols(ctx, x, out)
    return out


def _has_outer(ctx: _Ctx, node) -> bool:
    if isinstance(node, list):
        return any(_has_outer(ctx, x) for x in node)
    if not isinstance(node, tuple):
        return False
    return id(node) in ctx.outer or any(_has_outer(ctx, x) for x in node[1:] if isinstance(x, (tuple, list)))


def _reads(ctx: _Ctx, node) -> bool:
    """A value computed from the model, not from the row: [M], MAX(T[C]), CALCULATE(...), a VAR of one."""
    return id(node) in ctx.outer or node[0] == "ref" or (node[0] == "call" and node[1] in _READS) or \
        (node[0] == "col" and _measure_ref(ctx, node)[0] == "ref")


def _free_of(ctx: _Ctx, key: str, rows: list) -> bool:
    """Whether a metric's value is the same whichever of `rows` DAX's context transition puts in the
    filter: none of its parts reads rows those filters reach, or each part removes them (ALL)."""
    todo, seen = [key], set()
    while todo:
        k = todo.pop()
        if k in seen:
            continue
        seen.add(k)
        spec = ctx.out.get(k) or {}
        if any(x in spec for x in ("iterate", "rank", "by_scope")):
            return False
        if "table" in spec:
            reach = _reach(ctx, spec["table"])
            for t, c in rows:
                cols = [c] if c else [x.name for x in ctx.model.tables[t].columns] if t in ctx.model.tables else []
                if t in reach and not all(_removed(ctx, spec.get("context") or [], t, x) for x in cols):
                    return False
        todo += _refs(spec)
    return True


def _scalar(ctx: _Ctx, node) -> str:
    """A value a filter compares rows with, computed in the question's cell: {@key}. DAX computes a VAR
    and a plain aggregation (MAX(T[C])) once, in the context around the filter; a measure inside the
    condition again for each row it tests (context transition), which is the same value only when the
    rows tested do not reach what the measure reads."""
    owner, rows = ctx.filter
    if ctx.iterating:
        raise Unsupported("context", "filters on a value computed outside an iterator, inside its per-value "
                                     "expression; DAX computes it once, not per value; not captured")
    transition = id(node) not in ctx.outer and _has_measure(ctx, node)
    ctx.filter = None
    try:
        key = _part(ctx, node, owner)
    finally:
        ctx.filter = (owner, rows)
    if transition and not _free_of(ctx, key, rows):
        what = f"[{node[1] if node[0] == 'ref' else node[2]}]" if node[0] in ("ref", "col") else "a measure"
        tested = ", ".join(col(t, c)[1:-1] if c else t for t, c in rows) or "row"
        raise Unsupported("context", f"compares rows with {what} inside a filter: DAX computes it again for each "
                                     f"{tested} it tests (context transition), not once; captured when written "
                                     f"VAR _v = {what} RETURN CALCULATE(..., ... _v)")
    return "{@" + key + "}"


def _row(ctx: _Ctx, node) -> str:
    """A row-level expression (inside an iterator or a filter) as SQL over column placeholders; in a
    filter, a value computed from the model ([M], MAX(T[C]), a VAR) is that value in the question's cell."""
    if ctx.filter is not None and _reads(ctx, node):
        return _scalar(ctx, node)
    if id(node) in ctx.outer:
        raise Unsupported("context", "reads a VAR computed once, row by row inside an iterator; not captured")
    kind = node[0]
    if kind == "col":
        _column(ctx, node)
        return col(node[1], node[2])
    if kind in ("num", "str", "bool"):
        return _lit(node[1])
    if kind == "blank":
        return "NULL"
    if kind == "neg":
        return f"(-{_row(ctx, node[1])})"
    if kind == "not":
        return f"(NOT {_row(ctx, node[1])})"
    if kind == "bin":
        op, a, b = node[1], node[2], node[3]
        if op == "IN":
            if b[0] != "set":
                raise Unsupported("syntax", "IN with something other than a list of values")
            return f"{_row(ctx, a)} IN ({', '.join(_row(ctx, x) for x in b[1])})"
        if op in ("=", "<>") and (b[0] == "blank" or a[0] == "blank"):
            x = _row(ctx, a if b[0] == "blank" else b)
            return f"{x} IS {'NOT ' if op == '<>' else ''}NULL"
        sql_op = {"&&": "AND", "||": "OR", "&": "||"}.get(op, op)
        if op == "^":
            return f"power({_row(ctx, a)}, {_row(ctx, b)})"
        if op == "/":
            return f"({_row(ctx, a)} / nullif({_row(ctx, b)}, 0))"
        return f"({_row(ctx, a)} {sql_op} {_row(ctx, b)})"
    if kind == "call":
        fn, args = node[1], node[2]
        if fn == "RELATED":
            return _row(ctx, args[0])
        if fn == "DATE" and len(args) == 3 and all(isinstance(_const(a), int) for a in args):
            y, m, d = (_const(a) for a in args)
            return f"DATE '{y:04d}-{m:02d}-{d:02d}'"
        if fn == "NOT":
            return f"(NOT {_row(ctx, args[0])})"
        if fn in ("AND", "OR") and len(args) == 2:
            return f"({_row(ctx, args[0])} {fn} {_row(ctx, args[1])})"
        if fn == "ISBLANK":
            return f"({_row(ctx, args[0])} IS NULL)"
        if fn == "DIVIDE":
            q = f"({_row(ctx, args[0])} / nullif({_row(ctx, args[1])}, 0))"
            return f"coalesce({q}, {_row(ctx, args[2])})" if len(args) > 2 and args[2] is not None else q
        if fn == "IF" and len(args) >= 2:
            other = _row(ctx, args[2]) if len(args) > 2 and args[2] is not None else "NULL"
            return f"CASE WHEN {_row(ctx, args[0])} THEN {_row(ctx, args[1])} ELSE {other} END"
        if fn in ("ABS", "ROUND", "YEAR", "MONTH", "UPPER", "LOWER"):
            inner = ", ".join(_row(ctx, a) for a in args if a is not None)
            return f"{fn.lower()}({inner})"
        raise _why(fn)
    raise Unsupported("syntax", f"cannot capture {kind} in a row expression")


# ------------------------------------------------------------------ coverage

def _shape(why: str) -> str:
    """A reason with its specifics (names, columns, quoted DAX) taken out, so like reasons group."""
    why = re.sub(r"'[^']*'|\"[^\"]*\"", "'...'", why)
    why = re.sub(r"(?:'[^']*'|[\w.]+)?\[[^\]]+\](?:\.\[[^\]]+\])?", "[...]", why)
    return why


def coverage_data(metrics: dict) -> dict:
    """Why measures were not captured, by root cause: a measure built on one that is not captured is
    counted under the reason its root failed, so the few gaps that block the most come first.
    {measures, captured, presentation, not_captured, groups: [{kind, reason, blocks, roots: [key]}]}"""
    metrics = {k: m for k, m in metrics.items() if not m.get("hidden")}  # Power BI's measures, not their parts
    by_label = {m.get("label"): k for k, m in metrics.items()}
    missing = {k: m for k, m in metrics.items() if m.get("status") == "not_captured"}
    status = {}
    for m in metrics.values():
        status[m.get("status")] = status.get(m.get("status"), 0) + 1

    def root(k: str, seen=()) -> str:
        m = metrics[k]
        hit = re.match(r"built on \[(.+?)\], which is not captured", m.get("why", ""))
        dep = by_label.get(hit.group(1)) if hit else None
        return root(dep, (*seen, k)) if dep in missing and dep not in seen else k

    groups: dict[tuple, dict] = {}
    for k in missing:
        r = root(k)
        g = groups.setdefault((metrics[r].get("gap", "?"), _shape(metrics[r].get("why", ""))),
                              {"roots": set(), "blocked": 0})
        g["roots"].add(r)
        g["blocked"] += 1
    return {"measures": len(metrics), "captured": status.get("captured", 0),
            "presentation": status.get("presentation", 0), "not_captured": len(missing),
            "groups": [{"kind": gap, "reason": why, "blocks": g["blocked"], "roots": sorted(g["roots"])}
                       for (gap, why), g in sorted(groups.items(), key=lambda x: (-x[1]["blocked"], x[0]))]}


def coverage(metrics: dict, examples: int = 2, width: int = 160) -> str:
    """coverage_data as a report: a line per root cause, with example measures and their DAX."""
    d = coverage_data(metrics)
    lines = [f"{d['measures']} measures: {d['captured']} captured, {d['presentation']} "
             f"display-only, {d['not_captured']} not captured", "",
             "Not captured, by root cause (`blocks`: measures it stops, its own and those built on them):", "",
             f"{'blocks':>6} {'roots':>5}  {'kind':<17} reason"]
    for g in d["groups"]:
        lines.append(f"{g['blocks']:>6} {len(g['roots']):>5}  {g['kind']:<17} {g['reason'][:width]}")
        for r in g["roots"][:examples]:
            dax = metrics[r].get("dax", "")
            lines.append(f"{'':>14}e.g. [{metrics[r].get('label', r)}] = {dax[:width - 20]}{'...' if len(dax) > width - 20 else ''}")
    return "\n".join(lines)
