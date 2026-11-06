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
    direct            a question takes four steps and only the first may need a model: what it asks for (`read`, in
                      code when every word of it is accounted for, else one small call, `ask_form`), what that reaches
                      (the captured model), its queries (together) and its answer (the number at once, then a sentence).
                      What this cannot settle goes to the agent, as before

precedents.yaml beside capture.yaml (optional) holds what owners have decided:

    terms: {pmpm: total_pmpm}   this word means this metric
    period: latest_month        what a question with no period means (the default), or `all`
    explain_seconds: 60         how long one "why did it change" may run; past it, what was found so far is the answer
    turn_seconds: 75            how long one question may keep calling tools before it answers from what it has
    drivers: [Member[Plan], ...]  the columns a first look at a change splits by (default: the model's, without ids)
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
READ_SECONDS = 20      # past this the question goes to the agent
SAY_SECONDS = 30       # past this the number stands without its sentence
HEADLINES = 4          # the measures an open question shows, when no owner has said which
HEADLINE_SECONDS = 20  # past this an open question shows what came back
_PULSE = re.compile(r"pmpm|\bper\b|ratio|\brate\b", re.IGNORECASE)
_SLICE = re.compile(r"line of business|\blob\b|\bplan\b|product|segment|region", re.IGNORECASE)
_MONTHS = re.compile(r"\b(month before|previous month|prior month|last month|month over month|mom)\b", re.IGNORECASE)
_NOT_PLAIN = re.compile(r"\b(quarter|year|ytd|q[1-4])\b", re.IGNORECASE)
_READ = """You turn a business question into a form for a metrics engine. Reply with JSON only:
{"route": "compute", "metrics": [keys], "by": [columns], "filters": {column: value or [values]}, "top": n, "order_by": key, "ascending": false}
route "explain" for why something changed or what drove it: {"route": "explain", "metrics": [one key], "filters": {...}}.
route "overview" for an open question that names no one measure ("how's the business doing?", "where should I worry?"): {"route":
"overview", "metrics": []}. If a broad word fits several measures and nothing favours one ("how are we on cost?"), list those: 2 to 4 keys.
route "agent" for anything else: rows or lists of things, forecasts, whether a change is real or chance, or nothing listed fits.
Use only the keys, columns and values listed. Metrics are best first: take the first that fits unless the question says otherwise.
Leave out what the question does not ask for: a question with no period gets no date filter, and one is applied for you.
Dates: {"DATE:year": 2025}, {"DATE:quarter": "2026-Q1"}, {"DATE:month": "2026-09"}; several periods compared are a list of values with
the same column in `by`. "Highest" or "top 3" is `by` with `top` (1 or 3); lowest adds ascending true.
A follow-up starts from the previous form and changes only what it says."""
_SAY = """Write the answer to the question from the results, for a business reader. The number is already on screen above your words, so
add what it means: one or two plain sentences, at most three numbers, all taken from the results. Say the period, with months as
people say them (September 2026). A ratio or rate reads as a percent. Say which metric a result is if the question was loose.
With several measures, say what stands out most.
Plain sentences only: no headings, lists or bold. No metric keys, formulas, SQL or data source. Add no cause the results do not show."""
EXPLAIN_SECONDS = 60   # an investigation runs a query per column per level, one after another: on a wide model
EXPLAIN_QUERIES = 80   # that is hundreds, so it stops here and says it stopped
TURN_SECONDS = 75      # past this a turn's tools stop and it answers from what it has
TURN_CALLS = 12        # or past this many tool calls, so a question cannot go round and round
PLAN_STEPS = 6         # what one plan may ask for; more than this is more than one question
PLAN_SECONDS = 5       # a plan late in its turn still gets this long
PLAN_RAN = ("Error: this question's plan has run. Do not call compute again. Answer from its results, and say in one line "
            "what they do not cover.")
OUT_OF_TURN = ("Error: this question has had its time. Do not call another tool. Answer now, in two or three plain sentences, "
               "from what you already have, and say in one line what you could not finish.")
SPLITS = 12            # the columns a first look splits by, unless brief says which (`drivers` in precedents.yaml)
AT_ONCE = 4            # of those on the warehouse together
_DEEPER = re.compile(r"\b(deeper|dig|drill|further|underneath|behind (that|this|it)|break (that|this|it) down|root cause)\b", re.IGNORECASE)
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

    def __init__(self, wh, seconds: float, queries: int):
        self._wh, self.deadline, self.left, self.stopped = wh, time.monotonic() + seconds, queries, False
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
        with ThreadPoolExecutor(AT_ONCE) as pool:
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
        return ("Best first; the first is the default for these words. If the question names several, use each:\n" + "\n".join(lines)
                + f"\nGroup or filter it by: {', '.join(cols)}"
                + (f"\nDates: {dates[0]}:year, :quarter or :month, e.g. filters {{{dates[0]}:year: 2025}}" if dates else "")
                + "\nNo period in the question: leave it out, the period is applied for you.")

    def explain(self, metric, period, now, before, filters=None) -> str:
        """What is carrying a change: one level across the columns that can drive it, fetched together. The search
        further down runs only when the question asks for it. Within its limits, on a copy of the toolkit so nothing
        else meets the limited warehouse."""
        metric, filters = _root(self.idx, metric), self._grains(filters or {})
        deep = bool(_DEEPER.search(QUESTION.get()))
        run = copy.copy(self.kit)
        run.wh = wh = Limited(self.kit.wh, self.seconds, EXPLAIN_QUERIES)
        if not deep:
            every = self.kit._splits(metric, period)
            cols = [c for c in self.drivers if c in every] or [c for c in every if not _NOT_DRIVER.search(c)][:SPLITS]
            run._splits = lambda *_: cols
            try:
                where = self.kit.resolve(filters)[0]
                wh.ahead([self.layer.compile([metric], [c, period], {**where, period: [now, before]}, dialect=wh.dialect,
                                             row_filters=self.kit.row_filters) for c in cols])
            except Exception:  # noqa: BLE001, S110 - the investigation reports what is wrong with its arguments
                pass
        out = bi.BIToolkit.explain(run, metric, period, now, before, filters, depth=3 if deep else 1)
        self.kit.results, self.kit._seq = run.results, run._seq
        if wh.stopped:
            out += ("\n\nThis investigation stopped at its time limit, so it is partial: report what it found as far as it "
                    "went, and do not say there is no driver. Do not run it again for this question.")
        elif not deep:
            out += "\n\nThis is a first look, one level down. End by offering to dig deeper into what leads."
        return out

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
        for c in cols[:SLICES]:
            try:
                rows = self.kit.wh.query(self.kit._values_sql(c), limit=61)[1]
            except Exception:  # noqa: BLE001, S112 - a column it cannot read is one the agent looks up
                continue
            if len(rows) <= 60:
                self.values[c] = [str(r[0]) for r in rows if r[0] is not None]

    def _timed(self, wh) -> None:
        inner = wh.query

        def query(sql, limit=None):
            t0 = time.monotonic()
            try:
                return inner(sql, limit=limit)
            finally:
                if (t := TRACE.get()) is not None:
                    took = time.monotonic() - t0
                    t.update(n=t["n"] + 1, secs=t["secs"] + took, slowest=max(t["slowest"], took))
        wh.query = query

    def _grains(self, filters: dict) -> dict:
        """A date column filtered to a year or a month takes that grain: on the bare column "2025" matches no day."""
        out = {}
        for c, v in filters.items():
            grain = None
            if ":" not in c and self.kit._type(c) == "dateTime" and not isinstance(v, list):
                grain = "year" if re.fullmatch(r"\d{4}", str(v)) else "month" if re.fullmatch(r"\d{4}-\d{2}", str(v)) else None
            out[f"{c}:{grain}" if grain else c] = int(v) if grain == "year" else v
        return out

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
        given = self._grains(filters or {})
        filters, notes = dict(given), []
        dated = [c for c in [*(by or []), *filters] if metrics and self.kit._type(c.split(":")[0]) == "dateTime"]
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
        if metrics and not any(self.kit._type(c.split(":")[0]) == "dateTime" for c in by or []) and \
                (_THIS.search(question) or _LAST.search(question)) and (col := self._date(metrics[0])) and \
                (m := self._latest_month(metrics[0], col)):
            # "this month" is the data's latest, not the calendar's: the month under way has nothing in it yet
            y, mo = int(m[:4]), int(m[5:7])
            m = f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}" if _LAST.search(question) else m
            filters = {c: v for c, v in filters.items() if self.kit._type(c.split(":")[0]) != "dateTime"} | {col: m}
            led["period"] = {col: m}
            notes = [f"the latest month with data is {self._latest[col]}"]
        return {"metrics": metrics, "filters": filters, "given": given, "notes": notes}

    def _run(self, step: dict, compute, by, top, order_by, ascending, params) -> str:
        args = (by, top, order_by, ascending, params)
        filters, notes = step["filters"], step["notes"]
        out = compute(step["metrics"], by, filters, *args[1:])
        if out.startswith("Error") and filters != step["given"]:
            # a metric with a period of its own (a parameter, a contract) refuses a date filter: ours gives way
            filters, notes = step["given"], ["this metric sets its own period; the default was not applied"]
            self.ledger.setdefault(SESSION.get(), {}).pop("period", None)
            out = compute(step["metrics"], by, filters, *args[1:])
        step.update(filters=filters, notes=notes)
        return out

    def _line(self, step: dict) -> str:
        scope = "; ".join(f"{k} = {v}" for k, v in step["filters"].items()) or "everything, no filter"
        labels = ", ".join(self.layer.metrics[k]["label"] for k in step["metrics"])
        return f"{labels}; {scope}{' (' + ', '.join(step['notes']) + ')' if step['notes'] else ''}"

    def compute(self, metrics, by=None, filters=None, top=None, order_by=None, ascending=False, params=None):
        if not QUESTION.get():  # a dashboard or a brief on the same toolkit: only an agent's turn is guarded
            return self._compute(metrics, by, filters, top, order_by, ascending, params)
        step = self._settle(metrics, by, filters)
        if isinstance(step, str):
            return step
        out = self._run(step, self._compute, by, top, order_by, ascending, params)
        return out if out.startswith("Error") else f"Plan (state it in the answer, plainly): {self._line(step)}.\n\n{out}"

    def _check(self, step: dict, ask: dict) -> str | None:
        """Why the model cannot answer this step, without touching the warehouse: the compiler knows what each metric
        allows (the grouping it needs, the date filters its contract takes, the columns it reaches)."""
        def compiles(filters):
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
        settled, wrong = [], []
        for i, ask in enumerate(steps, 1):
            step = self._settle(ask.get("metrics"), ask.get("by"), ask.get("filters"))
            why = step if isinstance(step, str) else None if step["metrics"] else "Error: a step needs metrics."
            why = why or self._check(step, ask)
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
            out = self._run(step, kept, ask.get("by"), ask.get("top"), ask.get("order_by"), bool(ask.get("ascending")), ask.get("params"))
            return out, run

        left = max(t["start"] + self.turn_seconds - time.monotonic(), PLAN_SECONDS) if t is not None else self.turn_seconds
        left = min(left, seconds or left)
        seen = self.kit._seq
        pool = ThreadPoolExecutor(AT_ONCE)
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
               + f"\n\nQuestion: {question}")
        return [("system", _READ.replace("DATE", date)), ("human", ask)]

    def run(self, form: dict, question: str) -> tuple[str, str]:
        """Steps 2 and 3: (the results, or "" when the agent should take it, and which tool ran)."""
        steps = {k: form[k] for k in ("metrics", "by", "filters", "top", "order_by", "ascending") if form.get(k) not in (None, [], {}, "")}
        steps["metrics"] = list(dict.fromkeys([*self._named(question), *(steps.get("metrics") or [])]))
        if form.get("route") == "overview":
            return self.overview(form, question), "compute"
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
            r = next((r for r in self.kit.results if r["seq"] > seen and r.get("metrics") == [k]), None)
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
        metric = _root(self.idx, (steps.get("metrics") or [""])[-1])
        col = self._date(metric) if metric in self.idx else None
        latest = self._latest_month(metric, col) if col else None
        if not latest:
            return ""
        y, mo = int(latest[:4]), int(latest[5:7])
        before = f"{y - (mo == 1)}-{(mo - 2) % 12 + 1:02d}" if _MONTHS.search(question) else f"{y - 1}-{mo:02d}"
        filters = {c: v for c, v in (steps.get("filters") or {}).items() if self.kit._type(c.split(":")[0]) != "dateTime"}
        out = self.explain(metric, col, latest, before, filters)
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
        new = [r for r in self.kit.results if r["seq"] > seen and r.get("kind") == "compute"]
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

    async def compute(ctx: bi.RunContext, steps: list[Step]) -> str:
        """Run the plan for this question, once: a step for each number or table the answer needs, all sent together.
        A step has `metrics` (keys from find_metrics), and as needed `by` (Table[Column], dates may add :month etc.),
        `filters` ({Table[Column]: value or [values]}, loosely typed), `top`, `order_by`, `ascending` and what-if `params`.
        The whole plan is checked first, and nothing runs unless all of it can."""
        return await asyncio.to_thread(h.plan, steps)  # off the loop: Stop and everyone else still work

    async def explain(ctx: bi.RunContext, metric: str, period: str, now: str, before: str,
                      filters: dict[str, str | int | float | list[str]] | None = None) -> str:
        """Why a metric changed between two periods ("what drove the rise"): `period` is the date column with its grain
        (e.g. Calendar[Date]:month), `now` and `before` two of its values, `filters` as for compute. It splits the
        change by the columns and narrows to what carries most of it, within a time limit."""
        job = asyncio.to_thread(h.explain, metric, period, now, before, filters)  # off the loop: Stop and everyone else still work
        try:
            return await asyncio.wait_for(job, h.seconds + 30)
        except TimeoutError:
            return "Error: the warehouse did not answer within the time limit. Say so, and offer a narrower question."

    for fn in (find_metrics, compute, explain):
        fn.__annotations__["ctx"] = bi.RunContext

    def within_turn(fn):
        @functools.wraps(fn)
        async def run(*a, **k):
            t = TRACE.get()
            if t is not None:
                t["calls"] += 1
                # a slow model must not cost the question its answer: a plan yet to run still runs, within its own limits
                unplanned = fn.__name__ == "compute" and (not t.get("plans") or t.get("failed"))
                if t["calls"] > TURN_CALLS or (time.monotonic() - t["start"] > h.turn_seconds and not unplanned):
                    return OUT_OF_TURN
            return await fn(*a, **k)
        return run

    tools = [within_turn(t) for t in [find_metrics, compute, explain, *[t for t in kit.tools() if t.__name__ not in ("list_metrics", "compute", "explain")]]]

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
            return form if form.get("route") == "overview" or (form.get("route") in ("compute", "explain") and form.get("metrics")) else None

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
                if form := h.read(message):
                    t["read"] = "code"
                    tried.append(form)
                else:
                    if "@" in message:
                        return
                    if form := await ask_form(model, message, session_id):
                        t["read"] = "model"
                        tried.append(form)
                for form in tried:
                    tool = "explain" if form["route"] == "explain" else "compute"
                    yield Event.tool_call(agent, tool, {"form": form}).line() + "\n"
                    out, tool = await asyncio.to_thread(h.run, form, message)
                    open_ended = form["route"] == "overview"
                    head, offer = h.scoreboard(seen, form) if out and open_ended and form.get("now") else (h.number(seen) if out else "", "")
                    if head:  # ahead of the rows: the page picks the answer's picture by the numbers it states
                        yield json.dumps({"type": "chunk", "data": {"text": head}}) + "\n"
                    yield Event.tool_call(agent, tool, status="completed", summary=out[:4000]).line() + "\n"
                    if not out:
                        t.update(plans=0, failed=False)
                        t["read"] = "agent"
                        return
                    t["first"] = time.monotonic() - t["start"]
                    words = ""
                    moved = f"What stands out is {form['moved']}: it moved furthest, in percent.\n" if form.get("moved") else ""
                    results = "\n".join(x for x in out.splitlines() if not x.startswith(("Data:", "Step 1: Data:", "Plan (")))
                    try:
                        said = aiter(model.astream([("system", _SAY), ("human", f"Question: {message}\n\nOn screen, as the report shows it:\n{head}\n{moved}\nResults:\n{results[:6000]}")]))
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
                    led.setdefault("said", []).append((message, answer))
                    led.setdefault("unseen", []).append((message, answer))

        async def stream(message, session_id="default", **kw):
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
                        print(f"[desk] direct path gave way: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
                        t.update(read="agent", plans=0, failed=False)
                    if t.get("answered"):
                        return
                led = h.ledger.setdefault(session_id, {})
                told = "".join(f"Q: {q}\nA: {a}\n" for q, a in led.pop("unseen", []))  # the agent did not see those turns
                said = f"Answered earlier in this conversation:\n{told}\nNow: {message}" if told else message
                lines = aiter(inner(said, session_id=session_id, **kw))
                while True:
                    try:
                        line = await asyncio.wait_for(anext(lines), max(t0 + h.turn_seconds * 2 - time.monotonic(), 0.1))
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        yield json.dumps({"type": "error", "data": {"message": "That took too long to finish. Try a narrower question."}}) + "\n"
                        break
                    yield line
            finally:
                took = time.monotonic() - t0
                first = f", first number {t['first']:.1f}s" if "first" in t else ""
                print(f"[desk] {took:.1f}s, read by {t['read']}{first}, {t['calls']} tool calls: warehouse {t['secs']:.1f}s over {t['n']} queries (slowest "
                      f"{t['slowest']:.1f}s), model and the rest {max(took - t['secs'], 0):.1f}s", file=sys.stderr, flush=True)

        orch.stream = stream
        return orch

    return tools, prompt(kit), wrap
