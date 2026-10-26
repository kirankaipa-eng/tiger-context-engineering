"""ictx: operate an insurance enterprise context layer.

    ictx clients                                  list client deployments
    ictx check    <client>                        resolve schemas, lint metrics, SQL portability
    ictx fixture  <client>                        build the synthetic reference warehouse
    ictx install  <client> [--warehouse W]        create u_* / ont / ont_agent views
    ictx build    <client>                        export OWL, SHACL, JSON Schema, Pydantic, SQL, R2RML, docs
    ictx sql      <client> --dialect snowflake    print view DDL for a platform
    ictx validate <client> [--warehouse W]        conformance of each unit's data to the ontology
    ictx drift    <client> [--llm] [--write]      detect drift -> verified change proposals
    ictx metrics  <client>                        list governed metrics
    ictx query    <client> -m M1,M2 [--by D] [--where k=v] [--as-of YYYY-MM-DD]
    ictx triangle <client> [-m METRIC] [--where k=v]
    ictx ask      <client> "question"             ask the context agent (needs the agent extra)
    ictx demo     <client>                        fixture + install + build + validate + drift

    Power BI models (.pbix -> TMDL -> captured metrics -> SQL -> Q&A):
    ictx pbi import  <file.pbix> [--out DIR] [--catalog C] [--schema S]
    ictx pbi metrics <dir>
    ictx pbi query   <dir> -m M1,M2 [--by "Product[Category]"] [--where "Store[Type]=External"] [--top N]
                     [--param return_rate=0.1]       what-if parameters (default: the report's default)
    ictx pbi ask     <dir> ["question"]           one question, or an interactive session
    ictx pbi ci      <models> [--out public]       CI: convert, capture, diff, verify each model
    ictx pbi publish [public]                      CI on main: release capture-v<N>
    ictx pbi drift   [public | models] [--release latest] [--issue]   mapped tables/columns still in the warehouse
    ictx pbi serve   --gitlab URL --project P | --dir D   MCP server over the captured metrics
    ictx pbi serve keygen --principal P --groups a,b     an API key for `serve --http --keys`
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

import duckdb
import yaml

from .client import Client, list_clients
from .overlay import REPO_ROOT, OverlayError

FIXTURES = {
    "summit_group": "insurance_context.fixtures.pc_warehouse",
    "acme_health": "insurance_context.fixtures.payer_warehouse",
    "meridian_health": "insurance_context.fixtures.payer_warehouse:build_meridian",
    "northwind_life": "insurance_context.fixtures.la_warehouse:build_northwind",
}


def _warehouse(args) -> Path:
    return Path(args.warehouse) if args.warehouse else REPO_ROOT / "build" / args.client / "warehouse.duckdb"


def _as_of(args, con=None) -> date:
    if getattr(args, "as_of", None):
        return date.fromisoformat(args.as_of)
    if con is not None:
        try:
            return con.execute("SELECT valuation_date FROM main._fixture_meta").fetchone()[0]
        except duckdb.Error:
            pass
    return date.today()


def _where(items: list[str] | None) -> dict:
    out = {}
    for item in items or []:
        k, _, v = item.partition("=")
        values = [x.strip() for x in v.split(",")]
        out[k.strip()] = values if len(values) > 1 else values[0]
    return out


def _table(cols, rows) -> str:
    rows = [["" if v is None else _cell(v) for v in r] for r in rows]
    widths = [max(len(str(c)), *(len(r[i]) for r in rows)) if rows else len(str(c)) for i, c in enumerate(cols)]
    line = "  ".join(str(c).ljust(w) for c, w in zip(cols, widths))
    sep = "  ".join("-" * w for w in widths)
    return "\n".join([line, sep, *("  ".join(v.rjust(w) if _num(v) else v.ljust(w)
                                             for v, w in zip(r, widths)) for r in rows)])


def _cell(v) -> str:
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float) or (hasattr(v, "as_integer_ratio") and not isinstance(v, int)):
        v = float(v)
        return f"{v:.3f}" if abs(v) < 10 else f"{v:,.0f}"
    if isinstance(v, int) and abs(v) >= 10000:
        return f"{v:,}"
    return str(v)


def _num(s: str) -> bool:
    return s.replace(",", "").replace(".", "").replace("-", "").isdigit()


# ------------------------------------------------------------------ commands

def cmd_clients(args):
    for name in list_clients():
        c = Client(name)
        print(f"{name:16s} {c.line:14s} units: {', '.join(u.code for u in c.units):20s} {c.title}")


def cmd_check(args):
    from .semantic import SemanticLayer
    from .sqlgen import DIALECTS, portability_issues
    c = Client(args.client)
    problems = []
    if str(c.config.get("line_version", c.line_version)) != c.line_version:
        problems.append(f"client pins {c.line} {c.config.get('line_version')} but pack is {c.line_version}")
    sv = c.resolved.view
    print(f"group schema: {len(sv.all_classes())} classes, {len(sv.all_slots())} slots, {len(sv.all_enums())} enums")
    for u in c.units:
        u.resolved  # noqa: B018 - resolve to surface overlay errors
        print(f"unit {u.code}: {len(u.mappings.get('classes') or {})} entities mapped")
    sl = SemanticLayer(c)
    problems += sl.validate()
    print(f"metrics: {len(sl.metrics)} definitions, {len(sl.dimensions)} dimensions")
    for d in DIALECTS:
        issues = portability_issues(c, d)
        print(f"sql {d:10s} {'ok' if not issues else f'{len(issues)} portability issue(s)'}")
        problems += issues
    for p in problems:
        print(f"PROBLEM: {p}")
    return 1 if problems else 0


def cmd_fixture(args):
    import importlib
    if args.client not in FIXTURES:
        print(f"No fixture for {args.client}; known: {sorted(FIXTURES)}")
        return 1
    module, _, fn = FIXTURES[args.client].partition(":")
    mod = importlib.import_module(module)
    builder = getattr(mod, fn or "build")
    path = builder(_warehouse(args))
    valuation = getattr(builder, "valuation", None) or getattr(mod, "VALUATION", date.today())
    con = duckdb.connect(str(path))
    con.execute("CREATE OR REPLACE TABLE main._fixture_meta AS SELECT ?::DATE AS valuation_date", [valuation])
    con.close()
    print(f"fixture warehouse: {path} (valuation {valuation})")


def cmd_install(args):
    from .sqlgen import install
    c = Client(args.client)
    con = duckdb.connect(str(_warehouse(args)))
    views = install(c, con)
    by_schema = {}
    for v in views:
        by_schema[v.schema] = by_schema.get(v.schema, 0) + 1
    print("installed views: " + ", ".join(f"{s}={n}" for s, n in by_schema.items()))


def cmd_build(args):
    from .exports import build
    c = Client(args.client)
    out = getattr(args, "out", None)
    m = build(c, Path(out) if out else None)
    print(f"built {c.name} ({c.line} {m['line_version']}, client v{m['client_version']})")
    for k, v in m["artifacts"].items():
        print(f"  {k:22s} {v['path']}")


def cmd_sql(args):
    from .sqlgen import compile_ddl
    sys.stdout.write(compile_ddl(Client(args.client), args.dialect))


def cmd_validate(args):
    from .validate import validate
    c = Client(args.client)
    con = duckdb.connect(str(_warehouse(args)), read_only=True)
    findings = validate(c, con)
    if args.json:
        print(json.dumps([f.as_dict() for f in findings], indent=2, default=str))
        return 1 if findings else 0
    if not findings:
        print("all conformance checks pass")
        return 0
    print(_table(["unit", "entity", "slot", "check", "violations", "rows", "sample"],
                 [[f.unit, f.entity, f.slot, f.check, f.violations, f.rows, ", ".join(map(str, f.sample))]
                  for f in findings]))
    return 1


def cmd_drift(args):
    from . import drift, llm
    c = Client(args.client)
    con = duckdb.connect(str(_warehouse(args)), read_only=True)
    complete = None
    if args.llm:
        if not llm.configured():
            print("LLM not configured (API_KEY / API_BASE_URL); running without enrichment", file=sys.stderr)
        else:
            complete = llm.complete
    proposals = drift.run(c, con, complete)
    if not args.all:
        proposals = [p for p in proposals if p.kind != "coverage_gap"]
    if args.write:
        out = c.dir / "proposals"
        out.mkdir(exist_ok=True)
        for p in proposals:
            (out / f"{p.id}.yaml").write_text(yaml.safe_dump(p.as_dict(), sort_keys=False, allow_unicode=True))
        print(f"wrote {len(proposals)} proposal(s) to {out}/")
    for p in proposals:
        print(f"\n{p.id}  [{p.status}]  {p.kind}  -> {p.layer}")
        print(f"  {p.title}")
        print(f"  why: {p.rationale}")
        if p.overlay_change:
            print("  change:\n" + "\n".join("    " + ln for ln in yaml.safe_dump(p.overlay_change, sort_keys=False).splitlines()))
        if p.verification:
            print(f"  verification: {p.verification}")


def cmd_metrics(args):
    from .semantic import SemanticLayer
    sl = SemanticLayer(Client(args.client))
    for m in sl.catalog():
        src = "" if m["source"] == "pack" else f"  [{m['source']}]"
        print(f"{m['name']:30s} {m['label']}{src}")
        if args.verbose:
            print(f"{'':30s} {m.get('description', '')}")
            print(f"{'':30s} dimensions: {', '.join(m['dimensions'])}")


def cmd_query(args):
    from .semantic import MetricError, SemanticLayer
    c = Client(args.client)
    sl = SemanticLayer(c)
    con = duckdb.connect(str(_warehouse(args)), read_only=True)
    try:
        res = sl.query(con, args.metrics.split(","), args.by.split(",") if args.by else [],
                       _where(args.where), _as_of(args, con))
    except MetricError as exc:
        print(f"error: {exc}")
        return 1
    if args.sql:
        print(res.sql)
        print()
    print(f"as of {res.as_of}\n")
    print(_table(res.columns, res.rows))


def cmd_triangle(args):
    from .semantic import SemanticLayer
    c = Client(args.client)
    con = duckdb.connect(str(_warehouse(args)), read_only=True)
    res = SemanticLayer(c).triangle(con, args.metric, where=_where(args.where), as_of=_as_of(args, con))
    print(f"{args.metric} by accident year x development age, as of {res.as_of}\n")
    print(_table(res.columns, res.rows))


def cmd_ask(args):
    try:
        import langchain_openai, langgraph  # noqa: F401
    except ImportError:
        print("the agent extra is not installed: uv sync --extra agent")
        return 1
    from .agent_runtime import Orchestrator
    import os
    from . import llm
    from .agent_tools import ContextToolkit
    from .skills import context_skills
    if not llm.configured():
        print("no LLM configured: set API_KEY, API_BASE_URL and CHAT_MODEL in .env (see .env.example)",
              file=sys.stderr)
        return 1
    c = Client(args.client)
    wh = _warehouse(args)
    con = duckdb.connect(str(wh), read_only=True)
    as_of = _as_of(args, con)
    con.close()
    kit = ContextToolkit(c, wh, as_of=as_of)
    orch = Orchestrator(
        api_key=os.environ["API_KEY"], base_url=os.environ["API_BASE_URL"],
        model_name=os.environ.get("CHAT_MODEL", "gpt-4o"),
        system_prompt=kit.system_prompt() + f"\n\nDefault valuation date: {as_of.isoformat()}.",
        tools=kit.tools(), tool_registry=kit.registry(),
        builtin_skills=context_skills(kit),
        data_dir=REPO_ROOT / "build" / args.client / "agent",
        agent_name=f"{c.title} context agent",
    )

    async def run():
        chunks, final = [], None
        async for raw in orch.stream(args.question, session_id=args.session):
            for line in raw.splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                t, data = ev.get("type"), ev.get("data") or {}
                if t == "agent_tool_call" and data.get("status") == "started":
                    chunks.clear()  # text before a tool call is the model narrating; the answer is what follows the last
                    if args.trace:
                        print(f"  -> {data.get('tool')}({json.dumps(data.get('args', {}))[:160]})", file=sys.stderr)
                elif t == "chunk":
                    chunks.append(data.get("text", ""))
                elif t == "result":
                    final = data.get("answer")
                elif t == "error":
                    print(f"agent error: {data.get('message')}", file=sys.stderr)
        print(("".join(chunks) or final or "").strip())

    asyncio.run(run())


def cmd_demo(args):
    for step in (cmd_fixture, cmd_install, cmd_build):
        step(args)
        print()
    print("== validate")
    cmd_validate(argparse.Namespace(**{**vars(args), "json": False}))
    print("\n== drift")
    cmd_drift(argparse.Namespace(**{**vars(args), "llm": False, "write": False, "all": False}))
    return 0


# ------------------------------------------------------------------ Power BI models

def cmd_pbi_import(args):
    from .bilayer import import_pbix
    out = Path(args.out or REPO_ROOT / "build" / "pbi" / Path(args.pbix).stem)
    from .bici import counts_line, measure_counts
    spec = import_pbix(args.pbix, out, catalog=args.catalog, schema=args.schema, standin=not args.no_standin)
    count = measure_counts(spec)
    assumed = [t for t, v in spec["tables"].items() if str(v.get("from", "")).startswith("assumed")]
    print(f"{spec['model']}: {len(spec['tables'])} tables, {len(spec['joins'])} relationships, "
          f"{count['measures']} measures")
    print(f"  TMDL      {out / (spec['model'] + '.SemanticModel')}")
    print(f"  metrics   {out / 'capture.yaml'}: {counts_line(count).split(': ', 1)[1]}")
    if assumed:
        print(f"  warehouse {len(assumed)} tables have an assumed Databricks name; confirm them in capture.yaml")
    if not args.no_standin:
        print(f"  stand-in  {out / (args.catalog + '.duckdb')} (the .pbix's rows, until Databricks is configured)")
    return 0


def cmd_pbi_export(args):
    """A captured model (or a .pbix / .pbit / TMDL folder, captured first) as Databricks metric views."""
    from .bilayer import BILayer, build_spec, import_pbix, save
    from .bimetricview import export
    src = Path(args.source)
    out = Path(args.out or REPO_ROOT / "build" / "metric-views" / (src.stem if src.is_file() else src.name))
    if src.is_file() and src.suffix.lower() in (".pbix", ".pbit"):
        cap = out / "capture"
        import_pbix(src, cap, catalog=args.catalog, schema=args.schema, standin=not args.no_standin)
        layer = BILayer.load(cap)
    elif (src / "capture.yaml").exists():
        layer = BILayer.load(src)
    elif src.is_dir():  # a TMDL folder (<name>.SemanticModel or its definition/)
        from .pbimodel import read_tmdl
        definition = next((p for p in (src, src / "definition") if (p / "model.tmdl").exists()), None) or \
            next((p / "definition" for p in src.glob("*.SemanticModel")), src)
        name = src.name.removesuffix(".SemanticModel")
        model = read_tmdl(definition)
        model.name = name  # the model's folder, not TMDL's inner `definition`
        spec = build_spec(model, args.catalog, args.schema or name.replace("-", "_"))
        (out / "capture").mkdir(parents=True, exist_ok=True)
        save(spec, out / "capture")
        layer = BILayer(spec)
    else:
        raise SystemExit(f"{src}: give a .pbix, a .pbit, a TMDL folder or a folder `ictx pbi import` wrote")
    files = export(layer, out)
    print((out / "export.md").read_text())
    print("files:")
    for f in files:
        print(f"  {f}")
    return 0


def _capture_dir(folder: str) -> str:
    """The folder holding capture.yaml: the one given, or the `capture/` an export wrote inside it."""
    d = Path(folder)
    return str(d / "capture") if not (d / "capture.yaml").exists() and (d / "capture" / "capture.yaml").exists() \
        else folder


def cmd_pbi_metrics(args):
    from .bi_agent import describe, needs, param_range, parameters
    from .bilayer import BILayer
    args.dir = _capture_dir(args.dir)
    layer = BILayer.load(args.dir)
    for k, m in layer.metrics.items():
        if m.get("hidden"):
            continue
        if m["status"] == "captured":
            print(f"  {k:<32} {describe(layer, k)}{needs(layer, k)}")
        elif not args.verbose:
            continue
        elif m["status"] == "presentation":
            print(f"  {k:<32} [display-only] " + (f"shows {m['value']} as text" if m.get("value") else m.get("why", "")))
        else:
            print(f"  {k:<32} [not captured] {m.get('why', '')}")
    for p, info in parameters(layer).items():
        print(f"  what-if {param_range(p, info)}")
    return 0


def cmd_pbi_query(args):
    from .bi_agent import BIToolkit
    from .bilayer import BILayer, Warehouse
    args.dir = _capture_dir(args.dir)
    layer = BILayer.load(args.dir)
    wh = Warehouse.open(args.dir, layer, prefer=args.target)
    where = {}
    for w in args.where or []:
        k, _, v = w.partition("=")
        vals = [_num(x) for x in v.split("|")]
        where[k.strip()] = vals if len(vals) > 1 else vals[0]
    by = [b.strip() for b in args.by.split(";")] if args.by else []
    params = {}
    for w in args.param or []:
        k, sep, v = w.partition("=")
        if not sep:
            raise SystemExit(f"--param {w!r}: write key=value, e.g. --param return_rate=0.1")
        params[k.strip()] = _num(v.strip())
    kit = BIToolkit(layer, wh)
    metrics = args.metrics.split(",")
    out = kit.compute(metrics, by, where, args.top, params=params or None)
    print(out if args.sql else out.split("\n\nSQL (")[0])
    if args.dialect and args.dialect != wh.dialect:
        resolved, _ = kit.resolve(where)
        values, _ = kit.resolve_params(metrics, params)
        print(f"\nSQL ({args.dialect}, via sqlglot):\n"
              + layer.compile(metrics, by, resolved, args.top, dialect=args.dialect,
                              **({"params": values} if values else {})))
    return 0 if not out.startswith("Error:") else 1


def _num(v: str):
    try:
        return int(v)
    except ValueError:
        try:
            return float(v)
        except ValueError:
            return v


def cmd_pbi_ask(args):
    try:
        import langchain_openai, langgraph  # noqa: F401
    except ImportError:
        print("the agent extra is not installed: uv sync --extra agent")
        return 1
    from .agent_runtime import Orchestrator
    import os
    from . import llm
    from .bi_agent import BIToolkit
    from .bilayer import BILayer, Warehouse
    if not llm.configured():
        print("no LLM configured: set API_KEY, API_BASE_URL and CHAT_MODEL in .env (see .env.example)",
              file=sys.stderr)
        return 1
    args.dir = _capture_dir(args.dir)
    layer = BILayer.load(args.dir)
    kit = BIToolkit(layer, Warehouse.open(args.dir, layer, prefer=args.target))
    orch = Orchestrator(
        api_key=os.environ["API_KEY"], base_url=os.environ["API_BASE_URL"],
        model_name=os.environ.get("CHAT_MODEL", "gpt-4o"), system_prompt=kit.system_prompt(),
        tools=kit.tools(), tool_registry=kit.registry(), data_dir=Path(args.dir) / "agent",
        agent_name=f"{layer.model} analyst")

    async def turn(question: str) -> str:
        chunks, final = [], None
        async for raw in orch.stream(question, session_id=args.session):
            for line in raw.splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                t, data = ev.get("type"), ev.get("data") or {}
                if t == "agent_tool_call" and data.get("status") == "started":
                    chunks.clear()  # text before a tool call is the model narrating; the answer is what follows the last
                    if args.trace:
                        print(f"  -> {data.get('tool')}({json.dumps(data.get('args', {}))[:160]})", file=sys.stderr)
                elif t == "chunk":
                    chunks.append(data.get("text", ""))
                elif t == "result":
                    final = data.get("answer")
                elif t == "error":
                    print(f"agent error: {data.get('message')}", file=sys.stderr)
        return ("".join(chunks) or final or "").strip()

    if args.question:
        print(asyncio.run(turn(args.question)))
        return 0
    print(f"Ask about {layer.model} ({kit.wh.describe()}). Empty line or Ctrl-D to stop.")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            return 0
        print(asyncio.run(turn(q)))


def cmd_pbi_ci(args):
    from .bici import run_ci
    ok, report = run_ci(args.models, args.out, previous=args.previous)
    print(report)
    return 0 if ok else 1


def cmd_pbi_publish(args):
    from .bici import publish
    print(f"released {publish(args.out)}")
    return 0


def cmd_pbi_drift(args):
    import tempfile
    from .bici import drift_issue, fetch_release, run_drift
    with tempfile.TemporaryDirectory() as tmp:
        path = fetch_release(args.release, tmp, args.project) if args.release else Path(args.path)
        ok, report = run_drift(path, prefer=args.target)
    print(report)
    if args.out:
        Path(args.out).write_text(report)
    if args.issue:
        print(drift_issue(ok, report, args.project), file=sys.stderr)
    return 0 if ok else 1


def cmd_pbi_serve(args):
    """See bi_mcp's docstring for deployment: sources, auth modes, audit, limits, env vars."""
    import os
    from . import bi_mcp
    if args.action == "keygen":
        if not args.principal:
            raise SystemExit("keygen needs --principal (and usually --groups a,b)")
        key, line = bi_mcp.keygen(args.principal, bi_mcp.local_principal(args.principal, args.groups).groups)
        print(f"API key for {args.principal} (shown once; give it to the caller, store it nowhere else):\n  {key}\n"
              f"Add this line to the keys file (--keys):\n  {line}")
        return 0
    if not (args.dir or args.gitlab):
        raise SystemExit("serve needs --dir D or --gitlab URL --project P")
    from . import llm
    llm.configured()  # loads .env, which may hold GITLAB_TOKEN and DATABRICKS_*
    env = os.environ.get
    source = (bi_mcp.GitLabCaptures(args.gitlab, args.project, pin=args.pin) if args.gitlab
              else bi_mcp.LocalCaptures(args.dir))
    models = [m.strip() for m in args.model.split(",") if m.strip()] if args.model else None
    served = bi_mcp.Served(source, model=models, refresh=args.refresh, prefer=args.target, timeout=args.timeout)
    verifier, principal = None, None
    keys, issuer = args.keys or env("ICTX_PBI_KEYS"), args.oidc_issuer or env("ICTX_OIDC_ISSUER")
    if args.http and not args.no_auth:
        aud = args.oidc_audience or env("ICTX_OIDC_AUDIENCE")
        if issuer and not aud:
            raise SystemExit("--oidc-issuer needs --oidc-audience (the app id / api:// URI tokens are issued for)")
        split = lambda v: tuple(x.strip() for x in v.split(",") if x.strip())  # noqa: E731
        oidc = bi_mcp.OIDC(issuer, list(split(aud)), args.oidc_jwks or env("ICTX_OIDC_JWKS"),
                           split(args.principal_claim), split(args.groups_claim)) if issuer else None
        try:
            verifier = bi_mcp.Verifier(bi_mcp.ApiKeys(keys) if keys else None, oidc)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
    else:
        principal = bi_mcp.local_principal(args.principal, args.groups)
    audit = bi_mcp.Audit(args.audit or env("ICTX_AUDIT_LOG"))
    for name in served.release.models:
        print(f"serving {served.stamp(name)}", file=sys.stderr)
    print("auth: " + (", ".join(x for x, on in (("api keys", keys), ("oidc " + str(issuer), issuer)) if on)
                      if verifier else f"none, as {principal.id}"), file=sys.stderr)
    server = bi_mcp.build_server(served, verifier=verifier, principal=principal, audit=audit,
                                 max_rows=args.max_rows, max_top=args.max_top, max_concurrent=args.max_concurrent,
                                 timeout=args.timeout, host=args.host, port=args.http or 8000,
                                 public_url=args.public_url)
    server.run("streamable-http" if args.http else "stdio")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ictx", description="Insurance enterprise context layer")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, client=True, warehouse=False, help_=None):
        sp = sub.add_parser(name, help=help_)
        if client:
            sp.add_argument("client")
        if warehouse:
            sp.add_argument("--warehouse", help="DuckDB warehouse (default build/<client>/warehouse.duckdb)")
        sp.set_defaults(fn=fn)
        return sp

    add("clients", cmd_clients, client=False, help_="list client deployments")
    add("check", cmd_check, help_="resolve, lint metrics, check SQL portability")
    add("fixture", cmd_fixture, warehouse=True, help_="build the synthetic reference warehouse")
    add("install", cmd_install, warehouse=True, help_="create ontology views in the warehouse")
    b = add("build", cmd_build, help_="export all artifacts")
    b.add_argument("--out")
    s = add("sql", cmd_sql, help_="print view DDL")
    s.add_argument("--dialect", default="duckdb", choices=["duckdb", "snowflake", "databricks", "postgres"])
    v = add("validate", cmd_validate, warehouse=True, help_="data conformance checks")
    v.add_argument("--json", action="store_true")
    d = add("drift", cmd_drift, warehouse=True, help_="detect drift and draft verified change proposals")
    d.add_argument("--llm", action="store_true", help="enrich proposals via the configured LLM gateway")
    d.add_argument("--write", action="store_true", help="write proposals to clients/<client>/proposals/")
    d.add_argument("--all", action="store_true", help="include coverage gaps")
    m = add("metrics", cmd_metrics, help_="list governed metrics")
    m.add_argument("-v", "--verbose", action="store_true")
    q = add("query", cmd_query, warehouse=True, help_="compute governed metrics")
    q.add_argument("-m", "--metrics", required=True)
    q.add_argument("--by")
    q.add_argument("--where", action="append", help="dimension=value[,value...] (repeatable)")
    q.add_argument("--as-of")
    q.add_argument("--sql", action="store_true", help="also print the generated SQL")
    t = add("triangle", cmd_triangle, warehouse=True, help_="accident-year development triangle")
    t.add_argument("-m", "--metric", default="reported_incurred_loss_alae")
    t.add_argument("--where", action="append")
    t.add_argument("--as-of")
    a = add("ask", cmd_ask, warehouse=True, help_="ask the context agent")
    a.add_argument("question")
    a.add_argument("--as-of")
    a.add_argument("--session", default="cli")
    a.add_argument("--trace", action="store_true", help="print tool calls to stderr")
    add("demo", cmd_demo, warehouse=True, help_="fixture + install + build + validate + drift")
    pbi = sub.add_parser("pbi", help="Power BI model: import a .pbix, list captured metrics, query, ask")
    psub = pbi.add_subparsers(dest="pbi_cmd", required=True)
    pi = psub.add_parser("import", help=".pbix -> TMDL -> captured metrics (+ local stand-in data)")
    pi.add_argument("pbix")
    pi.add_argument("--out", help="output folder (default build/pbi/<name>)")
    pi.add_argument("--catalog", default="pilot", help="Databricks catalog the tables are assumed in")
    pi.add_argument("--schema", help="Databricks schema (default: the file name)")
    pi.add_argument("--no-standin", action="store_true", help="do not copy the .pbix rows into a local DuckDB")
    pi.set_defaults(fn=cmd_pbi_import)
    pe = psub.add_parser("export", help="Databricks metric views (YAML 1.1) + context file, from a .pbix, TMDL or "
                                        "capture folder")
    pe.add_argument("source", help="model.pbix, a .SemanticModel (TMDL) folder, or a folder `pbi import` wrote")
    pe.add_argument("--out", help="output folder (default build/metric-views/<name>)")
    pe.add_argument("--catalog", default="pilot", help="Databricks catalog of the tables and the views")
    pe.add_argument("--schema", help="Databricks schema (default: the model name)")
    pe.add_argument("--no-standin", action="store_true", help="do not copy the .pbix rows into a local DuckDB")
    pe.set_defaults(fn=cmd_pbi_export)
    pm = psub.add_parser("metrics", help="list captured metrics")
    pm.add_argument("dir")
    pm.add_argument("-v", "--verbose", action="store_true", help="also list measures not captured, with why")
    pm.set_defaults(fn=cmd_pbi_metrics)
    pq = psub.add_parser("query", help="compute captured metrics")
    pq.add_argument("dir")
    pq.add_argument("-m", "--metrics", required=True)
    pq.add_argument("--by", help='columns separated by ";", e.g. "Product[Category];Calendar[Date]:month"')
    pq.add_argument("--where", action="append", help="Table[Column]=value[|value...] (repeatable)")
    pq.add_argument("--top", type=int)
    pq.add_argument("--param", action="append", help="what-if parameter key=value (repeatable; `pbi metrics` lists them)")
    pq.add_argument("--sql", action="store_true", help="also print the generated SQL")
    pq.add_argument("--dialect", choices=["databricks", "snowflake", "postgres", "duckdb"],
                    help="also print the SQL for another warehouse")
    pq.add_argument("--target", default="auto", choices=["auto", "databricks", "local"])
    pq.set_defaults(fn=cmd_pbi_query)
    pa = psub.add_parser("ask", help="ask questions (interactive without a question)")
    pa.add_argument("dir")
    pa.add_argument("question", nargs="?")
    pa.add_argument("--session", default="cli")
    pa.add_argument("--trace", action="store_true", help="print tool calls to stderr")
    pa.add_argument("--target", default="auto", choices=["auto", "databricks", "local"])
    pa.set_defaults(fn=cmd_pbi_ask)
    pc = psub.add_parser("ci", help="CI: convert, capture, diff and verify every model folder")
    pc.add_argument("models", help="folder of model folders (each a .pbix and/or a .SemanticModel)")
    pc.add_argument("--out", default="public")
    pc.add_argument("--previous", help="an earlier --out to diff against (in CI: what main last published)")
    pc.set_defaults(fn=cmd_pbi_ci)
    pp = psub.add_parser("publish", help="CI on main: release capture-v<N> for the files `ci` wrote")
    pp.add_argument("out", nargs="?", default="public")
    pp.set_defaults(fn=cmd_pbi_publish)
    pd = psub.add_parser("drift", help="every mapped table and column still exists in the warehouse, typed alike")
    pd.add_argument("path", nargs="?", default="public",
                    help="a folder `ci` wrote, one model's capture, or a repo's models/ folder (default public)")
    pd.add_argument("--release", help="check a GitLab release instead (a capture-v* tag, or latest)")
    pd.add_argument("--project", help="models repo for --release/--issue outside its CI (with GITLAB_API, GITLAB_TOKEN)")
    pd.add_argument("--target", default="auto", choices=["auto", "databricks", "local"])
    pd.add_argument("--out", help="also write the markdown report here")
    pd.add_argument("--issue", action="store_true", help="open / update / close the warehouse-drift GitLab issue")
    pd.set_defaults(fn=cmd_pbi_drift)
    ps = psub.add_parser("serve", help="MCP server over the captured metrics (stdio, or --http PORT); "
                                       "`serve keygen` makes an API key")
    ps.add_argument("action", nargs="?", choices=["keygen"], help="keygen: print a new API key and its keys-file line")
    src = ps.add_mutually_exclusive_group()
    src.add_argument("--dir", help="a folder `ictx pbi ci` wrote")
    src.add_argument("--gitlab", help="GitLab URL; serves the newest capture-v* release of --project")
    ps.add_argument("--project", help="models repo, e.g. group/pbi-models")
    ps.add_argument("--pin", help="serve this release tag instead of the newest (rollback)")
    ps.add_argument("--model", help="serve only these model folders, comma separated (default all)")
    ps.add_argument("--refresh", type=float, default=30, help="seconds between checks for a newer release")
    ps.add_argument("--target", default="auto", choices=["auto", "databricks", "local"])
    ps.add_argument("--http", type=int, metavar="PORT", help="serve streamable HTTP on PORT instead of stdio")
    ps.add_argument("--host", default="127.0.0.1", help="HTTP bind address (0.0.0.0 behind a load balancer)")
    ps.add_argument("--public-url", help="the server's external /mcp URL (OIDC protected-resource metadata)")
    ps.add_argument("--keys", metavar="FILE", help="API keys file {sha256(key): {principal, groups}} "
                                                   "[ICTX_PBI_KEYS]")
    ps.add_argument("--oidc-issuer", help="accept JWTs from this issuer [ICTX_OIDC_ISSUER]")
    ps.add_argument("--oidc-audience", help="expected aud, comma separated [ICTX_OIDC_AUDIENCE]")
    ps.add_argument("--oidc-jwks", help="JWKS URL (default from the issuer's discovery) [ICTX_OIDC_JWKS]")
    ps.add_argument("--principal-claim", default="upn,preferred_username,email",
                    help="claims naming the caller, first present wins")
    ps.add_argument("--groups-claim", default="groups", help="claims holding the caller's groups, comma separated")
    ps.add_argument("--no-auth", action="store_true", help="HTTP without auth, as --principal (development only)")
    ps.add_argument("--principal", help="stdio / --no-auth caller, and keygen's principal "
                                        "[ICTX_PRINCIPAL, default the OS user]")
    ps.add_argument("--groups", help="that caller's groups, comma separated [ICTX_GROUPS]")
    ps.add_argument("--audit", metavar="PATH", help="append one JSON line per tool call here (default stderr) "
                                                    "[ICTX_AUDIT_LOG]")
    ps.add_argument("--timeout", type=float, default=120, help="seconds per statement, and per wait for a query slot")
    ps.add_argument("--max-rows", type=int, default=200, help="rows per answer")
    ps.add_argument("--max-top", type=int, default=1000, help="largest `top` a caller may ask for")
    ps.add_argument("--max-concurrent", type=int, default=4, help="warehouse queries at a time")
    ps.set_defaults(fn=cmd_pbi_serve)

    args = p.parse_args(argv)
    from . import llm
    llm._load_dotenv()  # noqa: SLF001 - .env in the working directory, for every command (never overrides the environment)
    try:
        return args.fn(args) or 0
    except (OverlayError, ValueError) as exc:  # ValueError: LayerError and unreadable Power BI files
        print(f"error: {exc}", file=sys.stderr)
        return 2


def pbi_main(argv: list[str] | None = None) -> int:
    """pbi-metrics: `ictx pbi ...` under its own name, for images and clients that only need Power BI."""
    return main(["pbi", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    sys.exit(main())
