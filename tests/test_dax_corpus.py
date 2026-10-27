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
    # what the question narrows to (ISFILTERED: grouped by or filtered on; ISINSCOPE: grouped by)
    "Paid When Specialty": "IF(ISFILTERED(Provider[Specialty]), [Paid], BLANK())",
    "Paid Unless Specialty": "IF(NOT ISFILTERED(Provider[Specialty]), [Paid], [Avg Paid])",
    "Paid When Provider": "IF(ISFILTERED(Provider), [Paid])",
    "Paid When Either": "IF(ISFILTERED(Provider[Specialty]) || ISFILTERED(Dim_Date[Date]), [Paid])",
    "Scoped Paid": "SWITCH(TRUE(), ISFILTERED(Dim_Date[Date]), [Running Paid], ISINSCOPE(Provider[Specialty]), [Avg Paid], "
                   "[Paid])",
    # date arithmetic: month ends and month shifts of the cell's latest date, today, per-row month ends
    "Month End": "EOMONTH(MAX(Dim_Date[Date]), 0)",
    "To Prior Month End": "VAR _e = EOMONTH(MAX(Dim_Date[Date]), -1) RETURN CALCULATE([Paid], ALL(Dim_Date), "
                          "Dim_Date[Date] <= _e)",
    "To Prior Month End Inline": "CALCULATE([Paid], FILTER(ALL(Dim_Date), Dim_Date[Date] <= "
                                 "EOMONTH(MAX(Dim_Date[Date]), -1)))",
    "Last 3 Months": "VAR _m = MAX(Dim_Date[Date]) RETURN CALCULATE([Paid], ALL(Dim_Date), "
                     "Dim_Date[Date] > EDATE(_m, -3), Dim_Date[Date] <= _m)",
    "Up To Today": "CALCULATE([Paid], Claims[ServiceDate] <= TODAY())",
    "March End Paid": "SUMX(FILTER(Claims, EOMONTH(Claims[ServiceDate], 0) = DATE(2025, 3, 31)), Claims[Paid])",
    "Months Back": "EOMONTH(MAX(Dim_Date[Date]), [Claim Count])",
    # a level of a hierarchy: grouped by month but not by specialty, by both, or neither
    "Level Paid": "SWITCH(TRUE(), ISINSCOPE(Dim_Date[Date]) && NOT ISINSCOPE(Provider[Specialty]), [Running Paid], "
                  "ISINSCOPE(Dim_Date[Date]) && ISINSCOPE(Provider[Specialty]), [Avg Paid], [Paid])",
    "Month Only Paid": "IF(ISINSCOPE(Dim_Date[Date]) && NOT ISINSCOPE(Provider[Specialty]), [Paid])",
    "Neither Paid": "SWITCH(TRUE(), NOT ISINSCOPE(Dim_Date[Date]) && NOT ISINSCOPE(Provider[Specialty]), [Paid])",
    # CALCULATE filters around a scope switch reach every branch, unless they touch what it tests
    "Specialty Over 100": "CALCULATE([Paid When Specialty], Claims[Paid] > 100)",
    "Scoped Over 100": "CALCULATE([Scoped Paid], Claims[Paid] > 100)",
    "Cardio When Specialty": "CALCULATE([Paid When Specialty], Provider[Specialty] = \"Cardio\")",
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


def test_scope_tests_joined_by_and(layer):
    from insurance_context.bilayer import LayerError
    assert ask(layer, ["level_paid"], [MONTH])[1] == ("2025-02", 600)  # running
    assert ask(layer, ["level_paid"], [MONTH, SPECIALTY])[0] == ("2025-01", "Cardio", 100)  # average
    assert ask(layer, ["level_paid"], [SPECIALTY]) == [("Cardio", 350), ("Ortho", 850)]  # else
    assert ask(layer, ["month_only_paid"], [MONTH])[0] == ("2025-01", 400)
    with pytest.raises(LayerError):
        ask(layer, ["month_only_paid"], [MONTH, SPECIALTY])
    assert "no test that must hold" in layer[0].metrics["neither_paid"]["why"]


