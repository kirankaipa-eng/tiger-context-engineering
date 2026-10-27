"""The tools and CI around capture contract v2: ranks, scope switches, what-if parameters and
display-only measures, as list_metrics shows them, compute takes them, verify runs them and the
report and MR diff count them. The compiler side is stood in by a small fake layer (needs + a
compile that records what it was asked), so these hold before and after the compiler lands."""

import copy
import json

import duckdb
import pytest
import yaml

pytest.importorskip("jwt", reason="needs the mcp extra: uv sync --extra mcp")
pytest.importorskip("mcp", reason="needs the mcp extra: uv sync --extra mcp")

from insurance_context import bici
from insurance_context.bi_agent import BIToolkit, metric_needs, needs
from insurance_context.bilayer import BILayer, LayerError, save
from insurance_context.biwarehouse import Warehouse

from test_bi_mcp import ME, _text, call, release  # noqa: F401 - fixture

DATE = "Calendar[Date]"
PARAMS = {"return_rate": {"label": "% Return Rate", "table": "% Return Rate", "column": "% Return Rate",
                          "min": 0, "max": 0.2, "step": 0.01, "default": 0.05},
          "growth": {"label": "Growth", "table": "Growth", "column": "Growth", "min": 0, "max": 1, "step": 0.1,
                     "default": None}}


def _spec() -> dict:
    leaf = {"status": "captured", "table": "Sales", "agg": "sum", "expr": "{Sales[Amount]}"}
    return {
        "model": "shop", "warehouse": {"dialect": "databricks", "catalog": "pilot", "schema": "shop"},
        "tables": {"Sales": {"table": "pilot.shop.sales", "columns": {
                       "StoreID": {"column": "StoreID", "type": "int64"}, "Amount": {"column": "Amount", "type": "int64"},
                       "Date": {"column": "Date", "type": "dateTime"}}},
                   "Store": {"table": "pilot.shop.store", "columns": {
                       "StoreID": {"column": "StoreID", "type": "int64"}, "Store": {"column": "Store", "type": "string"}}}},
        "joins": [{"from": "Sales[StoreID]", "to": "Store[StoreID]", "many_to_one": True, "one_to_one": False}],
        "parameters": copy.deepcopy(PARAMS),
        "metrics": {
            "net_sales": {**leaf, "label": "Net Sales", "dax": "SUM(Sales[Amount])"},
            "orders": {"status": "captured", "table": "Sales", "agg": "count", "expr": "*", "label": "Orders",
                       "dax": "COUNTROWS(Sales)"},
            "store_top_n": {"status": "captured", "label": "Store Top N", "dax": "RANKX(ALL(Store[Store]), [Net Sales])",
                            "rank": {"over": "Store[Store]", "of": "net_sales", "order": "desc", "ties": "skip"}},
            "sales_after_returns": {"status": "captured", "label": "Sales After Returns",
                                    "formula": "{net_sales} * (1 - {$return_rate})",
                                    "dax": "[Net Sales] * (1 - [% Return Rate Value])"},
            "sales_grown": {"status": "captured", "label": "Sales Grown", "formula": "{net_sales} * (1 + {$growth})",
                            "dax": "[Net Sales] * (1 + SELECTEDVALUE(Growth[Growth]))"},
            "sales_pm": {**leaf, "label": "Sales PM", "dax": "CALCULATE([Net Sales], PREVIOUSMONTH(...))",
                         "time": {"date": DATE, "shift": -1, "unit": "month", "grain": "month"}},
            "scoped": {"status": "captured", "label": "Scoped", "dax": "SWITCH(TRUE(), ISINSCOPE(Store[Store]), ...)",
                       "by_scope": [{"column": "Store[Store]", "metric": "net_sales"}]},
            "net_sales_label": {"status": "presentation", "label": "Net Sales Label", "value": "net_sales",
                                "why": "formats a value as text", "dax": 'FORMAT([Net Sales], "#,0")'},
            "kpi_arrow": {"status": "presentation", "label": "KPI Arrow", "value": "kpi_arrow_value",
                          "why": "formats a value as text", "dax": 'IF([x] > 0, "▲", "▼") & FORMAT(ABS([x]))'},
            "kpi_arrow_value": {"status": "captured", "label": "KPI Arrow (value)", "formula": "{net_sales} - {orders}"},
            "logo": {"status": "presentation", "label": "Logo", "why": "returns a constant for display",
                     "dax": '"https://x/logo.png"'},
            "association": {"status": "not_captured", "label": "Association",
                            "why": "a sort key glued from text", "dax": "VALUE(CONCATENATE(...))"},
        }}


