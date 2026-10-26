"""CI for a Power BI models repo: warehouse.yaml mapping, verify, MR note, publish guards, drift."""

import json

import duckdb
import pytest

from insurance_context import bici
from insurance_context.bilayer import BILayer, LayerError, Warehouse, build_spec
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table, write_tmdl

GITLAB_ENV = ("GITLAB_CI", "TF_BUILD", "CI_JOB_TOKEN", "CI_MERGE_REQUEST_IID", "GITLAB_TOKEN", "GITLAB_API",
              "CI_API_V4_URL", "CI_PROJECT_ID", "CI_PIPELINE_URL", "DATABRICKS_SERVER_HOSTNAME",
              "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in GITLAB_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr("insurance_context.llm._load_dotenv", lambda: None)


@pytest.fixture
def model() -> Model:
    sales = Table("Sales", [Column("StoreID", "int64", "StoreID"), Column("Amount", "int64", "Amount"),
                            Column("Status", "string", "Status")],
                  [Measure("Net Sales", 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")'),
                   Measure("Orders", "COUNTROWS(Sales)")], "let x = 1 in x", "m")
    store = Table("Store", [Column("StoreID", "int64", "StoreID"), Column("Type", "string", "Type")],
                  source="let x = 1 in x", source_kind="m")
    return Model("Shop", {"Sales": sales, "Store": store}, [Relationship("Sales", "StoreID", "Store", "StoreID")])


def _warehouse(path, sales="StoreID INT, net_amount BIGINT, Status VARCHAR", store=True):
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA shop")
    con.execute(f"CREATE TABLE shop.sales ({sales})")
    if "Status VARCHAR" in sales:
        con.execute("INSERT INTO shop.sales VALUES (1, 300, 'Sold'), (2, 50, 'Sold'), (1, 70, 'Returned')")
    if store:
        con.execute("CREATE TABLE shop.store_dim (StoreID INT, store_type VARCHAR)")
        con.execute("INSERT INTO shop.store_dim VALUES (1, 'Online'), (2, 'Retail')")
    con.close()


WAREHOUSE_YAML = """schema: shop
tables:
  Sales: pilot.shop.sales
columns:
  Sales: {Amount: net_amount}
sql:
  Store: SELECT StoreID, store_type AS Type FROM pilot.shop.store_dim
"""


def test_warehouse_yaml_maps_columns_and_sql_and_rejects_unknown_names(model):
    spec = bici.apply_warehouse(build_spec(model, "pilot", "shop"), __import__("yaml").safe_load(WAREHOUSE_YAML))
    assert spec["tables"]["Sales"]["columns"]["Amount"]["column"] == "net_amount"
    assert spec["tables"]["Store"]["sql"].startswith("SELECT StoreID") and spec["tables"]["Store"]["from"] == \
        "SQL in warehouse.yaml"
    for conf, msg in [({"tables": {"Salez": "pilot.shop.sales"}}, "no table 'Salez' in the model"),
                      ({"columns": {"Sales": {"Amt": "x"}}}, r"no column Sales\[Amt\]"),
                      ({"tables": {"Sales": "sales"}}, "is not catalog.schema.table"),
                      ({"sql": {"Store": "SELEC nope FROM"}}, "not valid Databricks SQL"),
                      ({"colums": {}}, "unknown key 'colums'")]:
        with pytest.raises(LayerError, match=msg):
            bici.apply_warehouse(build_spec(model, "pilot", "shop"), conf)


def test_access_yaml_fills_members_and_rejects_roles_the_model_lacks(model, tmp_path):
    (tmp_path / "access.yaml").write_text("roles:\n  Online: [ana@shop.com, group:Finance]\n")
    spec = build_spec(model, "pilot", "shop")
    with pytest.raises(LayerError, match="Online"):
        bici.apply_access(spec, tmp_path / "access.yaml")
    spec["security"] = {"roles": {"Online": {"status": "captured", "tables": {"Store": "{Store[Type]} = 'Online'"}}}}
    assert bici.apply_access(spec, tmp_path / "access.yaml")
    assert spec["security"]["members"] == {"Online": ["ana@shop.com", "group:Finance"]}


def test_ci_verifies_on_the_warehouse_in_one_query_and_says_which_were_checked(model, tmp_path, monkeypatch):
    src = tmp_path / "models" / "shop"
    write_tmdl(model, src)
    (src / "warehouse.yaml").write_text(WAREHOUSE_YAML)
    spec = bici.build_model(src, tmp_path / "public" / "shop")
    _warehouse(tmp_path / "public" / "shop" / "pilot.duckdb")
    seen = []
    real = Warehouse.query
    monkeypatch.setattr(Warehouse, "query", lambda self, sql, limit=None: seen.append(sql) or real(self, sql, limit))
    layer = BILayer(spec)
    targets, lines, ok = bici.warehouses(tmp_path / "public" / "shop", layer)
    rows = {r["metric"]: r for r in bici.verify(layer, tmp_path / "public" / "shop", targets)}
    assert ok and len(seen) == 1 and rows["net_sales"]["local"] == 350 and rows["orders"]["local"] == 3
    assert lines[0].startswith("local stand-in `pilot.duckdb`") and lines[1].startswith("Databricks: not configured")
    monkeypatch.setenv("DATABRICKS_SERVER_HOSTNAME", "adb-1.azuredatabricks.net")
    _, lines, ok = bici.warehouses(tmp_path / "public" / "shop", layer)
    assert not ok and "**misconfigured**, set DATABRICKS_HTTP_PATH and DATABRICKS_TOKEN" in lines[1]


def test_ci_off_gitlab_makes_no_api_calls_and_reports_a_bad_model(model, tmp_path, monkeypatch):
    monkeypatch.setattr(bici, "_api", lambda *a, **k: pytest.fail("no GitLab call off GitLab"))
    write_tmdl(model, tmp_path / "models" / "shop")
    (tmp_path / "models" / "shop" / "warehouse.yaml").write_text("tables:\n  Nope: a.b.c\n")
    monkeypatch.setenv("TF_BUILD", "True")
    monkeypatch.setenv("BUILD_BUILDNUMBER", "20261023.4")
    monkeypatch.setenv("BUILD_SOURCEVERSION", "0123456789abcdef")
    ok, report = bici.run_ci(tmp_path / "models", tmp_path / "public")
    v = json.loads((tmp_path / "public" / "version.json").read_text())
    assert not ok and "**Not captured**" in report and "no table 'Nope'" in report
    assert v["version"] == "build 20261023.4" and v["commit"] == "01234567"
    with pytest.raises(LayerError, match="GitLab release"):
        bici.publish(tmp_path / "public")


class FakeGitLab:
    def __init__(self, notes=(), issues=(), tags=()):
        self.calls, self.notes, self.issues, self.tags = [], list(notes), list(issues), set(tags)

    def __call__(self, path, method="GET", data=None, raw=False):
        import urllib.error
        self.calls.append((method, path.split("?")[0], data))
        if method == "GET" and path.endswith("/notes?per_page=100&sort=desc&order_by=updated_at"):
            return self.notes
        if method == "GET" and "/issues?" in path:
            return self.issues
        if method == "GET" and ("/releases/" in path or "/repository/tags/" in path):
            if path.rsplit("/", 1)[1] in self.tags:
                return {}
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)
        return {"id": 99, "web_url": "http://gl/issues/1", "iid": 1}


def test_the_merge_request_note_is_edited_not_reposted(monkeypatch):
    monkeypatch.setenv("CI_PROJECT_ID", "16")
    monkeypatch.setenv("CI_MERGE_REQUEST_IID", "3")
    gl = FakeGitLab()
    monkeypatch.setattr(bici, "_api", gl)
    assert bici.note_on_merge_request("# Power BI capture") == "posted note 99"
    assert gl.calls[-1][0] == "POST" and bici.NOTE_MARK in gl.calls[-1][2]["body"]
    gl.notes = [{"id": 5, "body": "a human's comment"}, {"id": 7, "body": "# old\n" + bici.NOTE_MARK}]
    assert bici.note_on_merge_request("# Power BI capture v2") == "updated note 7"
    assert gl.calls[-1][:2] == ("PUT", "/projects/16/merge_requests/3/notes/7")


def test_publish_refuses_previews_failures_and_existing_tags(tmp_path, monkeypatch):
    for k, v in {"GITLAB_CI": "true", "CI_PROJECT_ID": "16", "CI_API_V4_URL": "http://gl/api/v4",
                 "CI_COMMIT_SHA": "abc"}.items():
        monkeypatch.setenv(k, v)
    (tmp_path / "report.md").write_text("# report")
    gl = FakeGitLab(tags={"capture-v3"})
    monkeypatch.setattr(bici, "_api", gl)
    for version, ok, msg in [("preview of !4", True, "not a release"), ("v4", False, "did not verify"),
                             ("v3", True, "capture-v3 already exists")]:
        (tmp_path / "version.json").write_text(json.dumps({"version": version, "ok": ok, "job": 12}))
        with pytest.raises(LayerError, match=msg):
            bici.publish(tmp_path)
    (tmp_path / "version.json").write_text(json.dumps({"version": "v4", "ok": True, "job": 12}))
    assert bici.publish(tmp_path) == "capture-v4"
    method, path, data = gl.calls[-1]
    assert (method, path) == ("POST", "/projects/16/releases")
    assert data["assets"]["links"][0]["url"] == "http://gl/api/v4/projects/16/jobs/12/artifacts"


def test_drift_finds_missing_tables_columns_and_changed_types(model, tmp_path):
    src = tmp_path / "models" / "shop"
    write_tmdl(model, src)
    (src / "warehouse.yaml").write_text(WAREHOUSE_YAML)
    out = tmp_path / "public" / "shop"
    bici.build_model(src, out)
    _warehouse(out / "pilot.duckdb")
    ok, report = bici.run_drift(tmp_path / "public", prefer="local")
    assert ok and "- no drift" in report and "2 tables, 5 columns" in report
    (out / "pilot.duckdb").unlink()
    _warehouse(out / "pilot.duckdb", sales="StoreID INT, amount BIGINT, Status INT", store=False)
    ok, report = bici.run_drift(out, prefer="local")
    assert not ok and report.startswith("# Warehouse drift: found")
    assert "no column `net_amount` (model column Sales[Amount])" in report
    assert "`Status` is INTEGER; the model reads Sales[Status] as string" in report
    assert "`Store` (its SQL in warehouse.yaml): not readable" in report


def test_drift_without_a_warehouse_fails_and_types_group_by_kind(model, tmp_path):
    write_tmdl(model, tmp_path / "models" / "shop")
    ok, report = bici.run_drift(tmp_path / "models", prefer="local")  # TMDL only: nothing to check against
    assert not ok and "no data to run on" in report
    assert [bici._family(t) for t in ("BIGINT", "decimal(18,2)", "STRING", "timestamp_ntz", "TIMESTAMP WITH TIME ZONE",
                                      "boolean", "map<string,int>")] == \
        ["number", "number", "string", "datetime", "datetime", "boolean", None]


def test_drift_issue_opens_updates_and_closes(monkeypatch):
    monkeypatch.setenv("CI_PROJECT_ID", "16")
    assert "set GITLAB_TOKEN" in bici.drift_issue(False, "# drift")
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    gl = FakeGitLab()
    monkeypatch.setattr(bici, "_api", gl)
    assert bici.drift_issue(False, "# drift") == "opened http://gl/issues/1"
    assert gl.calls[-1][2]["labels"] == bici.DRIFT_LABEL
    gl.issues = [{"iid": 1, "web_url": "http://gl/issues/1"}]
    assert bici.drift_issue(False, "# drift again") == "updated http://gl/issues/1"
    assert bici.drift_issue(True, "# none") == "closed http://gl/issues/1"
    assert gl.calls[-1] == ("PUT", "/projects/16/issues/1", {"state_event": "close"})


def test_ci_says_loudly_when_a_model_with_roles_has_no_access_yaml(model, tmp_path):
    from insurance_context.pbimodel import Role
    model.roles = [Role("Online", tables={"Store": '[Type] = "Online"'})]
    write_tmdl(model, tmp_path / "models" / "shop")
    _, report = bici.run_ci(tmp_path / "models", tmp_path / "public")
    assert "**No access.yaml: every caller will be denied**" in report
    (tmp_path / "models" / "shop" / "access.yaml").write_text("roles:\n  Online: [ana@shop.com]\n")
    _, report = bici.run_ci(tmp_path / "models", tmp_path / "public2")
    assert "members for 1 of them from access.yaml" in report
    assert BILayer.load(tmp_path / "public2" / "shop").spec["security"]["members"] == {"Online": ["ana@shop.com"]}


def test_a_merge_request_diffs_against_the_newest_release_not_the_newest_main_pipeline(monkeypatch):
    """A nightly drift pipeline on main is newer than the last capture and has no capture job: the
    previous capture is the newest capture-v* release's own artifacts."""
    import io
    import zipfile
    from pathlib import Path
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("public/shop/capture.yaml", "metrics: {net_sales: {label: Net Sales, status: captured}}\n")
    calls = []

    def gl(path, method="GET", data=None, raw=False):
        calls.append(path)
        if path.endswith("/releases?per_page=100"):
            return [{"tag_name": "capture-v2"}, {"tag_name": "capture-v10"}, {"tag_name": "nightly"}]
        if path.endswith("/releases/capture-v10"):
            return {"assets": {"links": [{"name": "capture", "url": "http://gl/p/-/jobs/61/artifacts/browse"}]}}
        if path.endswith("/jobs/61/artifacts"):
            return buf.getvalue()
        raise AssertionError(f"unexpected call {path}")

    monkeypatch.setenv("CI_PROJECT_ID", "16")
    monkeypatch.setattr(bici, "_api", gl)
    zf = bici.published_files()
    assert bici._previous(zf, Path("public"), "shop")["metrics"]["net_sales"]["status"] == "captured"
    assert not any("jobs/artifacts/" in c for c in calls)  # never "latest artifacts on main"
    monkeypatch.setattr(bici, "_api", lambda path, *a, **k: [] if path.endswith("/releases?per_page=100") else
                        pytest.fail(path))
    assert bici.published_files() is None  # before the first release: a first capture
