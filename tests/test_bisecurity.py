"""Row-level security: a model's roles captured as row conditions and enforced on real queries."""

import re

import duckdb
import pytest

from insurance_context.bi_agent import BIToolkit
from insurance_context.bilayer import BILayer, Warehouse, build_spec
from insurance_context.bisecurity import AccessDenied, Principal, capture_roles, filters_for, load_access
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Role, Table, read_tmdl, write_tmdl

M = "let x = 1 in x"
ROLES = [
    Role("Retail", tables={"Store": '[Type] = "Retail"'}),
    Role("Online", tables={"Store": '[Type] = "Online Store"'}),
    Role("My Stores", tables={"Managers": "[Email] = USERPRINCIPALNAME()"}),
    Role("Big Orders", tables={"Sales": "Sales[Amount] >= 200\n&& NOT ISBLANK(Sales[Status])"}),
    Role("Nobody", tables={"Store": "FALSE()"}),
    Role("Paths", tables={"Managers": 'PATHCONTAINS("a|b", [Email])'}),
    Role("Auditors", "administrator", members=["audit@shop.com"]),
]


@pytest.fixture
def model() -> Model:
    sales = Table("Sales", [Column("ProductID", "int64", "ProductID"), Column("StoreID", "int64", "StoreID"),
                            Column("Status"), Column("Amount", "int64", "Amount")],
                  [Measure("Net Sales", 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")')], M, "m")
    store = Table("Store", [Column("StoreID", "int64", "StoreID"), Column("Store", "string", "Store"),
                            Column("Type", "string", "Type")], source=M, source_kind="m")
    managers = Table("Managers", [Column("StoreID", "int64", "StoreID"), Column("Email", "string", "Email")],
                     source=M, source_kind="m")
    return Model("Shop", {t.name: t for t in (sales, store, managers)},
                 [Relationship("Sales", "StoreID", "Store", "StoreID"),
                  Relationship("Sales", "StoreID", "Managers", "StoreID")],
                 [Role(r.name, r.model_permission, dict(r.tables), list(r.members)) for r in ROLES])


@pytest.fixture
def shop(model, tmp_path):
    con = duckdb.connect(str(tmp_path / "pilot.duckdb"))
    con.execute("CREATE SCHEMA shop")
    con.execute("CREATE TABLE shop.sales AS SELECT * FROM (VALUES (1, 1, 'Sold', 300), (2, 1, 'Sold', 50), "
                "(1, 2, 'Returned', 150), (2, 2, 'Sold', 200), (1, 3, 'Sold', 100), (2, 4, 'Sold', 25)) "
                "t(ProductID, StoreID, Status, Amount)")
    con.execute("CREATE TABLE shop.store AS SELECT * FROM (VALUES (1, 'North', 'Online Store'), "
                "(2, 'South', 'Retail'), (3, 'East', 'Retail'), (4, 'West', 'Kiosk')) t(StoreID, Store, Type)")
    con.execute("CREATE TABLE shop.managers AS SELECT * FROM (VALUES (1, 'ana@shop.com'), (2, 'o''brien@shop.com'), "
                "(3, 'ana@shop.com'), (4, 'kim@shop.com')) t(StoreID, Email)")
    con.close()
    spec = build_spec(model, "pilot", "shop")
    spec["security"]["members"] = {"Retail": ["group:Retail Ops", "ret@shop.com"], "Online": ["web@shop.com"],
                                   "My Stores": ["group:Managers"], "Big Orders": ["big@shop.com"],
                                   "Nobody": ["no@shop.com"], "Paths": ["paths@shop.com"],
                                   "Auditors": ["audit@shop.com"]}
    return BILayer(spec), Warehouse("local", str(tmp_path / "pilot.duckdb"))


def net_sales(shop, who: Principal, **kw):
    layer, wh = shop
    _, rows = wh.query(layer.compile(["net_sales"], row_filters=filters_for(layer.spec, who), **kw))
    return rows


def test_roles_round_trip_through_tmdl(model, tmp_path):
    root = write_tmdl(model, tmp_path)
    assert (root / "roles" / "My Stores.tmdl").read_text() == \
        "role 'My Stores'\n\tmodelPermission: read\n\n\ttablePermission Managers = [Email] = USERPRINCIPALNAME()\n"
    back = read_tmdl(tmp_path)
    assert sorted(map(vars, back.roles), key=lambda r: r["name"]) == sorted(map(vars, model.roles), key=lambda r: r["name"])
    assert back.tables["Sales"].measures[0].name == "Net Sales"  # the rest of the model reads as before


def test_reads_roles_as_power_bi_desktop_writes_them(tmp_path):
    d = tmp_path / "definition"
    (d / "roles").mkdir(parents=True)
    (d / "tables").mkdir()
    (d / "tables" / "Store.tmdl").write_text("table Store\n\n\tcolumn Type\n\t\tdataType: string\n")
    (d / "roles" / "Store Managers.tmdl").write_text(
        "role 'Store Managers'\n\tmodelPermission: read\n\n\tmember 'ana@shop.com'\n\t\tmemberType: user\n\n"
        "\ttablePermission Store = [Type] = \"External\"\n\n"
        "\ttablePermission Sales =\n\t\t\tSales[Amount] > 0\n\t\t\t&& Sales[Status] = \"Sold\"\n\n"
        "\ttablePermission Product\n\t\tcolumnPermission Cost\n\t\t\tmetadataPermission: none\n\n"
        "\tannotation PBI_Id = abc\n")
    (d / "roles" / "Fenced.tmdl").write_text(
        "role Fenced\n\tmodelPermission: read\n\n\ttablePermission Store = ```\n\t\t\t[Type] = \"A\"\n\t\t\t```\n")
    m = read_tmdl(tmp_path)
    r = m.role("Store Managers")
    assert (r.model_permission, r.members) == ("read", ["ana@shop.com"])
    assert r.tables == {"Store": '[Type] = "External"', "Sales": 'Sales[Amount] > 0\n&& Sales[Status] = "Sold"'}
    assert m.role("Fenced").tables == {"Store": '[Type] = "A"'}
    assert list(m.tables) == ["Store"]


def test_read_pbix_without_roles_has_none():
    from _samples import GET_IT, SAMPLE_PBIX as sample
    if not sample.exists():
        pytest.skip(f"sample .pbix not here: {GET_IT}")
    from insurance_context.pbimodel import read_pbix
    assert read_pbix(sample).roles == []


def test_read_pbix_reads_roles_and_memberships(monkeypatch):
    import sys
    import types

    import pandas as pd

    class Fake:
        def __init__(self, path):
            self.tables = ["Store"]
            self.schema = pd.DataFrame([{"TableName": "Store", "ColumnName": "Type", "PandasDataType": "object"}])
            self.dax_columns = self.dax_measures = self.power_query = self.dax_tables = self.relationships = None
            self.rls = pd.DataFrame([
                {"TableName": "Store", "RoleName": "Retail", "RoleDescription": None, "FilterExpression": '[Type] = "Retail"'},
                {"TableName": "Store", "RoleName": "Hidden", "RoleDescription": None, "FilterExpression": None}])
            self.tmschema_role_memberships = pd.DataFrame([{"RoleName": "Retail", "MemberName": "ana@shop.com"},
                                                           {"RoleName": "Empty", "MemberName": "bo@shop.com"}])

    monkeypatch.setitem(sys.modules, "pbixray", types.SimpleNamespace(PBIXRay=Fake))
    from insurance_context.pbimodel import read_pbix
    m = read_pbix("Shop.pbix")
    assert [vars(r) for r in m.roles] == [
        {"name": "Retail", "model_permission": "read", "tables": {"Store": '[Type] = "Retail"'}, "members": ["ana@shop.com"]},
        {"name": "Hidden", "model_permission": "read", "tables": {}, "members": []},
        {"name": "Empty", "model_permission": "read", "tables": {}, "members": ["bo@shop.com"]}]


def test_capture_turns_role_filters_into_row_conditions(model):
    roles = capture_roles(model)
    assert roles["Retail"] == {"status": "captured", "tables": {"Store": "({Store[Type]} = 'Retail')"},
                               "dax": {"Store": '[Type] = "Retail"'}}
    assert roles["My Stores"]["tables"] == {"Managers": "(lower({Managers[Email]}) = {USERPRINCIPALNAME})"}
    assert roles["My Stores"]["dynamic"] is True
    assert roles["Big Orders"]["tables"]["Sales"] == "(({Sales[Amount]} >= 200) AND (NOT ({Sales[Status]} IS NULL)))"
    assert roles["Nobody"]["tables"] == {"Store": "FALSE"}
    assert roles["Paths"]["status"] == "not_captured" and "PATHCONTAINS" in roles["Paths"]["why"]
    assert roles["Auditors"]["tables"] == {} and "administrator" in roles["Auditors"]["note"]
    assert roles["Auditors"]["members_in_model"] == ["audit@shop.com"]
    model.roles.append(Role("Measure", tables={"Sales": "[Net Sales] > 0"}))
    model.roles.append(Role("Lost", tables={"Ghost": "TRUE()"}))
    model.roles.append(Role("Closed", "none"))
    roles = capture_roles(model)
    assert "not a column of Sales" in roles["Measure"]["why"]
    assert "Ghost" in roles["Lost"]["why"] and "no read access" in roles["Closed"]["why"]


def test_static_role_on_a_dimension_narrows_a_fact_metric(shop):
    assert net_sales(shop, Principal("nobody@x.com", ("Retail Ops",))) == [(300,)]
    assert net_sales(shop, Principal("RET@shop.com")) == [(300,)]
    assert net_sales(shop, Principal("big@shop.com")) == [(500,)]
    assert net_sales(shop, Principal("no@shop.com")) == [(0,)]
    rows = net_sales(shop, Principal("ret@shop.com"), by=["Store[Store]"])
    assert rows == [("East", 100), ("South", 200)]
    # a role that filters no table sees everything
    assert net_sales(shop, Principal("audit@shop.com")) == [(675,)]


def test_dynamic_role_reads_the_caller_through_a_security_table(shop):
    assert net_sales(shop, Principal("Ana@Shop.com", ("Managers",))) == [(450,)]
    assert net_sales(shop, Principal("kim@shop.com", ("managers",))) == [(25,)]
    assert net_sales(shop, Principal("someone@shop.com", ("Managers",))) == [(0,)]


def test_quotes_in_the_caller_id_are_escaped(shop):
    assert net_sales(shop, Principal("O'Brien@shop.com", ("Managers",))) == [(200,)]
    f = filters_for(shop[0].spec, Principal("x' OR '1'='1", ("Managers",)))
    assert f == {"Managers": "(lower({Managers[Email]}) = 'x'' or ''1''=''1')"}
    assert net_sales(shop, Principal("x' OR '1'='1", ("Managers",))) == [(0,)]


def test_several_roles_are_a_union(shop):
    both = Principal("web@shop.com", ("Retail Ops",))
    assert filters_for(shop[0].spec, both) == {"Store": "(({Store[Type]} = 'Retail')) OR (({Store[Type]} = 'Online Store'))"}
    assert net_sales(shop, both) == [(650,)]
    # a table one role filters and the other does not is open to the caller
    assert filters_for(shop[0].spec, Principal("big@shop.com", ("Retail Ops",))) == {}
    assert net_sales(shop, Principal("big@shop.com", ("Retail Ops",))) == [(675,)]


def test_fail_closed(shop):
    layer, _ = shop
    with pytest.raises(AccessDenied, match="none of this model's roles"):
        filters_for(layer.spec, Principal("stranger@x.com", ("Sales",)))
    with pytest.raises(AccessDenied, match="identity is required"):
        filters_for(layer.spec, None)
    with pytest.raises(AccessDenied, match="'Paths', which was not captured.*PATHCONTAINS"):
        filters_for(layer.spec, Principal("paths@shop.com", ("Retail Ops",)))
    assert filters_for({"tables": {}}, None) is None
    assert filters_for({"security": {"roles": {}}}, Principal("a")) is None


def test_object_id_role_needs_the_callers_object_id(model):
    model.roles = [Role("Oid", tables={"Managers": "[Email] = USEROBJECTID()"})]
    spec = {"security": {"roles": capture_roles(model), "members": {"Oid": ["a@x.com"]}}}
    assert filters_for(spec, Principal("a@x.com", (), "ABC-1")) == {"Managers": "(lower({Managers[Email]}) = 'abc-1')"}
    with pytest.raises(AccessDenied, match="USEROBJECTID"):
        filters_for(spec, Principal("a@x.com"))


def test_values_respect_row_level_security(shop):
    layer, wh = shop
    f = filters_for(layer.spec, Principal("ret@shop.com"))
    assert wh.query(layer.values_sql("Store[Store]", row_filters=f))[1] == [("East",), ("South",)]
    f = filters_for(layer.spec, Principal("big@shop.com"))
    assert wh.query(layer.values_sql("Sales[Status]", row_filters=f))[1] == [("Sold",)]


def test_toolkit_answers_within_the_callers_rows(shop):
    layer, wh = shop
    kit = BIToolkit(layer, wh, row_filters=filters_for(layer.spec, Principal("ana@shop.com", ("Managers",))))
    out = kit.compute(["net_sales"], by=["Store[Store]"])
    assert "| East | 100 |" in out and "| North | 350 |" in out and "South" not in out
    assert re.search(r"lower\(j\d\.\"Email\"\) = 'ana@shop\.com'", out)


def test_load_access(tmp_path):
    p = tmp_path / "access.yaml"
    p.write_text("roles:\n  Retail: [group:Retail Ops, ret@shop.com]\n  Online:\n    - web@shop.com\n  Nobody:\n")
    assert load_access(p) == {"Retail": ["group:Retail Ops", "ret@shop.com"], "Online": ["web@shop.com"], "Nobody": []}
    with pytest.raises(ValueError, match="not defined in the model"):
        load_access(p, roles={"Retail": {}, "Online": {}})
    for bad in ("Retail: [a@x.com]\n", "roles:\n  Retail: a@x.com\n", "roles:\n  Retail: ['group:']\n",
                "roles: [Retail]\n", "roles:\n  Retail: [1]\n"):
        p.write_text(bad)
        with pytest.raises(ValueError, match="access.yaml"):
            load_access(p)
