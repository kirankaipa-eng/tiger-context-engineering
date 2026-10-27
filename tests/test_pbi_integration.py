"""The pieces together: a model's RLS roles (as TMDL) captured, served by the MCP server, enforced per
caller with the real bisecurity.filters_for; and and denial and column values under RLS."""

import json

import pytest
pytest.importorskip("jwt", reason="needs the mcp extra: uv sync --extra mcp")
pytest.importorskip("mcp", reason="needs the mcp extra: uv sync --extra mcp")

from insurance_context.bi_mcp import Audit, LocalCaptures, Served, build_server
from insurance_context.bilayer import build_spec, save
from insurance_context.bisecurity import Principal
from insurance_context.pbimodel import read_tmdl, write_tmdl

from test_bi_mcp import _text, call
from test_bisecurity import model, shop  # noqa: F401 - fixtures


def _release(shop, model, tmp_path):  # noqa: F811
    layer, wh = shop
    write_tmdl(model, tmp_path / "src")
    back = read_tmdl(tmp_path / "src")  # roles go through TMDL, as they would from the models repo
    spec = build_spec(back, "pilot", "shop")
    spec["security"]["members"] = layer.spec["security"]["members"]
    out = tmp_path / "public"
    (out / "shop").mkdir(parents=True)
    save(spec, out / "shop")
    (out / "shop" / "pilot.duckdb").write_bytes(open(wh.target, "rb").read())
    (out / "version.json").write_text(json.dumps({"version": "v3", "commit": "feed123"}))
    return Served(LocalCaptures(out), prefer="local")


def test_each_caller_sees_only_their_rows_through_the_server(shop, model, tmp_path):  # noqa: F811
    served = _release(shop, model, tmp_path)
    log = tmp_path / "audit.jsonl"
    ask = {"metrics": ["net_sales"], "by": ["Store[Store]"]}

    def as_(who):
        (res,) = call(build_server(served, principal=who, audit=Audit(log)), ("compute", ask))
        return _text(res)

    retail = as_(Principal("x@shop.com", ("Retail Ops",)))
    assert "| South | 200 |" in retail and "| East | 100 |" in retail and "North" not in retail
    ana = as_(Principal("ANA@shop.com", ("Managers",)))  # dynamic role: rows where Managers[Email] is the caller
    assert "| North | 350 |" in ana and "| East | 100 |" in ana and "South" not in ana
    denied = as_(Principal("stranger@shop.com"))
    assert "Access denied" in denied and "| " not in denied
    (vals,) = call(build_server(served, principal=Principal("x@shop.com", ("Retail Ops",)), audit=Audit(log)),
                   ("column_values", {"column": "Store[Store]"}))
    assert "East, South" in _text(vals) and "North" not in _text(vals)
    outcomes = [json.loads(x)["outcome"] for x in log.read_text().splitlines()]
    assert outcomes == ["ok", "ok", "denied", "ok"]
