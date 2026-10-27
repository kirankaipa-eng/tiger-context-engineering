"""DAX as cost-and-utilization models write it, end to end: DAX -> capture -> SQL -> rows. Each measure is
either captured and gives the number DAX gives (worked out by hand from the rows below), or is refused
with a reason; never a wrong number. Filters that compare rows with a computed value ({@key}): a running
total (FILTER(ALL(Dim_Date), Dim_Date[Date] <= MAX(Dim_Date[Date]))), claims above the cell's average
(VAR _avg = [Avg Paid] RETURN CALCULATE([Paid], Claims[Paid] > _avg)), a fixed threshold measure."""

import duckdb
import pytest
import sqlglot

from insurance_context.bilayer import BILayer, Warehouse, build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table

# Claims (ClaimID, ProviderID, ServiceDate, Paid): 1200 in all; by month Jan 400, Feb 200, Mar 450, Apr 150;
# Cardio 100 + 200 + 50 = 350 (avg 116.67), Ortho 300 + 400 + 150 = 850 (avg 283.33); overall avg 200.
CLAIMS = [(1, 1, "2025-01-10", 100), (2, 2, "2025-01-20", 300), (3, 1, "2025-02-05", 200),
          (4, 2, "2025-03-15", 400), (5, 1, "2025-03-20", 50), (6, 2, "2025-04-02", 150)]
PROVIDERS = [(1, "Cardio"), (2, "Ortho")]
MONTH = "Dim_Date[Date]:month"
SPECIALTY = "Provider[Specialty]"

MEASURES = {
    "Paid": "SUM(Claims[Paid])",
    "Claim Count": "DISTINCTCOUNT(Claims[ClaimID])",
    "Avg Paid": "DIVIDE([Paid], [Claim Count])",
    "Latest Date": "MAX(Dim_Date[Date])",
    # running totals, three ways
    "Running Paid": "CALCULATE([Paid], FILTER(ALL(Dim_Date), Dim_Date[Date] <= MAX(Dim_Date[Date])))",
    "Running Paid VAR": "VAR _m = MAX(Dim_Date[Date]) RETURN CALCULATE([Paid], ALL(Dim_Date), Dim_Date[Date] <= _m)",
    "Running Paid Measure": "VAR _m = [Latest Date] RETURN CALCULATE([Paid], FILTER(ALL(Dim_Date), "
                            "Dim_Date[Date] <= _m))",
    "Running Cardio": "CALCULATE([Running Paid], Provider[Specialty] = \"Cardio\")",
    # above the cell's own average, computed once
    "High Cost Paid": "VAR _avg = [Avg Paid] RETURN CALCULATE([Paid], Claims[Paid] > _avg)",
    # a threshold measure that ignores the visual (ALL of the fact table): the same value either way
    "Threshold": "CALCULATE(AVERAGE(Claims[Paid]), ALL(Claims))",
    "Above Threshold": "CALCULATE([Paid], Claims[Paid] > [Threshold])",
    "Since March": "CALCULATE([Paid], Claims[ServiceDate] >= DATE(2025, 3, 1))",
    "High Cost Share": "DIVIDE([High Cost Paid], [Paid])",
    # refused: DAX computes the measure again per row it tests (context transition)
    "High Cost Inline": "CALCULATE([Paid], FILTER(Claims, Claims[Paid] > [Avg Paid]))",
    "Up To Latest Inline": "CALCULATE([Paid], ALL(Dim_Date), Dim_Date[Date] <= [Latest Date])",
    # refused: a VAR computed before an iterator, read inside it
    "Share By Specialty": "VAR _t = [Paid] RETURN SUMX(VALUES(Provider[Specialty]), DIVIDE([Paid], _t))",
}


