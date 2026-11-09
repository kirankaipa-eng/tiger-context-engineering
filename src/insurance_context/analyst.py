"""The analyst: one loop, the way Claude Code works. The model reads the question, calls a few plain tools, sees what
they return, and answers. Code never guesses what a question means: it looks things up, runs what the model asks for
under the owner's scope, and says exactly what it ran. Spec: docs/powerbi/ANALYST_SPEC.md.

    tools, prompt, wrap = install(kit, folder)    # as desk_harness.install, so the server is unchanged
"""

from __future__ import annotations

import asyncio
import contextvars
import copy
import functools
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import TypedDict

import yaml

from . import bi_agent as bi
from . import findable, insurance
from .agent_runtime import RunContext

MAX_CALLS = 8        # tool calls one question may make
TURN_SECONDS = 120   # and the time it may take, before it answers from what it has
ROWS = 30            # rows of one result the model reads
AT_ONCE = 5          # steps on the warehouse together
EXPLAIN_SECONDS = 60
SPLITS = 6           # columns one layer of explain splits by
VALUES = 30          # a column with at most this many values has them listed in the prompt
COLUMNS = 40         # columns listed with their values
ENOUGH = "Answer now from what you have: this question has had its tool calls."
_SKIP = re.compile(r"(?i)(^id$|id$|key$|image|sort|colou?r|url|latitude|longitude|guid|flag)")

TURN = contextvars.ContextVar("analyst_turn", default=None)

METHOD = """You are a health-plan analyst. You answer business questions from the governed Power BI measures listed below, with
numbers from the warehouse. Never state a number you did not compute.

How to work:
- Plan every number the answer needs, then call compute once with all of them as steps; they run together. Each step is
  {"metrics": [keys], "by": [columns], "filters": {column: value or [values]}, "top": n, "order_by": key, "ascending": bool}.
  Use the keys as listed (total_pmpm, not base:total_pmpm), and columns as Table[Column]; a date column takes a grain,
  Calendar[Date]:month. Mark "show": true on the one step whose rows your answer is about: that is the chart beside it.
  The shown step holds the measure asked about and measures in its unit only (PMPMs with PMPMs, not counts or rates
  per 1,000); put the others in steps of their own.
- A question with no period means the latest month with data (below). Comparing periods is a list of values with the
  date column in `by`, e.g. by ["Calendar[Date]:month"], filters {"Calendar[Date]:month": ["2025-09", "2026-09"]}.
- "Highest", "where", "which": compute `by` that column with order_by, every value, and name the first.
- "Why is X the highest": compare X with everyone in the same period, the measure and its parts, both in one compute.
- "Why did it change" or "what drove it": compute the measure and its parts for the two periods, then call explain once
  for the measure to find which slice carried the change. One layer; offer the next.
- A result marked empty has no number under its scope: say so, never report it as zero, and say what the result says
  empties it (a part of the owner's scope, or the measure itself), in the owner's words.
- Only the owner's scope is applied for you, and each result says it. Never add a filter on a scope column.

How to answer: one to three sentences for a business reader. Lead with the measure asked about, name the period
(September 2026), give at most three numbers, and say what stands out. The chart and the table are on screen beside
your words: do not repeat them. Say what the numbers show, never how they were found: no tools (compute,
explain), steps, keys or SQL. End with at most one offer of one next step:
"Would you like what is driving inpatient PMPM in Illinois?". A reply such as "sure" takes it.

"""


class Step(TypedDict, total=False):
    metrics: list[str]
    by: list[str]
    filters: dict[str, str | int | float | list[str | int | float]]
    top: int
    order_by: str
    ascending: bool
    params: dict[str, str | int | float]
    show: bool


