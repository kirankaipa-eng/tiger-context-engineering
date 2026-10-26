"""Time intelligence: DAX period functions captured as a metric's period, answered in SQL at the question's grain."""

from datetime import date

import duckdb
import pytest
import sqlglot

from insurance_context import daxcapture
from insurance_context.bilayer import BILayer, LayerError, Warehouse, _renames, build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table

CAL = "'Calendar'[Date]"
MEASURES = {
    "Net Sales": 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")',
    "Net Sales PM": f"CALCULATE([Net Sales], PREVIOUSMONTH({CAL}))",
    "Net Sales NM": f"CALCULATE([Net Sales], NEXTMONTH({CAL}))",
    "Net Sales LY": f"CALCULATE([Net Sales], SAMEPERIODLASTYEAR({CAL}))",
    "Net Sales PQ": f"CALCULATE([Net Sales], DATEADD({CAL}, -1, QUARTER))",
    "Net Sales PP": f"CALCULATE([Net Sales], PARALLELPERIOD({CAL}, -12, MONTH))",
    "Net Sales YTD": f"TOTALYTD([Net Sales], {CAL})",
    "Net Sales YTD 2": f"CALCULATE([Net Sales], DATESYTD({CAL}))",
    "Net Sales QTD": f"CALCULATE([Net Sales], DATESQTD({CAL}))",
    "Net Sales MTD": f"TOTALMTD([Net Sales], {CAL})",
    "Big YTD": f"TOTALYTD([Net Sales], {CAL}, Sales[Amount] >= 1000)",
    "Variance": "[Net Sales] - [Net Sales PM]",
    "Variance %": "DIVIDE([Net Sales], [Net Sales PM], 0) - 1",
    "Fiscal YTD": f'TOTALYTD([Net Sales], {CAL}, "6/30")',
    "Fiscal Dates": f'CALCULATE([Net Sales], DATESYTD({CAL}, "6/30"))',
    "LY PM": f"CALCULATE([Net Sales LY], PREVIOUSMONTH({CAL}))",
    "Two Periods": f"CALCULATE([Net Sales], PREVIOUSMONTH({CAL}), SAMEPERIODLASTYEAR({CAL}))",
    "Not A Date": "CALCULATE([Net Sales], PREVIOUSMONTH(Sales[Status]))",
    "Last 3 Months": f"CALCULATE([Net Sales], DATESINPERIOD({CAL}, MAX({CAL}), -3, MONTH))",
}
ROWS = [("2025-01-10", 100), ("2025-02-15", 200), ("2025-03-20", 300), ("2025-11-05", 50), ("2025-12-10", 400),
        ("2025-12-31", 25), ("2026-01-15", 1000), ("2026-02-01", 2000), ("2026-03-31", 3000)]


@pytest.fixture
def model() -> Model:
    sales = Table("Sales", [Column("Status"), Column("Amount", "int64", "Amount"), Column("Date", "dateTime", "Date"),
                            Column("Shipped", "dateTime", "Shipped")],
                  [Measure(n, e) for n, e in MEASURES.items()], "let x = 1 in x", "m")
    cal = Table("Calendar", [Column("Date", "dateTime", "Date"), Column("Month", "string", "Month")],
                source="let x = 1 in x", source_kind="m")
    return Model("Shop", {t.name: t for t in (sales, cal)}, [Relationship("Sales", "Date", "Calendar", "Date")])


@pytest.fixture
def shop(model, tmp_path):
    """Sales across fourteen months of two years; the calendar runs 2025-01-01 .. 2026-03-31."""
    con = duckdb.connect(str(tmp_path / "pilot.duckdb"))
    con.execute("CREATE SCHEMA shop")
    values = ", ".join(f"('Sold', {a}, DATE '{d}')" for d, a in ROWS) + ", ('Returned', 999, DATE '2026-02-10')"
    con.execute(f"CREATE TABLE shop.sales AS SELECT *, Date + 2 AS Shipped FROM (VALUES {values}) t(Status, Amount, Date)")
    con.execute("CREATE TABLE shop.calendar AS SELECT CAST(range AS DATE) AS Date, strftime(range, '%B') AS Month "
                "FROM range(DATE '2025-01-01', DATE '2026-04-01', INTERVAL 1 DAY)")
    con.close()
    return BILayer(build_spec(model, "pilot", "shop")), Warehouse("local", str(tmp_path / "pilot.duckdb"))


