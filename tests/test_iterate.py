"""Iterators over measures (`iterate`): SUMX / AVERAGEX / MAXX over VALUES(T[C]) or a table, of an
expression that reads measures, and COUNTROWS(FILTER(VALUES(T[C]), <measure condition>)). The measure
is computed per value (DAX's context transition), then combined. Expected numbers are worked out from
test_bilayer_context's rows: sold per store North 220, South 500, West 400 (East = North + South)."""

import copy

import pytest
import sqlglot

from insurance_context.bilayer import BILayer, Warehouse

import test_bilayer_context as base
from test_bilayer_context import SPEC, shop  # noqa: F401 - the fixture
from test_daxcapture_patterns import cap

STORES = ["Store[Store]"]
METRICS = {
    "avg_store_sales": {"label": "Avg Store Sales", "status": "captured",
                        "iterate": {"over": STORES, "agg": "avg", "of": "net_sales"}},
    "best_store_sales": {"label": "Best Store Sales", "status": "captured",
                         "iterate": {"over": STORES, "agg": "max", "of": "net_sales"}},
    "big_store": {"label": "big", "status": "captured", "hidden": True, "of": "big_stores",
                  "formula": "CASE WHEN ({net_sales} > 300) THEN 1 ELSE 0 END"},
    "big_stores": {"label": "Big Stores", "status": "captured",
                   "iterate": {"over": STORES, "agg": "count", "of": "big_store", "having": "big_store"}},
    "big_share": {"label": "Big Share", "status": "captured", "formula": "({big_stores} / nullif(3, 0))"},
}


@pytest.fixture
def it(shop):  # noqa: F811
    _, wh = shop
    spec = copy.deepcopy(SPEC)
    spec["metrics"].update(METRICS)
    return BILayer(spec), wh


def ask(it, metrics, by=None, where=None):
    layer, wh = it
    sqlglot.parse_one(layer.compile(metrics, by=by, where=where, dialect="databricks"), read="databricks")
    return wh.query(layer.compile(metrics, by=by, where=where))[1]


def test_an_iterator_computes_per_value_then_combines(it):
    [(avg, best, big)] = ask(it, ["avg_store_sales", "best_store_sales", "big_stores"])
    assert avg == pytest.approx(1120 / 3) and best == 500 and big == 2  # South 500 and West 400 are over 300


def test_per_cell_of_the_question_and_under_its_filters(it):
    assert ask(it, ["net_sales", "avg_store_sales", "big_stores"], ["Store[Region]"]) == [
        ("East", 720, 360, 1), ("West", 400, 400, 1)]
    # bikes only: North 170, South 200, West 400
    [(avg,)] = ask(it, ["avg_store_sales"], where={"Product[Category]": "Bikes"})
    assert avg == pytest.approx(770 / 3)


def test_grouped_by_the_column_it_iterates_it_is_the_measure_itself(it):
    assert ask(it, ["net_sales", "avg_store_sales"], STORES) == [
        ("North", 220, 220), ("South", 500, 500), ("West", 400, 400)]


def test_a_formula_over_an_iterator(it):
    [(share,)] = ask(it, ["big_share"])
    assert share == pytest.approx(2 / 3)


def test_the_parser_captures_iterators_over_measures():
    out = cap(Units="SUM(Sales[Unit])",
              Avg_Store="AVERAGEX(VALUES(Store[Store]), [Net Sales])",
              Store_Total="SUMX(Store, [Net Sales] * 2)",
              Big="COUNTROWS(FILTER(VALUES(Store[Store]), [Net Sales] > 300))",
              Rowwise="SUMX(Sales, [Units])",
              Bike_Avg="AVERAGEX(FILTER(VALUES(Store[Store]), Store[Type] = \"A\"), [Net Sales])")
    assert all(out[k]["status"] == "captured" for k in ("avg_store", "store_total", "big", "rowwise", "bike_avg"))
    part = lambda k: out[k] if "iterate" in out[k] else out[out[k]["formula"].strip("{}")]  # noqa: E731
    assert part("avg_store")["iterate"] == {"agg": "avg", "over": ["Store[Store]"], "of": "net_sales"}
    assert part("store_total")["iterate"]["over"] == ["Store[StoreID]"]  # a table on the one side: its key
    big = part("big")["iterate"]
    assert big["agg"] == "count" and big["of"] == big["having"]
    assert out[big["having"]]["formula"] == "CASE WHEN ({net_sales} > 300) THEN 1 ELSE 0 END"
    assert out["rowwise"]["formula"] == "{units}"  # row by row over its own table: the sum itself
    assert "iterate" in part("bike_avg") and "filter" in out[part("bike_avg")["iterate"]["of"]]


def test_needs_and_columns_look_through_an_iterator(it):
    layer, _ = it
    assert layer.needs("avg_store_sales") == {"by": [], "params": {}}
    assert "Store[Region]" in layer.columns_for(["avg_store_sales"])
    assert base.SOLD  # the rows' rule is the one the expectations above assume


def test_not_filter_all_and_median():
    out = cap(Not_A="CALCULATE([Net Sales], NOT(Store[Type] = \"A\"))",
              All_A="CALCULATE([Net Sales], FILTER(ALL(Store), Store[Type] = \"A\"))",
              Median_Sale="MEDIAN(Sales[Amount])")
    assert "(NOT ({Store[Type]} = 'A'))" in out["not_a"]["filter"]
    assert out["all_a"]["context"] == [{"remove": "Store", "keep": "none"}]
    assert "({Store[Type]} = 'A')" in out["all_a"]["filter"]
    assert {k: out["median_sale"][k] for k in ("agg", "expr")} == {"agg": "median", "expr": "{Sales[Amount]}"}


def test_median_compiles(shop):  # noqa: F811
    layer, wh = shop
    spec = copy.deepcopy(SPEC)
    spec["metrics"]["median_sale"] = {"label": "Median", "status": "captured", "table": "Sales", "agg": "median",
                                      "expr": "{Sales[Amount]}", "filter": base.SOLD}
    [(v,)] = wh.query(BILayer(spec).compile(["median_sale"]))[1]
    assert v == 150  # sold: 50, 70, 100, 200, 300, 400: the middle two averaged, as DAX does
