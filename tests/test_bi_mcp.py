"""The metrics MCP server: several models per release, reloads, API key and JWT auth over real HTTP,
row-level security per caller, audit lines, limits, and the Databricks connection (a fake connector)."""

import asyncio
import hashlib
import json
import os
import socket
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anyio
import duckdb
import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.memory import create_connected_server_and_client_session

from insurance_context import bi_mcp
from insurance_context.bi_mcp import OIDC, ApiKeys, Audit, LocalCaptures, Served, Verifier, build_server, keygen
from insurance_context.bilayer import BILayer, LayerError, build_spec, save
from insurance_context.bisecurity import AccessDenied, Principal
from insurance_context.biwarehouse import Warehouse
from insurance_context.pbimodel import Column, Measure, Model, Relationship, Table

ME = Principal("me@corp.com", ("analysts",))


def _model(name: str) -> Model:
    sales = Table("Sales", [Column("StoreID", "int64", "StoreID"), Column("Status"),
                            Column("Amount", "int64", "Amount"), Column("Date", "dateTime", "Date")],
                  [Measure("Net Sales", 'CALCULATE(SUM(Sales[Amount]), Sales[Status] = "Sold")'),
                   Measure("Orders", "COUNTROWS(Sales)"),
                   Measure("Store Rank", "RANKX(ALL(Store[Store]), [Net Sales])")],
                  'let\n    Source = Sql.Database("x")\nin\n    Source', "m")
    store = Table("Store", [Column("StoreID", "int64", "StoreID"), Column("Store", "string", "Store")],
                  source="let x = 1 in x", source_kind="m")
    cal = Table("Calendar", [Column("Date", "dateTime", "Date")], source="let x = 1 in x", source_kind="m")
    return Model(name, {t.name: t for t in (sales, store, cal)}, [
        Relationship("Sales", "StoreID", "Store", "StoreID"), Relationship("Sales", "Date", "Calendar", "Date")])


def _write_model(folder, name: str, amounts: list[int]) -> None:
    folder.mkdir(parents=True)
    con = duckdb.connect(str(folder / "pilot.duckdb"))
    con.execute(f"CREATE SCHEMA {name}")
    rows = ", ".join(f"({1 + i % 2}, 'Sold', {a}, DATE '2026-0{1 + i % 2}-05')" for i, a in enumerate(amounts))
    con.execute(f"CREATE TABLE {name}.sales AS SELECT * FROM (VALUES {rows}) t(StoreID, Status, Amount, Date)")
    con.execute(f"CREATE TABLE {name}.store AS SELECT * FROM (VALUES (1, 'North'), (2, 'South')) t(StoreID, Store)")
    con.execute(f"CREATE TABLE {name}.calendar AS SELECT DISTINCT Date FROM {name}.sales")
    con.close()
    save(build_spec(_model(name), "pilot", name), folder)


@pytest.fixture
def release(tmp_path):
    """A folder `ictx pbi ci` would write: two models and version.json."""
    out = tmp_path / "public"
    _write_model(out / "shop", "shop", [300, 50, 200])      # North 500, South 50
    _write_model(out / "east", "east", [10, 20])
    (out / "version.json").write_text(json.dumps({"version": "v7", "commit": "abc1234", "models": {}}))
    return out


def _text(res) -> str:
    return "\n".join(c.text for c in res.content)


async def _calls(server, calls):
    async with create_connected_server_and_client_session(server._mcp_server) as s:
        return [await s.call_tool(name, args) for name, args in calls]


def call(server, *calls):
    return anyio.run(_calls, server, list(calls))