def ask(shop, metrics, by, where=None):
    layer, wh = shop
    return wh.query(layer.compile(metrics, by=by, where=where))[1]


def test_period_functions_are_captured_as_a_metric_period(model):
    out = daxcapture.capture(model)
    date = "Calendar[Date]"
    assert out["net_sales_pm"] == {"label": "Net Sales PM", "status": "captured", "dax": MEASURES["Net Sales PM"],
                                   "table": "Sales", "agg": "sum", "expr": "{Sales[Amount]}",
                                   "filter": ["({Sales[Status]} = 'Sold')"],
                                   "time": {"date": date, "shift": -1, "unit": "month", "grain": "month"}}
    assert out["net_sales_nm"]["time"] == {"date": date, "shift": 1, "unit": "month", "grain": "month"}
    assert out["net_sales_ly"]["time"] == {"date": date, "shift": -1, "unit": "year", "grain": None}
    assert out["net_sales_pq"]["time"] == {"date": date, "shift": -1, "unit": "quarter", "grain": None}
    assert out["net_sales_pp"]["time"] == {"date": date, "shift": -12, "unit": "month", "grain": "month"}
    assert out["net_sales_ytd"]["time"] == out["net_sales_ytd_2"]["time"] == {"date": date, "to_date": "year"}
    assert out["net_sales_qtd"]["time"] == {"date": date, "to_date": "quarter"}
    assert out["net_sales_mtd"]["time"] == {"date": date, "to_date": "month"}
    assert out["big_ytd"]["filter"] == ["({Sales[Status]} = 'Sold')", "({Sales[Amount]} >= 1000)"]
    assert out["variance"]["formula"] == "({net_sales} - {net_sales_pm})"
    assert out["variance_pct"]["formula"] == "(coalesce(({net_sales} / nullif({net_sales_pm}, 0)), 0) - 1)"
    for key, why in [("fiscal_ytd", "fiscal year end"), ("fiscal_dates", "fiscal year end"),
                     ("ly_pm", "already reads one"), ("two_periods", "two period functions"),
                     ("not_a_date", "not a date column"), ("last_3_months", "DATESINPERIOD")]:
        assert out[key]["status"] == "not_captured" and why in out[key]["why"], (key, out[key])


def test_previous_month_shifts_rows_and_stays_inside_the_calendar(shop):
    assert ask(shop, ["net_sales", "net_sales_pm"], ["Calendar[Date]:month"]) == [
        ("2025-01", 100, 0), ("2025-02", 200, 100), ("2025-03", 300, 200), ("2025-04", 0, 300),
        ("2025-11", 50, 0), ("2025-12", 425, 50), ("2026-01", 1000, 425), ("2026-02", 2000, 1000),
        ("2026-03", 3000, 2000)]  # March 2026's sales would land in April, after the calendar's last date
    assert ask(shop, ["net_sales_nm"], ["Calendar[Date]:month"])[:2] == [("2025-01", 200), ("2025-02", 300)]
    # a year filter on a previous-month metric still reads December of the year before
    assert ask(shop, ["net_sales_pm"], ["Calendar[Date]:month"], {"Calendar[Date]:year": 2026}) == [
        ("2026-01", 425), ("2026-02", 1000), ("2026-03", 2000)]
    assert ask(shop, ["net_sales_pm"], [], {"Calendar[Date]:month": "2026-01"}) == [(425,)]


