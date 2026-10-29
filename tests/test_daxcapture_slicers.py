"""What report pages do with slicers, in the shapes a large payer model uses (names masked there, a shop here):
a slicer table with no relationships that picks which measure to show (SWITCH on its SELECTEDVALUE), a measure
that answers only when the slicer keeps a value ("Bikes" IN VALUES(Product[Category])), and 1/0 flags that show,
hide or filter a visual. The first two are captured and run; the flags are display-only, not gaps."""

import duckdb
import pytest

from insurance_context import daxcapture
from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import BILayer, LayerError, Warehouse, build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table

NET = 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")'
MEASURES = {
    "Net Sales": NET,
    "Units": "SUM(Sales[Unit])",
    "Least Unit": "MIN(Sales[Unit])",
    # the slicer picks the measure: SWITCH(TRUE(), SELECTEDVALUE(T1[c1]) = 1, [m1], ...)
    "Picked": "SWITCH(TRUE(), SELECTEDVALUE(Pick[MetricId]) = 1, [Net Sales], SELECTEDVALUE(Pick[MetricId]) = 2, "
              "[Units])",
    "Picked Plain": "SWITCH(SELECTEDVALUE(Pick[MetricId]), 3, [Units] * 2)",
    "Picked By Name": 'IF(SELECTEDVALUE(Pick[Metric]) = "Units", [Units])',
    "Store Pick": "// ===== SECTION 1: SLICER VARIABLES =====\n-- the store\nVAR _s = SELECTEDVALUE(Store[Store]) RETURN _s",
    # a 1/0 slicer as a test: SELECTEDVALUE(T1[c1]) || (SELECTEDVALUE(T1[c1]) <> 1 && ISFILTERED(T2))
    "Paged": "IF(SELECTEDVALUE(Paged[On]) || (SELECTEDVALUE(Paged[On]) <> 1 && ISFILTERED(Store)), [Units], 0)",
    # VAR _v1 = [m1] RETURN IF("s" IN VALUES(T1[c1]), CALCULATE([m4], T2[c2] >= _v1), 0)
    "Bike Sales": 'VAR _lo = [Least Unit] RETURN IF("Bikes" IN VALUES(Product[Category]), '
                  'CALCULATE([Net Sales], Sales[Unit] > _lo), 0)',
    # var _v1 = ISFILTERED(T1) var _v2 = SELECTEDVALUE(T2[c1]) var _v3 = SWITCH(_v2, "s", SUM(T3[c2]), "s", ...,
    # SUM(T3[c2]) + SUM(T4[c3])) RETURN SWITCH(TRUE(), _v1, _v3, SUM(T5[c4])), T2 related
    "Segment Sales": 'VAR _f = ISFILTERED(Store) VAR _seg = SELECTEDVALUE(Product[Category]) '
                     'VAR _v = SWITCH(_seg, "Bikes", SUM(Sales[Amount]), "Helmets", SUM(Sales[Unit]), '
                     'SUM(Sales[Amount]) + SUM(Sales[Unit])) RETURN SWITCH(TRUE(), _f, _v, SUM(Sales[Unit]) * 10)',
    "Not Bikes": 'IF(NOT("Bikes" IN VALUES(Product[Category])), [Net Sales])',
    "Bike Sales Retail": 'CALCULATE([Bike Sales], Product[Category] = "Bikes")',
    # flags for visuals: IF(ISFILTERED(T1[c1]) && COUNTROWS(ALLSELECTED(T2)) > 1, 1, 0), INT(NOT(ISEMPTY(T1)) || ...)
    "Show Slicer": "IF(ISFILTERED(Store[Store]) && COUNTROWS(ALLSELECTED(Store)) > 1, 1, 0)",
    "Any Rows": "INT(NOT(ISEMPTY(Sales)) || NOT(ISEMPTY(Store)))",
    "Has Rows": "INT(NOT(ISEMPTY(Sales)))",
    "Is Narrowed": "IF(NOT ISFILTERED(Store[Store]), 1, VAR _n = COUNTROWS(ALLSELECTED(Store[Store])) "
                   "RETURN IF(_n = CALCULATE(DISTINCTCOUNT(Store[Store]), REMOVEFILTERS(Store)), 1, 0))",
    "Flag Of A Number": "IF([Net Sales] > COUNTROWS(ALLSELECTED(Store)), 1, 0)",
}
# Sales (StoreID, ProductID, Unit, Amount, Status); product 1 Bikes, 2 Helmets
SALES = [(1, 1, 1, 100, "Sold"), (1, 1, 3, 50, "Sold"), (2, 2, 2, 200, "Sold"), (2, 1, 4, 300, "Sold"),
         (1, 2, 5, 70, "Returned")]


