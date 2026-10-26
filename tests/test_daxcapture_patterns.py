"""DAX capture of what a measure reads of the visual: filter modifiers, latest period, conditionals,
what-if parameters, ranks, scope switches, and the number a presentation measure shows."""

from collections import Counter
from pathlib import Path

import pytest

from insurance_context import daxcapture
from insurance_context.bilayer import build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table, read_pbix, read_tmdl

from _samples import GET_IT, SAMPLE_PBIX, SAMPLE_TMDL

ROOT = Path(__file__).resolve().parents[1]
SAMPLE, SAMPLE_TMDL = SAMPLE_PBIX, SAMPLE_TMDL
PLAINS = ROOT / "clients/plains_health/powerbi/models/MEE_Client_Analytics"
NET = 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")'


def shop(**measures: str) -> Model:
    """Sales -> Store, Product, Calendar; Metrics on its own; a what-if table '% Rate' = GENERATESERIES(0, 60, 5)."""
    ms = {"Net Sales": NET, **{k.replace("_", " "): v for k, v in measures.items()}}
    m = lambda *cs: [Column(c, t, c) for c, t in cs]  # noqa: E731
    sales = Table("Sales", m(("StoreID", "int64"), ("ProductID", "int64"), ("Status", "string"), ("Unit", "int64"),
                             ("Amount", "int64"), ("Date", "dateTime")), [Measure(n, e) for n, e in ms.items()],
                  "let x = 1 in x", "m")
    store = Table("Store", m(("StoreID", "int64"), ("Store", "string"), ("Type", "string")),
                  source="x", source_kind="m")
    product = Table("Product", m(("ProductID", "int64"), ("Product", "string"), ("Category", "string")),
                    source="x", source_kind="m")
    cal = Table("Calendar", m(("Date", "dateTime"), ("Month", "string")), source="x", source_kind="m")
    metrics = Table("Metrics", m(("Year", "int64"), ("Paid", "double")), source="x", source_kind="m")
    rate = Table("% Rate", [Column("% Rate", "int64", None)],
                 [Measure("% Rate Value", "SELECTEDVALUE('% Rate'[% Rate])")], "GENERATESERIES(0, 60, 5)", "calculated")
    return Model("Shop", {t.name: t for t in (sales, store, product, cal, metrics, rate)}, [
        Relationship("Sales", "StoreID", "Store", "StoreID"),
        Relationship("Sales", "ProductID", "Product", "ProductID"),
        Relationship("Sales", "Date", "Calendar", "Date")])


def cap(**measures: str) -> dict:
    return daxcapture.capture(shop(**measures))


LEAF = {"table": "Sales", "agg": "sum", "expr": "{Sales[Amount]}", "filter": ["({Sales[Status]} = 'Sold')"]}


def leaf(spec: dict) -> dict:
    return {k: v for k, v in spec.items() if k not in ("label", "status", "dax")}


# ------------------------------------------------------------------ context

def test_filter_modifiers_become_the_leaf_context():
    out = cap(All_Stores="CALCULATE([Net Sales], ALL(Store[Store]))",
              All_Sales="CALCULATE([Net Sales], ALL(Sales))",
              Selected="CALCULATE([Net Sales], ALLSELECTED('Calendar'[Date]))",
              Removed="CALCULATE([Net Sales], REMOVEFILTERS(Store[Type], Store[Store]))",
              Except="CALCULATE([Net Sales], ALLEXCEPT(Sales, Store[Type]))",
              Months="CALCULATE([Net Sales], ALL('Calendar'[Date].[Month]))",
              Days="CALCULATE(DISTINCTCOUNT('Calendar'[Date]), ALL('Calendar'))")
    assert leaf(out["all_stores"]) == {**LEAF, "context": [{"remove": "Store[Store]", "keep": "none"}]}
    assert out["all_sales"]["context"] == [{"remove": "Sales", "keep": "none"}]
    assert out["selected"]["context"] == [{"remove": "Calendar[Date]", "keep": "filters"}]
    assert out["removed"]["context"] == [{"remove": "Store[Type]", "keep": "none"},
                                         {"remove": "Store[Store]", "keep": "none"}]
    assert out["except"]["context"] == [{"remove": "Sales", "keep": "none", "except": ["Store[Type]"]}]
    assert out["months"]["context"] == [{"remove": "Calendar[Date]", "keep": "none", "level": "month"}]
    assert leaf(out["days"]) == {"table": "Calendar", "agg": "count_distinct", "expr": "{Calendar[Date]}",
                                 "context": [{"remove": "Calendar", "keep": "none"}]}


