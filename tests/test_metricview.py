"""Captured metrics as Databricks metric views: the YAML follows the specification's shapes, what it cannot
express goes to the context file with the reason, and the measures compute what our compiler computes."""

import re

import pytest
import sqlglot
import yaml
from sqlglot import exp

from insurance_context.bilayer import BILayer, Warehouse, import_pbix
from insurance_context.bimetricview import export

from _samples import GET_IT, SAMPLE_PBIX

SPEC = {
    "model": "shop",
    "warehouse": {"dialect": "databricks", "catalog": "main", "schema": "shop"},
    "tables": {
        "Sales": {"table": "main.shop.sales", "columns": {
            "Amount": {"column": "amount", "type": "double"}, "Status": {"column": "status", "type": "string"},
            "StoreID": {"column": "store_id", "type": "int64"}, "Date": {"column": "order_date", "type": "dateTime"}}},
        "Store": {"table": "main.shop.store", "columns": {
            "StoreID": {"column": "id", "type": "int64"}, "Type": {"column": "kind", "type": "string"},
            "Store Name": {"column": "store name", "type": "string"}}},
        "Calendar": {"table": "main.shop.calendar", "columns": {"Date": {"column": "d", "type": "dateTime"}}},
    },
    "joins": [{"from": "Sales[StoreID]", "to": "Store[StoreID]", "many_to_one": True},
              {"from": "Sales[Date]", "to": "Calendar[Date]", "many_to_one": True}],
    "parameters": {"discount": {"label": "Discount", "min": 0, "max": 50, "step": 5, "default": 10}},
    "metrics": {
        "net_sales": {"label": "Net Sales", "status": "captured", "dax": 'CALCULATE(SUM(Sales[Amount]), Sales[Status]="Sold")',
                      "table": "Sales", "agg": "sum", "expr": "{Sales[Amount]}", "filter": ["({Sales[Status]} = 'Sold')"]},
        "external": {"label": "External Sales", "status": "captured", "table": "Sales", "agg": "sum",
                     "expr": "{Sales[Amount]}", "filter": ["({Sales[Status]} = 'Sold')", "({Store[Type]} = 'External')"]},
        "share": {"label": "External Share", "status": "captured", "formula": "({external} / nullif({net_sales}, 0))"},
        "net_sales_pm": {"label": "Net Sales PM", "status": "captured", "table": "Sales", "agg": "sum",
                         "expr": "{Sales[Amount]}", "filter": ["({Sales[Status]} = 'Sold')"],
                         "time": {"date": "Calendar[Date]", "shift": -1, "unit": "month", "grain": "month"}},
        "net_sales_py": {"label": "Net Sales PY", "status": "captured", "table": "Sales", "agg": "sum",
                         "expr": "{Sales[Amount]}", "time": {"date": "Calendar[Date]", "shift": -1, "unit": "year", "grain": None}},
        "net_sales_ytd": {"label": "Net Sales YTD", "status": "captured", "table": "Sales", "agg": "sum",
                          "expr": "{Sales[Amount]}", "time": {"date": "Calendar[Date]", "to_date": "year"}},
        "discount_value": {"label": "Discount Value", "status": "captured", "formula": "({$discount} / nullif(100, 0))"},
        "discounted": {"label": "Discounted Sales", "status": "captured", "formula": "({net_sales} * (1 - {discount_value}))"},
        "store_rank": {"label": "Store Rank", "status": "captured", "dax": "RANKX(ALL(Store[Store Name]), [Net Sales])",
                       "rank": {"over": "Store[Store Name]", "of": "net_sales", "order": "desc", "ties": "skip"}},
        "top3": {"label": "Top 3 Sales", "status": "captured", "formula": "CASE WHEN ({store_rank} < 4) THEN {net_sales} ELSE 0 END"},
        "label": {"label": "Net Sales Label", "status": "presentation", "value": "net_sales", "why": "formats a value as text"},
    },
}


@pytest.fixture
def files(tmp_path):
    return export(BILayer(SPEC), tmp_path), tmp_path


def test_the_view_has_the_specifications_shapes(files):
    _, out = files
    view = yaml.safe_load((out / "shop_sales.yaml").read_text())
    assert view["version"] == "1.1" and view["source"] == "main.shop.sales"
    assert view["parameters"] == [{"name": "discount", "data_type": "double", "default": 10}]
    assert {j["name"]: j["on"] for j in view["joins"]} == {"calendar": "source.order_date = calendar.d",
                                                         "store": "source.store_id = store.id"}
    fields = {f["name"]: f["expr"] for f in view["fields"]}
    assert fields["store_store_name"] == "store.`store name`"           # spaces quoted with backticks
    assert fields["calendar_date_month"] == "DATE_TRUNC('MONTH', calendar_date)"  # hierarchy on the order field
    m = {x["name"]: x for x in view["measures"]}
    assert m["external"]["expr"] == "SUM(amount) FILTER(WHERE ((status = 'Sold')) AND ((store.kind = 'External')))"
    assert m["share"]["expr"] == "(COALESCE(MEASURE(external), 0) / NULLIF(COALESCE(MEASURE(net_sales), 0), 0))"
    assert m["net_sales_pm"]["window"] == [{"order": "calendar_date_month", "range": "current", "offset": "-1 month",
                                            "semiadditive": "last"}]
    assert m["net_sales_py"]["window"][0]["offset"] == "-12 month"
    assert m["net_sales_ytd"]["window"] == [{"order": "calendar_date", "range": "cumulative", "semiadditive": "last"},
                                            {"order": "calendar_date_year", "range": "current", "semiadditive": "last"}]
    assert "discount_value" not in m and "(discount / NULLIF(100, 0))" in m["discounted"]["expr"]  # a what-if value is inlined
    names = [x["name"] for x in view["measures"]]
    for i, x in enumerate(view["measures"]):  # a measure only refers to measures defined before it
        assert all(names.index(r) < i for r in re.findall(r"MEASURE\((\w+)\)", x["expr"]))
        sqlglot.parse_one(f"SELECT {x['expr']}", read="databricks")
    assert "store_rank" not in m and "top3" not in m
    assert "'on':" in (out / "shop_sales.yaml").read_text()              # not read back as a YAML boolean