NEEDS = {"store_top_n": {"by": ["Store[Store]"]}, "sales_after_returns": {"params": {"return_rate": {}}},
         "sales_grown": {"params": {"growth": {}}}, "sales_pm": {"by": [f"{DATE}:month"]}}
VALUES = {"net_sales": 550, "orders": 3, "kpi_arrow_value": 547}


class FakeLayer(BILayer):
    """BILayer with the contract's needs() and a compile() that records each call and answers canned rows."""

    def __init__(self, spec):
        super().__init__(spec)
        self.calls = []

    def needs(self, key):
        n = NEEDS.get(key, {})
        return {"by": n.get("by", []), "params": {p: dict(PARAMS[p]) for p in n.get("params", {})}}

    def compile(self, metrics, by=None, where=None, top=None, order_by=None, ascending=False, dialect="duckdb",
                row_filters=None, params=None):
        self.calls.append({"metrics": list(metrics), "by": list(by or []), "params": params})
        p = {k: v["default"] for k, v in PARAMS.items()} | (params or {})
        if by == ["Store[Store]"]:
            return ('SELECT * FROM (VALUES (\'North\', 1), (\'South\', 2)) t("Store[Store]", ' + metrics[0] + ")")
        if by == [f"{DATE}:month"]:
            return f'SELECT * FROM (VALUES (\'2026-01\', NULL), (\'2026-02\', 300)) t("{DATE}:month", {metrics[0]})'
        if p.get("growth") is None and "sales_grown" in metrics:
            raise LayerError("Sales Grown reads Growth (growth): 0 to 1 step 0.1, no default: pass params")
        vals = VALUES | {"sales_after_returns": 550 * (1 - p["return_rate"]),
                         "sales_grown": 550 * (1 + (p.get("growth") or 0))}
        return "SELECT " + ", ".join(f"{vals[k]} AS {k}" for k in metrics)


@pytest.fixture
def kit(tmp_path):
    duckdb.connect(str(tmp_path / "pilot.duckdb")).close()
    return BIToolkit(FakeLayer(_spec()), Warehouse("local", str(tmp_path / "pilot.duckdb")))


def test_list_metrics_shows_needs_parameters_and_display_only(kit):
    out = kit.list_metrics()
    assert ("- store_top_n (Store Top N): rank of each Store[Store] by net_sales (highest = 1) [needs by=Store[Store]]"
            in out)
    assert ("- sales_after_returns (Sales After Returns): formula {net_sales} * (1 - {$return_rate}) "
            "[what-if % Return Rate (return_rate): 0 to 0.2 step 0.01, default 0.05]") in out
    assert "[what-if Growth (growth): 0 to 1 step 0.1, no default: must be given]" in out
    assert "- sales_pm (Sales PM): sum({Sales[Amount]}), Calendar[Date] shifted -1 month [needs by=Calendar[Date]:month]" \
        in out
    assert "- scoped (Scoped): depends on the grouping: by Store[Store]: net_sales; otherwise not answered" in out
    assert "- net_sales (Net Sales): sum({Sales[Amount]}) (shown as text by Net Sales Label)" in out
    display = out.split("Display-only measures")[1].split("\n\n")[0]
    assert "- Net Sales Label: shows net_sales as text" in display
    assert "- KPI Arrow: shows kpi_arrow_value as text" in display
    assert "- Logo: returns a constant for display" in display and "Association" not in display
    assert "- Association: a sort key glued from text" in out.split("not captured, and why")[1]
    assert "What-if parameters (compute params={key: value}" in out