def test_same_period_last_year_and_dateadd_at_any_grain(shop):
    assert ask(shop, ["net_sales_ly"], ["Calendar[Date]:month"]) == [("2026-01", 100), ("2026-02", 200),
                                                                     ("2026-03", 300)]
    # 2026 holds January to March in the calendar, so last year's same period is January to March 2025
    assert ask(shop, ["net_sales", "net_sales_ly"], ["Calendar[Date]:year"]) == [(2025, 1075, 0), (2026, 6000, 600)]
    assert ask(shop, ["net_sales_pq"], ["Calendar[Date]:quarter"]) == [("2025-Q2", 600), ("2026-Q1", 475)]


def test_running_totals_to_date(shop):
    assert ask(shop, ["net_sales_ytd"], ["Calendar[Date]:month"]) == [
        ("2025-01", 100), ("2025-02", 300), ("2025-03", 600), ("2025-04", 600), ("2025-05", 600), ("2025-06", 600),
        ("2025-07", 600), ("2025-08", 600), ("2025-09", 600), ("2025-10", 600), ("2025-11", 650), ("2025-12", 1075),
        ("2026-01", 1000), ("2026-02", 3000), ("2026-03", 6000)]
    assert ask(shop, ["net_sales_ytd"], ["Calendar[Date]:quarter"]) == [
        ("2025-Q1", 600), ("2025-Q2", 600), ("2025-Q3", 600), ("2025-Q4", 1075), ("2026-Q1", 6000)]
    assert ask(shop, ["net_sales_ytd", "net_sales"], ["Calendar[Date]:month"], {"Calendar[Date]:year": 2026}) == [
        ("2026-01", 1000, 1000), ("2026-02", 3000, 2000), ("2026-03", 6000, 3000)]
    assert ask(shop, ["net_sales_qtd"], ["Calendar[Date]:month"]) == [
        ("2025-01", 100), ("2025-02", 300), ("2025-03", 600), ("2025-11", 50), ("2025-12", 475),
        ("2026-01", 1000), ("2026-02", 3000), ("2026-03", 6000)]
    days = ask(shop, ["net_sales_mtd"], ["Calendar[Date]:day"], {"Calendar[Date]:month": "2025-12"})
    assert len(days) == 22 and days[0] == (date(2025, 12, 10), 400) and days[-1] == (date(2025, 12, 31), 425)
    assert ask(shop, ["big_ytd"], ["Calendar[Date]:year"]) == [(2026, 6000)]


def test_variance_over_a_period_metric(shop):
    rows = ask(shop, ["variance", "variance_pct"], ["Calendar[Date]:month"], {"Calendar[Date]:year": 2026})
    assert rows == [("2026-01", 575, pytest.approx(1000 / 425 - 1)), ("2026-02", 1000, 1.0), ("2026-03", 1000, 0.5)]


def test_period_questions_that_cannot_be_answered_say_how_to_ask(shop):
    layer, _ = shop
    with pytest.raises(LayerError, match=r"Net Sales PM compares with the previous month: group or filter by "
                                         r"Calendar\[Date\]:month"):
        layer.compile(["net_sales_pm"])
    with pytest.raises(LayerError, match=r"Net Sales PM compares with the previous month: group by Calendar\[Date\]:month"):
        layer.compile(["variance"], by=["Calendar[Date]:year"])
    with pytest.raises(LayerError, match=r"group by Calendar\[Date\]:month"):
        layer.compile(["net_sales_pm"], by=["Calendar[Date]:day"])
    with pytest.raises(LayerError, match=r"not by Calendar\[Month\]"):
        layer.compile(["net_sales_ly"], by=["Calendar[Month]"])
    with pytest.raises(LayerError, match="with a grain, e.g."):
        layer.compile(["net_sales_pq"])
    for by in ([], ["Calendar[Date]:week"], ["Calendar[Date]:year", "Calendar[Date]:month"]):
        with pytest.raises(LayerError, match="running total"):
            layer.compile(["net_sales_qtd"], by=by)
    with pytest.raises(LayerError, match="running total"):
        layer.compile(["net_sales_qtd"], by=["Calendar[Date]:year"])
    assert layer.time_by("variance") == ["Calendar[Date]:month"] and layer.time_by("net_sales") == []
    assert layer.time_by("net_sales_ly") == ["Calendar[Date]:year"]