def test_the_context_file_carries_what_the_specification_cannot(files):
    _, out = files
    ctx = yaml.safe_load((out / "shop_sales.context.yaml").read_text())
    assert ctx["metric_view"] == "main.shop.shop_sales_metrics"
    assert ctx["measures"]["net_sales"] == {"power_bi": "Net Sales", "dax": SPEC["metrics"]["net_sales"]["dax"],
                                            "fidelity": "exact"}
    assert ctx["measures"]["net_sales_pm"]["ask"] == {"by": ["Calendar[Date]:month"]}
    assert "not yet run on Databricks" in ctx["measures"]["net_sales_pm"]["fidelity"]
    assert ctx["not_in_view"]["store_rank"]["captured_as"]["rank"]["over"] == "Store[Store Name]"
    assert ctx["not_in_view"]["top3"]["why"].startswith("built on store_rank: ranks are not part")
    assert ctx["display_only"] == {"label": {"power_bi": "Net Sales Label", "shows": "net_sales"}}
    assert ctx["what_if"]["discount"]["max"] == 50 and ctx["fields"]["Store[Type]"] == "store_type"
    sql = (out / "shop_sales.sql").read_text()
    assert sql.startswith("CREATE OR REPLACE VIEW main.shop.shop_sales_metrics\nWITH METRICS\nLANGUAGE YAML\nAS $$\n")


# ------------------------------------------------------------------ the numbers, on the sample

def _emulate(view: dict, key: str) -> str:
    """A measure as plain SQL over the view's source and joins (no windows, no parameters): MEASURE(x)
    expanded to x's expression, bare columns read from the source."""
    exprs = {m["name"]: m["expr"] for m in view["measures"]}

    def expand(e: str) -> str:
        return re.sub(r"MEASURE\((\w+)\)", lambda x: f"({expand(exprs[x.group(1)])})", e)

    tree = sqlglot.parse_one(f"SELECT {expand(exprs[key])}", read="databricks")
    for c in tree.find_all(exp.Column):
        if not c.table:
            c.set("table", exp.to_identifier("source"))
    joins = []

    def walk(js):
        for j in js:
            joins.append(f"LEFT JOIN {j['source']} AS {j['name']} ON {j['on']}")
            walk(j.get("joins", []))

    walk(view.get("joins", []))
    sql = tree.sql("databricks") + f" FROM {view['source']} AS source " + " ".join(joins)
    return sqlglot.transpile(sql, read="databricks", write="duckdb")[0]


@pytest.mark.skipif(not SAMPLE_PBIX.exists(), reason=f"sample .pbix not here: {GET_IT}")
def test_exported_measures_compute_what_the_compiler_computes(tmp_path):
    pytest.importorskip("pbixray")
    import_pbix(SAMPLE_PBIX, tmp_path / "cap", catalog="pilot")
    layer = BILayer.load(tmp_path / "cap")
    export(layer, tmp_path / "mv")
    view = yaml.safe_load((tmp_path / "mv" / "sales_returns_sales.yaml").read_text())
    wh = Warehouse.open(tmp_path / "cap", layer, prefer="local")
    checked = 0
    for m in view["measures"]:
        k = m["name"]
        if m.get("window") or layer.metrics[k].get("hidden") or any(p["name"] in m["expr"] for p in view.get("parameters", [])):
            continue
        uses_window = any(x.get("window") for x in view["measures"] if re.search(rf"MEASURE\({x['name']}\)", m["expr"]))
        if uses_window:
            continue
        got = wh.query(_emulate(view, k))[1][0][0]
        _, rows = wh.query(layer.compile([k]))
        want = rows[0][0]
        assert got == pytest.approx(want, rel=1e-9, abs=1e-9), f"{k}: metric view {got} vs compiler {want}"
        checked += 1
    assert checked >= 6  # net_sales, units_sold, returns, units_returned, return_rate, wif_price_per_unit, ...


def test_the_command_exports_a_tmdl_folder(tmp_path, capsys):
    from insurance_context.cli import main
    plains = "clients/plains_health/powerbi/models/MEE_Client_Analytics"
    assert main(["pbi", "export", plains, "--schema", "medical_economics", "--out", str(tmp_path)]) == 0
    view = yaml.safe_load((tmp_path / "mee_client_analytics_metrics.yaml").read_text())
    assert view["source"] == "pilot.medical_economics.metrics" and len(view["measures"]) == 14
    ctx = yaml.safe_load((tmp_path / "mee_client_analytics_metrics.context.yaml").read_text())
    assert ctx["metric_view"] == "pilot.medical_economics.mee_client_analytics_metrics"
    assert "latest period" in ctx["not_in_view"]["paid_pmpm"]["why"] or "ALLSELECTED" in ctx["not_in_view"]["paid_pmpm"]["why"]
    assert "mee_client_analytics_metrics_metrics" not in capsys.readouterr().out
