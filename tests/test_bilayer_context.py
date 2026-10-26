"""The compiler's DAX filter-context shapes: ALL / ALLSELECTED / ALLEXCEPT (`context`), "the latest period of
the cell" (`latest`), conditionals and ISBLANK in formulas, what-if parameters, RANKX (`rank`) and ISINSCOPE
switches (`by_scope`), plus `needs()`. Specs are written by hand; every expected number is worked out from
the rows below with DAX semantics."""

import duckdb
import pytest
import sqlglot

from insurance_context.bilayer import BILayer, LayerError, Warehouse

# Sales (StoreID, ProductID, Date, Amount, Status). Sold: 1120 in all.
SALES = [(1, 1, "2025-01-10", 100, "Sold"), (1, 2, "2025-02-10", 50, "Sold"), (2, 1, "2025-02-15", 200, "Sold"),
         (2, 2, "2026-01-05", 300, "Sold"), (3, 1, "2026-03-01", 400, "Sold"), (3, 2, "2026-03-20", 25, "Returned"),
         (1, 1, "2026-02-01", 70, "Sold")]
STORES = [(1, "North", "East"), (2, "South", "East"), (3, "West", "West")]
PRODUCTS = [(1, "Bikes"), (2, "Helmets")]
# MEE-style metrics table (Region, Year, PaidPMPM)
METRICS = [("East", 2020, 10), ("East", 2021, 20), ("West", 2020, 5), ("West", 2021, 7), ("North", 2020, 3)]

SOLD = ["({Sales[Status]} = 'Sold')"]


def _net(**extra):
    return {"status": "captured", "table": "Sales", "agg": "sum", "expr": "{Sales[Amount]}", "filter": SOLD, **extra}


def _cols(*names):
    return {"columns": {n: {"column": n} for n in names}}