class Analyst:
    def __init__(self, kit, folder=None):
        self.kit, self.layer = kit, kit.layer
        self.folder = Path(folder or kit.folder)
        self.catalog = findable.load(self.folder, self.layer.captured())
        p = self.folder / "analysis_default.yaml"
        self._scope_said = ((yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("filters") or {}) if p.exists() else {}
        conf = self.folder / "precedents.yaml"
        self.drivers = ((yaml.safe_load(conf.read_text(encoding="utf-8")) or {}).get("drivers") or []) if conf.exists() else []
        self._scope: dict | None = None
        self._values: dict[str, list] = {}
        self._latest: str | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        threading.Thread(target=self._warm, daemon=True).start()

    # -- what is read once ------------------------------------------------------------------------------------------

    def scope(self) -> dict:
        """The owner's scope, with "latest" read once: the last value at which the headline measure has a number under
        the rest of the scope. A period table runs past the data, so its last value alone can be a month with nothing."""
        if self._scope is None:
            fixed = {c: v for c, v in self._scope_said.items() if str(v).lower() != "latest"}
            out = dict(self._scope_said)
            for col in [c for c, v in self._scope_said.items() if str(v).lower() == "latest"]:
                try:
                    out[col] = self._latest_of(col, fixed)
                except Exception as exc:  # noqa: BLE001 - said in the log; the value stays as written
                    print(f"[analyst] scope {col}: latest not read ({exc})", file=sys.stderr, flush=True)
            self._scope = out
        return self._scope

    def _latest_of(self, col: str, fixed: dict):
        last = lambda vs: max(vs, key=lambda x: (_num(x) is not None, _num(x) or 0, str(x)))  # noqa: E731 - 37529 after 9999
        if k := self.headline():
            try:
                reach = {c.split(":")[0] for c in self.layer.columns_for(k)}
                rest = {c: v for c, v in fixed.items() if c.split(":")[0] in reach}
                sql = self.layer.compile(k, [col], self.kit.resolve(rest)[0], dialect=self.kit.wh.dialect,
                                         row_filters=self.kit.row_filters)
                have = [r[0] for r in self.kit.wh.query(sql, bi.MAX_VALUES + 1)[1] if r[0] is not None and r[-1] not in (None, 0)]
                if have:
                    return last(have)
            except Exception as exc:  # noqa: BLE001 - then the column's own last value, under its table's scope
                print(f"[analyst] scope {col}: no value with {k[0]} ({str(exc)[:140]})", file=sys.stderr, flush=True)
        table = col.split("[")[0]
        same = {c: v for c, v in fixed.items() if c.split("[")[0] == table}
        held = [r[0] for r in self.kit.wh.query(self.kit._values_sql(col, self.kit.resolve(same)[0]), bi.MAX_VALUES + 1)[1]
                if r[0] is not None]
        return last(held) if held else "latest"

    def scope_for(self, metrics: list[str]) -> dict:
        """The part of the owner's scope these measures reach."""
        try:
            reach = {c.split(":")[0] for c in self.layer.columns_for(metrics)}
        except Exception:  # noqa: BLE001
            return {}
        return {c: v for c, v in self.scope().items() if c.split(":")[0] in reach}

    def date_column(self) -> str | None:
        cols = [c for k in self.headline() for c in self.layer.columns_for([k]) if self.kit._type(c) == "dateTime"]
        cols.sort(key=lambda c: (not re.search(r"calendar|date", c.split("[")[0], re.IGNORECASE), c))
        return cols[0] if cols else None

    def headline(self) -> list[str]:
        keys = [k for k in self.layer.captured() if re.search(r"pmpm", k)] or list(self.layer.captured())
        return sorted(keys, key=lambda k: ("total" not in k, k))[:1]

    def _warm(self) -> None:
        """The columns' values and the latest month with data, read once, ahead of the first question."""
        try:
            self.scope()
            counts: dict[str, int] = {}
            for k in self.layer.captured():
                try:
                    for c in self.layer.columns_for([k]):
                        if self.kit._type(c) == "string" and not _SKIP.search(c.split("[")[-1].rstrip("]")):
                            counts[c] = counts.get(c, 0) + 1
                except Exception:  # noqa: BLE001, S112 - a measure that reaches no table adds no column
                    continue
            cols = sorted(counts, key=lambda c: -counts[c])[:COLUMNS]

            def held(c):
                try:
                    return c, [r[0] for r in self.kit.wh.query(self.kit._values_sql(c), VALUES + 1)[1]]
                except Exception:  # noqa: BLE001
                    return c, None
            with ThreadPoolExecutor(AT_ONCE) as pool:
                self._values = {c: v for c, v in pool.map(held, cols) if v is not None}
            if (col := self.date_column()) and (k := self.headline()):
                sql = self.layer.compile(k, [f"{col}:month"], self.kit.resolve(self.scope_for(k))[0],
                                         dialect=self.kit.wh.dialect, row_filters=self.kit.row_filters)
                have = [str(r[0])[:7] for r in self.kit.wh.query(sql, 5000)[1] if r[-1] is not None]
                self._latest = max(have) if have else None
        except Exception as exc:  # noqa: BLE001 - the prompt then says less; questions still run
            print(f"[analyst] warming: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            self._ready.set()

    # -- the prompt -------------------------------------------------------------------------------------------------

    def catalog_text(self) -> str:
        lines = ["Measures (role:key \"label\"; a measure's versions on one line):", self.catalog.context()]
        parts = []
        for k in self.layer.captured():
            if (p := insurance.plan(self.catalog, k)) and p["head"]["pmpm"] == k:
                bits = [f"{x['pmpm']}" + (f" ({x['util']} × {x['cost']})" if x.get("util") and x.get("cost") else "")
                        for x in p["parts"]]
                parts.append(f"- {k} breaks into {' + '.join(bits)}")
        if parts:
            lines += ["", "Parts of each PMPM (by service category; utilization × unit cost):", *parts]
        return "\n".join(lines)

    def facts(self) -> str:
        """This turn's facts: what the owner's scope applies, the latest month with data, today."""
        self._ready.wait(timeout=30)
        cols = [f"- {c}: {', '.join(map(str, v[:VALUES]))}" if len(v) <= VALUES else f"- {c} (more than {VALUES} values)"
                for c, v in self._values.items()]
        out = ["Columns to cut by, with their values:", *cols]
        if col := self.date_column():
            out.append(f"Date column: {col} (grains :year :quarter :month)")
        if self._latest:
            out.append(f"Latest month with data: {self._latest}")
        if self.scope():
            out.append("Owner's scope, applied to every number for you (never add these): "
                       + "; ".join(f"{c} = {v}" for c, v in self.scope().items()))
        out.append(f"Today: {date.today().isoformat()}")
        return "\n".join(out)

    def prompt(self) -> str:
        return METHOD + insurance.PRIMER + "\n" + insurance.SAY + "\n\n" + self.catalog_text()

    # -- the tools --------------------------------------------------------------------------------------------------

    def compute(self, steps: list[dict]) -> str:
        """Every step under the owner's scope, together; the result each one keeps, in order, the shown one last."""
        if not steps:
            return "refused: no steps. Send each number the answer needs as a step."
        t = TURN.get() or {}
        first = t.get("n", 0) + 1
        t["n"] = first + len(steps) - 1
        with ThreadPoolExecutor(AT_ONCE) as pool:
            done = list(pool.map(lambda s: contextvars.copy_context().run(self._one, s), steps))
        shown = [i for i, s in enumerate(steps) if s.get("show")] or [len(steps) - 1]
        with self._lock:
            for i in [i for i in range(len(done)) if i not in shown] + shown:  # the page draws the last kept
                if done[i][1] is not None:
                    self.kit._keep(done[i][1])
        return "\n\n".join(f"R{first + i} {text}" for i, (text, _) in enumerate(done))

    def _one(self, step: dict) -> tuple[str, dict | None]:
        metrics, by = list(step.get("metrics") or []), list(step.get("by") or [])
        if unknown := [k for k in metrics if k not in self.layer.metrics]:
            return f"refused: {', '.join(unknown)} is not a measure key. Use the keys listed.", None
        scope = self.scope_for(metrics)
        given = {c: v for c, v in (step.get("filters") or {}).items() if c.split(":")[0] not in scope}
        try:
            params, what_if = self.kit.resolve_params(metrics, step.get("params"))
            where, notes = self.kit.resolve({**given, **scope})
            sql = self.layer.compile(metrics, by, where, step.get("top"), step.get("order_by"), bool(step.get("ascending")),
                                     dialect=self.kit.wh.dialect, row_filters=self.kit.row_filters,
                                     **({"params": params} if params else {}))
            began = time.monotonic()
            cols, rows = self.kit.wh.query(sql, limit=ROWS + 1)
        except Exception as exc:  # noqa: BLE001 - what the model gets back to fix
            return f"refused: {exc}", None
        ms = int((time.monotonic() - began) * 1000)
        record = {"kind": "compute", "columns": cols, "shown": rows[:ROWS], "metrics": metrics, "by": by, "filters": where,
                  "sql": sql, "ms": ms}
        empty = not rows or all(v in (None, 0) for r in rows for c, v in zip(cols, r) if c in metrics)
        names = ", ".join(self.layer.metrics[k].get("label", k) for k in metrics)
        head = (f"{'empty' if empty else 'ok'} · {names}" + (f" by {', '.join(by)}" if by else "")
                + f" · filters: {'; '.join(f'{c} = {v}' for c, v in where.items() if c not in scope) or 'none'}"
                + (f" · owner's scope: {'; '.join(f'{c} = {v}' for c, v in scope.items())}" if scope else "")
                + "".join(f" · {n}" for n in [*notes, *what_if]))
        if empty:
            return head + self._why_empty(metrics, by, given, scope, step), record
        table = bi._markdown(cols, [[bi._fmt(v) if c in metrics else str(v) for c, v in zip(cols, r)] for r in rows[:ROWS]])
        more = f"\n(more than {ROWS} rows; the first {ROWS} shown)" if len(rows) > ROWS else ""
        return f"{head}\n{table}{more}", record

    def _why_empty(self, metrics, by, given, scope, step) -> str:
        """Which part of the owner's scope leaves a result with no number: the step again without each one, together."""
        if not scope:
            return ""

        def has(drop):
            try:
                where = self.kit.resolve({**given, **{c: v for c, v in scope.items() if c not in drop}})[0]
                sql = self.layer.compile(metrics, by, where, step.get("top"), step.get("order_by"), bool(step.get("ascending")),
                                         dialect=self.kit.wh.dialect, row_filters=self.kit.row_filters)
                cols, rows = self.kit.wh.query(sql, limit=ROWS + 1)
                return any(v not in (None, 0) for r in rows for c, v in zip(cols, r) if c in metrics)
            except Exception:  # noqa: BLE001 - not known is not said
                return None
        tries = [[c] for c in scope] + ([list(scope)] if len(scope) > 1 else [])
        with ThreadPoolExecutor(AT_ONCE) as pool:
            got = list(pool.map(has, tries))
        alone = [f"{c} = {scope[c]}" for (c,), ok in zip(tries, got) if ok]
        if alone:
            return f" · it has numbers without the owner's {' or without '.join(alone)}"
        if len(scope) > 1 and got[-1]:
            return " · no single part of the owner's scope empties it, but it has numbers without the whole scope"
        if got[-1] is False or (len(scope) == 1 and got[0] is False):
            return " · it is empty even without the owner's scope: the measure has no number for this cut"
        return ""

    def explain(self, metric: str, period: str, now: str, before: str, filters: dict | None = None) -> str:
        """One layer of what drove a change, under the owner's scope, split by a few columns, within a time limit."""
        if metric not in self.layer.metrics:
            return f"refused: {metric} is not a measure key."
        scope = self.scope_for([metric])
        run = copy.copy(self.kit)
        every = self.kit._splits(metric, period)
        cols = ([c for c in self.drivers if c in every] or every)[:SPLITS]
        run._splits = lambda *_: cols
        given = {c: v for c, v in (filters or {}).items() if c.split(":")[0] not in scope}
        out = bi.BIToolkit.explain(run, metric, period, str(now), str(before), {**given, **scope}, depth=1)
        with self._lock:
            for r in run.results:
                if r["seq"] > self.kit._seq:
                    self.kit._keep(r)
        return ("refused: " + out[len("Error: "):]) if out.startswith("Error: ") else out

    def describe(self, metric: str) -> str:
        if metric not in self.layer.metrics:
            return f"refused: {metric} is not a measure key."
        p = insurance.plan(self.catalog, metric)
        parts = f"\nParts: {', '.join(insurance.keys(p))}" if p else ""
        return f"{metric}: {bi.describe(self.layer, metric)}{bi.needs(self.layer, metric)}{parts}\n{self.kit.list_columns([metric])}"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def install(kit, folder=None):
    """(tools, system prompt, wrap), as the server builds the agent from them."""
    a = Analyst(kit, folder)

    async def compute(ctx: RunContext, steps: list[Step]) -> str:
        """Run the numbers the answer needs, all together: one step per number or table. A step has `metrics` (keys),
        and as needed `by` (Table[Column], a date with :year/:quarter/:month), `filters` ({Table[Column]: value or
        [values]}), `top`, `order_by`, `ascending`, what-if `params`, and `show` on the step the answer is about.
        Each result is R<n> ok, empty or refused, with the filters and the owner's scope it ran under."""
        return await asyncio.to_thread(a.compute, [dict(s) for s in steps])

    async def explain(ctx: RunContext, metric: str, period: str, now: str, before: str,
                      filters: dict[str, str | int | float | list[str]] | None = None) -> str:
        """What drove a measure's change between two periods: `period` is the date column with its grain
        (Calendar[Date]:month), `now` and `before` two of its values. One layer down: the slice that carries most of it."""
        job = asyncio.to_thread(a.explain, metric, period, now, before, filters)
        try:
            return await asyncio.wait_for(job, EXPLAIN_SECONDS)
        except TimeoutError:
            return "refused: the warehouse did not answer in time. Answer from the levels you computed."

    async def describe(ctx: RunContext, metric: str) -> str:
        """What a measure is made of, what it needs, its parts, and the columns it can be cut by."""
        return await asyncio.to_thread(a.describe, metric)

    async def values(ctx: RunContext, column: str, filters: dict | None = None) -> str:
        """The values a column holds (Table[Column])."""
        return await asyncio.to_thread(kit.column_values, column, filters)

    async def find_metrics(ctx: RunContext, query: str) -> str:
        """Measures that match business words ("paid", "readmission rate"), for one not plainly in the list."""
        keys = a.catalog.query(query)[:12]
        return "\n".join(f"- {k}: {a.layer.metrics[k].get('label', k)}" for k in keys if k in a.layer.metrics) or "none match"

    def budgeted(fn):
        @functools.wraps(fn)  # the runtime reads the arguments and the description from the tool itself
        async def run(ctx: RunContext = None, **kw):
            t = TURN.get()
            if t is not None:
                t["calls"] += 1
                t["tools"].append(fn.__name__)
                if t["calls"] > MAX_CALLS or time.monotonic() - t["start"] > TURN_SECONDS:
                    return ENOUGH
            return await fn(ctx, **kw)
        return run

    def wrap(orch):
        inner, base = orch.stream, orch._system_prompt
        temp = os.environ.get("ANALYST_TEMPERATURE", "0")
        if getattr(orch, "_model_name", None) and not getattr(orch, "_chat_model", None) and temp != "none":
            from .agent_runtime import chat_openai
            orch._chat_model = lambda http: chat_openai(model=orch._model_name, api_key=orch._api_key, base_url=orch._base_url,
                                                        http_async_client=http, max_retries=2, temperature=float(temp))

        async def stream(message, session_id="default", **kw):
            t = {"calls": 0, "start": time.monotonic(), "n": 0, "tools": []}
            TURN.set(t)
            try:
                async for line in inner(message, session_id=session_id, system_prompt=f"{base}\n\n{await asyncio.to_thread(a.facts)}", **kw):
                    yield line
            finally:
                print(f"[analyst] {time.monotonic() - t['start']:.1f}s, {t['calls']} tool calls: {', '.join(t['tools']) or 'none'}",
                      file=sys.stderr, flush=True)

        orch.stream = stream
        return orch

    tools = [budgeted(f) for f in (compute, explain, describe, values, find_metrics)]
    return tools, a.prompt(), wrap