def test_needs_falls_back_to_time_by_rank_and_param_shapes_without_the_compilers_needs():
    class Old(BILayer):
        needs = property(lambda self: (_ for _ in ()).throw(AttributeError))  # a compiler before needs()
    layer = Old(_spec())
    assert not hasattr(layer, "needs")
    assert metric_needs(layer, "store_top_n") == {"by": ["Store[Store]"], "params": {}}
    assert metric_needs(layer, "sales_after_returns")["params"] == {"return_rate": PARAMS["return_rate"]}
    assert metric_needs(layer, "sales_pm")["by"] == [f"{DATE}:month"]
    assert needs(layer, "net_sales") == ""


def test_compute_passes_params_checks_their_range_and_says_which_value_it_used(kit):
    out = kit.compute(["sales_after_returns"], params={"% Return Rate": 0.1})
    assert kit.layer.calls[-1]["params"] == {"return_rate": 0.1} and "| 495 |" in out
    assert "What-if: % Return Rate = 0.1 (as asked)." in out
    out = kit.compute(["sales_after_returns"])
    assert kit.layer.calls[-1]["params"] is None  # no argument: the compiler applies the default
    assert "% Return Rate = 0.05 (the report's default; pass params={'return_rate': ...} to change it)" in out
    assert "outside the range the report offers" in kit.compute(["sales_after_returns"], params={"return_rate": 0.5})
    bad = kit.compute(["net_sales"], params={"discount": 0.1})
    assert bad.startswith("Error: no what-if parameter 'discount'") and "% Return Rate (return_rate)" in bad
    assert "is not a number" in kit.compute(["sales_after_returns"], params={"return_rate": "lots"})
    ranked = kit.compute(["store_top_n"], by=["Store[Store]"])
    assert "| North | 1 |" in ranked and "rank of each Store[Store] by net_sales" in ranked


def test_verify_runs_each_metric_as_it_must_be_asked_and_reports_the_values_used(kit):
    rows = {r["metric"]: r for r in bici.verify(kit.layer, ".", [kit.wh])}
    assert all(r["ok"] for r in rows.values()), [r for r in rows.values() if not r["ok"]]
    rank = rows["store_top_n"]
    assert rank["by"] == ["Store[Store]"] and rank["local"] == 1 and rank["at"] == {
        "latest": False, "row": {"Store[Store]": "North"}}
    assert bici._used(rank) == "at Store[Store] = North"
    assert rows["sales_pm"]["local"] == 300 and bici._used(rows["sales_pm"]) == "latest Calendar[Date]:month = 2026-02"
    assert rows["sales_after_returns"]["params"]["return_rate"] == {"value": 0.05, "chosen": "default",
                                                                    "label": "% Return Rate"}
    assert rows["sales_grown"]["params"]["growth"]["value"] == 0.5  # no default: the midpoint, on a step
    assert bici._used(rows["sales_grown"]) == "Growth = 0.5 (midpoint, no default)"
    assert rows["sales_grown"]["local"] == pytest.approx(825)
    assert rows["scoped"]["by"] == ["Store[Store]"]  # a scope switch with no else: asked by its column
    flat = [c for c in kit.layer.calls if not c["by"] and len(c["metrics"]) > 1][-1]
    assert flat["params"] == {"return_rate": 0.05, "growth": 0.5}
    assert bici.param_value({"min": 1, "max": 10, "step": 1}) == (6, "midpoint, no default")
    assert bici.param_value({"min": 0, "max": 0.2, "step": 0.03})[0] == 0.09


def test_counts_and_mr_diff_tell_display_only_from_not_captured():
    spec = _spec()
    c = bici.measure_counts(spec)
    assert c == {"measures": 11, "captured": 7, "presentation": 3, "not_captured": 1, "presentation_with_value": 2}
    assert bici.counts_line(c) == "11 measures: 7 captured, 3 display-only (2 show a captured number), 1 not captured"
    assert bici.counts_line(c | {"presentation_with_value": 1}).endswith("(1 shows a captured number), 1 not captured")
    old = _spec()
    old["metrics"]["net_sales_label"] = {"status": "not_captured", "label": "Net Sales Label", "why": "FORMAT",
                                         "dax": old["metrics"]["net_sales_label"]["dax"]}
    old["metrics"]["logo"]["status"] = "not_captured"
    del old["parameters"]["growth"]
    old["parameters"]["return_rate"] = {**PARAMS["return_rate"], "default": 0.1}
    lines = bici.diff(old, spec)
    assert "~ metric `net_sales_label`: not captured -> display-only, shows `net_sales`" in "\n".join(lines)
    assert "~ metric `logo`: not captured -> display-only" in "\n".join(lines)
    assert "+ what-if parameter Growth (growth): 0 to 1 step 0.1, no default: must be given" in lines
    assert ("~ what-if parameter % Return Rate (return_rate): 0 to 0.2 step 0.01, default 0.05 "
            "(was 0 to 0.2 step 0.01, default 0.1)") in lines
    added = bici.diff({**old, "metrics": {}}, spec)
    assert "+ metric `kpi_arrow` (display-only, shows `kpi_arrow_value`)" in "\n".join(added)