SPEC = {
    "model": "Shop",
    "tables": {
        "Sales": {"table": "pilot.shop.sales", **_cols("StoreID", "ProductID", "Date", "Amount", "Status")},
        "Store": {"table": "pilot.shop.store", **_cols("StoreID", "Store", "Region")},
        "Product": {"table": "pilot.shop.product", **_cols("ProductID", "Category")},
        "Calendar": {"table": "pilot.shop.calendar", **_cols("Date")},
        "Metrics": {"table": "pilot.shop.metrics", **_cols("Region", "Year", "PaidPMPM")},
    },
    "joins": [{"from": "Sales[StoreID]", "to": "Store[StoreID]", "many_to_one": True},
              {"from": "Sales[ProductID]", "to": "Product[ProductID]", "many_to_one": True},
              {"from": "Sales[Date]", "to": "Calendar[Date]", "many_to_one": True}],
    "parameters": {
        "return_rate": {"label": "% Return Rate", "table": "% Return Rate", "column": "% Return Rate",
                        "min": 0, "max": 0.5, "step": 0.05, "default": None},
        "uplift": {"label": "Uplift", "table": "Uplift", "column": "Uplift", "min": 0, "max": 100, "step": 10,
                   "default": 20},
    },
    "metrics": {
        "net_sales": {"label": "Net Sales", **_net()},
        "net_sales_pm": {"label": "Net Sales PM",
                         **_net(time={"date": "Calendar[Date]", "shift": -1, "unit": "month", "grain": "month"})},
        # ALL(Store) / ALLSELECTED(Store) / ALL(Sales) / ALLEXCEPT(Store, Store[Region]) / ALL(Store[Store])
        "all_store_sales": {"label": "All Store Sales", **_net(context=[{"remove": "Store", "keep": "none"}])},
        "selected_store_sales": {"label": "Selected Store Sales",
                                 **_net(context=[{"remove": "Store", "keep": "filters"}])},
        "all_sales": {"label": "All Sales", **_net(context=[{"remove": "Sales", "keep": "none"}])},
        "region_sales": {"label": "Region Sales",
                         **_net(context=[{"remove": "Store", "keep": "none", "except": ["Store[Region]"]}])},
        "any_store_sales": {"label": "Any Store Sales", **_net(context=[{"remove": "Store[Store]", "keep": "none"}])},
        "year_sales": {"label": "Year Sales",
                       **_net(context=[{"remove": "Calendar[Date]", "keep": "none", "level": "month"}])},
        "share": {"label": "Share", "status": "captured",
                  "formula": "coalesce({net_sales} / nullif({all_store_sales}, 0), 0)"},
        # VAR _y = MAX(Metrics[Year]) RETURN CALCULATE(SUM(Metrics[PaidPMPM]), ALLSELECTED(Metrics), Metrics[Year] = _y)
        "paid_pmpm": {"label": "Paid PMPM", "status": "captured", "table": "Metrics", "agg": "sum",
                      "expr": "{Metrics[PaidPMPM]}", "context": [{"remove": "Metrics", "keep": "filters"}],
                      "latest": {"column": "Metrics[Year]", "agg": "max", "offset": 0}},
        "paid_prior": {"label": "Paid Prior Year", "status": "captured", "table": "Metrics", "agg": "sum",
                       "expr": "{Metrics[PaidPMPM]}", "latest": {"column": "Metrics[Year]", "agg": "max", "offset": -1}},
        "paid_first": {"label": "Paid First Year", "status": "captured", "table": "Metrics", "agg": "sum",
                       "expr": "{Metrics[PaidPMPM]}", "latest": {"column": "Metrics[Year]", "agg": "min", "offset": 0}},
        # RANKX(ALL(Store[Store]), [Net Sales]) and friends
        "store_rank": {"label": "Store Rank", "status": "captured",
                       "rank": {"over": "Store[Store]", "of": "net_sales", "order": "desc", "ties": "skip"}},
        "big": {"label": "Big", "status": "captured", "hidden": True, "of": "big_rank",
                "formula": "CASE WHEN {net_sales} >= 400 THEN 1 ELSE 0 END"},
        "big_rank": {"label": "Big Rank", "status": "captured",
                     "rank": {"over": "Store[Store]", "of": "big", "order": "desc", "ties": "skip"}},
        "big_dense": {"label": "Big Dense", "status": "captured",
                      "rank": {"over": "Store[Store]", "of": "big", "order": "desc", "ties": "dense"}},
        "top2_sales": {"label": "Top 2 Sales", "status": "captured",
                       "formula": "CASE WHEN {store_rank} <= 2 THEN {net_sales} ELSE NULL END"},
        "rank_of_rank": {"label": "Rank of Rank", "status": "captured",
                         "rank": {"over": "Store[Store]", "of": "store_rank"}},
        # IF(ISBLANK([Net Sales]), -1, [Net Sales])
        "blank_flag": {"label": "Blank Flag", "status": "captured",
                       "formula": "CASE WHEN ({?net_sales} IS NULL) THEN -1 ELSE {net_sales} END"},
        "raw_share": {"label": "Raw Share", "status": "captured", "formula": "{?share}"},
        # SWITCH(TRUE(), ISINSCOPE(Product[Category]), [Net Sales], ISINSCOPE(Store[Store]), [All Store Sales], [All Sales])
        "scoped": {"label": "Scoped", "status": "captured",
                   "by_scope": [{"column": "Product[Category]", "metric": "net_sales"},
                                {"column": "Store[Store]", "metric": "all_store_sales"}, {"else": "all_sales"}]},
        "scoped_strict": {"label": "Scoped Strict", "status": "captured",
                          "by_scope": [{"column": "Product[Category]", "metric": "net_sales"},
                                       {"column": "Store[Store]", "metric": "store_rank"}]},
        # what-if parameters
        "after_returns": {"label": "After Returns", "status": "captured",
                          "formula": "{net_sales} * (1 - {$return_rate})"},
        "uplifted": {"label": "Uplifted", "status": "captured",
                     "formula": "{net_sales} * (1 + {$uplift} / 100.0)"},
        "rate_value": {"label": "% Return Rate Value", "status": "captured", "formula": "{$return_rate}"},
        "kept_amount": {"label": "Kept Amount", **_net(expr="{Sales[Amount]} * (1 - {$return_rate})")},
        "mixed": {"label": "Mixed", "status": "captured",
                  "formula": "{net_sales_pm} + {store_rank} + {after_returns}"},
        # CALCULATE(DISTINCTCOUNT('Calendar'[Date]), ALL('Calendar')): Calendar reaches no other table
        "days": {"label": "Days", "status": "captured", "table": "Calendar", "agg": "count_distinct",
                 "expr": "{Calendar[Date]}", "context": [{"remove": "Calendar", "keep": "none"}]},
        "bad_time": {"label": "Bad Time", **_net(time={"date": "Calendar[Date]", "shift": -1, "unit": "month",
                                                       "grain": "month"},
                                                 context=[{"remove": "Calendar", "keep": "none"}])},
    },
}


