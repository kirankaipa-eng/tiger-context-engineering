"""Context Studio over captured Power BI models: any model, no content repo, no reference data.

    ctx-studio serve --pbi build/pbi/<model> [--pbi build/pbi/<other>]    API on :8700
    (or CTX_STUDIO_PBI=<dir>[:<dir>...] uvicorn context_studio.pbi_app:app)

Each folder is what `ictx pbi import` or `ictx pbi export` wrote (capture.yaml, or a `capture/` inside
it). The web app shows its metrics and why the rest were not captured, computes metrics, explores rows
of its tables, and answers questions with the same agent as `ictx pbi ask`. Numbers run on Databricks
when DATABRICKS_* is set, else on the local copy of the .pbix's rows beside capture.yaml.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from decimal import Decimal
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from insurance_context import llm
from insurance_context.bi_agent import BIToolkit, _shown_by, _tree, describe, metric_needs, parameters
from insurance_context.bilayer import BILayer, LayerError, Warehouse, parse_ref
from insurance_context.daxcapture import coverage_data

MAX_ROWS = 500

app = FastAPI(title="Context Studio · Power BI", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class Loaded:
    """One captured model: its layer, and a warehouse once one can be opened."""

    def __init__(self, folder: Path):
        if not (folder / "capture.yaml").exists() and (folder / "capture" / "capture.yaml").exists():
            folder = folder / "capture"  # an export folder
        self.folder, self.layer = folder, BILayer.load(folder)
        self.id = re.sub(r"[^a-z0-9]+", "-", folder.parent.name.lower() if folder.name == "capture"
                         else folder.name.lower()).strip("-") or "model"
        self._kit: BIToolkit | None = None
        self.data_error: str | None = None
        self._orch = None

    def kit(self) -> BIToolkit:
        if self._kit is None:
            try:
                self._kit = BIToolkit(self.layer, Warehouse.open(self.folder, self.layer), max_rows=MAX_ROWS)
                self.data_error = None
            except Exception as exc:  # noqa: BLE001 - browsing works without data; say why numbers do not
                self.data_error = str(exc)
                raise HTTPException(409, f"no data to run on: {exc}") from None
        return self._kit

    def warehouse(self) -> str | None:
        try:
            return self.kit().wh.describe()
        except HTTPException:
            return None


_MODELS: dict[str, Loaded] | None = None


def models() -> dict[str, Loaded]:
    global _MODELS
    if _MODELS is None:
        found: dict[str, Loaded] = {}
        for d in [x for x in os.environ.get("CTX_STUDIO_PBI", "").split(os.pathsep) if x.strip()]:
            m = Loaded(Path(d).expanduser().resolve())
            while m.id in found:
                m.id += "-2"
            found[m.id] = m
        _MODELS = found
    return _MODELS


def _model(mid: str) -> Loaded:
    m = models().get(mid)
    if m is None:
        raise HTTPException(404, f"no model {mid!r}; served: {', '.join(models()) or 'none'}")
    return m


def _cell(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    return v


def _bad(exc: Exception):
    raise HTTPException(400, str(exc)) from None


@app.get("/api/health")
def health():
    return {"ok": True, "mode": "pbi", "llm": llm.configured(), "models": list(models())}


@app.get("/api/pbi/models")
def list_models():
    out = []
    for m in models().values():
        cov = coverage_data(m.layer.metrics)
        out.append({"id": m.id, "name": m.layer.model, "folder": str(m.folder), "warehouse": m.warehouse(),
                    "data_error": m.data_error, **{k: cov[k] for k in ("measures", "captured", "presentation",
                                                                      "not_captured")}})
    return {"models": out, "llm": llm.configured()}


@app.get("/api/pbi/{mid}/overview")
def overview(mid: str):
    m = _model(mid)
    cov = coverage_data(m.layer.metrics)
    for g in cov["groups"]:
        g["examples"] = [{"key": k, "label": m.layer.metrics[k].get("label", k)} for k in g["roots"][:5]]
    tables = {t: len((info.get("columns") or {})) for t, info in m.layer.tables.items()}
    return {"id": m.id, "name": m.layer.model, "folder": str(m.folder), "warehouse": m.warehouse(),
            "data_error": m.data_error, "imported": m.layer.spec.get("imported"), "coverage": cov,
            "tables": tables, "parameters": parameters(m.layer)}


def _row(m: Loaded, k: str, spec: dict) -> dict:
    out = {"key": k, "label": spec.get("label", k), "status": spec.get("status"), "table": spec.get("table"),
           "why": spec.get("why"), "gap": spec.get("gap"), "dax": spec.get("dax")}
    if spec.get("status") == "captured":
        try:
            out["definition"], out["needs"] = describe(m.layer, k), metric_needs(m.layer, k)
        except Exception as exc:  # noqa: BLE001 - one odd definition does not hide the list
            out["definition"] = f"(cannot describe: {exc})"
    return out


@app.get("/api/pbi/{mid}/metrics")
def metric_list(mid: str):
    m = _model(mid)
    shows = _shown_by(m.layer)
    rows = [{**_row(m, k, spec), "shown_by": shows.get(k, []), "value": spec.get("value")}
            for k, spec in m.layer.metrics.items() if not spec.get("hidden")]
    return {"metrics": rows}


@app.get("/api/pbi/{mid}/metrics/{key}")
def metric(mid: str, key: str):
    m = _model(mid)
    spec = m.layer.metrics.get(key)
    if spec is None or spec.get("hidden"):
        raise HTTPException(404, f"no measure {key!r}")
    out = _row(m, key, spec)
    if spec.get("status") == "captured":
        out["parts"] = [{"key": k, "definition": describe(m.layer, k)} for k in _tree(m.layer, key) if k != key]
        try:  # the tables its numbers are read from: the rows behind a drilldown
            out["tables"] = list(dict.fromkeys(m.layer.metrics[x]["table"] for x in m.layer.leaves(key)
                                               if m.layer.metrics[x].get("table")))
        except LayerError:
            out["tables"] = []
        try:
            out["columns"] = m.layer.columns_for([key])
        except LayerError:
            out["columns"] = []
        try:
            out["sql"] = m.layer.compile([key], by=(out.get("needs") or {}).get("by") or None,
                                         dialect=_dialect(m))
        except LayerError as exc:
            out["sql_error"] = str(exc)
    return out


def _dialect(m: Loaded) -> str:
    try:
        return m.kit().wh.dialect
    except HTTPException:
        return (m.layer.spec.get("warehouse") or {}).get("dialect", "duckdb")


@app.get("/api/pbi/{mid}/tables")
def tables(mid: str):
    m = _model(mid)
    out = []
    for t, info in sorted(m.layer.tables.items()):
        out.append({"name": t, "warehouse": info.get("table") or ("(query)" if info.get("sql") else None),
                    "from": info.get("from"), "why": info.get("why"),
                    "columns": [{"name": c, "column": ci.get("column"), "type": ci.get("type")}
                                for c, ci in (info.get("columns") or {}).items()]})
    joins = m.layer.spec.get("joins") or []
    return {"tables": out, "joins": joins}


@app.get("/api/pbi/{mid}/values")
def values(mid: str, column: str):
    m = _model(mid)
    kit = m.kit()
    try:
        parse_ref(column)
        _, rows = kit._query(kit._values_sql(column), 1001)
    except Exception as exc:  # noqa: BLE001
        _bad(exc)
    return {"column": column, "values": [_cell(r[0]) for r in rows[:1000]], "more": len(rows) > 1000}


@app.post("/api/pbi/{mid}/compute")
def compute(mid: str, body: dict = Body(...)):
    m = _model(mid)
    kit = m.kit()
    metrics = list(body.get("metrics") or [])
    try:
        params, what_if = kit.resolve_params(metrics, body.get("params"))
        where, notes = kit.resolve(body.get("filters"))
        sql = m.layer.compile(metrics, body.get("by") or None, where, body.get("top"), body.get("order_by"),
                              bool(body.get("ascending")), dialect=kit.wh.dialect,
                              **({"params": params} if params else {}))
        cols, rows = kit._query(sql, MAX_ROWS + 1)
    except Exception as exc:  # noqa: BLE001 - the reason goes to the screen
        _bad(exc)
    return {"columns": cols, "rows": [[_cell(v) for v in r] for r in rows[:MAX_ROWS]], "truncated": len(rows) > MAX_ROWS,
            "metrics": metrics, "notes": notes, "what_if": what_if, "sql": sql, "warehouse": kit.wh.describe(),
            "ms": kit.last.get("ms")}


@app.post("/api/pbi/{mid}/explore")
def explore(mid: str, body: dict = Body(...)):
    m = _model(mid)
    kit = m.kit()
    limit = min(int(body.get("limit") or 100), MAX_ROWS)
    try:
        where, notes = kit.resolve(body.get("filters"))
        sql = m.layer.explore_sql(body.get("columns"), body.get("aggregates"), where, body.get("conditions"),
                                  body.get("order_by"), bool(body.get("descending")), limit + 1,
                                  bool(body.get("distinct")), body.get("table") or None, dialect=kit.wh.dialect)
        cols, rows = kit._query(sql, limit + 1)
    except Exception as exc:  # noqa: BLE001
        _bad(exc)
    return {"columns": cols, "rows": [[_cell(v) for v in r] for r in rows[:limit]], "truncated": len(rows) > limit,
            "aggregates": list(body.get("aggregates") or []), "notes": notes, "sql": sql,
            "warehouse": kit.wh.describe(), "ms": kit.last.get("ms")}


SCREEN = """
On this screen every compute and explore result is drawn under your answer as a chart and a table, with
its SQL. Do not repeat those tables: give the answer and the few numbers that matter in a sentence or a
short list, then the definition used, in one line."""


def _orchestrator(m: Loaded):
    if m._orch is None:
        from insurance_context.agent_runtime import Orchestrator
        kit = m.kit()
        m._orch = Orchestrator(
            api_key=os.environ["API_KEY"], base_url=os.environ["API_BASE_URL"],
            model_name=os.environ.get("CHAT_MODEL", "gpt-4o"), system_prompt=kit.system_prompt() + SCREEN,
            tools=kit.tools(), agent_name=f"{m.layer.model} analyst")
    return m._orch


@app.post("/api/pbi/{mid}/ask")
async def ask(mid: str, body: dict = Body(...)):
    m = _model(mid)
    if not llm.configured():
        raise HTTPException(503, "no LLM configured: set API_KEY, API_BASE_URL and CHAT_MODEL in .env, then restart")
    try:
        import langgraph  # noqa: F401
    except ImportError:
        raise HTTPException(503, "the agent extra is not installed: uv sync --extra agent") from None
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "empty message")
    orch = _orchestrator(m)
    kit = m.kit()
    session = f"{m.id}:{body.get('session_id') or 'default'}"

    async def lines():
        """The agent's events, plus `pbi_result` after each compute / explore: its rows, to chart."""
        seen = kit.results[-1]["seq"] if kit.results else 0
        async for line in orch.stream(message, session_id=session):
            yield line
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            d = ev.get("data") or {}
            if ev.get("type") == "agent_tool_call" and d.get("status") == "completed" and \
                    d.get("tool") in ("compute", "explore"):
                new = [r for r in kit.results if r["seq"] > seen]  # parallel calls may finish in any order
                seen = max([seen, *[r["seq"] for r in new]])
                for last in new:
                    yield json.dumps({"type": "pbi_result", "data": {
                        "kind": last["kind"], "columns": last["columns"], "rows": [[_cell(v) for v in r] for r in last["shown"]],
                        "metrics": last["metrics"], "by": last["by"], "sql": last.get("sql"), "ms": last.get("ms"),
                        "filters": {k: _cell(v) for k, v in (last.get("filters") or {}).items()},
                        "conditions": last.get("conditions") or [],
                        "warehouse": kit.wh.describe(),
                        "labels": {k: m.layer.metrics[k].get("label", k) for k in last["metrics"] if k in m.layer.metrics}}},
                        default=str) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")