def test_ci_report_counts_measures_for_a_business_reader(tmp_path, monkeypatch):
    spec = _spec()
    keep = ("net_sales", "orders", "net_sales_label", "kpi_arrow", "kpi_arrow_value", "logo", "association")
    spec["metrics"] = {k: v for k, v in spec["metrics"].items() if k in keep}

    def build_model(src, out, catalog="pilot"):
        out.mkdir(parents=True, exist_ok=True)
        save(spec | {"source": "TMDL in the repo"}, out)
        return yaml.safe_load((out / "capture.yaml").read_text())
    monkeypatch.setattr(bici, "build_model", build_model)
    for k in ("GITLAB_CI", "TF_BUILD", "DATABRICKS_SERVER_HOSTNAME"):
        monkeypatch.delenv(k, raising=False)
    (tmp_path / "models" / "shop").mkdir(parents=True)
    ok, report = bici.run_ci(tmp_path / "models", tmp_path / "public")
    assert "6 measures: **2 captured**, 3 display-only (2 show a captured number), 1 not captured." in report
    assert "- Net Sales Label: shows `net_sales` as text" in report
    assert "- Logo: no captured number behind it (returns a constant for display)" in report
    assert "**Not captured** (agents cannot compute these)" in report and "- Association: a sort key" in report
    assert "% Return Rate (return_rate): 0 to 0.2 step 0.01, default 0.05" in report
    v = json.loads((tmp_path / "public" / "version.json").read_text())["models"]["shop"]
    assert (v["measures"], v["presentation"], v["presentation_with_value"]) == (6, 3, 2)


def test_mcp_and_cli_pass_params_and_audit_them(release, tmp_path, monkeypatch, capsys):  # noqa: F811
    from insurance_context.bi_mcp import Audit, LocalCaptures, Served, build_server
    from insurance_context.cli import main
    path = release / "shop" / "capture.yaml"
    spec = yaml.safe_load(path.read_text())
    save(spec | {"parameters": PARAMS}, release / "shop")
    seen, real = [], BILayer.compile

    def compile(self, metrics, *a, params=None, **kw):  # the real SQL, with the params it was handed recorded
        seen.append(params)
        return real(self, metrics, *a, **kw)
    monkeypatch.setattr(BILayer, "compile", compile)
    log = tmp_path / "audit.jsonl"
    server = build_server(Served(LocalCaptures(release), prefer="local"), principal=ME, audit=Audit(log))
    res, bad, listed = call(server, ("compute", {"metrics": ["net_sales"], "params": {"return_rate": 0.1},
                                                 "model": "shop"}),
                            ("compute", {"metrics": ["net_sales"], "params": {"return_rate": 2}, "model": "shop"}),
                            ("list_metrics", {"model": "shop"}))
    assert seen == [{"return_rate": 0.1}] and "| 550 |" in _text(res)
    assert "outside the range" in _text(bad)
    assert "% Return Rate (return_rate): 0 to 0.2 step 0.01, default 0.05" in _text(listed)
    recs = [json.loads(x) for x in log.read_text().splitlines()]
    assert recs[0]["args"]["params"] == {"return_rate": 0.1} and recs[1]["outcome"] == "error"
    assert main(["pbi", "query", str(release / "shop"), "-m", "net_sales", "--param", "return_rate=0.15",
                 "--target", "local"]) == 0
    assert seen[-1] == {"return_rate": 0.15} and "| 550 |" in capsys.readouterr().out