@pytest.fixture
def shop(tmp_path):
    path = str(tmp_path / "pilot.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA shop")
    rows = ", ".join(f"({s}, {p}, DATE '{d}', {a}, '{st}')" for s, p, d, a, st in SALES)
    con.execute(f"CREATE TABLE shop.sales AS SELECT * FROM (VALUES {rows}) t(StoreID, ProductID, Date, Amount, Status)")
    con.execute("CREATE TABLE shop.store AS SELECT * FROM (VALUES "
                + ", ".join(f"({i}, '{s}', '{r}')" for i, s, r in STORES) + ") t(StoreID, Store, Region)")
    con.execute("CREATE TABLE shop.product AS SELECT * FROM (VALUES "
                + ", ".join(f"({i}, '{c}')" for i, c in PRODUCTS) + ") t(ProductID, Category)")
    con.execute("CREATE TABLE shop.calendar AS SELECT CAST(range AS DATE) AS Date "
                "FROM range(DATE '2025-01-01', DATE '2026-04-01', INTERVAL 1 DAY)")
    con.execute("CREATE TABLE shop.metrics AS SELECT * FROM (VALUES "
                + ", ".join(f"('{r}', {y}, {v})" for r, y, v in METRICS) + ") t(Region, Year, PaidPMPM)")
    con.close()
    return BILayer(SPEC), Warehouse("local", path)


def ask(shop, metrics, by=None, where=None, **kw):
    layer, wh = shop
    sql = layer.compile(metrics, by=by, where=where, **kw)
    sqlglot.parse_one(layer.compile(metrics, by=by, where=where, dialect="databricks", **kw), read="databricks")
    return wh.query(sql)[1]


# ------------------------------------------------------------------ context

def test_all_removes_the_grouping_and_the_filters_on_a_table(shop):
    assert ask(shop, ["net_sales", "all_store_sales", "share"], ["Store[Store]"]) == [
        ("North", 220, 1120, 220 / 1120), ("South", 500, 1120, 500 / 1120), ("West", 400, 1120, 400 / 1120)]
    # ALL ignores the region filter; ALLSELECTED keeps it (720 = East's two stores)
    assert ask(shop, ["net_sales", "all_store_sales", "selected_store_sales"], ["Store[Store]"],
               {"Store[Region]": "East"}) == [("North", 220, 1120, 720), ("South", 500, 1120, 720)]
    assert ask(shop, ["all_store_sales", "selected_store_sales"], [], {"Store[Region]": "East"}) == [(1120, 720)]


def test_removal_keeps_the_other_groupings_and_adds_the_cells_it_fills(shop):
    # per category across every store: Bikes 100+200+400+70, Helmets 50+300. West sold no helmets (one was
    # returned), yet ALL(Store) still has a number there, so the row appears, as it does in Power BI.
    assert ask(shop, ["net_sales", "all_store_sales"], ["Store[Store]", "Product[Category]"]) == [
        ("North", "Bikes", 170, 770), ("North", "Helmets", 50, 350), ("South", "Bikes", 200, 770),
        ("South", "Helmets", 300, 350), ("West", "Bikes", 400, 770), ("West", "Helmets", 0, 350)]


def test_a_table_removal_covers_what_it_reaches(shop):
    # ALL(Sales) removes Store, Product and Calendar too (the expanded table)
    assert ask(shop, ["net_sales", "all_sales"], ["Product[Category]"], {"Store[Region]": "East"}) == [
        ("Bikes", 370, 1120), ("Helmets", 350, 1120)]
    assert ask(shop, ["all_sales"], ["Calendar[Date]:year"]) == [(2025, 1120), (2026, 1120)]


def test_allexcept_and_a_column_removal(shop):
    by = ["Store[Region]", "Store[Store]"]
    assert ask(shop, ["net_sales", "region_sales", "any_store_sales"], by) == [
        ("East", "North", 220, 720, 720), ("East", "South", 500, 720, 720), ("West", "West", 400, 400, 400)]
    # grouped by store alone there is no region in the question, so both are the grand total
    assert ask(shop, ["region_sales", "any_store_sales"], ["Store[Store]"]) == [
        ("North", 1120, 1120), ("South", 1120, 1120), ("West", 1120, 1120)]
    assert ask(shop, ["region_sales"], ["Store[Store]"], {"Store[Region]": "West"}) == [("West", 400)]