def model() -> Model:
    m = lambda *cs: [Column(c, t, c) for c, t in cs]
    sales = Table("Sales", m(("StoreID", "int64"), ("ProductID", "int64"), ("Unit", "int64"), ("Amount", "int64"),
                             ("Status", "string")), [Measure(n, e) for n, e in MEASURES.items()], "x", "m")
    store = Table("Store", m(("StoreID", "int64"), ("Store", "string")), source="x", source_kind="m")
    product = Table("Product", m(("ProductID", "int64"), ("Category", "string")), source="x", source_kind="m")
    pick = Table("Pick", m(("MetricId", "int64"), ("Metric", "string")), source="x", source_kind="m")
    paged = Table("Paged", m(("On", "int64"),), source="x", source_kind="m")
    return Model("Shop", {t.name: t for t in (sales, store, product, pick, paged)}, [
        Relationship("Sales", "StoreID", "Store", "StoreID"), Relationship("Sales", "ProductID", "Product", "ProductID")])


@pytest.fixture()
def layer(tmp_path):
    path = str(tmp_path / "pilot.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE SCHEMA shop")
    con.execute("CREATE TABLE shop.sales AS SELECT * FROM (VALUES " + ", ".join(
        f"({s}, {p}, {u}, {a}, '{st}')" for s, p, u, a, st in SALES) + ") t(StoreID, ProductID, Unit, Amount, Status)")
    con.execute("CREATE TABLE shop.store AS SELECT * FROM (VALUES (1, 'North'), (2, 'South')) t(StoreID, Store)")
    con.execute("CREATE TABLE shop.product AS SELECT * FROM (VALUES (1, 'Bikes'), (2, 'Helmets')) t(ProductID, Category)")
    con.close()
    return BILayer(build_spec(model(), "pilot", "shop")), Warehouse("local", path)


def value(layer, metric, **kw):
    lay, wh = layer
    return wh.query(lay.compile([metric], **kw))[1][0][-1]


def test_a_slicer_that_picks_the_measure_is_a_choice_the_question_makes(layer):
    params = {}
    out = daxcapture.capture(model(), params)
    assert params["pick"] == {"label": "Pick", "table": "Pick", "column": "MetricId", "choices": [1, 2, 3],
                              "default": None}
    assert out["picked"]["formula"] == ("CASE WHEN ({$pick} = 1) THEN {net_sales} WHEN ({$pick} = 2) THEN {units} "
                                        "ELSE NULL END")
    assert value(layer, "picked", params={"pick": 1}) == 650 and value(layer, "picked", params={"pick": 2}) == 15
    assert value(layer, "picked_plain", params={"pick": 3}) == 30
    with pytest.raises(LayerError, match="no default.*one of 1, 2, 3"):
        value(layer, "picked")
    # the agent checks the value against the slicer's and says what was used
    kit = BIToolkit(*layer)
    assert kit.resolve_params(["picked"], {"Pick": 2.0})[0] == {"pick": 2}
    with pytest.raises(LayerError, match="one of 1, 2, 3"):
        kit.resolve_params(["picked"], {"pick": 7})
    # a slicer of text is a choice of text; the visual's current value on a related table is still refused
    assert params["pick_metric"]["choices"] == ["Units"] and params["pick_metric"]["column"] == "Metric"
    assert value(layer, "picked_by_name", params={"pick_metric": "Units"}) == 15
    assert kit.resolve_params(["picked_by_name"], {"pick_metric": "units"})[0] == {"pick_metric": "Units"}
    with pytest.raises(LayerError, match="one of them"):
        value(layer, "picked_by_name", params={"pick_metric": "Other"})
    assert "not a what-if parameter" in out["store_pick"]["why"]
    # a 1/0 slicer as the test itself, mixed with a scope test
    assert params["paged"]["choices"] == [1] and out["paged"]["status"] == "captured"
    assert value(layer, "paged", params={"paged": 1}) == 15 and value(layer, "paged", params={"paged": 0}) == 0
    assert value(layer, "paged", params={"paged": 0}, where={"Store[Store]": "North"}) == 9