def test_calculate_filters_around_a_scope_switch(layer):
    assert ask(layer, ["specialty_over_100"], [SPECIALTY]) == [("Cardio", 200), ("Ortho", 850)]
    assert ask(layer, ["scoped_over_100"], [MONTH]) == [("2025-01", 300), ("2025-02", 500), ("2025-03", 900),
                                                        ("2025-04", 1050)]
    assert ask(layer, ["scoped_over_100"], [SPECIALTY]) == [("Cardio", 200), ("Ortho", 283.33)]
    assert ask(layer, ["scoped_over_100"]) == [(1050,)]
    # a filter on the column ISFILTERED tests would make it true; refused rather than guessed
    assert "which its ISFILTERED / ISINSCOPE tests" in layer[0].metrics["cardio_when_specialty"]["why"]


def test_month_ends_and_month_shifts(layer):
    import datetime
    assert ask(layer, ["month_end"], [MONTH])[1] == ("2025-02", datetime.date(2025, 2, 28))
    # through the end of the month before the cell's latest date: all of Jan-Mar, or Jan-Feb for March
    assert ask(layer, ["to_prior_month_end", "to_prior_month_end_inline"]) == [(1050, 1050)]
    assert ask(layer, ["to_prior_month_end", "to_prior_month_end_inline"], where={MONTH: "2025-03"}) == [(600, 600)]
    assert ask(layer, ["last_3_months"]) == [(800,)]  # after 2025-01-30: Feb + Mar + Apr
    assert ask(layer, ["up_to_today"]) == [(1200,)]
    assert ask(layer, ["march_end_paid"]) == [(450,)]
    assert "whole number written in the measure" in layer[0].metrics["months_back"]["why"]


def test_isfiltered_picks_the_branch_from_what_the_question_narrows_to(layer):
    from insurance_context.bilayer import LayerError
    assert ask(layer, ["paid_when_specialty"], [SPECIALTY]) == [("Cardio", 350), ("Ortho", 850)]
    assert ask(layer, ["paid_when_specialty"], where={SPECIALTY: "Ortho"}) == [(850,)]  # filtered, not grouped
    with pytest.raises(LayerError, match="group by or filter on Provider\\[Specialty\\]"):
        ask(layer, ["paid_when_specialty"])
    assert ask(layer, ["paid_unless_specialty"]) == [(1200,)]
    assert ask(layer, ["paid_unless_specialty"], [SPECIALTY]) == [("Cardio", 116.67), ("Ortho", 283.33)]
    assert ask(layer, ["paid_when_provider"], where={SPECIALTY: "Cardio"}) == [(350,)]  # a table: any of its columns
    assert ask(layer, ["paid_when_either"], [MONTH])[0] == ("2025-01", 400)
    assert ask(layer, ["paid_when_either"], where={SPECIALTY: "Ortho"}) == [(850,)]
    # a date grouped at a grain filters the date; a grouping, not a filter, is what ISINSCOPE reads
    assert ask(layer, ["scoped_paid"], [MONTH])[-1] == ("2025-04", 1200)
    assert ask(layer, ["scoped_paid"], [SPECIALTY]) == [("Cardio", 116.67), ("Ortho", 283.33)]
    assert ask(layer, ["scoped_paid"], where={SPECIALTY: "Cardio"}) == [(350,)]
    assert ask(layer, ["scoped_paid"]) == [(1200,)]


def test_per_row_measures_are_refused_with_the_reason(layer):
    for key in ("high_cost_inline", "up_to_latest_inline"):
        assert status(layer, key) == "not_captured"
        assert "context transition" in layer[0].metrics[key]["why"] and "VAR _v" in layer[0].metrics[key]["why"]
    assert status(layer, "share_by_specialty") == "not_captured"
    assert "VAR computed once" in layer[0].metrics["share_by_specialty"]["why"]
