"""Power BI model -> TMDL -> captured metrics -> SQL (sqlglot) -> answers, on a small sales model."""

import duckdb
import pytest

from insurance_context import daxcapture
from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import BILayer, LayerError, Warehouse, build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table, read_tmdl, write_tmdl

MEASURES = {
    "Net Sales": 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")',
    "Units Sold": 'CALCULATE(SUM(Sales[Unit]), Sales[Status] = "Sold")',
    "Price per Unit": "DIVIDE([Net Sales], [Units Sold], 0)",
    "Bike Sales": 'CALCULATE([Net Sales], Product[Category] = "Bikes")',
    "Big Orders": "COUNTROWS(FILTER(Sales, Sales[Amount] >= 100))",
    "List Value": "SUMX(Sales, Sales[Unit] * RELATED(Product[Price]))",
    "Products Sold": "\nVAR s = DISTINCTCOUNT(Sales[ProductID])\nRETURN\n    s",
    "Net Sales PM": "CALCULATE([Net Sales], PREVIOUSMONTH('Calendar'[Date]))",
    "Growth": "[Net Sales] - [Net Sales PM]",
    "Store Rank": "RANKX(ALL(Store[Store]), [Net Sales])",
    "Sales Label": 'FORMAT([Net Sales], "$#,0")',
}


@pytest.fixture
def model() -> Model:
    sales = Table("Sales", [Column("ProductID", "int64", "ProductID"), Column("StoreID", "int64", "StoreID"),
                            Column("Status"), Column("Unit", "int64", "Unit"), Column("Amount", "int64", "Amount"),
                            Column("Date", "dateTime", "Date")],
                  [Measure(n, e) for n, e in MEASURES.items()], 'let\n    Source = Sql.Database("x")\nin\n    Source', "m")
    product = Table("Product", [Column("ProductID", "int64", "ProductID"), Column("Category", "string", "Category"),
                                Column("Price", "int64", "Price")], source="let x = 1 in x", source_kind="m")
    store = Table("Store", [Column("StoreID", "int64", "StoreID"), Column("Store", "string", "Store"),
                            Column("Type", "string", "Type")], source="let x = 1 in x", source_kind="m")
    cal = Table("Calendar", [Column("Date", "dateTime", "Date")], source="let x = 1 in x", source_kind="m")
    return Model("Shop", {t.name: t for t in (sales, product, store, cal)}, [
        Relationship("Sales", "ProductID", "Product", "ProductID"),
        Relationship("Sales", "StoreID", "Store", "StoreID"),
        Relationship("Sales", "Date", "Calendar", "Date")])


@pytest.fixture
def shop(model, tmp_path):
    """The model's capture, with a warehouse holding a few rows under <catalog>.<schema>.<table>."""
    con = duckdb.connect(str(tmp_path / "pilot.duckdb"))
    con.execute("CREATE SCHEMA shop")
    con.execute("CREATE TABLE shop.sales AS SELECT * FROM (VALUES (1, 1, 'Sold', 2, 300, DATE '2026-01-05'), "
                "(2, 1, 'Sold', 1, 50, DATE '2026-01-20'), (1, 2, 'Returned', 1, 150, DATE '2026-02-03'), "
                "(2, 2, 'Sold', 4, 200, DATE '2026-02-10')) t(ProductID, StoreID, Status, Unit, Amount, Date)")
    con.execute("CREATE TABLE shop.product AS SELECT * FROM (VALUES (1, 'Bikes', 150), (2, 'Helmets', 50)) "
                "t(ProductID, Category, Price)")
    con.execute("CREATE TABLE shop.store AS SELECT * FROM (VALUES (1, 'North', 'Online Store'), "
                "(2, 'South', 'Retail')) t(StoreID, Store, Type)")
    con.execute("CREATE TABLE shop.calendar AS SELECT DISTINCT Date FROM shop.sales")
    con.close()
    layer = BILayer(build_spec(model, "pilot", "shop"))
    return layer, Warehouse("local", str(tmp_path / "pilot.duckdb"))


def test_tmdl_round_trip_keeps_the_model(model, tmp_path):
    write_tmdl(model, tmp_path)
    back = read_tmdl(tmp_path)
    assert back.name == "Shop"
    assert [(m.name, m.expression) for _, m in back.measures] == [(n, e.strip()) for n, e in MEASURES.items()]
    assert [(c.name, c.data_type) for c in back.tables["Sales"].columns] == \
        [(c.name, c.data_type) for c in model.tables["Sales"].columns]
    assert back.tables["Sales"].source == model.tables["Sales"].source
    assert [vars(r) for r in back.relationships] == [vars(r) for r in model.relationships]