def test_serves_every_model_and_asks_which_when_there_are_several(release, tmp_path):
    served = Served(LocalCaptures(release), prefer="local")
    server = build_server(served, principal=ME, audit=Audit(tmp_path / "audit.jsonl"))
    models, nomodel, shop, east, bad = call(
        server, ("list_models", {}), ("compute", {"metrics": ["net_sales"]}),
        ("compute", {"metrics": ["net_sales"], "by": ["Store[Store]"], "model": "shop"}),
        ("compute", {"metrics": ["net_sales"], "model": "east"}), ("list_metrics", {"model": "west"}))
    got = json.loads(_text(models))
    assert got["version"] == "v7" and [m["name"] for m in got["models"]] == ["east", "shop"]
    shop_counts = got["models"][1]
    # Store Rank (RANKX) is captured once the rank capture lands; until then it is not captured
    assert shop_counts["captured"] + shop_counts["not_captured"] == 3 and shop_counts["captured"] >= 2
    assert {k: shop_counts[k] for k in ("name", "measures", "presentation", "presentation_with_value", "warehouse")} \
        == {"name": "shop", "measures": 3, "presentation": 0, "presentation_with_value": 0, "warehouse": "local"}
    assert shop_counts["summary"] == (f"3 measures: {shop_counts['captured']} captured, 0 display-only, "
                                      f"{shop_counts['not_captured']} not captured")
    assert nomodel.isError and "2 models are served; pass model= one of: east, shop" in _text(nomodel)
    assert _text(shop).startswith("[shop capture v7 · commit abc1234 · local]")
    assert "| North | 500 |" in _text(shop) and "| South | 50 |" in _text(shop)
    assert "| 30 |" in _text(east)
    assert bad.isError and "no model 'west' in capture v7; served: east, shop" in _text(bad)
    # --model narrows what is served, so no model= is needed
    one = build_server(Served(LocalCaptures(release), model=["east"], prefer="local"), principal=ME)
    (res,) = call(one, ("compute", {"metrics": ["orders"]}))
    assert not res.isError and "[east capture v7" in _text(res)


def test_a_failed_reload_keeps_serving_the_previous_release(release, tmp_path):
    served = Served(LocalCaptures(release), refresh=0, prefer="local")
    server = build_server(served, principal=ME, audit=Audit(tmp_path / "a.jsonl"))
    good = (release / "east" / "capture.yaml").read_text()
    (release / "east" / "capture.yaml").write_text("metrics: [unclosed\n")
    (release / "version.json").write_text(json.dumps({"version": "v8", "commit": "def5678"}))
    res, ver = call(server, ("compute", {"metrics": ["net_sales"], "model": "shop"}),
                    ("model_version", {"model": "shop"}))
    assert _text(res).startswith("[shop capture v7") and "still serving v7" in _text(res) and "| 550 |" in _text(res)
    assert "still serving v7" in json.loads(_text(ver))["reload_error"]
    (release / "east" / "capture.yaml").write_text(good)
    os.utime(release / "version.json")
    res, ver = call(server, ("compute", {"metrics": ["net_sales"], "model": "east"}),
                    ("model_version", {"model": "shop"}))
    assert _text(res).startswith("[east capture v8 · commit def5678") and "still serving" not in _text(res)
    assert json.loads(_text(ver))["version"] == "v8"
    # the first load has nothing to fall back on, so it fails loudly
    (release / "east" / "capture.yaml").write_text("metrics: [unclosed\n")
    with pytest.raises(Exception):
        Served(LocalCaptures(release), prefer="local")


def test_each_call_runs_under_the_callers_row_filters_and_denial_returns_no_data(release, tmp_path, monkeypatch):
    seen = []

    def filters_for(spec, principal):
        seen.append(principal)
        if "auditors" in principal.groups:
            raise AccessDenied(f"{principal.id} is in no role of this model")
        return {"Store": "{Store[Store]} = 'North'"}
    monkeypatch.setattr(bi_mcp, "filters_for", filters_for)
    served = Served(LocalCaptures(release), prefer="local")
    log = tmp_path / "audit.jsonl"
    (res,) = call(build_server(served, principal=ME, audit=Audit(log)), ("compute", {"metrics": ["net_sales"],
                                                                                    "model": "shop"}))
    assert "| 500 |" in _text(res) and "North" in _text(res) and seen == [ME]
    who = Principal("eve@corp.com", ("auditors",))
    (res,) = call(build_server(served, principal=who, audit=Audit(log)), ("compute", {"metrics": ["net_sales"],
                                                                                     "model": "shop"}))
    assert res.isError and "Access denied: eve@corp.com is in no role" in _text(res)
    assert "500" not in _text(res) and "SELECT" not in _text(res)
    denied = json.loads(log.read_text().splitlines()[-1])
    assert (denied["principal"], denied["outcome"], denied["sql_sha256"]) == ("eve@corp.com", "denied", None)


