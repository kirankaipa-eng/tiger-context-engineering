"""The agent's guarantees, in code: what a question means is settled here, not left to the model on each run.

    tools, prompt, wrap = install(kit, folder)   build the agent from tools and prompt, and pass it through wrap

    index(layer)      768 measures as ~50 families: a base metric, its variants, its aliases and what is legacy
    find(query)       the few metrics a question means, instead of a list of every measure
    guard             every compute passes here: an alias becomes its base metric, a prior-year measure the question
                      did not ask for is refused, a question with no period gets the model's one default period, and
                      the plan it ran under comes back to be stated in the answer
    plan(steps)       the agent's `compute`: once the metrics are found, every number the answer needs goes in as one
                      plan. All of it is checked against the model before the warehouse sees any of it, a plan that
                      cannot run comes back whole with what to fix, and one that can runs together, once
    resolve           a filter is settled against the tables themselves, however it was written: the column ("LOB", "Date"),
                      the grain, the date, quarter or range, a value's spelling. A month-end date on monthly data reads as
                      its month, and a period on a table only part of a measure reaches moves to one all of it reaches
    layers            "why" is peeled, not dug: one level over a few columns, then one more each time the user asks
    map               what a conversation has found is kept as a structure, not as chat: every table, every layer of why. A
                      question it covers is answered from there with no query, a value of a table it has is that row, and
                      the model reads it, so an answer can stand on all of what is known
    jobs              a question is a job of its own, not tied to the connection that asked it: the chat follows it for a few
                      seconds, then lets go and is free. Asked again, or reopened, it picks the job up where it is. A model that
                      stalls costs the words, never the answer: the question is read in code and the numbers still come
    query             every query of a turn shares one limit (`parallel`, 3 to 5 at a time), and asks nothing twice
    direct            a question takes four steps and only the first may need a model: what it asks for (`read`, in
                      code when every word of it is accounted for, else one small call, `ask_form`), what that reaches
                      (the captured model), its queries (together) and its answer (the number at once, then a sentence).
                      What this cannot settle goes to the agent, as before

precedents.yaml beside capture.yaml (optional) holds what owners have decided:

    terms: {pmpm: total_pmpm}   this word means this metric
    period: latest_month        what a question with no period means (the default), or `all`
    explain_seconds: 25         how long one layer of "why did it change" may run; past it, what was found so far is the answer
    hold_seconds: 3             how long the chat waits for an answer before the work carries on in the background
    parallel: 5                 how many queries run on the warehouse at once, 3 to 5
    turn_seconds: 75            how long one question may keep calling tools before it answers from what it has
    drivers: [Member[Plan], ...]  the columns a layer of "why" splits by, five at a time (default: the model's, without ids)
    direct: false               every question goes to the agent
    headline: [total_pmpm, ...]  what an open question ("how's the business?") answers with first (default: the rates
                                the model's other measures lean on most)

Works on any agent runtime: it wraps the toolkit, so the tools the runtime sees keep their names and signatures.
"""

from __future__ import annotations

import asyncio
import calendar
import contextvars
import copy
import datetime
import difflib
import functools
import json
import math
import re
import sys
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as finished
from pathlib import Path
from typing import TypedDict

import yaml

from . import bi_agent as bi
from .bilayer import GRAINS, ref

QUESTION = contextvars.ContextVar("desk_question", default="")
SESSION = contextvars.ContextVar("desk_session", default="")
TRACE = contextvars.ContextVar("desk_trace", default=None)  # this turn's warehouse time, for the line in the log

_ALIAS = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_BASE = re.compile(r"^\s*(?:CALCULATE|TOTALYTD)\(\s*\[([^\]]+)\]", re.IGNORECASE)
_YOY = re.compile(r"^\s*DIVIDE\(\s*\[([^\]]+)\]\s*,.*\)\s*-\s*1\s*$", re.IGNORECASE)
_PRIOR = re.compile(r"SAMEPERIODLASTYEAR|PREVIOUSYEAR|PARALLELPERIOD|DATEADD|TOTALYTD|\bPY\b|YoY", re.IGNORECASE)
_LEGACY = re.compile(r"^(zz|_|old\b|tmp\b|test\b)|\b(bkp|backup|deprecated|old)\b", re.IGNORECASE)
_ASKS_PRIOR = re.compile(r"\b(last year|prior|previous|year before|year ago|py|yoy|year over year|ytd|year to date|versus|vs|"
                         r"compared?|change[ds]?|growth|grew|trend)\b", re.IGNORECASE)
_NAMES_PERIOD = re.compile(r"\b(20\d\d|q[1-4]|ytd|year to date|this year|last year|last month|this month|january|february|march|"
                           r"april|may|june|july|august|september|october|november|december)\b", re.IGNORECASE)
_THIS = re.compile(r"\b(this|current|latest|most recent) month\b|\bright now\b", re.IGNORECASE)
_LAST = re.compile(r"\b(last|previous|prior) month\b", re.IGNORECASE)
_WHEN = re.compile(r"\b(this|last|current|latest|previous|prior|most recent) (month|quarter|year)\b|\b20\d\d\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_QUIET = {"what", "is", "are", "was", "were", "the", "our", "my", "we", "for", "of", "and", "in", "on", "a", "an", "how", "much",
          "many", "s", "did", "do", "doe", "me", "show", "give", "it", "that"}
SHOWN = 6
SLICES = 30            # the columns whose values are read ahead, once
SHORTLIST = 20         # the metrics one small call chooses from
READ_SECONDS = 20      # past this the question is read in code
HOLD_SECONDS = 3       # past this the chat lets go and the work carries on in the background
RUN_SECONDS = 60       # what a settled question may take before the agent is asked instead
JOB_SECONDS = 300      # what the agent may take in the background before the answer is what there is
HELD = "Working on that in the background. You can keep asking, or go to another chat: this will be here when it is ready."
GAVE_UP = "I could not finish that one. Name a measure and a period, and I will answer it straight away."
SAY_SECONDS = 30       # past this the number stands without its sentence
HEADLINES = 4          # the measures an open question shows, when no owner has said which
HEADLINE_SECONDS = 20  # past this an open question shows what came back
_PULSE = re.compile(r"pmpm|\bper\b|ratio|\brate\b", re.IGNORECASE)
_SLICE = re.compile(r"line of business|\blob\b|\bplan\b|product|segment|region", re.IGNORECASE)
_MONTHS = re.compile(r"\b(month before|previous month|prior month|last month|month over month|mom)\b", re.IGNORECASE)
_NOT_PLAIN = re.compile(r"\b(quarter|year|ytd|q[1-4])\b", re.IGNORECASE)
_READ = """You turn a business question into a form for a metrics engine. Reply with JSON only:
{"route": "compute", "metrics": [keys], "by": [columns], "filters": {column: value or [values]}, "top": n, "order_by": key, "ascending": false}
route "explain" for why something changed or what drove it: {"route": "explain", "metrics": [a key for each measure it names], "filters": {...}}.
route "overview" for an open question that names no one measure ("how's the business doing?", "where should I worry?"): {"route":
"overview", "metrics": []}. If a broad word fits several measures and nothing favours one ("how are we on cost?"), list those: 2 to 4 keys.
route "known" when every number the answer needs is already under "Already known": {"route": "known"}.
route "agent" for anything else: rows or lists of things, forecasts, whether a change is real or chance, or nothing listed fits.
Use only the keys, columns and values listed. Metrics are best first: take the first that fits unless the question says otherwise.
Leave out what the question does not ask for: a question with no period gets no date filter, and one is applied for you.
Dates: {"DATE:year": 2025}, {"DATE:quarter": "2026-Q1"}, {"DATE:month": "2026-09"}; several periods compared are a list of values with
the same column in `by`. "Highest" or "top 3" is `by` with `top` (1 or 3); lowest adds ascending true.
A follow-up ("what about X", "and by plan", "that one") starts from the previous form and changes only what it says. A question complete in
itself starts from nothing: no metric, column or filter of the previous form comes with it."""
_SAY = """Write the answer to the question from the results, for a business reader. The number is already on screen above your words, so
add what it means: one or two plain sentences, at most three numbers, all taken from the results. Say the period, with months as
people say them (September 2026). A ratio or rate reads as a percent. Say which metric a result is if the question was loose.
With several measures, say what stands out most. "Already known" is what this conversation found earlier: use it to connect or compare, and
say which period a number from it is for.
Plain sentences only: no headings, lists or bold. No metric keys, formulas, SQL or data source. Add no cause the results do not show."""
EXPLAIN_SECONDS = 25   # one layer of "why": what it has found by then is the answer, and the next layer is the user's to ask for
EXPLAIN_QUERIES = 12   # queries a layer may run after the ones it fetches together, to test what it found
MAP_SECONDS = 1800     # how long what a conversation found stands without asking the warehouse again
MAP_TABLES = 40        # the tables one conversation keeps, newest first
MAP_CHARS = 1800       # what of it the model reads
LOOKED = ("Error: this metric has had its layer for this question. Do not look again. Answer from what it found and offer to dig "
          "into what leads.")
TURN_SECONDS = 75      # past this a turn's tools stop and it answers from what it has
TURN_CALLS = 12        # or past this many tool calls, so a question cannot go round and round
REFUSALS = 2           # the same refusal this often and asking again will not help: the turn answers from what it has
WHY_METRICS = 3        # the measures one "what is driving" question looks into
PLAN_STEPS = 6         # what one plan may ask for; more than this is more than one question
PLAN_SECONDS = 5       # a plan late in its turn still gets this long
PLAN_RAN = ("Error: this question's plan has run. Do not call compute again. Answer from its results, and say in one line "
            "what they do not cover.")
OUT_OF_TURN = ("Error: this question has had its time. Do not call another tool. Answer now, in two or three plain sentences, "
               "from what you already have, and say in one line what you could not finish.")
SAME_REFUSAL = ("That is the same refusal again, so another try will not help. Do not call another tool. Answer now from what "
                "you have, and say in one plain line which number could not be got and why.")
SPLITS = 12            # the columns a first look splits by, unless brief says which (`drivers` in precedents.yaml)
AT_ONCE = 5            # queries on the warehouse together, unless `parallel` says fewer; never under 3 or over 5
LAYER_COLUMNS = 5      # what one layer of "why" splits by; the next layer takes the next ones
QUERY_MEMORY = 300     # seconds a turn may reuse the answer to a query it has asked before
_DEEPER = re.compile(r"\b(deeper|dig|drill|further|underneath|behind (that|this|it)|break (that|this|it) down|root cause)\b", re.IGNORECASE)
_PEEL = re.compile(r"\b(what is driving|what s driving|whats driving|what drove|why)\b", re.IGNORECASE)
_DRIVES = re.compile(r"\b(drivers?|driving|drove|drive|why|behind|caus\w+)\b", re.IGNORECASE)
_LOOSE = {"primary", "main", "key", "top", "biggest", "major", "drivers", "driver", "driving", "drove", "drive", "why", "behind", "trends", "trend", "cost",
          "costs", "spend", "spending", "increase", "increases", "increased", "rise", "rising", "rose", "change", "changes", "changed", "caused", "causing",
          "cause", "recent", "recently", "up", "down", "over", "time", "s", "has", "have", "been", "going", "gone", "so", "much", "high", "higher"}
_LEANS = re.compile(r"\b(what about|how about|and|that|those|these|this one|it|same|instead|also|only|just|now|then|them|there|their)\b", re.IGNORECASE)
_GRAIN_SAID = {"monthly": "month", "mon": "month", "yearly": "year", "annual": "year", "yr": "year", "quarterly": "quarter", "qtr": "quarter",
               "weekly": "week", "daily": "day", "date": "day"}