def test_a_modifier_reaches_every_part_of_a_formula_and_the_fixed_filter_stays():
    out = cap(Units="SUM(Sales[Unit])", Price="DIVIDE([Net Sales], [Units])",
              Total_Price="CALCULATE([Price], ALL(Store), Sales[Unit] > 1)")
    f = out["total_price"]["formula"]
    parts = [out[k] for k in sorted(out) if k.startswith("total_price__")]
    assert f.count("{total_price__") == 2 and all(p["hidden"] for p in parts)
    assert all(p["context"] == [{"remove": "Store", "keep": "none"}] and "({Sales[Unit]} > 1)" in p["filter"]
               for p in parts)


def test_modifiers_that_would_change_meaning_are_refused():
    out = cap(All_Stores="CALCULATE([Net Sales], ALL(Store))",
              Outer="CALCULATE([All Stores], Store[Type] = \"Retail\")",       # DAX would drop this filter
              Through="CALCULATE([All Sales], Store[Type] = \"Retail\")",      # ALL(Sales) expands to Store
              All_Sales="CALCULATE([Net Sales], ALL(Sales))",
              Kept="CALCULATE([Excepted], Store[Type] = \"Retail\")",
              Excepted="CALCULATE([Net Sales], ALLEXCEPT(Sales, Store[Type]))",
              Everything="CALCULATE([Net Sales], ALL())",
              Years="CALCULATE([Net Sales], ALL('Calendar'[Date].[Year]))",
              Shifted="CALCULATE([Net Sales], ALL('Calendar'), PREVIOUSMONTH('Calendar'[Date]))",
              Iterated="SUMX(ALL(Sales), Sales[Amount])")
    for key, why in [("outer", "DAX would drop that filter"), ("through", "DAX would drop that filter"),
                     ("everything", "removes every filter"), ("years", "Year level"),
                     ("shifted", "computes without that date's filters"), ("iterated", "ALL")]:
        assert out[key]["status"] == "not_captured" and why in out[key]["why"], (key, out[key])
    assert out["kept"]["status"] == "captured" and "({Store[Type]} = 'Retail')" in out["kept"]["filter"]


# ------------------------------------------------------------------ latest

def test_latest_period_from_a_var_and_a_fixed_year_stays_a_filter():
    out = cap(Paid="VAR _y = MAX(Metrics[Year]) RETURN CALCULATE(SUM(Metrics[Paid]), ALLSELECTED(Metrics), "
                   "Metrics[Year] = _y)",
              Paid_PY="VAR _y = MAX(Metrics[Year]) RETURN CALCULATE(SUM(Metrics[Paid]), ALL(Metrics), "
                      "Metrics[Year] = _y - 1)",
              Baseline="CALCULATE(SUM(Metrics[Paid]), ALL(Metrics), Metrics[Year] = 2021)",
              Change="VAR _y = MAX(Metrics[Year]) VAR _c = CALCULATE(SUM(Metrics[Paid]), ALLSELECTED(Metrics), "
                     "Metrics[Year] = _y) VAR _b = CALCULATE(SUM(Metrics[Paid]), ALL(Metrics), Metrics[Year] = 2021) "
                     "RETURN _c - _b",
              Narrowed="CALCULATE([Paid], Metrics[Paid] > 0)",
              Other_Column="CALCULATE(SUM(Metrics[Paid]), Metrics[Year] = MAX(Metrics[Paid]))")
    assert leaf(out["paid"]) == {"table": "Metrics", "agg": "sum", "expr": "{Metrics[Paid]}",
                                 "context": [{"remove": "Metrics", "keep": "filters"}],
                                 "latest": {"column": "Metrics[Year]", "agg": "max", "offset": 0}}
    assert out["paid_py"]["latest"] == {"column": "Metrics[Year]", "agg": "max", "offset": -1}
    assert out["baseline"]["filter"] == ["({Metrics[Year]} = 2021)"] and "latest" not in out["baseline"]
    a, b = [out[k] for k in out["change"]["formula"].strip("()").replace("{", "").replace("}", "").split(" - ")]
    assert a["latest"]["offset"] == 0 and a["context"][0]["keep"] == "filters"
    assert b["filter"] == ["({Metrics[Year]} = 2021)"] and b["context"][0]["keep"] == "none"
    assert out["narrowed"]["status"] == "not_captured" and "which one is latest" in out["narrowed"]["why"]
    assert out["other_column"]["status"] == "not_captured"