def test_audit_lines_describe_each_call(release, tmp_path):
    log = tmp_path / "audit.jsonl"
    served = Served(LocalCaptures(release), prefer="local")
    res, err = call(build_server(served, principal=ME, audit=Audit(log)),
                    ("compute", {"metrics": ["net_sales"], "by": ["Store[Store]"], "model": "shop"}),
                    ("compute", {"metrics": ["nope"], "model": "shop"}))
    ok, bad = [json.loads(x) for x in log.read_text().splitlines()]
    sql = _text(res).split("SQL (duckdb):\n", 1)[1]
    assert {k: ok[k] for k in ("principal", "groups", "model", "version", "tool", "rows", "outcome", "error")} == \
        {"principal": "me@corp.com", "groups": ["analysts"], "model": "shop", "version": "v7", "tool": "compute",
         "rows": 2, "outcome": "ok", "error": None}
    assert ok["sql_sha256"] == hashlib.sha256(sql.encode()).hexdigest()
    assert ok["args"]["by"] == ["Store[Store]"] and ok["ms"] >= 0 and ok["ts"].endswith("+00:00")
    assert bad["outcome"] == "error" and "nope" in bad["error"]


def test_limits_on_rows_top_concurrency_and_statement_time(release, tmp_path, monkeypatch):
    served = Served(LocalCaptures(release), prefer="local")
    server = build_server(served, principal=ME, audit=Audit(tmp_path / "a.jsonl"), max_rows=1, max_top=2)
    rows, top = call(server, ("compute", {"metrics": ["net_sales"], "by": ["Store[Store]"], "model": "shop"}),
                     ("compute", {"metrics": ["net_sales"], "by": ["Store[Store]"], "top": 5, "model": "shop"}))
    assert "more than 1 rows; first 1 shown" in _text(rows) and "South" not in _text(rows)
    assert "top is capped at 2" in _text(top)

    real = Warehouse.query

    def slow(self, sql, limit=None):
        time.sleep(0.6)
        return real(self, sql, limit)
    monkeypatch.setattr(Warehouse, "query", slow)
    busy = build_server(served, principal=ME, audit=Audit(tmp_path / "b.jsonl"), max_concurrent=1, timeout=0.2)

    async def both():
        async with create_connected_server_and_client_session(busy._mcp_server) as s:
            return await asyncio.gather(*[s.call_tool("compute", {"metrics": ["orders"], "model": "east"})
                                          for _ in range(2)])
    got = anyio.run(both)
    assert sorted(r.isError for r in got) == [False, True]
    assert any("server is busy (1 queries running)" in _text(r) for r in got)
    monkeypatch.setattr(Warehouse, "query", real)

    wh = Warehouse("local", str(release / "shop" / "pilot.duckdb"), timeout=0.2)
    t0 = time.monotonic()
    with pytest.raises(LayerError, match="cancelled after the 0.2s timeout"):
        wh.query("SELECT count(*) FROM range(100000000000) a")
    assert time.monotonic() - t0 < 5 and wh.ping() == (True, "ok")


# ------------------------------------------------------------------ over real HTTP

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def signer():
    """An RSA key, and a JWKS endpoint publishing its public half the way an IdP does."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {"kid": "k1", "use": "sig"}

    class JWKS(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"keys": [jwk]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), JWKS)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def sign(claims: dict, k=key) -> str:
        now = int(time.time())
        return jwt.encode({"iss": "https://login.example/tenant/v2.0", "aud": "api://pbi-metrics", "iat": now,
                           "exp": now + 600, **claims}, k, algorithm="RS256", headers={"kid": "k1"})
    yield sign, f"http://127.0.0.1:{httpd.server_address[1]}/keys"
    httpd.shutdown()


@pytest.fixture
def http(release, tmp_path, signer, monkeypatch):
    """The server on a real port behind API keys and OIDC, with what each call's filters_for saw."""
    import uvicorn
    sign, jwks = signer
    key, line = keygen("claims-bot", ["claims"])
    (tmp_path / "keys.yaml").write_text(line + "\n")
    seen = []
    monkeypatch.setattr(bi_mcp, "filters_for", lambda spec, p: seen.append(p))
    oidc = OIDC("https://login.example/tenant/v2.0", "api://pbi-metrics", jwks)
    port, log = _free_port(), tmp_path / "audit.jsonl"
    server = build_server(Served(LocalCaptures(release), prefer="local"), audit=Audit(log), port=port,
                          verifier=Verifier(ApiKeys(tmp_path / "keys.yaml"), oidc))
    uv = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=uv.run, daemon=True)
    t.start()
    while not uv.started:
        time.sleep(0.02)
    yield types.SimpleNamespace(url=f"http://127.0.0.1:{port}", key=key, sign=sign, seen=seen, log=log)
    uv.should_exit = True
    t.join(5)