def test_the_date_relationships_join_column_is_the_periods_date(shop):
    """Sales[Date] -> Calendar[Date]: grouping or filtering a period metric by Sales[Date] reads it as
    Calendar[Date], shifted or run to date; another date column (Sales[Shipped]) stays as it is."""
    by_cal = ask(shop, ["net_sales", "net_sales_pm"], ["Calendar[Date]:month"])
    assert ask(shop, ["net_sales", "net_sales_pm"], ["Sales[Date]:month"]) == by_cal
    assert ask(shop, ["net_sales_pm"], ["Sales[Date]:month"], {"Sales[Date]:year": 2026}) == [
        ("2026-01", 425), ("2026-02", 1000), ("2026-03", 2000)]
    assert ask(shop, ["net_sales_ytd"], ["Sales[Date]:month"], {"Calendar[Date]:year": 2026}) == [
        ("2026-01", 1000), ("2026-02", 3000), ("2026-03", 6000)]
    assert ask(shop, ["net_sales", "net_sales_ly"], ["Sales[Date]:year"]) == [(2025, 1075, 0), (2026, 6000, 600)]
    # shipped 2026-02-03, sold 2026-02-01: its sales are March's previous month
    assert ask(shop, ["net_sales_pm"], ["Calendar[Date]:month"], {"Sales[Shipped]:month": "2026-02"}) == [
        ("2026-03", 2000)]
    with pytest.raises(LayerError, match=r"group or filter by Calendar\[Date\]:month"):
        shop[0].compile(["net_sales_pm"], by=["Sales[Shipped]:month"])


def test_period_sql_transpiles_to_databricks(shop):
    layer, _ = shop
    for metrics, by in ((["net_sales", "net_sales_pm", "net_sales_pq"], ["Calendar[Date]:month"]),
                        (["net_sales_ytd", "net_sales_mtd"], ["Calendar[Date]:day"])):
        sql = layer.compile(metrics, by=by, where={"Calendar[Date]:year": 2026}, dialect="databricks")
        sqlglot.parse_one(sql, read="databricks")
        assert '"' not in sql
    sql = layer.compile(["net_sales_pm", "net_sales_pq"], by=["Calendar[Date]:month"], dialect="databricks")
    assert "INTERVAL '1' MONTH" in sql and "INTERVAL '3' MONTH" in sql


def test_power_query_renames_map_model_columns_to_warehouse_columns(model):
    m = ('let Source = Sql.Database("x"),\n'
         '  #"Renamed" = Table.RenameColumns(Source,{{"Txn Date", "Date"}, {"Amt", "Paid"}}),\n'
         '  #"Renamed1" = Table.RenameColumns(#"Renamed", {"Paid", "Amount"}),\n'
         '  #"Added" = Table.AddColumn(#"Renamed1", "Mult", each [Amount] * 2, Int64.Type),\n'
         '  #"Expanded" = Table.ExpandTableColumn(#"Added", "P", {"Name"}, {"Name.1"}),\n'
         '  #"Renamed2" = Table.RenameColumns(#"Expanded",{{"Mult", "Double"}, {"Name.1", "Product"}})\nin #"Renamed2"')
    assert _renames(m) == {"Date": "Txn Date", "Amount": "Amt"}
    model.tables["Sales"].source = m
    cols = build_spec(model, "pilot", "shop")["tables"]["Sales"]["columns"]
    assert cols["Date"] == {"column": "Txn Date", "type": "dateTime", "renamed_from": "Txn Date"}
    assert cols["Amount"]["column"] == "Amt" and cols["Status"] == {"column": "Status", "type": "string"}