# ------------------------------------------------------------------ conditionals

def test_if_switch_and_isblank_become_case_over_metrics():
    out = cap(Top="IF([Net Sales] > 100, [Net Sales], 0)", Only="IF([Net Sales] >= 100, [Net Sales])",
              Blank="IF(ISBLANK([Net Sales]), 0, 1)",
              Both="IF([Net Sales] > 0 && NOT([Net Sales] > 100), 1, 2)",
              Tier="SWITCH(TRUE(), [Net Sales] < 10, 1, [Net Sales] < 100, 2, 3)",
              Pick="SWITCH([Net Sales], 1, 10, 2, 20)",
              Text_Test="IF([Net Sales] = \"x\", 1, 0)",
              Bool="[Net Sales] > 0")
    assert out["top"]["formula"] == "CASE WHEN ({net_sales} > 100) THEN {net_sales} ELSE 0 END"
    assert out["only"]["formula"] == "CASE WHEN ({net_sales} >= 100) THEN {net_sales} ELSE NULL END"
    assert out["blank"]["formula"] == "CASE WHEN ({?net_sales} IS NULL) THEN 0 ELSE 1 END"
    assert out["both"]["formula"] == "CASE WHEN (({net_sales} > 0) AND (NOT ({net_sales} > 100))) THEN 1 ELSE 2 END"
    assert out["tier"]["formula"] == ("CASE WHEN ({net_sales} < 10) THEN 1 WHEN ({net_sales} < 100) THEN 2 "
                                      "ELSE 3 END")
    assert out["pick"]["formula"] == "CASE WHEN ({net_sales} = 1) THEN 10 WHEN ({net_sales} = 2) THEN 20 ELSE NULL END"
    assert out["text_test"]["status"] == out["bool"]["status"] == "not_captured"


def test_a_filter_around_isblank_keeps_the_raw_value():
    out = cap(Units="SUM(Sales[Unit])", Ratio="DIVIDE([Net Sales], [Units])", Flag="IF(ISBLANK([Ratio]), 0, 1)",
              Retail_Flag="CALCULATE([Flag], Store[Type] = \"Retail\")")
    raw = out["retail_flag"]["formula"].split("{?")[1].split("}")[0]
    assert out[raw]["hidden"] and "formula" in out[raw]


# ------------------------------------------------------------------ what-if parameters