async def _over_http(url: str, token: str, calls):
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as hc, \
            streamable_http_client(url + "/mcp", http_client=hc) as (r, w, _), ClientSession(r, w) as s:
        await s.initialize()
        return [await s.call_tool(name, args) for name, args in calls]


def test_http_needs_a_key_and_the_key_names_the_caller(http):
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    hdrs = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(http.url + "/mcp", json=init, headers=hdrs).status_code == 401
    assert httpx.post(http.url + "/mcp", json=init,
                      headers=hdrs | {"Authorization": "Bearer pbik_wrong"}).status_code == 401
    health = httpx.get(http.url + "/healthz")
    assert health.status_code == 200
    assert health.json() | {"reload_error": None} == {"status": "ok", "version": "v7", "models": ["east", "shop"],
                                                      "warehouse": ["local"], "warehouse_ok": True,
                                                      "reload_error": None}
    res, models = anyio.run(_over_http, http.url, http.key,
                            [("compute", {"metrics": ["net_sales"], "model": "shop"}), ("list_models", {})])
    assert "| 550 |" in _text(res) and len(json.loads(_text(models))["models"]) == 2
    assert http.seen == [Principal("claims-bot", ("claims",))]
    log = http.log.read_text()
    assert '"principal": "claims-bot"' in log and http.key not in log


def test_http_accepts_a_signed_jwt_and_refuses_a_bad_one(http):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    good = http.sign({"upn": "ana@corp.com", "groups": ["actuarial", "claims"]})
    (res,) = anyio.run(_over_http, http.url, good, [("list_metrics", {"model": "east"})])
    assert not res.isError and "net_sales" in _text(res)
    assert http.seen == [Principal("ana@corp.com", ("actuarial", "claims"))]
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    hdrs = {"Accept": "application/json, text/event-stream"}
    for bad in (http.sign({"upn": "a@corp.com", "exp": int(time.time()) - 3600}),
                http.sign({"upn": "a@corp.com", "aud": "api://something-else"}),
                http.sign({"upn": "a@corp.com", "iss": "https://evil.example/"}),
                http.sign({"upn": "a@corp.com"}, k=other), http.sign({"sub": "no-name-claim"})):
        r = httpx.post(http.url + "/mcp", json=init, headers=hdrs | {"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401
    assert '"a@corp.com"' not in http.log.read_text() and good not in http.log.read_text()


def test_keys_file_is_reread_so_keys_can_be_added_and_revoked(tmp_path):
    key, line = keygen("bot", ["a", "b"])
    path = tmp_path / "keys.yaml"
    path.write_text("# sha256(key): {principal, groups}\n" + line + "\n")
    keys = ApiKeys(path)
    assert keys.lookup(key) == Principal("bot", ("a", "b")) and keys.lookup(key + "x") is None
    assert key not in path.read_text()
    path.write_text("{}\n")
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 1_000_000))
    assert keys.lookup(key) is None


def test_keygen_and_serve_flags_from_the_cli(capsys):
    from insurance_context.cli import main
    assert main(["pbi", "serve", "keygen", "--principal", "x@corp.com", "--groups", "a,b"]) == 0
    out = capsys.readouterr().out
    key = next(w for w in out.split() if w.startswith("pbik_"))
    assert f'{hashlib.sha256(key.encode()).hexdigest()}: {{"principal": "x@corp.com", "groups": ["a", "b"]}}' in out
    with pytest.raises(ValueError, match="--keys and/or --oidc-issuer"):
        Verifier(None, None)  # HTTP never starts unauthenticated by accident


# ------------------------------------------------------------------ Databricks, through a fake connector

