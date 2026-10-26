"""Consistency lab: the same question, asked N times, by two LangGraph agents.

    governed   resolves business words through the ontology and computes through the
               semantic layer (governed metrics, governed joins, valuation date)
    raw        same model, same question, SQL over the client's raw source tables only

Each run ends with one headline number. The lab reports the spread: governed answers
should land on one value, stamped with the definition and content version that produced it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from typing import AsyncIterator

import duckdb

from .proposals import Studio

from insurance_context.agent_runtime import RunContext

ANSWER_RULE = ("\n\nEnd your reply with one final line exactly like `ANSWER: <number>` holding the single headline "
               "figure you reported (a plain number; percentages as e.g. 57.1%, money as e.g. 25600000).")

RAW_PROMPT = """You are a careful data analyst at {title}. Answer the business question from the company's
source system tables using SQL. Data is complete up to {as_of}. The units and their systems: {units}.
Use list_tables to see the tables and columns, then run_sql (DuckDB SQL, SELECT only). Work out the
answer yourself; there is no metric catalog. Be concise: the answer, then the number.""" + ANSWER_RULE


def parse_number(text: str) -> float | None:
    m = list(re.finditer(r"ANSWER:\s*\**\s*([-$\d.,]+)\s*([%kKmMbB]?)", text or ""))
    if not m:
        return None
    raw, suffix = m[-1].group(1), m[-1].group(2)
    try:
        v = float(raw.replace("$", "").replace(",", "").rstrip("."))
    except ValueError:
        return None
    return v / 100 if suffix == "%" else v * {"k": 1e3, "m": 1e6, "b": 1e9}.get(suffix.lower(), 1)


class Lab:
    def __init__(self, studio: Studio):
        self.studio = studio

    # ------------------------------------------------------------ agents

    def _model_kwargs(self) -> dict:
        return {"api_key": os.environ.get("API_KEY"), "base_url": os.environ.get("API_BASE_URL"),
                "model_name": os.environ.get("CHAT_MODEL", "gpt-4o")}

    def _governed(self, client: str):
        from insurance_context.agent_tools import ContextToolkit
        from insurance_context.agent_runtime import Orchestrator
        from . import warehouse
        kit = ContextToolkit(self.studio.client(client), self.studio.served_warehouse(client),
                             as_of=warehouse.valuation(client))
        tools = kit.tools()
        d = self.studio.s.agent_dir / "lab"
        d.mkdir(parents=True, exist_ok=True)
        return Orchestrator(**self._model_kwargs(), system_prompt=kit.system_prompt() + ANSWER_RULE, tools=tools,
                            tool_registry={f.__name__: (f, (f.__doc__ or "").split("\n")[0]) for f in tools},
                            data_dir=d, storage=str(d / "lab.db"), agent_name="Governed agent")

    def _raw(self, client: str):
        from insurance_context.agent_runtime import Orchestrator
        from . import warehouse
        c = self.studio.client(client)
        raw_db = str(warehouse.raw_path(self.studio.s.warehouses, client))
        schemas = sorted({u.mappings.get("source_schema", "main") for u in c.units})

        def con():
            return duckdb.connect(raw_db, read_only=True,
                                  config={"enable_external_access": False, "lock_configuration": True})

        async def list_tables(ctx: RunContext) -> str:
            """List the source tables and their columns."""
            cx = con()
            try:
                rows = cx.execute(
                    "SELECT table_schema, table_name, string_agg(column_name || ' ' || data_type, ', ') "
                    "FROM information_schema.columns WHERE table_schema IN (" +
                    ",".join(f"'{s}'" for s in schemas) + ") GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
            finally:
                cx.close()
            return "\n".join(f"{s}.{t}: {cols}" for s, t, cols in rows)

        async def run_sql(ctx: RunContext, sql: str) -> str:
            """Run one read-only SELECT over the source tables."""
            if not re.match(r"^\s*(select|with)\b", sql, re.I) or ";" in sql.strip().rstrip(";"):
                return "Only a single SELECT is allowed."
            cx = con()
            try:
                cur = cx.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(60)
            except duckdb.Error as exc:
                return f"SQL error: {exc}"
            finally:
                cx.close()
            return " | ".join(cols) + "\n" + "\n".join(" | ".join(str(v) for v in r) for r in rows)

        units = "; ".join(f"{u.code} = {u.name} ({u.source_system}, schema {u.mappings.get('source_schema')})"
                          for u in c.units)
        d = self.studio.s.agent_dir / "lab"
        d.mkdir(parents=True, exist_ok=True)
        tools = [list_tables, run_sql]
        return Orchestrator(**self._model_kwargs(),
                            system_prompt=RAW_PROMPT.format(title=c.title, as_of=warehouse.valuation(client), units=units),
                            tools=tools, tool_registry={f.__name__: (f, (f.__doc__ or "").split("\n")[0]) for f in tools},
                            data_dir=d, storage=str(d / "lab.db"), agent_name="Raw-data agent")

    # ------------------------------------------------------------ runs

    async def _one(self, orch, side: str, i: int, question: str) -> dict:
        t0 = time.monotonic()
        text, tools, defs = "", [], []
        async for raw in orch.stream(question, session_id=f"lab-{side}-{uuid.uuid4().hex[:8]}"):
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            data = ev.get("data") or {}
            if ev.get("type") == "chunk":
                text += data.get("text", "")
            elif ev.get("type") == "agent_tool_call":
                if data.get("status") == "started":
                    tools.append({"tool": data.get("tool"), "args": data.get("args")})
                elif data.get("status") == "completed" and data.get("tool") == "compute_metrics":
                    defs.append((data.get("summary") or "")[:1500])
            elif ev.get("type") == "result" and not text:
                text = data.get("answer", "")
        return {"side": side, "i": i, "answer": re.sub(r"\n*ANSWER:.*$", "", text.strip(), flags=re.S),
                "value": parse_number(text), "tools": tools, "evidence": defs[-1] if defs else "",
                "secs": round(time.monotonic() - t0, 1)}

    async def run(self, client: str, question: str, n: int = 5) -> AsyncIterator[dict]:
        governed, raw = self._governed(client), self._raw(client)
        yield {"type": "started", "data": {"client": client, "question": question, "n": n,
                                           "content_version": self.studio.head()[:10]}}
        sem = asyncio.Semaphore(4)

        async def go(orch, side, i):
            async with sem:
                try:
                    return await self._one(orch, side, i, question)
                except Exception as exc:
                    return {"side": side, "i": i, "answer": f"Run failed: {exc}", "value": None, "tools": [],
                            "evidence": "", "secs": 0}

        tasks = [asyncio.create_task(go(governed, "governed", i)) for i in range(n)] + \
                [asyncio.create_task(go(raw, "raw", i)) for i in range(n)]
        results = []
        for fut in asyncio.as_completed(tasks):
            r = await fut
            results.append(r)
            yield {"type": "run", "data": r}
        summary = {}
        for side in ("governed", "raw"):
            vals = [r["value"] for r in results if r["side"] == side and r["value"] is not None]
            distinct = sorted({round(v, 6) for v in vals})
            summary[side] = {"runs": n, "answered": len(vals), "distinct": len(distinct), "values": vals,
                             "min": min(vals) if vals else None, "max": max(vals) if vals else None,
                             "spread_pct": (max(vals) - min(vals)) / abs(sum(vals) / len(vals)) * 100
                             if vals and sum(vals) else None}
        yield {"type": "summary", "data": {**summary, "content_version": self.studio.head()[:10]}}
