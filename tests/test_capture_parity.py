"""Independent acceptance tests for the DAX capture (capture contract v2): coverage and numbers.

Each expected number here is worked out from what the DAX means (and, for the sample, from the
.pbix's own rows with plain SQL written from that meaning), never from the capture or the compiler.
The SQL in `_oracle` below reads the stand-in's rows directly; it is the "by hand" side.

DAX rules leaned on throughout:
- BLANK: an aggregate over no rows is BLANK; in arithmetic BLANK acts as 0; DIVIDE(x, BLANK) is BLANK.
- RANKX(ALL(T[C]), [m], , DESC): ALL removes the filter on T[C] only (other filters, e.g. Store[Type],
  stay); ties share a rank and the next is skipped (default Skip).
- ALL(T) / REMOVEFILTERS: neither the visual's grouping nor slicers on T apply. ALLSELECTED(T): the
  visual's own grouping on T goes, slicers (here: `where`) stay. A CALCULATE filter then applies.
- VAR _y = MAX(T[C]) is evaluated in the visual's cell (before CALCULATE's modifiers).
- ALL('Calendar'[Date].[Month]): a month cell is evaluated at its year.
- ISINSCOPE(c) is true when the visual groups by c.

Where DAX is ambiguous for a SQL question the comment says "INTERPRETATION" and names the
contract's choice.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import duckdb
import pytest

from insurance_context.bilayer import BILayer, LayerError, Warehouse, build_spec, import_pbix
from insurance_context.pbimodel import Column, read_tmdl

from _samples import GET_IT, SAMPLE_PBIX, SAMPLE_TMDL

REPO = Path(__file__).resolve().parents[1]
PLAINS_TMDL = REPO / "clients/plains_health/powerbi/models/MEE_Client_Analytics"

needs_sample = pytest.mark.skipif(not SAMPLE_PBIX.exists(), reason=f"sample .pbix not here: {GET_IT}")


# ------------------------------------------------------------------ helpers

def key(layer: BILayer, label: str) -> str:
    """The metric key of a model measure, found by its label (no guessing of key spelling)."""
    hits = [k for k, m in layer.metrics.items() if m.get("label") == label]
    assert hits, f"no metric labelled {label!r} in the capture"
    return hits[0]


def param_key(layer: BILayer, table: str) -> str:
    params = layer.spec.get("parameters") or {}
    hits = [k for k, p in params.items() if p.get("table") == table]
    assert hits, (f"contract §4: GENERATESERIES table {table!r} should be a top-level `parameters:` entry; "
                  f"spec has parameters={params}")
    return hits[0]


def run(layer: BILayer, wh: Warehouse, metrics: list[str], by=(), where=None, params=None) -> dict:
    """{(dim values...): {metric: value}} for one compiled question."""
    by = list(by)
    kw = {"params": params} if params is not None else {}
    try:
        sql = layer.compile(metrics, by, where, **kw)
    except TypeError as e:
        pytest.fail(f"compile() should take params= (contract §4): {e}")
    except LayerError as e:
        pytest.fail(f"compile({metrics}, by={by}, where={where}, params={params}) was refused: {e}")
    cols, rows = wh.query(sql)
    n = len(by)
    return {tuple(r[:n]): dict(zip(cols[n:], r[n:])) for r in rows}


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)


def check(got: dict, metric: str, expected: dict, why: str, exact_rows: bool = True):
    """Every expected row is present with the expected value; with exact_rows, no other rows."""
    bad = []
    for row, want in expected.items():
        if row not in got:
            bad.append(f"  row {row}: missing (expected {want})")
        elif not same(got[row].get(metric), want):
            bad.append(f"  row {row}: got {got[row].get(metric)!r}, expected {want!r}")
    extra = sorted(set(got) - set(expected)) if exact_rows else []
    if extra:
        bad.append(f"  unexpected rows: {extra}")
    assert not bad, f"{metric}: {why}\n" + "\n".join(bad)


# ------------------------------------------------------------------ 1. coverage finish line

# Measures whose DAX returns text (FORMAT, &, CONCATENATE, a text literal). Their numbers are named
# by `value:`: the label of the captured metric behind the text (contract §7).
SAMPLE_PRESENTATION = {
    # IF([x]<=0,"","+") & FORMAT([x],"0.0%"): the number shown is [x] itself
    "Net Sales Indicator": "Net Sales Variance %",
    "Units Sold Indicator": "Units Sold Variance %",
    "Returns Indicator": "Returns Variance %",
    "Units Returned Indicator": "Returns Variance %",  # the model's DAX reads Returns Variance %, faithfully
    "Net Sales Label": "Net Sales",                     # FORMAT([Net Sales], ...)
    "Profit Indicator": "WIF Profit Difference",
    "Lift Label": "Lift Label (value)",                 # FORMAT(SUM(Association[Importance])) -> own metric
    "Info Tooltip": None,                               # an image URL: a display constant, no number
    "Info Tooltip 2": None,
}
# Plains: every YoY text and chip shows ABS(_d) with ▲/▼ on the sign of _d -> value is _d, an expression,
# so captured as its own "<Label> (value)" metric.
PLAINS_PRESENTATION = {m: f"{m} (value)" for m in (
    "Members YoY", "Risk Score YoY", "Paid PMPM YoY", "Claimants YoY", "PMPM YoY", "Chip Admits K",
    "Chip Per Admit Cost", "Chip OP Visits K", "Chip Per OP Visit Cost", "Chip Prof Visits K",
    "Chip Per Prof Visit Cost")}


def _coverage(spec: dict, presentation: dict, allow: set[str]):
    layer = BILayer(spec)
    measures = {m["label"]: (k, m) for k, m in spec["metrics"].items() if not m.get("hidden")}
    problems = []
    for label, (k, m) in measures.items():
        if label.endswith(" (value)"):
            continue  # a presentation measure's number, checked below
        want = "presentation" if label in presentation else ("not_captured" if label in allow else "captured")
        if m["status"] != want:
            problems.append(f"  {label!r} ({k}): status {m['status']}, expected {want} ({m.get('why', '')})")
    for label, value_label in presentation.items():
        k, m = measures[label]
        v = m.get("value")
        if value_label is None:
            if v:
                problems.append(f"  {label!r}: a display constant should have no value, has {v!r}")
            continue
        if not v:
            problems.append(f"  {label!r}: displays a number, so needs value: naming its metric "
                            f"(expected the metric labelled {value_label!r})")
        elif v not in layer.captured():
            problems.append(f"  {label!r}: value {v!r} is not a captured, visible metric")
        elif layer.metrics[v].get("label") != value_label:
            problems.append(f"  {label!r}: value {v!r} is labelled {layer.metrics[v].get('label')!r}, "
                            f"expected {value_label!r}")
    assert not problems, "capture finish line (contract 'Goal'):\n" + "\n".join(problems)


@needs_sample
def test_sample_pbix_coverage(sample):
    _coverage(sample[0].spec, SAMPLE_PRESENTATION, allow={"Association"})


@needs_sample
def test_sample_edited_tmdl_coverage():
    spec = build_spec(read_tmdl(SAMPLE_TMDL), "pilot", "sales_returns")
    assert "External Net Sales" in {m.get("label") for m in spec["metrics"].values()}
    _coverage(spec, SAMPLE_PRESENTATION, allow={"Association"})


def test_plains_coverage():
    _coverage(build_spec(read_tmdl(PLAINS_TMDL), "pilot", "mee"), PLAINS_PRESENTATION, allow=set())


def _compiles_everything(layer: BILayer, wh: Warehouse):
    """'captured' means it answers: each visible captured metric compiles and runs with what needs() says."""
    problems = []
    for k in layer.captured():
        try:
            need = layer.needs(k)
        except AttributeError:
            pytest.fail("contract: BILayer.needs(key) -> {'by': [...], 'params': {...}} is missing")
        params = {p: (d.get("default") if d.get("default") is not None else d.get("min"))
                  for p, d in (need.get("params") or {}).items()}
        try:
            wh.query(layer.compile([k], need.get("by") or [], None, params=params))
        except Exception as e:  # noqa: BLE001 - every failure is reported
            problems.append(f"  {k}: by={need.get('by')} params={params}: {type(e).__name__}: {e}")
    assert not problems, "captured metrics that do not answer:\n" + "\n".join(problems)


@needs_sample
def test_sample_every_captured_metric_answers(sample):
    _compiles_everything(*sample[:2])


def test_plains_every_captured_metric_answers(plains):
    _compiles_everything(*plains)


# ------------------------------------------------------------------ 2. numbers, the sample model

@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """The product's own path (`ictx pbi import`): .pbix -> TMDL -> capture.yaml + the DuckDB stand-in.
    Returns (layer, warehouse, oracle): oracle is a separate copy of the stand-in with views under the
    model's own table and column names, for the expectations."""
    if not SAMPLE_PBIX.exists():
        pytest.skip(f"sample .pbix not here: {GET_IT}")
    out = tmp_path_factory.mktemp("sales_returns")
    import_pbix(SAMPLE_PBIX, out, catalog="pilot")
    layer = BILayer.load(out)
    wh = Warehouse("local", str(out / "pilot.duckdb"))
    copy = out / "oracle.duckdb"
    shutil.copy(out / "pilot.duckdb", copy)
    con = duckdb.connect(str(copy))
    for t in ("Sales", "Store", "Product", "Calendar"):
        spec = layer.tables[t]
        schema, name = spec["table"].split(".")[1:]
        cols = ", ".join(f'"{v["column"]}" AS "{c}"' for c, v in spec["columns"].items())
        con.execute(f'CREATE VIEW o_{t.lower()} AS SELECT {cols} FROM "{schema}"."{name}"')
    con.execute("""CREATE VIEW o_fact AS SELECT s.*, st.Store, st.Type, p.Product, p.Category, p.Segment,
                   strftime(s.Date, '%Y-%m') AS month FROM o_sales s JOIN o_store st USING (StoreID)
                   JOIN o_product p USING (ProductID)""")
    yield layer, wh, con
    con.close()