_NOT_DRIVER = re.compile(r"\b(id|key|guid|number|no|date|time|month|year)\]", re.IGNORECASE)


class Step(TypedDict, total=False):
    """One number or table the answer needs."""
    metrics: list[str]
    by: list[str]
    filters: dict[str, str | int | float | list[str]]
    top: int
    order_by: str
    ascending: bool
    params: dict[str, float]


def _text(content) -> str:
    """What a model said, without its reasoning: a reasoning model's reply is a list of blocks, not a string."""
    if isinstance(content, str):
        return content
    return "".join(c.get("text") or "" for c in content or [] if isinstance(c, dict) and c.get("type", "text") == "text")


def _period(value) -> str:
    """2026-09 as people say it, for a screen: September 2026, and Q1 2026."""
    v = str(value)
    if re.fullmatch(r"\d{4}-\d{2}", v):
        return f"{calendar.month_name[int(v[5:7])]} {v[:4]}"
    return f"{v[5:]} {v[:4]}" if re.fullmatch(r"\d{4}-Q\d", v) else v


def _words(text: str) -> list[str]:
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in _WORD.findall(text.lower())]


def index(layer) -> dict:
    """Each captured metric's place: {key: {base, kind}}. kind is base, alias (the same number under another name),
    prior (a period other than the one asked), variant (a slice of its base) or legacy."""
    by_label = {m["label"].lower(): k for k, m in layer.captured().items()}
    out = {}
    for k, m in layer.captured().items():
        dax, label = " ".join(str(m.get("dax") or "").split()), m["label"]
        alias, base = _ALIAS.match(dax), _BASE.match(dax) or _YOY.match(dax)
        target = alias or base
        root = by_label.get(target.group(1).lower(), k) if target else k
        kind = ("legacy" if _LEGACY.search(label) else "alias" if alias else "prior" if _PRIOR.search(f"{dax} {label}")
                else "variant" if base and root != k else "base")
        out[k] = {"base": root, "kind": kind}
    return out


class Limited:
    """The warehouse for one investigation: past its time or its queries every query fails at once, which the
    investigation already treats as a split it cannot make, so it ends with what it has."""

    def __init__(self, wh, seconds: float, queries: int, at_once: int = AT_ONCE):
        self._wh, self.deadline, self.left, self.stopped, self.at_once = wh, time.monotonic() + seconds, queries, False, at_once
        self.ready: dict = {}  # answers fetched ahead, together

    def __getattr__(self, name):
        return getattr(self._wh, name)

    def ahead(self, sqls: list[str]) -> None:
        """Run these together; each is then answered from here when the investigation reaches it."""
        def one(sql):
            try:
                self.ready[sql] = self._wh.query(sql, limit=2000)
            except Exception:  # noqa: BLE001, S110 - it fails again in its turn, where it is handled
                pass
        with ThreadPoolExecutor(self.at_once) as pool:
            list(pool.map(lambda sql: contextvars.copy_context().run(one, sql), sqls))

    def query(self, sql, limit=None):
        if sql in self.ready:
            return self.ready[sql]
        self.left -= 1
        if self.left < 0 or time.monotonic() > self.deadline:
            self.stopped = True
            raise TimeoutError("this investigation has used its time")
        return self._wh.query(sql, limit=limit)


def _root(idx: dict, key: str) -> str:
    seen = set()
    while key in idx and idx[key]["kind"] in ("alias", "legacy") and idx[key]["base"] != key and key not in seen:
        seen.add(key)
        key = idx[key]["base"]
    return key