def test_month_level_removal_answers_at_year(shop):
    assert ask(shop, ["net_sales", "year_sales"], ["Calendar[Date]:month"]) == [
        ("2025-01", 100, 350), ("2025-02", 250, 350), ("2026-01", 300, 770), ("2026-02", 70, 770),
        ("2026-03", 400, 770)]
    assert ask(shop, ["net_sales", "year_sales"], ["Calendar[Date]:year"]) == [(2025, 350, 350), (2026, 770, 770)]
    assert ask(shop, ["year_sales"], ["Calendar[Date]:quarter"]) == [("2025-Q1", 350), ("2026-Q1", 770)]
    assert ask(shop, ["net_sales", "year_sales"], [], {"Calendar[Date]:month": "2026-02"}) == [(70, 770)]
    # by store the month slicer is read as its year: every store's 2026 sales, so every store has a row
    assert ask(shop, ["net_sales", "year_sales"], ["Store[Store]"], {"Calendar[Date]:month": "2026-02"}) == [
        ("North", 70, 70), ("South", 0, 300), ("West", 0, 400)]
    with pytest.raises(LayerError, match=r"group by Calendar\[Date\]:year or Calendar\[Date\]:quarter or "
                                         r"Calendar\[Date\]:month"):
        shop[0].compile(["year_sales"], by=["Calendar[Date]:day"])


def test_context_with_time_intelligence(shop):
    assert ask(shop, ["net_sales_pm", "all_store_sales"], ["Calendar[Date]:month"]) == [
        ("2025-01", 0, 100), ("2025-02", 100, 250), ("2025-03", 250, 0), ("2026-01", 0, 300),
        ("2026-02", 300, 70), ("2026-03", 70, 400)]
    with pytest.raises(LayerError, match="not supported"):
        shop[0].compile(["bad_time"], by=["Calendar[Date]:month"])


def test_row_level_security_still_narrows_a_removed_table(shop):
    rls = {"Store": "{Store[Region]} = 'West'"}
    assert ask(shop, ["net_sales", "all_store_sales"], ["Store[Store]"], row_filters=rls) == [("West", 400, 400)]
    assert ask(shop, ["all_sales"], [], row_filters=rls) == [(400,)]


def test_a_grouping_a_metric_does_not_reach_leaves_it_alone(shop):
    # the calendar holds 2025-01-01 .. 2026-03-31: 455 days. A store filter or grouping does not reach it,
    # so the value repeats per store (DAX: a filter that does not reach a table has no effect on it)
    assert ask(shop, ["net_sales", "days"], ["Store[Store]"]) == [("North", 220, 455), ("South", 500, 455),
                                                                  ("West", 400, 455)]
    assert ask(shop, ["net_sales", "days"], [], {"Store[Region]": "East"}) == [(720, 455)]
    assert ask(shop, ["net_sales", "paid_pmpm"], ["Store[Region]"]) == [("East", 720, 27), ("West", 400, 27)]
    # when no metric asked for reaches the column, the question is a mistake: refused
    for by, where in [(["Store[Store]"], None), ([], {"Store[Region]": "East"})]:
        with pytest.raises(LayerError, match=r"Store is not related to Calendar.*group or filter by a column"):
            shop[0].compile(["days"], by=by, where=where)
    with pytest.raises(LayerError, match=r"no column Store\[Nope\]"):
        shop[0].compile(["net_sales", "days"], by=["Store[Nope]"])


# ------------------------------------------------------------------ latest

def test_latest_with_allselected_is_the_cells_latest_year_over_every_selected_row(shop):
    assert ask(shop, ["paid_pmpm"]) == [(27,)]  # 2021: East 20 + West 7
    # each region's own latest year, then every region in that year (North's latest is 2020)
    assert ask(shop, ["paid_pmpm"], ["Metrics[Region]"]) == [("East", 27), ("North", 18), ("West", 27)]
    # ALLSELECTED keeps the slicer: only East and North
    assert ask(shop, ["paid_pmpm"], ["Metrics[Region]"], {"Metrics[Region]": ["East", "North"]}) == [
        ("East", 20), ("North", 13)]
    assert ask(shop, ["paid_pmpm"], ["Metrics[Year]"]) == [(2020, 18), (2021, 27)]