def model() -> Model:
    cols = lambda *cs: [Column(c, t, c) for c, t in cs]  # noqa: E731
    claims = Table("Claims", cols(("ClaimID", "int64"), ("ProviderID", "int64"), ("ServiceDate", "dateTime"),
                                  ("Paid", "double")), [Measure(n, e) for n, e in MEASURES.items()], "x", "m")
    provider = Table("Provider", cols(("ProviderID", "int64"), ("Specialty", "string")), source="x", source_kind="m")
    dates = Table("Dim_Date", cols(("Date", "dateTime")), source="x", source_kind="m")
    return Model("Claims", {t.name: t for t in (claims, provider, dates)}, [
        Relationship("Claims", "ProviderID", "Provider", "ProviderID"),
        Relationship("Claims", "ServiceDate", "Dim_Date", "Date")])


@pytest.fixture(scope="module")
def layer(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("corpus") / "pilot.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA shop")
    rows = ", ".join(f"({c}, {p}, DATE '{d}', {v})" for c, p, d, v in CLAIMS)
    con.execute(f"CREATE TABLE shop.claims AS SELECT * FROM (VALUES {rows}) t(ClaimID, ProviderID, ServiceDate, Paid)")
    con.execute("CREATE TABLE shop.provider AS SELECT * FROM (VALUES "
                + ", ".join(f"({i}, '{s}')" for i, s in PROVIDERS) + ") t(ProviderID, Specialty)")
    con.execute("CREATE TABLE shop.dim_date AS SELECT CAST(range AS DATE) AS Date "
                "FROM range(DATE '2025-01-01', DATE '2025-05-01', INTERVAL 1 DAY)")
    con.close()
    return BILayer(build_spec(model(), "pilot", "shop")), Warehouse("local", path)


def ask(layer, metrics, by=None, where=None):
    lay, wh = layer
    sqlglot.parse_one(lay.compile(metrics, by=by, where=where, dialect="databricks"), read="databricks")
    return [tuple(round(v, 2) if isinstance(v, float) else v for v in r)
            for r in wh.query(lay.compile(metrics, by=by, where=where))[1]]


def status(layer, key):
    return layer[0].metrics[key]["status"]


def test_running_totals_by_month(layer):
    assert ask(layer, ["paid", "running_paid", "running_paid_var", "running_paid_measure"], [MONTH]) == [
        ("2025-01", 400, 400, 400, 400), ("2025-02", 200, 600, 600, 600),
        ("2025-03", 450, 1050, 1050, 1050), ("2025-04", 150, 1200, 1200, 1200)]
    assert ask(layer, ["running_paid"]) == [(1200,)]  # no month: the latest date of all, so every claim
    # the latest date of the cell under a filter on the question: March only
    assert ask(layer, ["running_paid"], where={MONTH: "2025-03"}) == [(1050,)]


def test_a_filter_around_a_running_total_reaches_the_value_it_compares_with(layer):
    assert ask(layer, ["running_cardio"], [MONTH]) == [
        ("2025-01", 100), ("2025-02", 300), ("2025-03", 350), ("2025-04", 350)]


def test_rows_above_the_cells_own_average(layer):
    assert ask(layer, ["high_cost_paid", "high_cost_share"]) == [(700, 0.58)]  # 300 + 400 over 200
    assert ask(layer, ["avg_paid", "high_cost_paid"], [SPECIALTY]) == [("Cardio", 116.67, 200),
                                                                       ("Ortho", 283.33, 700)]


def test_a_measure_that_ignores_the_visual_is_the_same_value_inline(layer):
    assert status(layer, "above_threshold") == "captured"
    # Cardio has no claim over 200: no row, as Power BI leaves out a row whose only value is blank
    assert ask(layer, ["above_threshold"], [SPECIALTY]) == [("Ortho", 700)]
    assert ask(layer, ["paid", "above_threshold"], [SPECIALTY]) == [("Cardio", 350, 0), ("Ortho", 850, 700)]


def test_date_literals(layer):
    assert ask(layer, ["since_march"]) == [(600,)]


def test_per_row_measures_are_refused_with_the_reason(layer):
    for key in ("high_cost_inline", "up_to_latest_inline"):
        assert status(layer, key) == "not_captured"
        assert "context transition" in layer[0].metrics[key]["why"] and "VAR _v" in layer[0].metrics[key]["why"]
    assert status(layer, "share_by_specialty") == "not_captured"
    assert "VAR computed once" in layer[0].metrics["share_by_specialty"]["why"]