def test_selectedvalue_of_a_generateseries_table_is_a_parameter():
    model = shop(Adjusted="SUM(Sales[Unit]) * '% Rate'[% Rate Value] / 100",
                 With_Default="SELECTEDVALUE('% Rate'[% Rate], 20)", Store_Pick="SELECTEDVALUE(Store[Store])")
    params = {}
    out = daxcapture.capture(model, params)
    assert out["pct_rate_value"]["formula"] == "{$pct_rate}"
    assert "{pct_rate_value}" in out["adjusted"]["formula"]  # 'Table'[Measure] is a measure reference
    assert out["with_default"]["status"] == "not_captured" and "different default" in out["with_default"]["why"]
    assert params == {"pct_rate": {"label": "% Rate", "table": "% Rate", "column": "% Rate", "min": 0, "max": 60,
                                   "step": 5, "default": None}}
    assert out["store_pick"]["status"] == "not_captured" and "not a what-if parameter" in out["store_pick"]["why"]
    spec = build_spec(model, "pilot", "shop")
    assert spec["parameters"] == params and spec["metrics"] == out


# ------------------------------------------------------------------ rank and scope

def test_rankx_over_a_column_is_a_rank_metric():
    out = cap(Store_Rank="RANKX(ALL('Store'[Store]), [Net Sales], , DESC)",
              Low_Rank="RANKX(ALL(Store[Store]), [Net Sales], , ASC, Dense)",
              Unit_Rank="RANKX(ALL(Store[Store]), SUM(Sales[Unit]))",
              Top_3="IF([Store Rank] < 4, [Net Sales], 0)",
              Table_Rank="RANKX(ALL(Store), [Net Sales])",
              Ranked_Retail="CALCULATE([Store Rank], Store[Type] = \"Retail\")")
    assert leaf(out["store_rank"]) == {"rank": {"over": "Store[Store]", "of": "net_sales", "order": "desc",
                                                "ties": "skip"}}
    assert out["low_rank"]["rank"] | {"of": None} == {"over": "Store[Store]", "of": None, "order": "asc",
                                                       "ties": "dense"}
    part = out[out["unit_rank"]["rank"]["of"]]
    assert part["hidden"] and part["expr"] == "{Sales[Unit]}"
    assert out["top_3"]["formula"] == "CASE WHEN ({store_rank} < 4) THEN {net_sales} ELSE 0 END"
    assert out["table_rank"]["status"] == "not_captured" and "ALL(Table[Column])" in out["table_rank"]["why"]
    assert out["ranked_retail"]["status"] == "not_captured" and "rank or scope" in out["ranked_retail"]["why"]


def test_isinscope_switch_is_a_scope_switch():
    out = cap(Product_Rank="SWITCH(TRUE(), ISINSCOPE('Product'[Product]), RANKX(ALL('Product'[Product]), [Net Sales]),"
                           " ISINSCOPE(Product[Category]), [Net Sales], RANKX(ALL('Product'[Category]), [Net Sales]))",
              No_Else="SWITCH(TRUE(), ISINSCOPE(Product[Product]), [Net Sales])",
              Mixed="SWITCH(TRUE(), ISINSCOPE(Product[Product]), 1, [Net Sales] > 0, 2)")
    scope = out["product_rank"]["by_scope"]
    assert [b.get("column") for b in scope] == ["Product[Product]", "Product[Category]", None]
    assert scope[1]["metric"] == "net_sales"
    assert out[scope[0]["metric"]]["rank"]["over"] == "Product[Product]" and out[scope[0]["metric"]]["hidden"]
    assert out[scope[2]["else"]]["rank"]["over"] == "Product[Category]"
    assert out["no_else"]["by_scope"] == [{"column": "Product[Product]", "metric": "net_sales"}]
    assert out["mixed"]["status"] == "not_captured"


# ------------------------------------------------------------------ presentation