def test_latest_without_context_and_with_an_offset(shop):
    assert ask(shop, ["paid_prior"], ["Metrics[Region]"]) == [("East", 10), ("North", 0), ("West", 5)]
    # the year filter picks the cell's year; the metric then reads the year before (Year = _y - 1 replaces it)
    assert ask(shop, ["paid_prior"], [], {"Metrics[Year]": 2021}) == [(18,)]
    assert ask(shop, ["paid_prior"], ["Metrics[Region]"], {"Metrics[Year]": 2021}) == [("East", 10), ("West", 5)]
    assert ask(shop, ["paid_first"], ["Metrics[Region]"]) == [("East", 10), ("North", 3), ("West", 5)]


def test_latest_under_row_level_security(shop):
    rls = {"Metrics": "{Metrics[Region]} <> 'East'"}
    assert ask(shop, ["paid_pmpm"], row_filters=rls) == [(7,)]  # latest visible year 2021: West only
    assert ask(shop, ["paid_pmpm"], ["Metrics[Region]"], row_filters=rls) == [("North", 8), ("West", 7)]


# ------------------------------------------------------------------ formulas, rank, scope

def test_raw_value_is_null_when_the_metric_has_no_rows(shop):
    assert ask(shop, ["blank_flag", "all_store_sales"], ["Store[Store]", "Product[Category]"]) == [
        ("North", "Bikes", 170, 770), ("North", "Helmets", 50, 350), ("South", "Bikes", 200, 770),
        ("South", "Helmets", 300, 350), ("West", "Bikes", 400, 770), ("West", "Helmets", -1, 350)]
    assert ask(shop, ["raw_share", "all_store_sales"], ["Store[Store]", "Product[Category]"])[-1] == (
        "West", "Helmets", 0, 350)  # share reads all_store_sales too, which has a value, so it is not blank


def test_rank_over_a_column_partitions_by_the_rest(shop):
    assert ask(shop, ["net_sales", "store_rank"], ["Store[Store]"]) == [
        ("North", 220, 3), ("South", 500, 1), ("West", 400, 2)]
    assert ask(shop, ["net_sales", "store_rank"], ["Product[Category]", "Store[Store]"]) == [
        ("Bikes", "North", 170, 3), ("Bikes", "South", 200, 2), ("Bikes", "West", 400, 1),
        ("Helmets", "North", 50, 2), ("Helmets", "South", 300, 1)]
    # ties: big is 0, 1, 1 -> skip ranks 3, 1, 1; dense 2, 1, 1
    assert ask(shop, ["big_rank", "big_dense"], ["Store[Store]"]) == [("North", 3, 2), ("South", 1, 1),
                                                                     ("West", 1, 1)]


def test_rank_in_a_case_and_with_top(shop):
    assert ask(shop, ["top2_sales"], ["Store[Store]"]) == [("North", None), ("South", 500), ("West", 400)]
    assert ask(shop, ["net_sales", "store_rank"], ["Store[Store]"], top=2) == [("South", 500, 1), ("West", 400, 2)]
    # ranked before the limit: the lowest-ranked store keeps its rank of 3
    assert ask(shop, ["store_rank", "net_sales"], ["Store[Store]"], top=1, order_by="store_rank", ascending=False) == [
        ("North", 3, 220)]


def test_rank_needs_its_column_in_the_question(shop):
    layer = shop[0]
    with pytest.raises(LayerError, match=r"group by Store\[Store\]"):
        layer.compile(["store_rank"], by=["Product[Category]"])
    with pytest.raises(LayerError, match="itself a rank"):
        layer.compile(["rank_of_rank"], by=["Store[Store]"])


def test_scope_switch_follows_the_grouping(shop):
    assert ask(shop, ["scoped"], ["Product[Category]"]) == [("Bikes", 770), ("Helmets", 350)]
    assert ask(shop, ["scoped"], ["Store[Store]"]) == [("North", 1120), ("South", 1120), ("West", 1120)]
    assert ask(shop, ["scoped"], ["Store[Store]", "Product[Category]"])[0] == ("North", "Bikes", 170)
    assert ask(shop, ["scoped"]) == [(1120,)]
    assert ask(shop, ["scoped_strict"], ["Store[Store]"]) == [("North", 3), ("South", 1), ("West", 2)]
    with pytest.raises(LayerError, match=r"group by Product\[Category\] or Store\[Store\]"):
        shop[0].compile(["scoped_strict"])