class _FakeDatabricks:
    def __init__(self):
        self.connects, self.oauth, self.fail_next = [], [], None
        sql, sdk, core = (types.ModuleType(n) for n in ("databricks.sql", "databricks.sdk", "databricks.sdk.core"))
        sql.connect = self.connect
        core.Config = lambda **kw: kw
        core.oauth_service_principal = lambda cfg: self.oauth.append(cfg) or "header-factory"
        self.modules = {"databricks": types.ModuleType("databricks"), "databricks.sql": sql,
                        "databricks.sdk": sdk, "databricks.sdk.core": core}
        self.modules["databricks"].sql, self.modules["databricks"].sdk, sdk.core = sql, sdk, core

    def connect(self, **kw):
        self.connects.append(kw)
        fake = self

        class Cursor:
            description = [("x",)]

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def execute(self, sql):
                if fake.fail_next:
                    exc, fake.fail_next = fake.fail_next, None
                    raise exc

            def fetchmany(self, n):
                return [(1,), (2,), (3,)][:n]

            def fetchall(self):
                return [(1,), (2,), (3,)]

        return types.SimpleNamespace(cursor=Cursor, close=lambda: None)


@pytest.fixture
def fake_dbx(monkeypatch):
    fake = _FakeDatabricks()
    for name, mod in fake.modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for k in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET", "DATABRICKS_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    return fake


def test_databricks_uses_a_service_principal_reuses_connections_and_times_out(fake_dbx, monkeypatch):
    monkeypatch.setenv("DATABRICKS_CLIENT_ID", "sp-id")
    monkeypatch.setenv("DATABRICKS_CLIENT_SECRET", "sp-secret")
    monkeypatch.setenv("DATABRICKS_TOKEN", "pat-not-used")
    wh = Warehouse("databricks", "adb-1.azuredatabricks.net", timeout=45)
    assert wh.auth == "oauth-m2m"
    assert wh.query("SELECT x", limit=2) == (["x"], [(1,), (2,)])
    assert wh.query("SELECT x") == (["x"], [(1,), (2,), (3,)]) and wh.ping() == (True, "ok")
    (kw,) = fake_dbx.connects  # one connection, reused
    assert kw["server_hostname"] == "adb-1.azuredatabricks.net" and kw["http_path"] == "/sql/1.0/warehouses/abc"
    assert kw["session_configuration"] == {"STATEMENT_TIMEOUT": "45"} and "access_token" not in kw
    assert kw["credentials_provider"]() == "header-factory"
    assert fake_dbx.oauth == [{"host": "https://adb-1.azuredatabricks.net", "client_id": "sp-id",
                               "client_secret": "sp-secret"}]
    # a stale pooled connection is replaced and the query retried once; a SQL error is not retried
    fake_dbx.fail_next = ConnectionResetError("idle connection dropped")
    assert wh.query("SELECT x", limit=1) == (["x"], [(1,)]) and len(fake_dbx.connects) == 2
    ServerOperationError = type("ServerOperationError", (Exception,), {})
    fake_dbx.fail_next = ServerOperationError("[UNRESOLVED_COLUMN] y")
    with pytest.raises(ServerOperationError):
        wh.query("SELECT y")
    assert len(fake_dbx.connects) == 2


def test_databricks_falls_back_to_a_pat_and_says_what_is_missing(fake_dbx, monkeypatch):
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-123")
    wh = Warehouse("databricks", "adb-1.azuredatabricks.net")
    assert wh.auth == "pat" and wh.query("SELECT 1")[1][0] == (1,)
    assert fake_dbx.connects[0]["access_token"] == "dapi-123" and "session_configuration" not in fake_dbx.connects[0]
    monkeypatch.delenv("DATABRICKS_TOKEN")
    ok, why = Warehouse("databricks", "adb-1.azuredatabricks.net").ping()
    assert not ok and "DATABRICKS_CLIENT_ID" in why


def test_served_models_share_one_databricks_pool(release, fake_dbx, monkeypatch):
    monkeypatch.setenv("DATABRICKS_SERVER_HOSTNAME", "adb-1.azuredatabricks.net")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-123")
    served = Served(LocalCaptures(release), timeout=30)
    (_, a), (_, b) = served.release.models.values()
    assert a is b and a.kind == "databricks" and a.timeout == 30
    layer = BILayer.load(release / "shop")
    assert "`pilot`.`shop`.`sales`" in layer.compile(["net_sales"], dialect=a.dialect)