def test_presentation_names_the_number_it_shows():
    out = cap(Variance="DIVIDE([Net Sales], CALCULATE([Net Sales], PREVIOUSMONTH('Calendar'[Date]))) - 1",
              Label='FORMAT([Net Sales], "$0,000")',
              Indicator='CONCATENATE(IF([Variance] <= 0, "", "+"), FORMAT([Variance], "0.0%"))',
              Arrow='VAR _d = [Variance] * 2 RETURN IF(ISBLANK([Variance]), "--", IF(_d < 0, "▼ ", "▲ ") & '
                    'FORMAT(ABS(_d), "0.0%") & " YoY")',
              Magnitude='FORMAT(ABS([Variance]), "0.0")',
              Lift='CONCATENATE(FORMAT(SUM(Sales[Unit]), "0.0"), "x")',
              Image='"https://example.com/a.gif"',
              Two='FORMAT([Net Sales], "0") & FORMAT([Variance], "0%")')
    assert {k: out[k].get("value") for k in ("label", "indicator", "image")} == \
        {"label": "net_sales", "indicator": "variance", "image": None}
    assert all(out[k]["status"] == "presentation" for k in ("label", "indicator", "arrow", "lift", "image", "two"))
    assert out["arrow"]["value"] == "arrow_value"
    assert out["arrow_value"] == {"label": "Arrow (value)", "status": "captured", "formula": "({variance} * 2)"}
    assert out["magnitude_value"]["formula"] == "abs({variance})"  # no sign branch: the value is ABS(x)
    assert leaf(out[out["lift"]["value"]]) == {"table": "Sales", "agg": "sum", "expr": "{Sales[Unit]}"}
    assert "value" not in out["two"] and "several numbers" in out["two"]["why"]
    assert list(out).index("arrow_value") == list(out).index("arrow") + 1


def test_a_sort_key_glued_from_text_is_not_captured():
    out = cap(Association='VALUE(CONCATENATE([Net Sales], SUM(Sales[Unit])))')
    assert out["association"]["status"] == "not_captured" and "sort key" in out["association"]["why"]


# ------------------------------------------------------------------ the real models

def _finish_line(out: dict) -> Counter:
    shown = {k: v for k, v in out.items() if not v.get("hidden")}
    for k, v in shown.items():
        if v["status"] == "presentation" and v.get("value"):
            assert shown[v["value"]]["status"] == "captured", k
    assert [v["label"] for v in shown.values() if v["status"] == "not_captured"] in ([], ["Association"])
    return Counter(v["status"] for v in shown.values() if not v["label"].endswith(" (value)"))


def test_sample_model_captures_every_number():
    if not SAMPLE.exists():
        pytest.skip(f"sample .pbix not here: {GET_IT}")
    pytest.importorskip("pbixray")
    out = daxcapture.capture(read_pbix(SAMPLE))
    assert _finish_line(out) == {"captured": 48, "presentation": 9, "not_captured": 1}
    assert out["association"]["status"] == "not_captured" and "sort key" in out["association"]["why"]
    assert out["store_top_n"]["rank"]["over"] == "Store[Store]"
    assert [b.get("column") for b in out["product_top_n"]["by_scope"]] == ["Product[Product]", "Product[Segment]", None]
    assert out["wif_sales"]["context"] == [{"remove": "Calendar[Date]", "keep": "none", "level": "month"}]
    assert "{$pct_return_rate}" in out["pct_return_rate_value"]["formula"]
    assert [out[k].get("value") for k in ("net_sales_label", "net_sales_indicator", "info_tooltip")] == \
        ["net_sales", "net_sales_variance_pct", None]
    if SAMPLE_TMDL.exists():
        assert _finish_line(daxcapture.capture(read_tmdl(SAMPLE_TMDL)))["captured"] == 49


def test_plains_model_captures_every_number():
    if not PLAINS.exists():
        pytest.skip("Plains model not present")
    out = daxcapture.capture(read_tmdl(PLAINS))
    assert _finish_line(out) == {"captured": 25, "presentation": 11}
    assert out["paid_pmpm"]["latest"] == {"column": "Metrics[Year]", "agg": "max", "offset": 0}
    assert out["m_2021_baseline"]["context"] == [{"remove": "Metrics", "keep": "none"}]
    assert out["paid_pmpm_yoy"]["value"] == "paid_pmpm_yoy_value"
    parts = [out[k] for k in out if k.startswith("paid_pmpm_yoy_value__")]
    assert sorted(p["latest"]["offset"] for p in parts) == [-1, 0]