# ------------------------------------------------------------------ parameters

def test_what_if_parameters(shop):
    assert ask(shop, ["after_returns"], params={"return_rate": 0.1}) == [(pytest.approx(1008),)]
    assert ask(shop, ["after_returns"], ["Store[Store]"], params={"return_rate": "0.5"}) == [
        ("North", 110), ("South", 250), ("West", 200)]
    assert ask(shop, ["uplifted"]) == [(pytest.approx(1344),)]  # default 20
    assert ask(shop, ["uplifted"], params={"uplift": 50}) == [(pytest.approx(1680),)]
    assert ask(shop, ["rate_value"], params={"return_rate": 0.25}) == [(pytest.approx(0.25),)]
    assert ask(shop, ["kept_amount"], params={"return_rate": 0.5}) == [(pytest.approx(560),)]
    layer = shop[0]
    with pytest.raises(LayerError, match=r"After Returns reads the what-if parameter return_rate \(% Return Rate\)"
                                         r".*between 0 and 0.5"):
        layer.compile(["after_returns"])
    with pytest.raises(LayerError, match="between 0 and 0.5"):
        layer.compile(["kept_amount"])
    with pytest.raises(LayerError, match="no what-if parameter 'nope'"):
        layer.compile(["net_sales"], params={"nope": 1})
    with pytest.raises(LayerError, match="must be a number"):
        layer.compile(["after_returns"], params={"return_rate": "lots"})
    with pytest.raises(LayerError, match="only what-if parameters"):
        layer.compile(["rate_value"], by=["Store[Store]"], params={"return_rate": 0.1})


# ------------------------------------------------------------------ needs, leaves, dialect

def test_needs_names_what_the_question_must_include(shop):
    layer = shop[0]
    rate = SPEC["parameters"]["return_rate"]
    assert layer.needs("net_sales") == {"by": [], "params": {}}
    assert layer.needs("store_rank") == {"by": ["Store[Store]"], "params": {}}
    assert layer.needs("top2_sales") == {"by": ["Store[Store]"], "params": {}}
    assert layer.needs("after_returns") == {"by": [], "params": {"return_rate": rate}}
    assert layer.needs("kept_amount") == {"by": [], "params": {"return_rate": rate}}
    assert layer.needs("mixed") == {"by": ["Calendar[Date]:month", "Store[Store]"], "params": {"return_rate": rate}}
    assert layer.needs("scoped") == {"by": [], "params": {}}  # has an else
    assert layer.needs("scoped_strict") == {"by": ["Product[Category]"], "params": {}}
    assert layer.time_by("mixed") == ["Calendar[Date]:month"]
    # needs() is enough to compile
    for key in ("mixed", "scoped_strict", "store_rank"):
        n = layer.needs(key)
        layer.compile([key], by=n["by"], params={p: 0.1 for p in n["params"]})


def test_leaves_follow_the_new_references(shop):
    layer = shop[0]
    assert layer.leaves("top2_sales") == ["net_sales"]
    assert layer.leaves("blank_flag") == ["net_sales"]
    assert layer.leaves("raw_share") == ["net_sales", "all_store_sales"]
    assert layer.leaves("scoped") == ["net_sales", "all_store_sales", "all_sales"]
    assert layer.leaves("scoped", by=["Store[Store]"]) == ["all_store_sales"]
    assert layer.leaves("rate_value") == []


def test_every_shape_transpiles_to_databricks(shop):
    layer = shop[0]
    for metrics, by, where in [
            (["share", "selected_store_sales", "region_sales"], ["Store[Region]", "Store[Store]"], {"Store[Region]": "East"}),
            (["year_sales", "net_sales_pm"], ["Calendar[Date]:month"], None),
            (["paid_pmpm", "paid_prior"], ["Metrics[Region]"], None),
            (["paid_pmpm"], [], None),
            (["top2_sales", "big_dense", "blank_flag"], ["Product[Category]", "Store[Store]"], None)]:
        sql = layer.compile(metrics, by=by, where=where, dialect="databricks", top=5,
                            row_filters={"Store": "{Store[Region]} <> 'Nowhere'"})
        assert '"' not in sql
        sqlglot.parse_one(sql, read="databricks")