def q(con, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


NS = "sum(Amount) FILTER (WHERE Status = 'Sold')"  # Net Sales = CALCULATE(SUM(Sales[Amount]), Sales[Status]="Sold")


@needs_sample
def test_store_top_n_and_top_3_by_store(sample):
    layer, wh, con = sample
    # Store Top N = RANKX(ALL(Store[Store]), [Net Sales], , DESC): each store's rank among all stores by
    # Net Sales, largest first, ties skip (this data has no ties, so Skip vs Dense is not distinguished).
    ns = dict(q(con, f"SELECT Store, {NS} FROM o_fact GROUP BY 1"))
    ranked = sorted(ns, key=lambda s: -ns[s])
    rank = {s: 1 + sum(1 for o in ns.values() if o > ns[s]) for s in ns}
    assert ranked[:3] == ["Contoso", "Aliqui", "Fama"]  # sanity on the oracle itself
    got = run(layer, wh, [key(layer, "Store Top N"), key(layer, "Store Sales Top 3"),
                          key(layer, "Store Sales Other")], by=["Store[Store]"])
    # Palma has no sales: in DAX its Store Top N is 14 (BLANK ranks last). Whether a row with no fact rows
    # appears at all is up to the key set, so it is checked only if present (exact_rows=False below).
    if ("Palma",) in got:
        assert same(got[("Palma",)][key(layer, "Store Top N")], 14), "Palma (no sales): BLANK ranks last -> 14"
        assert same(got[("Palma",)][key(layer, "Store Sales Top 3")], 0), "IF(14 < 4, ., 0) = 0"
        # DAX: IF(14 > 3, [Net Sales], 0) returns BLANK; the contract's {key} renders coalesce(.., 0) -> 0.
        assert got[("Palma",)][key(layer, "Store Sales Other")] in (0, None)
        del got[("Palma",)]
    check(got, key(layer, "Store Top N"), {(s,): rank[s] for s in ns},
          "RANKX(ALL(Store[Store]), [Net Sales],,DESC): rank among stores, 1 = largest Net Sales")
    # IF([Store Top N] < 4, [Net Sales], 0) and IF([Store Top N] > 3, [Net Sales], 0)
    check(got, key(layer, "Store Sales Top 3"), {(s,): ns[s] if rank[s] < 4 else 0 for s in ns},
          "Net Sales for the three top-ranked stores, 0 for the rest")
    check(got, key(layer, "Store Sales Other"), {(s,): ns[s] if rank[s] > 3 else 0 for s in ns},
          "Net Sales for stores ranked 4th and below, 0 for the top three")


@needs_sample
def test_store_top_n_keeps_the_other_filters(sample):
    layer, wh, con = sample
    # ALL(Store[Store]) removes the filter on Store[Store] only; a filter on Store[Type] stays, so the
    # three Internal stores with sales rank 1..3 among themselves (Contoso, Fama, Victoria), all "top 3".
    ns = dict(q(con, f"SELECT Store, {NS} FROM o_fact WHERE Type = 'Internal' GROUP BY 1"))
    assert sorted(ns, key=lambda s: -ns[s]) == ["Contoso", "Fama", "Victoria"]
    got = run(layer, wh, [key(layer, "Store Top N"), key(layer, "Store Sales Other")], by=["Store[Store]"],
              where={"Store[Type]": "Internal"})
    got.pop(("Palma",), None)  # Internal, no sales; see the previous test
    check(got, key(layer, "Store Top N"), {("Contoso",): 1, ("Fama",): 2, ("Victoria",): 3},
          "ranked among the stores the Store[Type] filter leaves (ALL removes Store[Store] only)")
    check(got, key(layer, "Store Sales Other"), {(s,): 0 for s in ns}, "all three are in the top 3")


@needs_sample
def test_product_top_n_switches_on_what_the_question_groups_by(sample):
    layer, wh, con = sample
    top, top3, other = key(layer, "Product Top N"), key(layer, "Product Sales Top 3"), key(layer, "Product Sales Other")
    # by Product[Category]: neither ISINSCOPE(Product[Product]) nor ISINSCOPE(Product[Segment]) -> the
    # else branch, RANKX(ALL(Product[Category]), [Net Sales]): Office 365 1, Power Platform 2, XBOX 3.
    ns = dict(q(con, f"SELECT Category, {NS} FROM o_fact GROUP BY 1"))
    got = run(layer, wh, [top, top3, other], by=["Product[Category]"])
    check(got, top, {("Office 365",): 1, ("Power Platform",): 2, ("XBOX",): 3},
          "SWITCH(TRUE(), ISINSCOPE(...)...) falls to RANKX over categories when grouped by Category")
    check(got, top3, {(c,): ns[c] for c in ns}, "three categories, all ranked < 4 -> their Net Sales")
    check(got, other, {(c,): 0 for c in ns}, "no category ranks > 3")

    # by Product[Product]: ISINSCOPE(Product[Product]) -> RANKX(ALL(Product[Product]), [Net Sales]).
    ns = dict(q(con, f"SELECT Product, {NS} FROM o_fact GROUP BY 1"))
    rank = {p: 1 + sum(1 for o in ns.values() if o > ns[p]) for p in ns}
    got = run(layer, wh, [top, top3], by=["Product[Product]"])
    check(got, top, {(p,): rank[p] for p in ns}, "rank of each of the 24 products by Net Sales")
    check(got, top3, {(p,): ns[p] if rank[p] < 4 else 0 for p in ns},
          "Net Sales for Power BI, XBOX, Word (ranks 1-3), 0 for the rest")

    # by Category and Product: Product is in scope -> product rank; ALL(Product[Product]) keeps the
    # Category cell's filter, so products rank within their category (contract §5: partition by other dims).
    rows = q(con, f"SELECT Category, Product, rank() OVER (PARTITION BY Category ORDER BY {NS} DESC) "
                  "FROM o_fact GROUP BY 1, 2")
    got = run(layer, wh, [top], by=["Product[Category]", "Product[Product]"])
    check(got, top, {(c, p): r for c, p, r in rows},
          "ISINSCOPE(Product[Product]) is true; RANKX(ALL(Product[Product])) ranks within the Category cell")


@needs_sample
def test_product_top_n_needs_a_product_grouping(sample):
    layer, _, _ = sample
    # INTERPRETATION (contract §5/§6): with no grouping, the else branch ranks over Product[Category],
    # which is not in `by`, so the compiler refuses. (DAX at a grand total would show 1: the total is
    # ranked among categories with the Category filter removed, and is >= every category.)
    with pytest.raises(LayerError, match=r"Product\[(Category|Product|Segment)\]"):
        layer.compile([key(layer, "Product Top N")], [])
    with pytest.raises(LayerError, match=r"Store\[Store\]"):
        layer.compile([key(layer, "Store Top N")], [])
    assert "Store[Store]" in layer.needs(key(layer, "Store Top N"))["by"]


def _round_half_away(x: float) -> float:
    return math.floor(x + 0.5) if x >= 0 else -math.floor(-x + 0.5)


def _return_rate(returned, total) -> float:
    # Return Rate = DIVIDE(ROUND(DIVIDE([Returns], SUM(Sales[Amount]), 0) * 100, 0), 100, 0)
    # SUM(Sales[Amount]) is unfiltered by status; ROUND rounds half away from zero.
    return _round_half_away((returned / total if total else 0) * 100) / 100


@needs_sample
def test_total_return_rate_by_month_is_the_year_rate(sample):
    layer, wh, con = sample
    rows = q(con, "SELECT month, sum(Amount) FILTER (WHERE Status = 'Returned'), sum(Amount) FROM o_fact GROUP BY 1")
    yr, = q(con, "SELECT sum(Amount) FILTER (WHERE Status = 'Returned'), sum(Amount) FROM o_fact")
    year_rate = _return_rate(*yr)
    assert year_rate == 0.27  # 464007 / 1712020 = 27.1% -> 27 -> 0.27
    got = run(layer, wh, [key(layer, "Return Rate"), key(layer, "Total Return Rate")], by=["Calendar[Date]:month"])
    check(got, key(layer, "Return Rate"), {(m,): _return_rate(r, a) for m, r, a in rows},
          "each month's own rate (0.30, 0.23, 0.31, 0.41, 0.37, 0.12)")
    # CALCULATE([Return Rate], ALL('Calendar'[Date].[Month])): the Month level of the date hierarchy is
    # removed, Year stays, so every month cell shows 2019's rate. The rate is recomputed at year level
    # (ratio then ROUND), not averaged over months.
    check(got, key(layer, "Total Return Rate"), {(m,): year_rate for m, _, _ in rows},
          "ALL(Calendar[Date].[Month]) evaluates each month at its year: 0.27 in every month")
    # contract §1: at day or week the month-level removal has no meaning -> LayerError saying how to ask
    with pytest.raises(LayerError):
        layer.compile([key(layer, "Total Return Rate")], ["Calendar[Date]:day"])


@needs_sample
def test_last_2_months_net_sales_by_month(sample):
    layer, wh, con = sample
    ns = dict(q(con, f"SELECT month, {NS} FROM o_fact GROUP BY 1"))
    months = sorted(ns)
    # [Net Sales] + [Net Sales PM]; PREVIOUSMONTH of January 2019 is December 2018, outside the Calendar
    # table -> BLANK, and BLANK + x = x. No July row: the date table ends 2019-06-30.
    want = {(m,): ns[m] + (ns[months[i - 1]] if i else 0) for i, m in enumerate(months)}
    got = run(layer, wh, [key(layer, "Last 2 Months Net Sales")], by=["Calendar[Date]:month"])
    check(got, key(layer, "Last 2 Months Net Sales"), want, "this month's Net Sales plus last month's")


@needs_sample
def test_wif_sales_by_month(sample):
    layer, wh, con = sample
    total, = q(con, f"SELECT {NS} FROM o_fact")[0]
    got = run(layer, wh, [key(layer, "WIF Sales")], by=["Calendar[Date]:month"])
    check(got, key(layer, "WIF Sales"), {(m,): total for m in [f"2019-0{i}" for i in range(1, 7)]},
          "CALCULATE([Net Sales], ALL(Calendar[Date].[Month])): 2019's Net Sales (1,248,013) in every month")


# ---- what-if: '% Return Rate' = GENERATESERIES(0, 60, 1); [% Return Rate Value] = SELECTEDVALUE(...)/100

@needs_sample
def test_what_if_parameter_is_declared_and_required(sample):
    layer, wh, _ = sample
    p = param_key(layer, "% Return Rate")
    spec = layer.spec["parameters"][p]
    assert (spec["min"], spec["max"], spec["step"]) == (0, 60, 1), f"GENERATESERIES(0, 60, 1), got {spec}"
    assert spec.get("default") is None, "SELECTEDVALUE has no alternate result -> no default"
    value = key(layer, "% Return Rate Value")
    got = run(layer, wh, [value], params={p: 10})
    check(got, value, {(): 0.10}, "SELECTEDVALUE('% Return Rate'[% Return Rate]) / 100 at 10 -> 0.10")
    assert p in layer.needs(key(layer, "WIF Adjusted Units Returned"))["params"]
    with pytest.raises(LayerError, match="Return Rate") as e:
        layer.compile([key(layer, "WIF Adjusted Units Returned")], [])
    assert "60" in str(e.value), f"the error should give the range 0..60: {e.value}"


def _cells(con, group: str, where: str = "TRUE") -> dict:
    """Per cell: NS, returned amount, all amount, units sold, units returned, all units."""
    sel = f"{group}, " if group else ""
    rows = q(con, f"""SELECT {sel}{NS}, sum(Amount) FILTER (WHERE Status = 'Returned'), sum(Amount),
                     sum(Unit) FILTER (WHERE Status = 'Sold'), sum(Unit) FILTER (WHERE Status = 'Returned'),
                     sum(Unit) FROM o_fact WHERE {where} {'GROUP BY 1' if group else ''}""")
    return {tuple(r[:1]) if group else (): r[1:] if group else r for r in rows}


@needs_sample
@pytest.mark.parametrize("pct", [10, 25])
def test_wif_units_returned_by_month(sample, pct):
    layer, wh, con = sample
    p = pct / 100
    cells, (_, _, _, _, ur_all, u_all) = _cells(con, "month"), _cells(con, "")[()]
    n_dates, = q(con, "SELECT count(DISTINCT Date) FROM o_calendar")[0]
    assert n_dates == 27
    # WIF Units Returned_1 = CALCULATE([Units Returned], ALLSELECTED('Calendar'[Date])), _2 the same over
    # SUM(Sales[Unit]) * p. INTERPRETATION: a question grouped by Calendar[Date]:month is a visual grouped
    # on Calendar[Date], so ALLSELECTED(Calendar[Date]) drops the month grouping (no `level`), keeping
    # `where` -> both are whole-period numbers in every month cell.
    diff = ur_all - u_all * p
    # Average = DIVIDE(Difference, CALCULATE(DISTINCTCOUNT(Calendar[Date]), ALL('Calendar')), 0): 27 dates
    avg = diff / n_dates
    # WIF Units Returned = IF(UR - avg < 0, 0, UR - avg), UR in the month's own context
    k_avg, k_wur = key(layer, "WIF Units Returned Average"), key(layer, "WIF Units Returned")
    pk = param_key(layer, "% Return Rate")
    got = run(layer, wh, [key(layer, "WIF Adjusted Units Returned"), k_avg, k_wur],
              by=["Calendar[Date]:month"], params={pk: pct})
    check(got, key(layer, "WIF Adjusted Units Returned"), {m: c[5] * p for m, c in cells.items()},
          f"DIVIDE(SUM(Sales[Unit]) * {p}, 1, 0): the month's units (all statuses) times the what-if rate")
    check(got, k_avg, {m: avg for m in cells},
          f"(whole-period units returned {ur_all} - {u_all} x {p}) / 27 dates, the same in every month")
    check(got, k_wur, {m: max(0.0, c[4] - avg) for m, c in cells.items()},
          "the month's units returned less the average, floored at 0 by IF(..<0, 0, ..)")
    # and the grand total: UR - (UR - U*p)/27
    got = run(layer, wh, [k_wur], params={pk: pct})
    check(got, k_wur, {(): max(0.0, ur_all - avg)}, "at the total, ALLSELECTED changes nothing")


@needs_sample
def test_wif_average_with_a_store_filter(sample):
    layer, wh, con = sample
    # ALLSELECTED('Calendar'[Date]) keeps the Store[Type] slicer; ALL('Calendar') only touches Calendar
    # (and a Store filter never reaches Calendar: Sales -> Calendar is many-to-one), so the 27 dates stay.
    # UNDERSPECIFIED in the contract: a leaf on a table the `where` cannot reach (DISTINCTCOUNT over
    # Calendar under a Store filter). DAX: the filter has no effect on it.
    _, _, _, _, ur, u = _cells(con, "", "Type = 'Internal'")[()]
    pk = param_key(layer, "% Return Rate")
    got = run(layer, wh, [key(layer, "WIF Units Returned Average")], where={"Store[Type]": "Internal"},
              params={pk: 10})
    check(got, key(layer, "WIF Units Returned Average"), {(): (ur - u * 0.10) / 27},
          "Internal stores' (units returned - units x 0.10) over the 27 calendar dates; a Store filter "
          "does not reach Calendar, so DISTINCTCOUNT(Calendar[Date]) stays 27 (DAX ignores it, not an error)")
    # The same by store (the natural visual): each store's own numbers over the same 27 dates.
    per_store = _cells(con, "Store")
    got = run(layer, wh, [key(layer, "WIF Units Returned Average")], by=["Store[Store]"], params={pk: 10})
    got.pop(("Palma",), None)
    check(got, key(layer, "WIF Units Returned Average"),
          {s: (c[4] - c[5] * 0.10) / 27 for s, c in per_store.items()},
          "by Store: the store grouping applies to the Sales parts and cannot reach Calendar -> / 27")


def _profit(cell, p: float, rate_cell) -> int:
    """WIF Profit in one cell, from the DAX:
    WIF Same  = IF(p >= CALCULATE([Return Rate], ALL(Store[Type]), ALL(Store[Store])), 0, 1)
    Adjusted  = SUM(Unit) * PPU - DIVIDE(SUM(Unit) * p, 1, 0) * PPU, PPU = DIVIDE([Net Sales], [Units Sold], 0)
    WIF Profit = IF(Same = 0, 0, IF(ROUNDDOWN(Adjusted - NS, 0) < 0, 0, ROUNDDOWN(Adjusted - NS, 0)))
    ROUNDDOWN(x, 0) truncates toward zero."""
    ns, _, _, us, _, u = cell
    ppu = ns / us if us else 0
    adjusted = u * ppu - u * p * ppu
    if p >= _return_rate(rate_cell[1], rate_cell[2]):
        return 0
    return max(0, math.trunc(adjusted - ns))


@needs_sample
@pytest.mark.parametrize("pct", [10, 25, 30])
def test_wif_profit_and_forecast(sample, pct):
    layer, wh, con = sample
    p = pct / 100
    months, total = _cells(con, "month"), _cells(con, "")[()]
    pk = param_key(layer, "% Return Rate")
    prof, fc, tfc = key(layer, "WIF Profit"), key(layer, "WIF Forecast"), key(layer, "WIF Total Forecast")
    # By month (no store grouping, so ALL(Store[...]) changes nothing): Same compares p with the month's
    # rate. At 30, January's rate is exactly 0.30 (ROUND(30.2) / 100) so p >= rate -> Same = 0 -> profit 0
    # although Adjusted - NS is +2,833: the gate, not the floor, zeroes it. March (0.31) passes: 99.
    # At 25, February (0.23) and June (0.12) are gated. At 10 every month passes.
    want_p = {m: _profit(c, p, c) for m, c in months.items()}
    want_t = _profit(total, p, total)
    got = run(layer, wh, [prof, fc, tfc], by=["Calendar[Date]:month"], params={pk: pct})
    check(got, prof, want_p, f"WIF Profit per month at {pct}%: {want_p}")
    check(got, fc, {m: c[0] + want_p[m] for m, c in months.items()}, "WIF Forecast = [Net Sales] + [WIF Profit]")
    # WIF Total Forecast = CALCULATE([WIF Forecast], ALL(Calendar[Date].[Month])): the whole formula at the
    # year (IF and ROUNDDOWN on year totals), not the sum of monthly forecasts.
    check(got, tfc, {m: total[0] + want_t for m in months},
          f"each month shows the year's forecast {total[0] + want_t} (profit {want_t} computed at year level)")
    got = run(layer, wh, [prof], params={pk: pct})
    check(got, prof, {(): want_t}, f"WIF Profit at the total, {pct}%: 288,999 at 10, 32,830 at 25, 0 at 30")


# ------------------------------------------------------------------ 3. numbers, the Plains model

# Plains has no data of its own. Its Metrics table has only Year as a dimension; to exercise "the VAR
# is the visual's cell" the fixture adds a Segment column to Metrics (a line of business, as a real
# payer extract has). The measures' DAX is unchanged. No 2023 rows at all.
#
#   Segment     Year  Members  PaidPMPM  AvgAge  RiskScore  AdmitsK
PLAINS_ROWS = [
    ("Commercial", 2021, 1000, 300.0, 35.0, 1.00, 50.0),
    ("Commercial", 2022, 1100, 320.0, 36.0, 1.10, 55.0),
    ("Commercial", 2024, 1200, 350.0, 37.0, 1.20, 60.0),
    ("Commercial", 2025, 1300, 400.0, 38.0, 1.30, 66.0),   # Commercial's latest year: 2025
    ("Medicare", 2021, 500, 900.0, 70.0, 2.00, 200.0),
    ("Medicare", 2022, 520, 950.0, 71.0, 2.10, 210.0),
    ("Medicare", 2024, 540, 1000.0, 72.0, 2.20, 220.0),    # Medicare's latest: 2024 (2023 missing)
    ("Individual", 2021, 200, 250.0, 40.0, 0.90, 40.0),
    ("Individual", 2022, 210, 260.0, 41.0, 0.95, 42.0),    # Individual's latest: 2022
]
SEG = "Metrics[Segment]"
YEAR = "Metrics[Year]"


@pytest.fixture(scope="module")
def plains(tmp_path_factory):
    model = read_tmdl(PLAINS_TMDL)
    model.tables["Metrics"].columns.append(Column("Segment", "string", "Segment"))
    layer = BILayer(build_spec(model, "pilot", "mee"))
    folder = tmp_path_factory.mktemp("mee")
    con = duckdb.connect(str(folder / "pilot.duckdb"))
    try:
        t = layer.tables["Metrics"]  # capture.yaml `tables:` -> pilot.mee.metrics, column -> warehouse column
        schema, name = t["table"].split(".")[1:]
        cols = {c: v["column"] for c, v in t["columns"].items()}
        given = ["Segment", "Year", "Members", "PaidPMPM", "AvgAge", "RiskScore", "AdmitsK"]
        con.execute(f'CREATE SCHEMA "{schema}"')
        kind = {"Segment": "VARCHAR", "Year": "BIGINT"}
        defs = ", ".join(f'"{cols[c]}" {kind.get(c, "DOUBLE")}' for c in cols)
        con.execute(f'CREATE TABLE "{schema}"."{name}" ({defs})')
        for r in PLAINS_ROWS:
            row = dict(zip(given, r))
            vals = [row.get(c, 0) for c in cols]
            con.execute(f'INSERT INTO "{schema}"."{name}" VALUES ({", ".join("?" * len(vals))})', vals)
    finally:
        con.close()
    return layer, Warehouse("local", str(folder / "pilot.duckdb"))


def _value_of(layer, label):
    v = layer.metrics[key(layer, label)].get("value")
    assert v, f"{label!r} should name its number in value: (contract §7)"
    return v


def test_paid_pmpm_reads_the_latest_year_of_the_cell(plains):
    layer, wh = plains
    k = key(layer, "Paid PMPM")
    # VAR _y = MAX(Metrics[Year]) RETURN CALCULATE(SUM(Metrics[PaidPMPM]), ALLSELECTED(Metrics), Metrics[Year] = _y)
    # INTERPRETATION (contract §1): with no visual, ALLSELECTED keeps the question's `where` (as slicers)
    # and has no grouping to drop. By none: _y = 2025 -> only Commercial has 2025 -> 400.
    check(run(layer, wh, [k]), k, {(): 400}, "no grouping: latest year 2025, summed over every segment")
    # By Segment: _y is the segment's own latest year, but ALLSELECTED(Metrics) drops the Segment
    # grouping, so the sum is over ALL segments in that year:
    #   Commercial _y=2025 -> 400; Medicare _y=2024 -> 350 + 1000; Individual _y=2022 -> 320 + 950 + 260.
    check(run(layer, wh, [k], by=[SEG]), k,
          {("Commercial",): 400, ("Medicare",): 1350, ("Individual",): 1530},
          "VAR in the segment's cell; ALLSELECTED removes the segment grouping before Year = _y")
    # By Year: each year's total across segments (2023 has no rows, so no row).
    check(run(layer, wh, [k], by=[YEAR]), k, {(2021,): 1450, (2022,): 1530, (2024,): 1350, (2025,): 400},
          "by year: ALLSELECTED drops the Year grouping, Year = _y puts it back -> year totals")
    # where Segment = Medicare: _y = 2024 (in the filtered cell), and ALLSELECTED keeps the filter -> 1000.
    check(run(layer, wh, [k], where={SEG: "Medicare"}), k, {(): 1000},
          "a where filter is a slicer: kept by ALLSELECTED, so Medicare 2024 only")
    # where Segment in (Medicare, Individual), by Segment: grouping dropped, filter kept.
    #   Medicare _y=2024 -> Medicare 2024 = 1000 (Commercial filtered out); Individual _y=2022 -> 950 + 260.
    check(run(layer, wh, [k], by=[SEG], where={SEG: ["Medicare", "Individual"]}), k,
          {("Medicare",): 1000, ("Individual",): 1210},
          "ALLSELECTED: the where filter stays, the grouping goes")
    # where Year = 2022: _y = 2022. (DAX: Metrics[Year] = _y replaces the Year filter; the contract ANDs
    # it. Same answer here because _y is inside the filter.)
    check(run(layer, wh, [k], where={YEAR: 2022}), k, {(): 1530}, "_y = 2022, all segments")


def test_avg_age_is_a_sum_like_the_dax_says(plains):
    layer, wh = plains
    k = key(layer, "Avg Age")
    # Same shape as Paid PMPM over SUM(Metrics[AvgAge]). The model assumes one row per year, so with
    # segments DAX adds ages up: Medicare's cell is 37 + 72 = 109. The test holds the DAX, not intuition.
    check(run(layer, wh, [k]), k, {(): 38}, "latest year 2025: Commercial's 38")
    check(run(layer, wh, [k], by=[SEG]), k, {("Commercial",): 38, ("Medicare",): 109, ("Individual",): 148},
          "SUM(AvgAge) over every segment in the cell's latest year")
    check(run(layer, wh, [k], where={SEG: "Individual"}), k, {(): 41}, "Individual's 2022 only")


def test_members_yoy_value(plains):
    layer, wh = plains
    v = _value_of(layer, "Members YoY")
    # _c = CALCULATE(SUM(Members), ALL(Metrics), Year = _y), _p the same at _y - 1, value _d = DIVIDE(_c - _p, _p).
    # ALL removes groupings AND filters; only _y comes from the cell. DIVIDE by a BLANK _p is BLANK (the
    # text shows "--"), so the value is NULL.
    tot = {2021: 1700, 2022: 1830, 2024: 1740, 2025: 1300}
    d = lambda y: (tot[y] - tot[y - 1]) / tot[y - 1] if y - 1 in tot else None  # noqa: E731
    assert d(2025) == pytest.approx(-0.2528735632) and d(2022) == pytest.approx(0.0764705882)
    check(run(layer, wh, [v]), v, {(): d(2025)}, "(1300 - 1740) / 1740: 2025 vs 2024, all segments")
    check(run(layer, wh, [v], by=[SEG]), v,
          {("Commercial",): d(2025), ("Medicare",): None, ("Individual",): d(2022)},
          "Commercial _y=2025 -> -25.3%; Medicare _y=2024 and no 2023 rows -> BLANK; Individual _y=2022 -> "
          "(1830 - 1700)/1700 = +7.6% over ALL segments")
    check(run(layer, wh, [v], by=[YEAR]), v,
          {(2021,): None, (2022,): d(2022), (2024,): None, (2025,): d(2025)},
          "by year: 2021 and 2024 have no prior year rows -> BLANK")
    # where Segment = Individual: _y = 2022 from the filtered cell, then ALL drops the filter -> +7.6%
    # (keeping the filter would give (210 - 200) / 200 = +5%).
    check(run(layer, wh, [v], where={SEG: "Individual"}), v, {(): d(2022)},
          "ALL(Metrics) ignores the where filter; only _y comes from it")


def test_2021_baseline_and_5yr_change(plains):
    layer, wh = plains
    base, chg = key(layer, "2021 Baseline"), key(layer, "5Yr Change")
    # 2021 Baseline = CALCULATE(SUM(RiskScore), ALL(Metrics), Year = 2021): 1.00 + 2.00 + 0.90 = 3.90
    # everywhere (a fixed value is an ordinary filter; ALL drops groupings and filters).
    # 5Yr Change = (CALCULATE(SUM(RiskScore), ALLSELECTED(Metrics), Year = _y)) - 3.90
    check(run(layer, wh, [base, chg]), base, {(): 3.90}, "all segments' 2021 risk score")
    check(run(layer, wh, [base, chg]), chg, {(): 1.30 - 3.90}, "2025 (Commercial 1.30) - 3.90")
    got = run(layer, wh, [base, chg], by=[SEG])
    check(got, base, {(s,): 3.90 for s in ("Commercial", "Medicare", "Individual")},
          "ALL: the same baseline in every segment")
    check(got, chg, {("Commercial",): 1.30 - 3.90, ("Medicare",): (1.20 + 2.20) - 3.90,
                     ("Individual",): (1.10 + 2.10 + 0.95) - 3.90},
          "_c in the segment's latest year over all segments (ALLSELECTED), minus 3.90")
    got = run(layer, wh, [base, chg], where={SEG: "Medicare"})
    check(got, base, {(): 3.90}, "ALL ignores the Medicare filter")
    check(got, chg, {(): 2.20 - 3.90}, "ALLSELECTED keeps it: Medicare 2024 2.20 - 3.90")


def test_chip_value(plains):
    layer, wh = plains
    v = _value_of(layer, "Chip Admits K")
    # Chip Admits K: _d = DIVIDE(_c - _p, _p) on SUM(AdmitsK) with ALL(Metrics), shown as ABS with ▲/▼ -> value _d.
    #   by none: _y = 2025: (66 - (60 + 220)) / 280; Individual: _y = 2022: (307 - 290) / 290.
    check(run(layer, wh, [v]), v, {(): (66 - 280) / 280}, "2025 vs 2024 admits/K, all segments")
    check(run(layer, wh, [v], by=[SEG]), v,
          {("Commercial",): (66 - 280) / 280, ("Medicare",): None, ("Individual",): (307 - 290) / 290},
          "the chip's number per segment cell: Medicare's prior year 2023 has no rows -> BLANK")