def _when(text: str) -> tuple[str, str | None] | None:
    """A date however it is written, as (yyyy-mm, yyyy-mm-dd); the day is None when it names only a month. None: not a date."""
    s = text.strip()
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?(?:[T ].*)?", s):
        y, mo, d = m.group(1), int(m.group(2)), m.group(3)
    elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s):
        y, mo, d = m.group(3), int(m.group(1)), m.group(2)
    elif (m := re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+(\d{4})", s)) and m.group(1)[:3].title() in calendar.month_abbr:
        y, mo, d = m.group(2), list(calendar.month_abbr).index(m.group(1)[:3].title()), None
    else:
        return None
    if not 1 <= mo <= 12 or (d and not 1 <= int(d) <= calendar.monthrange(int(y), mo)[1]):
        return None
    return f"{y}-{mo:02d}", f"{y}-{mo:02d}-{int(d):02d}" if d else None


class Job:
    """One question at work: what it has said so far, and what it has found. It belongs to no connection, so a chat can
    leave and another can pick it up from the first line."""

    def __init__(self):
        self.id, self.lines, self.kept, self.done, self.at, self.task = uuid.uuid4().hex, [], [], False, time.monotonic(), None


def _scope(filters: dict) -> str:
    return json.dumps(sorted((c, sorted(map(str, v)) if isinstance(v, (list, tuple, set)) else str(v)) for c, v in (filters or {}).items()))


class Known:
    """The map of one conversation: what it has looked at, as data. `tables` are computed results, each with the metrics,
    grouping and filters it answers; `layers` are the layers of why, by what they looked at. It stands for MAP_SECONDS."""

    def __init__(self):
        self.tables: list[dict] = []
        self.layers: dict[tuple, dict] = {}
        self.lock = threading.Lock()

    def keep(self, metrics: list[str], by: list[str], where: dict, record: dict, whole: bool) -> None:
        entry = {"metrics": list(metrics), "by": list(by or []), "where": dict(where or {}), "scope": _scope(where), "record": record,
                 "whole": whole, "at": time.monotonic()}
        with self.lock:
            same = lambda t: t["scope"] == entry["scope"] and t["by"] == entry["by"] and t["metrics"] == entry["metrics"]
            self.tables = [*[t for t in self.tables if not same(t)], entry][-MAP_TABLES:]

    def find(self, metrics: list[str], by: list[str], where: dict, plain) -> tuple[list[str], list[list], dict] | None:
        """(columns, rows, record) of a table that answers this: the same question, or a value of a column it is grouped
        by, which is that row. `plain(metric)` says a metric's row stands alone: a share of the total does not."""
        by, fresh = list(by or []), time.monotonic() - MAP_SECONDS
        for t in reversed(self.tables):
            r = t["record"]
            cols = list(r.get("columns") or [])
            if t["at"] < fresh or not t["whole"] or not set(metrics) <= set(t["metrics"]) or not set(metrics) <= set(cols):
                continue
            if not set(t["where"]) <= set(where) or len(cols) != len(t["by"]) + len(t["metrics"]):
                continue
            narrow = {c: v for c, v in where.items() if c not in t["where"] or _scope({c: v}) != _scope({c: t["where"][c]})}
            if set(narrow) - set(t["by"]) or by != [b for b in t["by"] if b not in narrow or b in by]:
                continue
            if narrow and not all(plain(k) for k in metrics):
                continue
            wanted = {c: set(map(str, v if isinstance(v, (list, tuple, set)) else [v])) for c, v in narrow.items()}
            at = {c: i for i, c in enumerate(t["by"])}
            rows = [row for row in r.get("shown") or [] if all(str(row[at[c]]) in vals for c, vals in wanted.items())]
            if not rows or any(len({str(row[at[c]]) for row in rows}) < len(vals) for c, vals in wanted.items()):
                continue
            keep = [at[b] for b in by] + [cols.index(k) for k in metrics]
            return [cols[i] for i in keep], [[row[i] for i in keep] for row in rows], r
        return None

    def text(self, label, show, limit: int = MAP_CHARS) -> str:
        """What the model reads: the newest first, as far as `limit`."""
        out = []
        for e in reversed(list(self.layers.values())):
            x = e["record"].get("explain") or {}
            if not x.get("total_before"):
                continue
            step = (x.get("steps") or [{}])[-1]
            within = ", ".join(str(v) for c, v in e["where"].items() if ":" not in c)
            line = f"- {x['label']}{' in ' + within if within else ''}, {x['before']} to {x['now']}: {x['total_after'] / x['total_before'] - 1:+.1%}"
            if step.get("value"):
                line += f", led by {step['by']} = {step['value']} ({step['carries']:.0%} of the movement)"
                others = [f"{o['by']} = {o['value']}" for o in step.get("tested") or [] if o.get("status") == "also"]
                line += f"; also {', '.join(others)}" if others else ""
            out.append(line + "\n")
        for t in reversed(self.tables):
            r = t["record"]
            cols = list(r.get("columns") or [])
            scope = "; ".join(f"{c} = {v}" for c, v in t["where"].items())
            rows = [" · ".join(show(c, v) for c, v in zip(cols, row)) for row in (r.get("shown") or [])[:8]]
            more = f" and {len(r['shown']) - 8} more rows" if len(r.get("shown") or []) > 8 else ""
            out.append(f"- {', '.join(label(k) for k in t['metrics'])}{' by ' + ', '.join(t['by']) if t['by'] else ''}"
                       f"{' (' + scope + ')' if scope else ''}: {' | '.join(rows)}{more}\n")
        said, room = "", limit
        for line in out:
            if len(line) > room:
                break
            said, room = said + line, room - len(line)
        return said.strip()


def _quarter(text: str) -> str | None:
    """2026-Q3, however it is written."""
    if m := re.fullmatch(r"(\d{4})[-\s]?Q([1-4])", text.strip(), re.IGNORECASE):
        return f"{m.group(1)}-Q{m.group(2)}"
    if m := re.fullmatch(r"Q([1-4])[-\s,]*(\d{4})", text.strip(), re.IGNORECASE):
        return f"{m.group(2)}-Q{m.group(1)}"
    return None


def _at(grain: str, value) -> str | int | None:
    """A date, month, quarter or year as a column at this grain holds it. None: it is none of those, or too coarse."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value if grain == "year" else None
    text = str(value).strip()
    q, when = _quarter(text), _when(text)
    if grain == "year":
        return int(text) if re.fullmatch(r"\d{4}", text) else int(q[:4]) if q else int(when[0][:4]) if when else None
    if grain == "quarter":
        return q or (f"{when[0][:4]}-Q{(int(when[0][5:]) + 2) // 3}" if when else None)
    return when[0] if grain == "month" and when else None


def _span(value) -> tuple | None:
    """The two ends of a range, as a dict or as words; None when it is not one."""
    if isinstance(value, dict):
        low = {str(k).lower(): v for k, v in value.items()}
        ends = next(((low[a], low[b]) for a, b in (("from", "to"), ("start", "end"), ("gte", "lte")) if a in low and b in low), None)
    else:
        parts = re.split(r"\s+(?:to|through|thru)\s+|\s*\.\.\s*", value.strip()) if isinstance(value, str) else []
        ends = tuple(parts) if len(parts) == 2 else None
    return ends if ends and all(_when(str(e)) for e in ends) else None


def _months_between(low: str, high: str) -> list[str]:
    a, b = (int(x[:4]) * 12 + int(x[5:7]) - 1 for x in sorted((low, high)))
    return [f"{i // 12}-{i % 12 + 1:02d}" for i in range(a, b + 1)]


def _squeeze(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


class Harness:
    def __init__(self, kit, folder: str | Path | None = None):
        self.kit, self.layer = kit, kit.layer
        self.idx = index(self.layer)
        p = Path(folder or kit.folder or ".") / "precedents.yaml"
        conf = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
        self.terms = {" ".join(_words(k)): v for k, v in (conf.get("terms") or {}).items()}
        self.period = conf.get("period", "latest_month")
        self.seconds = float(conf.get("explain_seconds") or EXPLAIN_SECONDS)
        self.drivers = conf.get("drivers") or []
        self.turn_seconds = float(conf.get("turn_seconds") or TURN_SECONDS)
        self.at_once = min(5, max(3, int(conf.get("parallel") or AT_ONCE)))
        self._gate, self._lock, self._memo = threading.BoundedSemaphore(self.at_once), threading.Lock(), {}
        self.hold = float(conf.get("hold_seconds") or HOLD_SECONDS)
        self.jobs: dict[tuple, Job] = {}
        # what a metric is made of counts too: Rx PMPM is found by "pharmacy" through [Pharmacy Paid]
        labels = {m["label"].lower() for m in self.layer.captured().values()}
        made_of = lambda m: " ".join(r for r in re.findall(r"\[([^\]]+)\]", str(m.get("dax") or "")) if r.lower() in labels)
        self.docs = {k: Counter(_words(f"{m['label']} {m.get('description') or ''} {made_of(m)}")) for k, m in self.layer.captured().items()}
        self.df = Counter(w for d in self.docs.values() for w in d)
        self.names = {k: " ".join(_words(m["label"])) for k, m in self.layer.captured().items()
                      if self.idx[k]["kind"] in ("base", "variant", "prior") and len(_words(m["label"])) > 1}
        self.direct = conf.get("direct", True)
        self.values: dict[str, list[str]] = {}  # what the columns a question slices by hold, for reading it
        self.headline = [k for k in conf.get("headline") or [] if k in self.idx] or self._pulse()
        self.ledger: dict[str, dict] = {}   # per conversation: the period in play
        self._latest: dict[str, str] = {}
        self._compute = kit.compute
        kit.compute = self.compute
        self._known_values: dict[str, tuple[list, bool] | None] = {}  # what a column holds, and whether that is all of it
        tables = getattr(self.layer, "tables", None) or {}
        self._refs = {(_squeeze(t), _squeeze(c)): ref(t, c) for t, i in tables.items() for c in (i.get("columns") or {})}
        self._resolve = getattr(kit, "resolve", None)
        if self._resolve:
            kit.resolve = self.resolve
        if hasattr(kit, "column_values"):
            kit.column_values = lambda column, filters=None: type(kit).column_values(kit, self._column(column), filters)
        self._timed(kit.wh)
        threading.Thread(target=self._warm, daemon=True).start()

    def _pulse(self) -> list[str]:
        """The measures to open with when no owner has said: rates and per-member figures, those most built on first."""
        daxes = " ".join(str(m.get("dax") or "") for m in self.layer.captured().values()).lower()
        rates = [k for k, m in self.layer.captured().items() if self.idx[k]["kind"] == "base" and _PULSE.search(m["label"])]
        total = lambda k: "total" not in self.layer.metrics[k]["label"].lower()  # the whole before its parts
        return sorted(rates, key=lambda k: (total(k), -daxes.count(f"[{self.layer.metrics[k]['label'].lower()}]"), k))[:HEADLINES]

    def rank(self, query: str, n: int = SHOWN) -> list[str]:
        """The metrics a question means, best first: an owner's precedent, then base metrics on the words asked."""
        said = " ".join(_words(query))
        words = [w for w in _words(_WHEN.sub(" ", query)) if w not in _QUIET]
        fixed = [v for t, v in self.terms.items() if re.search(rf"\b{re.escape(t)}\b", said)]
        total = len(self.docs)

        def score(k: str) -> float:
            d, kind = self.docs[k], self.idx[k]["kind"]
            s = sum(math.log(1 + total / (1 + self.df[w])) for w in set(words) if w in d)
            label = _words(self.layer.metrics[k]["label"])
            extra = len([w for w in label if w not in words])
            return s - 0.25 * extra + (1.5 if kind == "base" else 0) + (0.5 if "total" in label else 0)

        pool = [k for k in self.docs if self.idx[k]["kind"] not in ("alias", "legacy") and set(words) & set(self.docs[k])]
        if not _ASKS_PRIOR.search(query):
            pool = [k for k in pool if self.idx[k]["kind"] != "prior"]
        # a variant only when the question names what sets it apart from its base
        pool = [k for k in pool if self.idx[k]["kind"] == "base" or
                set(words) & (set(_words(self.layer.metrics[k]["label"])) - set(_words(self.layer.metrics[self.idx[k]["base"]]["label"])))]
        covers = lambda k: len(set(words) & set(self.docs[k]))  # every word asked beats a shorter name
        return list(dict.fromkeys(fixed + sorted(pool, key=lambda k: (-covers(k), -score(k), k))))[:n]

    def find(self, query: str) -> str:
        """What rank found, as the agent reads it: what each needs and what it can be cut by."""
        best = self.rank(query)
        if not best:
            return f"No metric matches {query!r}. Try other words, or say it is not in the model."
        lines = [f"- {k} ({self.layer.metrics[k]['label']}): {bi.describe(self.layer, k)}{bi.needs(self.layer, k)}" for k in best]
        cols = [c for c in self.layer.columns_for(best[:1]) if self.kit._type(c) != "dateTime"]
        dates = [c for c in self.layer.columns_for(best[:1]) if self.kit._type(c) == "dateTime"][:1]
        holds = [f"{c}: {', '.join(self.values[c][:8])}" for c in cols if self.values.get(c)][:8]
        return ("Best first; the first is the default for these words. If the question names several, use each:\n" + "\n".join(lines)
                + f"\nGroup or filter it by: {', '.join(cols)}"
                + (f"\nWhat those hold: {'; '.join(holds)}" if holds else "")
                + (f"\nDates: {dates[0]}:year, :quarter or :month, e.g. filters {{{dates[0]}:year: 2025}}" if dates else "")
                + "\nNo period in the question: leave it out, the period is applied for you. Filters are repaired against the data, so "
                  "write them as they come. about_metric gives one metric's full detail.")

    def details(self, metric: str) -> str:
        """All an asker needs to get one metric right, from the model and the tables: what it is made of, what it must be
        given, the period it has data for, how to write one, what each column holds, and what only reaches part of it."""
        key = _root(self.idx, metric)
        if key not in self.idx:
            best = self.rank(metric)
            if not best:
                return f"Error: no metric {metric!r}. Call find_metrics with the business words."
            key = best[0]
        m, reach = self.layer.metrics[key], self.layer.columns_for([key])
        lines = [f"{m['label']} ({key}): {bi.describe(self.layer, key)}{bi.needs(self.layer, key)}"]
        if m.get("description"):
            lines.append(str(m["description"]))
        parts = [k for k in self.layer.leaves(key) if k != key]
        if parts:
            lines.append(f"Made of: {', '.join(self.layer.metrics[k]['label'] for k in parts)}")
        if (col := self._date(key)):
            base = col.split(":")[0]
            days, latest = self._days(base), self._latest_month(key, col)
            span = f"; the column runs {days[0][:10]} to {days[-1][:10]}" if days else ""
            lines.append(f"Period: {base}; latest month with a number {latest or 'not known'}{span}.")
            lines.append(f"Write it as {base}:month = \"2026-09\", {base}:quarter = \"2026-Q3\" or {base}:year = 2026; several periods "
                         f"are a list, and a list needs {base}:month in `by` to compare them.")
        for c in [c for c in reach if self._kind(c) != "dateTime"][:SLICES]:
            held = self.values.get(c)
            lines.append(f"{c}: {', '.join(held[:12])}" + (f" and {len(held) - 12} more" if len(held) > 12 else "") if held else c)
        some = sorted({r for k in self.layer.leaves(key) for r in self.layer.columns_for([k])} - set(reach))
        wrong = [c for c in some if self._kind(c) == "dateTime"]
        if wrong:
            lines.append(f"Reach only part of it, so do not use them for its period: {', '.join(wrong)}.")
        return "\n".join(lines)

    def _map(self) -> Known:
        return self.ledger.setdefault(SESSION.get(), {}).setdefault("map", Known())

    def known(self, limit: int = MAP_CHARS) -> str:
        """The conversation's map, as the model reads it."""
        label = lambda k: self.layer.metrics[k]["label"] if k in self.layer.metrics else k
        return self._map().text(label, lambda c, v: f"{label(c)} {self._show(c, v)}" if c in self.layer.metrics else str(v), limit)

    def _stands_alone(self, key: str) -> bool:
        """A value's row is what filtering to it would give, unless the measure reads past the filter (a share, a rank)."""
        try:
            return not any(self.layer.metrics[k].get(x) for k in bi._tree(self.layer, key) for x in ("context", "rank", "latest"))
        except Exception:  # noqa: BLE001 - when in doubt it is asked
            return False

    def _carried(self, form: dict, question: str) -> dict:
        """What the model carried over from the last question and this one does not lean on, taken off: a new question about
        everyone must not quietly be about the last answer's Commercial. Left when the question says it, or leans on the last."""
        last = (self.ledger.get(SESSION.get(), {}).get("form") or {}).get("filters") or {}
        if _LEANS.search(question) or not form.get("filters"):
            return form
        said = set(_words(question))
        held = lambda v: said & set(_words(" ".join(map(str, v if isinstance(v, list) else [v]))))
        keep = {c: v for c, v in form["filters"].items() if self._kind(c) == "dateTime" or last.get(c) != v or held(v)}
        return {**form, "filters": keep}

    def _recall(self, step: dict, by, top, order_by, ascending, params, kit) -> str | None:
        """The answer from the map, as the query would have given it; None when the map does not cover it."""
        if params or (order_by and order_by not in step["metrics"]):
            return None
        try:
            where = self.kit.resolve(step["filters"])[0] if self._resolve else step["filters"]
        except Exception:  # noqa: BLE001 - the query reports it
            return None
        hit = self._map().find(step["metrics"], by, where, self._stands_alone)
        if not hit:
            return None
        cols, rows, was = hit
        if order_by or top:
            at = cols.index(order_by or step["metrics"][0])
            rows = sorted(rows, key=lambda r: (r[at] is not None, r[at] or 0), reverse=not ascending)
        rows = rows[:top] if top else rows
        kit._keep({**was, "kind": "compute", "columns": cols, "shown": [tuple(r) for r in rows], "metrics": list(step["metrics"]),
                   "by": list(by or []), "filters": dict(where)})
        if (t := TRACE.get()) is not None:
            with self._lock:
                t["recalled"] = t.get("recalled", 0) + 1
        table = bi._markdown(cols, [[bi._fmt(v) if c in step["metrics"] else str(v) for c, v in zip(cols, r)] for r in rows])
        return f"Data: already found in this conversation\n\n{table}"

    def _note(self, step: dict, by, kit) -> None:
        r = (getattr(kit, "results", None) or [None])[-1]
        if r and r.get("kind") == "compute" and r.get("metrics") == step["metrics"]:
            self._map().keep(step["metrics"], by, r.get("filters") or {}, r, len(r.get("shown") or []) < getattr(kit, "max_rows", 10 ** 9))

    def _spread(self, cols: list[str]) -> list[str]:
        """Columns taken a table at a time, so one layer looks across the business and not five ways at one table."""
        tables: dict[str, list[str]] = {}
        for c in cols:
            tables.setdefault(c.split("[")[0], []).append(c)
        rounds = [t[i] for i in range(max(map(len, tables.values()), default=0)) for t in tables.values() if i < len(t)]
        return rounds

    def explain(self, metric, period, now, before, filters=None, used=()) -> str:
        """One layer of why a metric changed: the columns that can drive it, a few at a time, fetched together, one level
        down. What leads is kept, and the next layer starts from it when the user asks: never before. Within its limits,
        on a copy of the toolkit so nothing else meets the limited warehouse."""
        metric = _root(self.idx, metric)
        t = TRACE.get()
        period = self._column(period, [metric])
        period = period if ":" in period else f"{period}:month"
        grain = period.split(":")[1]
        now, before = (str(_at(grain, v) or v) for v in (now, before))
        filters = self._grains({self._column(c, [metric]): v for c, v in (filters or {}).items()})
        layer = (metric, period, now, before, _scope(filters))
        known = self._map().layers.get(layer)
        if known and time.monotonic() - known["at"] < MAP_SECONDS:
            with self._lock:
                self.kit._keep(known["record"])
            self.ledger.setdefault(SESSION.get(), {}).setdefault("peel", {})[metric] = known["peel"]
            if t is not None:
                t["recalled"] = t.get("recalled", 0) + 1
            return known["said"]
        if t is not None:
            with self._lock:
                if metric in t.setdefault("looked", set()):
                    return LOOKED
                t["looked"].add(metric)
        seen = self.kit._seq
        run = copy.copy(self.kit)
        run.wh = wh = Limited(self.kit.wh, self.seconds, EXPLAIN_QUERIES, self.at_once)
        every = [c for c in self.kit._splits(metric, period) if c not in used]
        cols = ([c for c in self.drivers if c in every] or self._spread([c for c in every if not _NOT_DRIVER.search(c)]))[:LAYER_COLUMNS]
        run._splits = lambda *_: cols
        try:
            where = self.kit.resolve(filters)[0]
            sql = lambda by, w: self.layer.compile([metric], by, w, dialect=wh.dialect, row_filters=self.kit.row_filters)
            pair = {**where, period: [now, before]}
            wh.ahead([sql([period], pair), sql([period], where), *[sql([c, period], pair) for c in cols]])
        except Exception:  # noqa: BLE001, S110 - the investigation reports what is wrong with its arguments
            pass
        out = bi.BIToolkit.explain(run, metric, period, now, before, filters, depth=1)
        fresh = [r for r in run.results if r["seq"] > seen]
        with self._lock:  # another measure's layer may be finishing beside this one
            for r in fresh:
                self.kit._keep(r)
        steps = next((r["explain"]["steps"] for r in reversed(fresh) if r.get("explain")), [])
        lead = (steps[-1]["by"], steps[-1]["value"]) if steps else None
        peel = {"metric": metric, "period": period, "now": now, "before": before, "filters": filters, "used": [*used, *cols], "lead": lead}
        self.ledger.setdefault(SESSION.get(), {}).setdefault("peel", {})[metric] = peel
        if wh.stopped:
            out += ("\n\nThis layer stopped at its limit, so it is partial: report what it found, and do not say there is no driver. "
                    "Do not run it again for this question.")
        else:
            out += ("\n\nThis is a first look, one layer down, and that is the answer for now: do not look further unless asked."
                    + (f" End by offering to dig into {lead[1]}." if lead else ""))
        record = next((r for r in reversed(fresh) if r.get("explain")), None)
        if record and not wh.stopped:  # a partial layer is not what is known
            self._map().layers[layer] = {"record": record, "said": out, "peel": peel, "where": filters, "at": time.monotonic()}
        return out

    def next_layer(self, question: str) -> dict | None:
        """A follow-up that peels: "dig deeper" takes what led each measure, a click on an offer the one it names."""
        peel = self.ledger.get(SESSION.get(), {}).get("peel") or {}
        leads = [p for p in peel.values() if p.get("lead")]
        said = " ".join(_words(question))
        named = [p for p in leads if re.search(rf"\b{re.escape(' '.join(_words(str(p['lead'][1]))))}\b", said)]
        if named and (_PEEL.search(said) or _DEEPER.search(said)):
            leads = named
        elif not _DEEPER.search(said):
            return None
        return {"route": "layer", "metrics": [p["metric"] for p in leads], "peel": leads[:WHY_METRICS]} if leads else None

    def peel(self, form: dict) -> str:
        """The next layer of each: inside what led the last, across the columns not looked at yet."""
        one = lambda p: self.explain(p["metric"], p["period"], p["now"], p["before"], {**p["filters"], p["lead"][0]: p["lead"][1]}, p["used"])
        found = self._together([functools.partial(one, p) for p in form["peel"]])
        return "\n\n".join(x for x in found if not x.startswith("Error"))

    def _together(self, jobs: list) -> list:
        with ThreadPoolExecutor(max(len(jobs), 1)) as pool:
            return [f.result() for f in [pool.submit(contextvars.copy_context().run, j) for j in jobs]]

    def _mine(self, seen: int) -> list[dict]:
        """What this question found: two questions may be at work in one conversation."""
        me = bi.ASKER.get()
        return [r for r in self.kit.results if r["seq"] > seen and r.get("asker") in (None, me)]

    def layer_line(self, seen: int) -> str:
        """Where each measure moved and what leads it, as soon as the layer is back: ahead of the sentence."""
        lines = []
        for r in self._mine(seen):
            e = r.get("explain")
            if not e or not e.get("total_before"):
                continue
            change = e["total_after"] / e["total_before"] - 1
            lead = f" · led by {e['steps'][-1]['value']}" if e.get("steps") else ""
            within = [str(v) for c, v in (r.get("filters") or {}).items() if self._kind(c) != "dateTime"]  # a layer's number is of its part
            where = f" in {', '.join(within)}" if within else ""
            lines.append(f"{e['label']}{where} **{change:+.1%}** · {_period(e['now'])} against {_period(e['before'])}{lead}")
        return "\n\n".join(lines) + "\n\n" if lines else ""

    def layer_offer(self) -> str:
        """What the next layer would be, to ask for with a click."""
        peel = self.ledger.get(SESSION.get(), {}).get("peel") or {}
        asks = [f"what is driving {self.layer.metrics[p['metric']]['label']} in {p['lead'][1]}" for p in peel.values() if p.get("lead")]
        return f"\n\nWould you like {' or '.join(asks[:WHY_METRICS])}?" if asks else ""

    def _date(self, metric: str) -> str | None:
        cols = [c for c in self.layer.columns_for([metric]) if self.kit._type(c) == "dateTime"]
        cols.sort(key=lambda c: (not re.search(r"calendar|date\b|dim.?date", c.split("[")[0], re.IGNORECASE), c))
        return f"{cols[0]}:month" if cols else None

    def _latest_month(self, metric: str, col: str) -> str | None:
        """The last month with a number: a calendar table runs past the data as often as not. Asked once per date
        column, and ahead of the first question, because it reads the whole history."""
        if col not in self._latest:
            try:
                sql = self.layer.compile([metric], [col], dialect=self.kit.wh.dialect, row_filters=self.kit.row_filters)
                have = [str(r[0]) for r in self.kit.wh.query(sql, limit=5000)[1] if r[-1] is not None]
            except Exception:  # noqa: BLE001 - without it a question keeps the period the agent gave
                return None
            if have:
                self._latest[col] = max(have)[:7]
        return self._latest.get(col)

    def _warm(self) -> None:
        for k, v in self.idx.items():
            if v["kind"] == "base" and (col := self._date(k)):
                self._latest_month(k, col)
                break
        self._slices()

    def _slices(self) -> None:
        """What a question may name: the values of the columns it slices by. A few small queries, once, ahead of it."""
        base = next((k for k, v in self.idx.items() if v["kind"] == "base"), None)
        if not base:
            return
        cols = self.drivers or [c for c in self.layer.columns_for([base]) if self.kit._type(c) == "string" and not _NOT_DRIVER.search(c)]

        def one(c):
            try:
                rows = self.kit.wh.query(self.kit._values_sql(c), limit=61)[1]
            except Exception:  # noqa: BLE001 - a column it cannot read is one the agent looks up
                return
            if len(rows) <= 60:
                self.values[c] = [str(r[0]) for r in rows if r[0] is not None]
        with ThreadPoolExecutor(self.at_once) as pool:
            list(pool.map(one, cols[:SLICES]))

    def _timed(self, wh) -> None:
        """One limit on what runs at once, whoever asks; and within a turn, nothing asked twice."""
        inner = wh.query

        def query(sql, limit=None):
            turn, key = bool(QUESTION.get()), (sql, limit)
            kept = self._memo.get(key) if turn else None
            if kept and time.monotonic() - kept[0] < QUERY_MEMORY:
                return kept[1]
            t = TRACE.get()
            with self._gate:  # time in the queue is not the warehouse's
                t0 = time.monotonic()
                if t is not None:
                    with self._lock:
                        t["live"] = t.get("live", 0) + 1
                        t.setdefault("since", t0) if t["live"] > 1 else t.update(since=t0)
                try:
                    out = inner(sql, limit=limit)
                finally:
                    if t is not None:
                        ended = time.monotonic()
                        with self._lock:
                            t.update(n=t["n"] + 1, secs=t["secs"] + ended - t0, slowest=max(t["slowest"], ended - t0), live=t["live"] - 1)
                            if not t["live"]:
                                t["wall"] = t.get("wall", 0.0) + ended - t["since"]
            if turn:
                if len(self._memo) >= 500:
                    self._memo.clear()
                self._memo[key] = (time.monotonic(), out)
            return out
        wh.query = query

    def _kind(self, column: str) -> str | None:
        try:
            return self.kit._type(column.split(":")[0])
        except Exception:  # noqa: BLE001 - not a column: what it is checked against says so
            return None

    def _column(self, name, metrics: list[str] | None = None) -> str:
        """The column a name means, from the tables: "LOB", "Date", calendar[date]:Month. A name that fits nothing, or more
        than one column the measure reaches, comes back as it was, to be refused with the reason."""
        base, _, grain = str(name).partition(":")
        grain = _GRAIN_SAID.get(grain.strip().lower(), grain.strip().lower())
        grain = f":{grain}" if grain in GRAINS else f":{name.split(':', 1)[1]}" if ":" in str(name) else ""
        if not self._refs:
            return str(name)
        table, col = (base.split("[", 1) + [""])[:2] if "[" in base else ("", base)
        table, col = _squeeze(table), _squeeze(col.rstrip("] "))
        if (table, col) in self._refs:
            return self._refs[table, col] + grain
        words = lambda c: "".join(w[0] for w in re.findall(r"[a-z0-9]+", re.sub(r"(?<=[a-z])(?=[A-Z])", " ", c).lower()))
        spoken = re.sub(r"^.*\[|\]\s*$", "", base)
        hits = [r for (t, c), r in self._refs.items() if c == col] or \
               [r for (t, c), r in self._refs.items() if col and words(r.split("[")[-1]) == col] or \
               [r for (t, c), r in self._refs.items() if col and c == _squeeze(words(spoken))]
        if len(hits) > 1:
            try:
                reach = set(self.layer.columns_for(metrics)) if metrics else set()
            except Exception:  # noqa: BLE001 - a metric that does not exist is refused on its own account
                reach = set()
            hits = [r for r in hits if r in reach] or hits
        if len(hits) > 1:
            kept = [r for r in hits if re.search(r"calendar|dim.?date", r.split("[")[0], re.IGNORECASE)]
            hits = kept if len(kept) == 1 else hits
        return hits[0] + grain if len(hits) == 1 else str(name)

    def _known(self, col: str, fresh: bool = False) -> tuple[list, bool] | None:
        """What a column holds, and whether that is all of it. Read once, and again when a value is not there: data
        loaded since has values the first look does not. None: it cannot be read."""
        if fresh or col not in self._known_values:
            sql = None
            try:
                sql = self.kit._values_sql(col)
                self._memo.pop((sql, bi.MAX_VALUES + 1), None)
                rows = self.kit.wh.query(sql, limit=bi.MAX_VALUES + 1)[1]
            except Exception:  # noqa: BLE001 - a column it cannot read is reported by the check it was on its way to
                return None
            self._known_values[col] = ([r[0] for r in rows if r[0] is not None], len(rows) <= bi.MAX_VALUES)
        return self._known_values[col]

    def _days(self, col: str, fresh: bool = False) -> list[str] | None:
        """The days a date column holds, as text. None: it does not hold dates."""
        vals = (self._known(col, fresh) or ([], False))[0]
        if not vals or not (isinstance(vals[0], datetime.date) or (isinstance(vals[0], str) and _when(vals[0]))):
            return None
        return sorted(map(str, vals))

    def _on(self, col: str, said: str, when: tuple, notes: list[str]) -> str | None:
        """The value of `col` a date means; None for a month of many days, which is read at month grain."""
        month, day = when
        for fresh in (False, True):
            days = self._days(col, fresh) or []
            hit = [d for d in days if d[:10] == day]
            if hit:
                return hit[0]
            inside = [d for d in days if d[:7] == month]
            if len(inside) == 1:
                notes.append(f"read {col}={said!r} as {inside[0][:10]!r}")
                return inside[0]
            if inside and not day:
                return None
        if not days or not self._known(col)[1]:
            return day
        raise bi.LayerError(f"{col} has no {said}: it runs from {days[0][:10]} to {days[-1][:10]}")

    def _pick(self, col: str, said, notes: list[str]):
        """The value of `col` a spelling means: exactly, or by case, initials, one word of it, or a slip of the keys."""
        if not isinstance(said, str):
            return said
        text, vals = said.strip(), []
        for fresh in (False, True):
            vals, whole = self._known(col, fresh) or ([], False)
            shown = {str(v): v for v in vals}
            if text in shown:
                v = shown[text]
                return v if isinstance(v, (str, int, float)) else str(v)
            strs = [v for v in vals if isinstance(v, str)]
            hit = bi._match_value(text, strs)
            if hit is None:
                words = [v for v in strs if text.lower() in v.lower().split()]
                hit = words[0] if len(words) == 1 else None
            if hit is None:
                near = difflib.get_close_matches(text.lower(), [v.lower() for v in strs], n=2, cutoff=0.8)
                hit = next((v for v in strs if v.lower() == near[0]), None) if len(near) == 1 else None
            if hit is not None:
                notes.append(f"read {col}={said!r} as {hit!r}")
                return hit
        if not vals or not whole:
            return said
        near = difflib.get_close_matches(text, [str(v) for v in vals], n=12, cutoff=0.3) or [str(v) for v in vals[:40]]
        raise bi.LayerError(f"no {col} value matches {said!r}. Values in the data: {', '.join(near)}")

    def _dates(self, base: str, grain: str, wanted: list, notes: list[str]) -> tuple[str, list]:
        """(the column at the grain these values are at, the values as it holds them)."""
        days = self._days(base) or []
        odd = [x for x in wanted if isinstance(x, str) and not (_when(x) or _quarter(x) or re.fullmatch(r"\s*\d{4}\s*", x))]
        if odd:
            raise bi.LayerError(f"{odd[0]!r} is not a date. {base} runs from {days[0][:10]} to {days[-1][:10]}: write 2026-09, 2026-Q3, 2026 or 2026-09-30")
        if not grain:  # the values say the grain
            if all(re.fullmatch(r"\s*\d{4}\s*", str(x)) for x in wanted):
                grain = "year"
            elif all(isinstance(x, str) and _quarter(x) for x in wanted):
                grain = "quarter"
        if grain in ("year", "quarter", "month"):
            col = f"{base}:{grain}"
            return col, [self._pick(col, v, notes) if isinstance(v := _at(grain, x), str) else v if v is not None else x for x in wanted]
        col = f"{base}:{grain}" if grain else base
        got = [self._on(col, x, _when(x), notes) if isinstance(x, str) else x for x in wanted]
        if None in got:
            return f"{base}:month", list(dict.fromkeys(_when(x)[0] for x in wanted))
        return col, got

    def resolve(self, filters: dict | None) -> tuple[dict, list[str]]:
        """Every filter on every path comes through here. The tables are what a filter is right against, so it is written
        the way they hold it, whatever came in; what cannot be meant is refused with what the column does hold. Reading a
        column once also spares the query the toolkit would run for it on every call."""
        plain, settled, notes = {}, {}, []
        for said, v in (filters or {}).items():
            c = self._column(said)
            if c != said:
                notes.append(f"read {said} as {c}")
            base, _, grain = c.partition(":")
            span = _span(v)
            if span and self._days(base) is not None:
                c, v = f"{base}:month", _months_between(*[_when(str(e))[0] for e in span])
                grain = "month"
            many = isinstance(v, (list, tuple, set))
            wanted = list(v) if many else [v]
            if not wanted or self._known(base if self._days(base) is not None else c) is None:
                plain[c] = v
                continue
            if self._days(base) is not None:
                c, got = self._dates(base, grain, wanted, notes)
            else:
                got = [self._pick(c, x, notes) for x in wanted]
            settled[c] = got if many else got[0]
        where, told = self._resolve(plain) if plain else ({}, [])
        return {**where, **settled}, [*told, *notes]

    def _months(self, filters: dict) -> dict:
        """A date on the first or last of a month, as the month: monthly data is dated one day, and rarely that one."""
        out = {}
        for c, v in filters.items():
            when = _when(v) if isinstance(v, str) and ":" not in c else None
            edge = when and when[1] and int(when[1][8:]) in (1, calendar.monthrange(int(when[0][:4]), int(when[0][5:]))[1])
            if edge and self._days(c) is not None:
                out[f"{c}:month"] = when[0]
            else:
                out[c] = v
        return out

    def _grains(self, filters: dict) -> dict:
        """A date column filtered to a year or a month takes that grain: on the bare column "2025" matches no day."""
        out = {}
        for c, v in filters.items():
            grain = None
            if ":" not in c and self._kind(c) == "dateTime" and not isinstance(v, (list, dict)):
                grain = "year" if re.fullmatch(r"\d{4}", str(v)) else "month" if re.fullmatch(r"\d{4}-\d{2}", str(v)) else None
            out[f"{c}:{grain}" if grain else c] = int(v) if grain == "year" else v
        return out

    def _whole(self, metrics: list[str], filters: dict) -> tuple[dict, list[str]]:
        """A period on a table only part of a measure reaches narrows that part alone: a month of members under all the
        paid claims is a number, and a wrong one. It moves to a date all of the measure reaches, unless the measure asks
        for that column by name."""
        try:
            reach = set(self.layer.columns_for(metrics)) if metrics else set()
        except Exception:  # noqa: BLE001 - what the model refuses is refused when it is checked
            return filters, []
        out, notes = {}, []
        for c, v in filters.items():
            base, _, grain = c.partition(":")
            target = self._date(metrics[0]) if reach and base not in reach and self._kind(c) == "dateTime" else None
            try:
                asked = target and any(base == d.split(":")[0] for k in metrics for d in bi.metric_needs(self.layer, k)["by"])
            except Exception:  # noqa: BLE001
                asked = True
            if not target or asked:
                out[c] = v
                continue
            grain = grain or "month"
            to = f"{target.split(':')[0]}:{grain}"
            values = [(_at(grain, x) or x) for x in (v if isinstance(v, (list, tuple, set)) else [v])]
            out[to] = values if isinstance(v, (list, tuple, set)) else values[0]
            notes.append(f"{base} reaches only part of this measure, so the period is on {to.split(':')[0]}")
        return out, notes

    def _settle(self, metrics, by, filters) -> dict | str:
        """What a request means, from the question and the conversation: {metrics, filters, given, notes}, or why not."""
        question = QUESTION.get()
        led = self.ledger.setdefault(SESSION.get(), {})
        metrics = [_root(self.idx, k) for k in dict.fromkeys(metrics or [])]
        metrics = list(dict.fromkeys(metrics))
        blocked = [k for k in metrics if self.idx.get(k, {}).get("kind") == "prior" and not _ASKS_PRIOR.search(question)]
        if blocked:
            return (f"Error: {', '.join(blocked)} is a prior-period measure and the question did not ask for one. Use "
                    f"{', '.join(dict.fromkeys(self.idx[k]['base'] for k in blocked))} for the period asked.")
        inner = [k for k in metrics if k not in self.idx]
        if inner:
            return f"Error: {', '.join(inner)} is not a metric to ask for. Call find_metrics with the business words."
        given, moved = self._whole(metrics, self._grains({self._column(c, metrics): v for c, v in (filters or {}).items()}))
        filters, notes = dict(given), moved
        dated = [c for c in [*(by or []), *filters] if metrics and self._kind(c) == "dateTime"]
        if dated:
            kept = {c: v for c, v in filters.items() if c in dated}
            if kept:
                led["period"] = kept
        elif metrics and not _NAMES_PERIOD.search(question):
            if led.get("period"):
                filters.update(led["period"])
                notes.append("the period already in this conversation")
            elif self.period == "latest_month" and (col := self._date(metrics[0])) and (m := self._latest_month(metrics[0], col)):
                filters[col] = m
                notes.append(f"latest month with data, {m}")
        if metrics and not any(self._kind(c) == "dateTime" for c in by or []) and \
                (_THIS.search(question) or _LAST.search(question)) and (col := self._date(metrics[0])) and \
                (m := self._latest_month(metrics[0], col)):
            # "this month" is the data's latest, not the calendar's: the month under way has nothing in it yet
            y, mo = int(m[:4]), int(m[5:7])
            m = f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}" if _LAST.search(question) else m
            filters = {c: v for c, v in filters.items() if self._kind(c) != "dateTime"} | {col: m}
            led["period"] = {col: m}
            notes = [f"the latest month with data is {self._latest[col]}"]
        return {"metrics": metrics, "filters": filters, "given": given, "notes": notes}

    def _run(self, step: dict, compute, by, top, order_by, ascending, params, kit=None) -> str:
        args = (by, top, order_by, ascending, params)
        filters, notes = step["filters"], step["notes"]
        if (out := self._recall(step, by, top, order_by, ascending, params, kit or self.kit)) is not None:
            return out
        out = compute(step["metrics"], by, filters, *args[1:])
        if out.startswith("Error") and filters != step["given"]:
            # a metric with a period of its own (a parameter, a contract) refuses a date filter: ours gives way
            filters, notes = step["given"], ["this metric sets its own period; the default was not applied"]
            self.ledger.setdefault(SESSION.get(), {}).pop("period", None)
            out = compute(step["metrics"], by, filters, *args[1:])
        if "No data matches" in out and (months := self._months(filters)) != filters:
            again = compute(step["metrics"], by, months, *args[1:])
            if not again.startswith("Error") and "No data matches" not in again:
                out, filters, notes = again, months, [*notes, "that date read as its month"]
                kit = kit or self.kit
                kit.results = [*kit.results[:-2], *kit.results[-1:]]  # the empty try is no result
        step.update(filters=filters, notes=notes)
        if not out.startswith("Error") and "No data matches" not in out and not (top or params):
            self._note(step, by, kit or self.kit)
        return out

    def _line(self, step: dict) -> str:
        scope = "; ".join(f"{k} = {v}" for k, v in step["filters"].items()) or "everything, no filter"
        labels = ", ".join(self.layer.metrics[k]["label"] for k in step["metrics"])
        return f"{labels}; {scope}{' (' + ', '.join(step['notes']) + ')' if step['notes'] else ''}"

    def compute(self, metrics, by=None, filters=None, top=None, order_by=None, ascending=False, params=None):
        if not QUESTION.get():  # a dashboard or a brief on the same toolkit: only an agent's turn is guarded
            return self._compute(metrics, by, filters, top, order_by, ascending, params)
        by = [self._column(c, metrics) for c in by] if by else by
        step = self._settle(metrics, by, filters)
        if isinstance(step, str):
            return step
        out = self._run(step, self._compute, by, top, order_by, ascending, params)
        return out if out.startswith("Error") else f"Plan (state it in the answer, plainly): {self._line(step)}.\n\n{out}"

    def _check(self, step: dict, ask: dict) -> str | None:
        """Why the model cannot answer this step, without touching the warehouse: the compiler knows what each metric
        allows (the grouping it needs, the date filters its contract takes, the columns it reaches)."""
        def compiles(filters):
            filters = self.kit.resolve(filters)[0] if self._resolve else filters
            params = self.kit.resolve_params(step["metrics"], ask.get("params"))[0]
            self.layer.compile(step["metrics"], ask.get("by"), filters, ask.get("top"), ask.get("order_by"), bool(ask.get("ascending")),
                               dialect=self.kit.wh.dialect, row_filters=self.kit.row_filters, **({"params": params} if params else {}))
        try:
            compiles(step["filters"])
        except Exception as exc:  # noqa: BLE001 - what the model refuses is the agent's to fix
            if step["filters"] == step["given"]:
                return str(exc)
            try:
                compiles(step["given"])
            except Exception as again:  # noqa: BLE001
                return str(again)
            step.update(filters=step["given"], notes=["this metric sets its own period; the default was not applied"])
        return None

    def _needed(self, step: dict, ask: dict) -> dict | None:
        """A step refused for want of a grouping its metric always needs (a rank, a running total) has it added."""
        try:
            more = [d for k in step["metrics"] for d in bi.metric_needs(self.layer, k)["by"] if d not in (ask.get("by") or [])]
        except Exception:  # noqa: BLE001
            return None
        mended = {**ask, "by": [*(ask.get("by") or []), *dict.fromkeys(more)]}
        return mended if more and not self._check(step, mended) else None

    def _named(self, question: str) -> list[str]:
        """The metrics a question calls by their full name; of "Total PMPM - Commercial", that one and not Total PMPM."""
        said = " ".join(_words(question))
        hits = {k: n for k, n in self.names.items() if re.search(rf"\b{re.escape(n)}\b", said)}
        return [k for k, n in hits.items() if not any(n != o and re.search(rf"\b{re.escape(n)}\b", o) for o in hits.values())]

    def plan(self, steps: list[dict], seconds: float | None = None) -> str:
        """All of what a question needs: checked whole before anything runs, then run together, once. What it returns is
        the plan and each step's table; a step that fails or runs out of time says so and the others stand."""
        t = TRACE.get()
        if t is not None and (t.get("plans", 0) >= 2 or (t.get("plans") and not t.get("failed"))):
            return PLAN_RAN
        if not steps:
            return "Error: the plan has no steps. Call find_metrics, then send each number the answer needs as a step."
        if len(steps) > PLAN_STEPS:
            return f"Error: a plan has at most {PLAN_STEPS} steps. Keep what the question asks for; group by a column instead of a step per value."
        settled, wrong, steps = [], [], [{**a, "by": [self._column(c, a.get("metrics")) for c in a["by"]]} if a.get("by") else a for a in steps]
        for i, ask in enumerate(steps, 1):
            step = self._settle(ask.get("metrics"), ask.get("by"), ask.get("filters"))
            why = step if isinstance(step, str) else None if step["metrics"] else "Error: a step needs metrics."
            why = why or self._check(step, ask)
            if why and isinstance(step, dict) and (mended := self._needed(step, ask)):
                ask, why = mended, None
                steps[i - 1] = ask
            if why:
                wrong.append(f"Step {i}: {why.removeprefix('Error: ')}")
            settled.append(step)
        planned = {k for s in settled if isinstance(s, dict) for k in s["metrics"]}
        left_out = [k for k in self._named(QUESTION.get()) if k not in planned]
        if left_out and not (t or {}).get("told"):  # said once: the agent may have a reason, and it does not go round
            if t is not None:
                t["told"] = True
            wrong += [f"The question names {self.layer.metrics[k]['label']} ({k}) and no step has it. Use that metric, not a sibling."
                      for k in left_out]
        if wrong:
            return "Error: the plan was not run, nothing was computed. Fix these and send the whole plan again.\n" + "\n".join(wrong)

        def one(step, ask):
            run = copy.copy(self.kit)  # each with its own `last` and `results`, so tables do not cross
            kept = lambda *a: type(self.kit).compute(run, *a)
            out = self._run(step, kept, ask.get("by"), ask.get("top"), ask.get("order_by"), bool(ask.get("ascending")), ask.get("params"), run)
            return out, run

        left = max(t["start"] + self.turn_seconds - time.monotonic(), PLAN_SECONDS) if t is not None else self.turn_seconds
        left = min(left, seconds or left)
        seen = self.kit._seq
        pool = ThreadPoolExecutor(self.at_once)
        jobs = [pool.submit(contextvars.copy_context().run, one, s, a) for s, a in zip(settled, steps)]
        finished(jobs, timeout=left)
        pool.shutdown(wait=False, cancel_futures=True)
        parts, failed = [], False
        for i, (job, step) in enumerate(zip(jobs, settled), 1):
            if not job.done() or job.cancelled():
                failed = True
                parts.append(f"Step {i}: did not finish in the time this question has.")
                continue
            try:
                out, run = job.result()
            except Exception as exc:  # noqa: BLE001 - one step's failure is reported, the others stand
                failed = True
                parts.append(f"Step {i}: failed: {exc}")
                continue
            failed = failed or out.startswith("Error")
            for r in run.results:  # kept in the plan's order, whichever finished first
                if r["seq"] > seen:
                    self.kit._keep(r)
            parts.append(f"Step {i}: {out.split(chr(10) * 2 + 'Definitions')[0]}")
        if t is not None:
            t.update(plans=t.get("plans", 0) + 1, failed=failed)
        said = "\n".join(f"{i}. {self._line(s)}" for i, s in enumerate(settled, 1))
        return f"Plan (state it in the answer, plainly):\n{said}\n\n" + "\n\n".join(parts)


    def read(self, question: str) -> dict | None:
        """Step 1 in code, for a question every word of which is accounted for: metrics by their full names, values the
        columns hold, a year, "by" a column. Any word left over, and it is not this simple: None."""
        if "@" in question or _NOT_PLAIN.search(_WHEN.sub(" ", question)) or len(re.findall(r"\b20\d\d\b", question)) > 1:
            return None
        said = " ".join(_words(question))
        metrics = list(dict.fromkeys([v for t, v in self.terms.items() if re.search(rf"\b{re.escape(t)}\b", said)] + self._named(question)))
        if not metrics or re.search(r"\b(quarter|year)\b", question, re.IGNORECASE):
            return None
        left = " ".join(_words(_WHEN.sub(" ", question)))

        def take(phrase: str) -> bool:
            nonlocal left
            left, n = re.subn(rf"\b{re.escape(phrase)}\b", " ", left)
            return bool(n)

        for k in metrics:
            take(self.names.get(k, ""))
        for t in self.terms:
            take(t)
        filters: dict = {}
        if year := re.search(r"\b20\d\d\b", question):
            if not (col := self._date(metrics[0])):
                return None
            filters[col.rsplit(":", 1)[0] + ":year"] = int(year.group())
        reach = set(self.layer.columns_for(metrics))
        by = [c for c in reach if self.kit._type(c) == "string" and take("by " + " ".join(_words(c.split("[")[-1])))]
        names: dict[str, list] = {}
        for c, vals in self.values.items():
            for v in vals:
                if len(v) > 2:
                    names.setdefault(" ".join(_words(v)), []).append((c, v))
        for phrase in sorted(names, key=len, reverse=True):
            if phrase and re.search(rf"\b{re.escape(phrase)}\b", left):
                if len(names[phrase]) > 1 or names[phrase][0][0] not in reach:
                    return None
                take(phrase)
                c, v = names[phrase][0]
                filters[c] = [*filters[c], v] if isinstance(filters.get(c), list) else [filters[c], v] if c in filters else v
        if set(left.split()) - _QUIET - {"by"}:
            return None
        return {"route": "compute", "metrics": metrics, "by": by, "filters": filters}

    def _leaned_on(self, key: str) -> int:
        """How many measures are made from this one: the plainest measure of a word is the one the rest are built on."""
        label = self.layer.metrics[key]["label"].lower()
        return sum(label in str(m.get("dax") or "").lower() for m in self.layer.captured().values())

    def guess(self, question: str, every_word: bool = False) -> dict | None:
        """A question read in code alone, by what the model holds: a measure for each thing it names, a value it names, a
        year. For when the model is not there to read it, and for "what is driving X", which needs nothing more.
        `every_word`: None if a word is left over, so a question it cannot fully read goes to the model."""
        left: list[str] = []
        metrics: list[str] = []
        filters: dict = {}
        said = " ".join(_words(question))
        names = {" ".join(_words(v)): (c, v) for c, vals in self.values.items() for v in vals if len(v) > 2}
        for phrase in sorted(names, key=len, reverse=True):
            if phrase and re.search(rf"\b{re.escape(phrase)}\b", said):
                c, v = names[phrase]
                filters.setdefault(c, v)
                said = re.sub(rf"\b{re.escape(phrase)}\b", " ", said)
        for part in re.split(r"\band\b|\bvs\b|\bversus\b", said):
            words = [w for w in part.split() if w not in _QUIET and w not in _LOOSE and not re.fullmatch(r"20\d\d|in|to|by|from|with|for", w)]
            whole = [w for w in part.split() if w not in _QUIET and not re.fullmatch(r"20\d\d|in|to|by|from|with|for", w)]
            pool = self.rank(" ".join(words) or " ".join(whole), 20) if whole else []
            fits = [k for k in pool if set(words) <= set(_words(self.layer.metrics[k]["label"]))] if words else []
            if fits:
                metrics.append(max(fits, key=lambda k: (self.idx[k]["kind"] == "base", self._leaned_on(k))))
            elif pool and not every_word:
                metrics.append(pool[0])
            else:
                left += words
        metrics = list(dict.fromkeys(metrics))[:WHY_METRICS]
        if not metrics or (every_word and left):
            return None
        if year := re.search(r"\b20\d\d\b", question):
            if col := self._date(metrics[0]):
                filters[f"{col.rsplit(':', 1)[0]}:year"] = int(year.group())
        reach = set(self.layer.columns_for(metrics))
        filters = {c: v for c, v in filters.items() if c.split(":")[0] in reach or self._kind(c) == "dateTime"}
        return {"route": "explain" if _DRIVES.search(question) else "compute", "metrics": metrics, "by": [], "filters": filters}

    def read_why(self, question: str) -> dict | None:
        """"What is driving medical and pharmacy cost": read in code, with no model to wait for."""
        return self.guess(question, every_word=True) if _DRIVES.search(question) else None

    def form_for(self, question: str, session: str) -> list:
        """What one small call is given: the shortlist, what it can be cut by, the last form, and nothing else."""
        led = self.ledger.get(session, {})
        last = led.get("form") or {}
        keys = list(dict.fromkeys([*(last.get("metrics") or []), *self.rank(question, SHORTLIST), *self.headline]))[:SHORTLIST]
        if not keys:
            return []
        cols = [c for c in self.layer.columns_for(keys[:3]) if self.kit._type(c) != "dateTime" and not _NOT_DRIVER.search(c)]
        date = (self._date(keys[0]) or "Calendar[Date]:month").rsplit(":", 1)[0]
        shown = [f"{c}: {', '.join(self.values[c][:30])}" if len(self.values.get(c, [])) <= 30 and c in self.values else c for c in cols]
        ask = (f"Metrics:\n" + "\n".join(f"- {k}: {self.layer.metrics[k]['label']}{bi.needs(self.layer, k)}" for k in keys)
               + "\n\nColumns:\n" + "\n".join(shown) + f"\n\nDate column: {date}"
               + (f"\n\nPrevious question: {led['said'][-1][0]}\nPrevious form: {json.dumps(last)}" if last and led.get("said") else "")
               + (f"\n\nAlready known:\n{known}" if (known := self.known(900)) else "")
               + f"\n\nQuestion: {question}")
        return [("system", _READ.replace("DATE", date)), ("human", ask)]

    def run(self, form: dict, question: str) -> tuple[str, str]:
        """Steps 2 and 3: (the results, or "" when the agent should take it, and which tool ran)."""
        form = self._carried(form, question)
        steps = {k: form[k] for k in ("metrics", "by", "filters", "top", "order_by", "ascending") if form.get(k) not in (None, [], {}, "")}
        steps["metrics"] = list(dict.fromkeys([*self._named(question), *(steps.get("metrics") or [])]))
        if form.get("route") == "overview":
            return self.overview(form, question), "compute"
        if form.get("route") == "known":
            return self.known(), "compute"
        if form.get("route") == "layer":
            return self.peel(form), "explain"
        if form.get("route") == "explain":
            return self._why(steps, question), "explain"
        out = self.plan([steps])
        return ("" if out.startswith("Error") or "\nStep 1: Error" in out or "No data matches" in out or "Step 1: failed" in out
                or "Step 1: did not finish" in out else out), "compute"

    def overview(self, form: dict, question: str) -> str:
        """An open question gets an answer, not a question back: the headline measures, latest month against the same
        month a year before. A measure with a period of its own shows that; one the model refuses is left out."""
        keys = [_root(self.idx, k) for k in form.get("metrics") or [] if k in self.idx][:PLAN_STEPS] or self.headline
        col = self._date(keys[0]) if keys else None
        now = self._latest_month(keys[0], col) if col else None
        then = f"{int(now[:4]) - 1}{now[4:]}" if now else None
        led = self.ledger.setdefault(SESSION.get(), {})
        steps = []
        for k in reversed(keys):  # the page draws from the last one run: the first headline leads
            for ask in ([{"metrics": [k], "by": [col], "filters": {col: [then, now]}}] if now else []) + [{"metrics": [k]}]:
                step = self._settle(ask["metrics"], ask.get("by"), ask.get("filters"))
                if isinstance(step, dict) and not self._check(step, ask):
                    steps.append(ask)
                    break
        form.update(metrics=keys, now=now, then=then, col=col)
        if not steps:
            return ""
        out = self.plan(steps, HEADLINE_SECONDS)
        if now:
            led["period"] = {col: now}  # a follow-up carries on from the latest month, not from the pair
        return "" if out.startswith("Error") else out

    def scoreboard(self, seen: int, form: dict) -> tuple[str, str]:
        """(the headlines as a table, the offer that follows them): the biggest mover is where to go next."""
        rows, moved = [], {}
        for k in form["metrics"]:
            r = next((r for r in self._mine(seen) if r.get("metrics") == [k]), None)
            if not r or not r.get("shown"):
                continue
            cells = {str(x[0])[:7]: x[-1] for x in r["shown"]} if len(r["columns"]) > 1 else {form["now"]: r["shown"][0][-1]}
            now, then = cells.get(form["now"]), cells.get(form["then"])
            change = ""
            if isinstance(now, (int, float)) and isinstance(then, (int, float)) and then:
                moved[k] = abs(now / then - 1)
                change = (f"{(now - then) * 100:+.1f} pts" if "%" in str(self.layer.metrics[k].get("format") or "") else f"{(now / then - 1) * 100:+.1f}%")
            rows.append(f"| {self.layer.metrics[k]['label']} | {self._show(k, now) if now is not None else ''} | "
                        f"{self._show(k, then) if then is not None else ''} | {change} |")
        if not rows:
            return "", ""
        table = f"| | {_period(form['now'])} | {_period(form['then'])} | Change |\n|---|---:|---:|---:|\n" + "\n".join(rows) + "\n\n"
        lead = max(moved, key=moved.get) if moved else form["metrics"][0]
        name = form["moved"] = self.layer.metrics[lead]["label"]
        cols = [c for c in self.values if c in self.layer.columns_for([lead])]
        cut = next((c for c in self.drivers if c in cols), None) or next((c for c in self.layer.columns_for([lead]) if _SLICE.search(c)), None) \
            or next(iter(sorted(cols, key=lambda c: len(self.values[c]) < 3)), None)
        by = f"{name} by {cut.split('[')[-1].split(']')[0].lower()}" if cut else f"the trend in {name}"
        return table, f"\n\nWould you like {by} or what drove {name}?"

    def _why(self, steps: dict, question: str) -> str:
        """What is driving each measure the question names, together. One that cannot be looked into is said, and the others
        stand."""
        keys = list(dict.fromkeys(_root(self.idx, k) for k in steps.get("metrics") or [""]))[:WHY_METRICS]
        self.ledger.setdefault(SESSION.get(), {})["peel"] = {}  # a new question starts from the top
        found = dict(zip(keys, self._together([functools.partial(self._why_one, k, steps, question) for k in keys])))
        said = [out for out in found.values() if out]
        missed = [self.layer.metrics[k]["label"] for k, out in found.items() if not out and k in self.layer.metrics]
        return "\n\n".join(said + [f"Could not be looked into: {', '.join(missed)}."] * bool(said and missed))

    def _why_one(self, metric: str, steps: dict, question: str) -> str:
        col = self._date(metric) if metric in self.idx else None
        latest = self._latest_month(metric, col) if col else None
        if not latest:
            return ""
        y, mo = int(latest[:4]), int(latest[5:7])
        before = f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}" if _MONTHS.search(question) else f"{y - 1}-{mo:02d}"
        filters = {c: v for c, v in (steps.get("filters") or {}).items() if self._kind(c) != "dateTime"}
        out = self.explain(metric, col, latest, before, filters)
        if out.startswith("Error") and (t := TRACE.get()) is not None:
            t["why"] = f"{metric}: {out[:140]}"
        return "" if out.startswith("Error") else out

    def _show(self, key: str, value) -> str:
        """A number the way the report shows it, from the measure's Power BI format."""
        fmt = str(self.layer.metrics[key].get("format") or "").split(";")[0]
        places = len(fmt.split(".")[1].rstrip("%")) if "." in fmt else 0
        if not isinstance(value, (int, float)) or not fmt:
            return bi._fmt(value)
        return f"{value * 100:,.{places}f}%" if "%" in fmt else f"${value:,.{places}f}" if "$" in fmt else f"{value:,.{places}f}"

    def number(self, seen: int) -> str:
        """The result in a line or two, as soon as the rows are back: the page draws its charts when the turn ends."""
        new = [r for r in self._mine(seen) if r.get("kind") == "compute"]
        if not new or len(new[-1].get("shown") or []) > 6:
            return ""
        r = new[-1]
        when = next((_period(v) for c, v in (r.get("filters") or {}).items() if ":" in c and not isinstance(v, list)), "")
        lines = []
        for row in r["shown"]:
            cells = dict(zip(r["columns"], row))
            head = " · ".join(str(v) for c, v in cells.items() if c not in r["metrics"])
            said = " · ".join(f"{self.layer.metrics[k]['label']} **{self._show(k, cells[k])}**" for k in r["metrics"] if cells.get(k) is not None)
            if said:
                lines.append(" · ".join(x for x in (f"{head}: {said}" if head else said, when) if x))
        return "\n\n".join(lines) + "\n\n"