def test_capture_sorts_measures_and_says_why(model):
    out = daxcapture.capture(model)
    assert out["net_sales"] == {"label": "Net Sales", "status": "captured", "dax": MEASURES["Net Sales"],
                                "table": "Sales", "agg": "sum", "expr": "{Sales[Amount]}",
                                "filter": ["({Sales[Status]} = 'Sold')"]}
    assert out["price_per_unit"]["formula"] == "coalesce(({net_sales} / nullif({units_sold}, 0)), 0)"
    # CALCULATE's filter narrows the measure it wraps
    assert out["bike_sales"]["filter"] == ["({Sales[Status]} = 'Sold')", "({Product[Category]} = 'Bikes')"]
    assert (out["big_orders"]["agg"], out["big_orders"]["expr"], out["big_orders"]["filter"]) == \
        ("count", "*", ["({Sales[Amount]} >= 100)"])
    assert out["list_value"]["expr"] == "({Sales[Unit]} * {Product[Price]})"
    assert out["products_sold"]["agg"] == "count_distinct"
    assert out["net_sales_pm"]["time"] == {"date": "Calendar[Date]", "shift": -1, "unit": "month", "grain": "month"}
    assert out["growth"]["formula"] == "({net_sales} - {net_sales_pm})"
    assert out["store_rank"]["rank"] == {"over": "Store[Store]", "of": "net_sales", "order": "desc", "ties": "skip"}
    assert out["sales_label"]["status"] == "presentation"


def test_metrics_compile_to_sql_that_answers(shop):
    layer, wh = shop
    cols, rows = wh.query(layer.compile(["net_sales", "units_sold", "price_per_unit", "big_orders", "list_value"]))
    assert dict(zip(cols, rows[0])) == {"net_sales": 550, "units_sold": 7, "price_per_unit": 550 / 7,
                                        "big_orders": 3, "list_value": 2 * 150 + 50 + 150 + 4 * 50}
    # grouped through a relationship, and filtered on another table
    cols, rows = wh.query(layer.compile(["net_sales"], by=["Product[Category]"], where={"Store[Type]": "Retail"}))
    assert rows == [("Helmets", 200)]
    cols, rows = wh.query(layer.compile(["net_sales"], by=["Calendar[Date]:month"]))
    assert rows == [("2026-01", 350), ("2026-02", 200)]
    cols, rows = wh.query(layer.compile(["bike_sales"]))
    assert rows == [(300,)]
    _, rows = wh.query(layer.compile(["net_sales"], by=["Store[Store]"], top=1))
    assert rows == [("North", 350)]


def test_the_same_definition_transpiles_to_databricks(shop):
    layer, _ = shop
    sql = layer.compile(["net_sales"], by=["Calendar[Date]:month", "Product[Category]"], dialect="databricks")
    assert "`pilot`.`shop`.`sales`" in sql and "DATE_FORMAT(" in sql and '"' not in sql


def test_layer_refuses_what_it_cannot_answer(shop):
    layer, _ = shop
    with pytest.raises(LayerError, match="compares with the previous month"):
        layer.compile(["net_sales_pm"])
    with pytest.raises(LayerError, match="Store\\[Store\\]"):  # a rank needs its column in the question
        layer.compile(["store_rank"])
    with pytest.raises(LayerError, match="Table\\[Column\\]"):
        layer.compile(["net_sales"], by=["Category"])


def test_toolkit_reads_loose_filter_values_and_lists_valid_ones(shop):
    kit = BIToolkit(*shop)
    out = kit.compute(["net_sales"], filters={"Store[Type]": "online"})
    assert "350" in out and "read Store[Type]='online' as 'Online Store'" in out
    assert "Values in the data: Online Store, Retail" in kit.compute(["net_sales"], filters={"Store[Type]": "Kiosk"})
    assert "net_sales" in kit.list_metrics() and "store_rank" in kit.list_metrics()


def test_ci_diffs_against_the_published_capture_and_verifies(model, tmp_path):
    from insurance_context.bici import diff, run_ci
    old = build_spec(model, "pilot", "shop")
    model.tables["Sales"].measures.append(Measure("Retail Sales", 'CALCULATE([Net Sales], Store[Type] = "Retail")'))
    model.tables["Sales"].measures = [m for m in model.tables["Sales"].measures if m.name != "Big Orders"]
    new = build_spec(model, "pilot", "shop")
    lines = diff(old, new)
    assert any(x.startswith("+ metric `retail_sales` (captured)") for x in lines)
    assert "- metric `big_orders` removed" in lines
    assert diff(None, new) == ["first capture of this model"]
    # a repo folder holding TMDL is captured from it, with warehouse.yaml's confirmed tables
    src = tmp_path / "models" / "shop"
    write_tmdl(model, src)
    (src / "warehouse.yaml").write_text("schema: shop\ntables:\n  Sales: pilot.shop.sales\n")
    ok, report = run_ci(tmp_path / "models", tmp_path / "public")
    assert ok and "first capture" in report and "| `retail_sales` |  | ok |" in report
    spec = BILayer.load(tmp_path / "public" / "shop").spec
    assert spec["tables"]["Sales"]["from"] == "confirmed in warehouse.yaml"


def test_mcp_tools_stamp_answers_with_the_capture_version(shop, tmp_path):
    import json
    from insurance_context.bi_mcp import LocalCaptures, Served
    layer, wh = shop
    out = tmp_path / "public"
    (out / "shop").mkdir(parents=True)
    from insurance_context.bilayer import save
    save(layer.spec, out / "shop")
    (out / "shop" / "pilot.duckdb").write_bytes((tmp_path / "pilot.duckdb").read_bytes())
    (out / "version.json").write_text(json.dumps({"version": "v7", "commit": "abc1234"}))
    served = Served(LocalCaptures(out), prefer="local")
    got = served.call(lambda: served.kit.compute(["net_sales"], ["Calendar[Date]:year"]))
    assert got.startswith("[shop capture v7 · commit abc1234 · local]") and "| 2026 | 550 |" in got