def test_a_measure_that_needs_a_value_in_the_slicer_follows_the_questions_filter(layer):
    out = daxcapture.capture(model())
    assert out["bike_sales"]["status"] == "captured" and out["bike_sales"]["by_scope"][0]["has"] == ["Bikes"]
    # every value is there when nothing filters it: sold rows with more units than the fewest (1)
    assert value(layer, "bike_sales") == 550
    assert value(layer, "bike_sales", where={"Product[Category]": "Helmets"}) == 0
    assert value(layer, "bike_sales", where={"Product[Category]": ["Bikes", "Helmets"]}) == 550
    assert value(layer, "bike_sales", where={"Product[Category]": "Bikes"}) == 350
    assert value(layer, "bike_sales", where={"Store[Store]": "North"}) == 50
    assert value(layer, "not_bikes") is None and value(layer, "not_bikes", where={"Product[Category]": "Helmets"}) == 200
    with pytest.raises(LayerError, match="filter on Product\\[Category\\] itself"):
        value(layer, "bike_sales", by=["Product[Category]"])
    # a CALCULATE filter on the column it tests would change the test: refused, as for ISFILTERED
    assert out["bike_sales_retail"]["status"] == "not_captured"


def test_the_one_value_a_related_column_is_filtered_to_picks_the_branch(layer):
    out = daxcapture.capture(model())
    assert out["segment_sales"]["status"] == "captured"
    north = {"Store[Store]": "North"}
    assert value(layer, "segment_sales", where={**north, "Product[Category]": "Bikes"}) == 150
    assert value(layer, "segment_sales", where={**north, "Product[Category]": "Helmets"}) == 5
    assert value(layer, "segment_sales", where=north) == 229  # no one value selected: the SWITCH's else
    assert value(layer, "segment_sales", where={**north, "Product[Category]": ["Bikes", "Helmets"]}) == 229
    assert value(layer, "segment_sales") == 150  # Store not filtered: the outer else, units * 10
    with pytest.raises(LayerError, match="one value Product\\[Category\\] is filtered to"):
        value(layer, "segment_sales", by=["Product[Category]"], where=north)


def test_flags_for_visuals_are_display_only(layer):
    out = daxcapture.capture(model())
    for k in ("show_slicer", "any_rows", "has_rows", "is_narrowed"):
        assert out[k]["status"] == "presentation" and "flag" in out[k]["why"], k
    assert out["flag_of_a_number"]["status"] == "not_captured"  # it reads a measure: not only the visual


def test_a_shape_shows_past_a_comment_banner():
    out = daxcapture.capture(model())
    assert out["store_pick"]["dax"].startswith("/* ===== SECTION 1: SLICER VARIABLES ===== */ /* the store */ VAR")
    report = daxcapture.coverage(out, examples=5, shape=True)
    assert "VAR _v1 = SELECTEDVALUE(T1[c1]) RETURN _v1" in report and "SECTION" not in report