def prompt(kit) -> str:
    """The toolkit's prompt with the two rules the harness has taken over."""
    text = kit.system_prompt()
    text = text.replace("1. Call list_metrics first in a conversation, and list_columns when you need a column.",
                        "1. Call find_metrics with the business words of the question, then compute what it puts first. "
                        "Call list_columns only for a column to group or filter by.")
    return text.replace("5. When a question is ambiguous but has a sensible default, pick it, state the assumption in one\n"
                        "   line, and answer. Ask only if there is no sensible default.",
                        "5. Defaults are decided for you: find_metrics puts the default metric first and compute applies the\n"
                        "   default period. Use them and state the plan compute returns.") + """

How a question runs: find_metrics, then one call to compute with the whole plan. Every number the answer needs is a step
in that call, and each step takes the arguments these rules give compute (metrics, by, filters, top, order_by, ascending,
params). If it says the plan was not run, nothing ran: fix what it names and send the whole plan again. Once a plan has run,
answer from it."""


def install(kit, folder=None):
    """(tools, system prompt, wrap): build the agent from the first two and pass it through `wrap`."""
    h = Harness(kit, folder)

    async def find_metrics(ctx: bi.RunContext, query: str) -> str:
        """Find the metrics a question means, by its business words ("paid", "readmission rate"): the default first,
        with what each needs."""
        return h.find(query)

    async def about_metric(ctx: bi.RunContext, metric: str) -> str:
        """All of one metric, from the model and the tables: what it is made of, what it needs, the period it has data for
        and how to write one, what each column it can be cut by holds, and what reaches only part of it. For a metric that
        was refused, or before a filter you are not sure of."""
        return await asyncio.to_thread(h.details, metric)

    async def compute(ctx: bi.RunContext, steps: list[Step]) -> str:
        """Run the plan for this question, once: a step for each number or table the answer needs, all sent together.
        A step has `metrics` (keys from find_metrics), and as needed `by` (Table[Column], dates may add :month etc.),
        `filters` ({Table[Column]: value or [values]}, loosely typed), `top`, `order_by`, `ascending` and what-if `params`.
        The whole plan is checked first, and nothing runs unless all of it can."""
        return await asyncio.to_thread(h.plan, steps)  # off the loop: Stop and everyone else still work

    async def explain(ctx: bi.RunContext, metric: str, period: str, now: str, before: str,
                      filters: dict[str, str | int | float | list[str]] | None = None) -> str:
        """Why a metric changed between two periods ("what drove the rise"): `period` is the date column with its grain
        (e.g. Calendar[Date]:month), `now` and `before` two of its values, `filters` as for compute. It looks one layer down
        for what carries most of the change, within a time limit. Once per metric per question: the user asks for the next
        layer."""
        job = asyncio.to_thread(h.explain, metric, period, now, before, filters)  # off the loop: Stop and everyone else still work
        try:
            return await asyncio.wait_for(job, h.seconds + 30)
        except TimeoutError:
            return "Error: the warehouse did not answer within the time limit. Say so, and offer a narrower question."

    for fn in (find_metrics, about_metric, compute, explain):
        fn.__annotations__["ctx"] = bi.RunContext

    def within_turn(fn):
        @functools.wraps(fn)
        async def run(*a, **k):
            t = TRACE.get()
            if t is not None:
                t["calls"] += 1
                t.setdefault("tools", Counter())[fn.__name__] += 1
                # a slow model must not cost the question its answer: a plan yet to run still runs, within its own limits
                unplanned = fn.__name__ == "compute" and (not t.get("plans") or t.get("failed"))
                if t["calls"] > TURN_CALLS or (time.monotonic() - t["start"] > h.turn_seconds and not unplanned):
                    return OUT_OF_TURN
            out = await fn(*a, **k)
            if t is not None and isinstance(out, str) and out.startswith("Error") and out != PLAN_RAN:
                # a value in the refusal is what a retry changes: the refusal itself is what repeats
                shape = re.sub(r"'[^']*'|\d+", "#", out.split("Values in the data")[0])
                seen = t.setdefault("refused", Counter())
                seen[shape] += 1
                if seen[shape] >= REFUSALS:
                    t["calls"] = TURN_CALLS
                    return f"{out}\n\n{SAME_REFUSAL}"
            return out
        return run

    tools = [within_turn(t) for t in [find_metrics, about_metric, compute, explain, *[t for t in kit.tools() if t.__name__ not in ("list_metrics", "compute", "explain")]]]

    def wrap(orch):
        inner = orch.stream
        can_talk = hasattr(orch, "_model_name")  # another runtime: the agent takes every question

        def chat(http):
            if getattr(orch, "_chat_model", None):
                return orch._chat_model(http)
            from . import agent_runtime as rt
            make = getattr(rt, "chat_openai", None)
            if make is None:
                from langchain_openai import ChatOpenAI as make
            return make(model=orch._model_name, api_key=orch._api_key, base_url=orch._base_url, http_async_client=http, max_retries=1)

        async def ask_form(model, message, session_id) -> dict | None:
            sent = h.form_for(message, session_id)
            if not sent:
                return None
            reply = await asyncio.wait_for(model.ainvoke(sent), READ_SECONDS)
            found = re.search(r"\{.*\}", _text(reply.content), re.DOTALL)
            form = json.loads(found.group()) if found else {}
            return form if form.get("route") in ("overview", "known") or (form.get("route") in ("compute", "explain") and form.get("metrics")) else None

        async def direct(message, session_id, t):
            """The four steps. Yields lines; sets t["answered"] once the question is settled. Before that, any failure
            leaves it to the agent with nothing lost."""
            import httpx

            from .agent_runtime import OrchestratorEvent as Event
            led = h.ledger.setdefault(session_id, {})
            agent = uuid.uuid4().hex[:12]
            async with httpx.AsyncClient(timeout=httpx.Timeout(READ_SECONDS + SAY_SECONDS, connect=10)) as http:
                model, seen = chat(http), h.kit._seq
                yield Event.agent_status(agent, "active", "Analyzing request...").line() + "\n"
                tried = []
                again = led.get("asked", {}).get(" ".join(_words(message)))  # this very question, in this conversation
                if again:
                    t["read"] = "map"
                    tried.append(again["form"])
                elif form := h.next_layer(message) or h.read(message) or h.read_why(message):
                    t["read"] = "code"
                    tried.append(form)
                else:
                    if "@" in message:
                        t["why"] = "it names someone"
                        return
                    try:
                        form = await ask_form(model, message, session_id)
                        t["read"] = "model"
                    except Exception as exc:  # noqa: BLE001 - a model that stalls costs the words, never the answer
                        print(f"[desk] the model did not read it ({type(exc).__name__}): reading it in code", file=sys.stderr, flush=True)
                        form = h.guess(message)
                        t.update(read="code", stalled=True, why=f"{type(exc).__name__} reading it")
                    if form:
                        tried.append(form)
                    else:
                        t["read"] = "agent"
                        t.setdefault("why", "the reading found no form for it")
                for form in tried:
                    tool = "explain" if form["route"] in ("explain", "layer") else "compute"
                    yield Event.tool_call(agent, tool, {"form": form}).line() + "\n"
                    out, tool = await asyncio.wait_for(asyncio.to_thread(h.run, form, message), RUN_SECONDS)
                    open_ended = form["route"] == "overview"
                    head, offer = h.scoreboard(seen, form) if out and open_ended and form.get("now") else (h.number(seen) if out else "", "")
                    if out and tool == "explain":
                        head, offer = h.layer_line(seen), h.layer_offer()
                    if head:  # ahead of the rows: the page picks the answer's picture by the numbers it states
                        yield json.dumps({"type": "chunk", "data": {"text": head}}) + "\n"
                    yield Event.tool_call(agent, tool, status="completed", summary=out[:4000]).line() + "\n"
                    if not out:
                        t.update(plans=0, failed=False)
                        t["read"] = "agent"
                        t.setdefault("why", f"{form['route']} found nothing to show")
                        return
                    t["first"] = time.monotonic() - t["start"]
                    words = ""
                    earlier = f"\n\nAlready known:\n{h.known()}" if form["route"] != "known" and h.known() else ""
                    moved = f"What stands out is {form['moved']}: it moved furthest, in percent.\n" if form.get("moved") else ""
                    results = "\n".join(x for x in out.splitlines() if not x.startswith(("Data:", "Step 1: Data:", "Plan (", "This is a first look", "The screen shows")))
                    try:
                        async def before():  # nothing new came back, so what was said of it stands
                            yield type("Said", (), {"content": again["words"]})

                        said = before() if again and not t["n"] else aiter(model.astream([("system", _SAY), ("human", f"Question: {message}\n\nOn screen, as the report shows it:\n{head}\n{moved}\nResults:\n{results[:6000]}{earlier}")]))
                        if t.get("stalled"):  # it could not read the question: it will not write about it either
                            raise StopAsyncIteration
                        ends = time.monotonic() + SAY_SECONDS
                        while True:
                            part = _text((await asyncio.wait_for(anext(said), max(ends - time.monotonic(), 0.1))).content)
                            if part:
                                words += part
                                yield json.dumps({"type": "chunk", "data": {"text": part}}) + "\n"
                    except StopAsyncIteration:
                        pass
                    except Exception as exc:  # noqa: BLE001 - the number stands without its sentence
                        print(f"[desk] no sentence: {type(exc).__name__}", file=sys.stderr, flush=True)
                    if offer:
                        yield json.dumps({"type": "chunk", "data": {"text": offer}}) + "\n"
                    answer = head + words + offer or "The results are in the table."
                    yield Event("result", {"type": "analysis", "answer": answer}).line() + "\n"
                    t["answered"] = True
                    led["form"] = form
                    led.setdefault("asked", {})[" ".join(_words(message))] = {"form": form, "words": words}
                    led.setdefault("said", []).append((message, answer))
                    led.setdefault("unseen", []).append((message, answer))

        async def rescue(message, seen):
            """Past its time the answer is what there is: what the agent found, or the question read in code. Never an error."""
            from .agent_runtime import OrchestratorEvent as Event
            agent, form, head = uuid.uuid4().hex[:12], None, h.layer_line(seen) or h.number(seen)
            if not head and (form := h.guess(message)):
                yield Event.tool_call(agent, "explain" if form["route"] == "explain" else "compute", {"form": form}).line() + "\n"
                try:
                    await asyncio.wait_for(asyncio.to_thread(h.run, form, message), RUN_SECONDS)
                except Exception as exc:  # noqa: BLE001 - the words below say so
                    print(f"[desk] no rescue: {type(exc).__name__}", file=sys.stderr, flush=True)
                head = h.layer_line(seen) or h.number(seen)
            if form:
                yield Event.tool_call(agent, "explain" if form["route"] == "explain" else "compute", status="completed", summary=head[:4000]).line() + "\n"
            answer = head + h.layer_offer() if head else GAVE_UP
            yield json.dumps({"type": "chunk", "data": {"text": answer}}) + "\n"
            yield Event("result", {"type": "analysis", "answer": answer}).line() + "\n"

        async def turn(message, session_id="default", **kw):
            QUESTION.set(message)
            SESSION.set(session_id)
            t0 = time.monotonic()
            t = {"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": t0, "read": "agent"}
            TRACE.set(t)
            try:
                if h.direct and can_talk:
                    try:
                        async for line in direct(message, session_id, t):
                            yield line
                    except Exception as exc:  # noqa: BLE001 - whatever went wrong, the agent can still answer
                        t.update(read="agent", plans=0, failed=False, why=f"{type(exc).__name__}: {str(exc)[:140]}")
                    if t.get("answered"):
                        return
                led = h.ledger.setdefault(session_id, {})
                told = "".join(f"Q: {q}\nA: {a}\n" for q, a in led.pop("unseen", []))  # the agent did not see those turns
                said = f"Answered earlier in this conversation:\n{told}\nNow: {message}" if told else message
                if known := h.known():
                    said = f"Already found in this conversation, with no need to ask again:\n{known}\n\n{said}"
                seen = h.kit._seq
                lines = aiter(inner(said, session_id=session_id, **kw))
                while True:
                    try:
                        line = await asyncio.wait_for(anext(lines), max(t0 + JOB_SECONDS - time.monotonic(), 0.1))
                    except StopAsyncIteration:
                        break
                    except Exception as exc:  # noqa: BLE001 - however the agent fails, the question still gets its answer
                        print(f"[desk] the agent stopped ({type(exc).__name__}): answering from what there is", file=sys.stderr, flush=True)
                        t["why"] = f"agent: {type(exc).__name__}"
                        async for out in rescue(message, seen):
                            yield out
                        break
                    if '"type": "error"' in line[:40] or '"type":"error"' in line[:40]:
                        t["why"] = "agent: it reported an error"
                        async for out in rescue(message, seen):
                            yield out
                        break
                    yield line
            finally:
                took = time.monotonic() - t0
                first = f", first number {t['first']:.1f}s" if "first" in t else ""
                why = f" ({t['why']})" if t["read"] == "agent" and t.get("why") else ""
                used = f" ({', '.join(f'{k} {n}' for k, n in t['tools'].most_common())})" if t.get("tools") else ""
                wall = t.get("wall", 0.0)
                recalled = f", {t['recalled']} from the map" if t.get("recalled") else ""
                print(f"[desk] {took:.1f}s, read by {t['read']}{why}{first}{recalled}, {t['calls']} tool calls{used}: warehouse {wall:.1f}s, {t['secs']:.1f}s of queries "
                      f"over {t['n']} (slowest {t['slowest']:.1f}s), model and the rest {max(took - wall, 0):.1f}s", file=sys.stderr, flush=True)

        async def work(job, message, session_id, kw):
            """The question, whoever is watching. Its results are its own until a chat following it takes them."""
            bi.ASKER.set(job.id)
            try:
                async for line in turn(message, session_id, **kw):
                    job.lines.append(line)
            except Exception as exc:  # noqa: BLE001 - a job always ends, and ends with something to say
                print(f"[desk] the job stopped: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                job.lines.append(json.dumps({"type": "result", "data": {"type": "analysis", "answer": GAVE_UP}}) + "\n")
            finally:
                job.done = True

        def hand_over(job, sent: int) -> tuple[list, int]:
            """What the job has found, as this chat's: the page draws a result the turn it is kept under its conversation."""
            known = {r["seq"] for r in job.kept}
            job.kept += [r for r in h.kit.results if r.get("asker") == job.id and r["seq"] not in known]
            fresh = job.kept[sent:]
            for r in fresh:
                h.kit._keep(r)
            return h.kit.results[len(h.kit.results) - len(fresh):] if fresh else [], len(job.kept)

        async def stream(message, session_id="default", **kw):
            """Follow the job for this question, starting it if it is new. A new one is followed for a few seconds, then the
            chat is let go; the job goes on, and asking again picks it up at the start, or gets what it found."""
            key = (session_id, " ".join(_words(message)))
            for k in [k for k, j in h.jobs.items() if j.done and time.monotonic() - j.at > MAP_SECONDS]:
                del h.jobs[k]
            job = h.jobs.get(key)
            new = job is None
            if new:
                job = h.jobs[key] = Job()
                job.task = asyncio.create_task(work(job, message, session_id, kw))
            ends = time.monotonic() + h.hold if new else None
            at, given = 0, 0
            while True:
                while at < len(job.lines):
                    line, at = job.lines[at], at + 1
                    handed = []
                    if '"status": "completed"' in line or '"status":"completed"' in line:
                        handed, given = hand_over(job, given)
                    yield line
                    for r in handed:  # read by this chat: another following this conversation must not draw it
                        r["asker"] = None
                if job.done:
                    return
                if ends and time.monotonic() > ends:
                    yield json.dumps({"type": "held", "data": {"message": HELD}}) + "\n"
                    yield json.dumps({"type": "result", "data": {"type": "analysis", "answer": HELD}}) + "\n"
                    return
                await asyncio.sleep(0.05)

        orch.stream = stream
        return orch

    return tools, prompt(kit), wrap
