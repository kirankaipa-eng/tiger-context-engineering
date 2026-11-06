"""The agent's guarantees, in code: what a question means is settled here, not left to the model on each run.

    tools, prompt, wrap = install(kit, folder)   build the agent from tools and prompt, and pass it through wrap

    index(layer)      768 measures as ~50 families: a base metric, its variants, its aliases and what is legacy
    find(query)       the few metrics a question means, instead of a list of every measure
    guard             every compute passes here: an alias becomes its base metric, a prior-year measure the question
                      did not ask for is refused, a question with no period gets the model's one default period, and
                      the plan it ran under comes back to be stated in the answer

precedents.yaml beside capture.yaml (optional) holds what owners have decided:

    terms: {pmpm: total_pmpm}   this word means this metric
    period: latest_month        what a question with no period means (the default), or `all`

Works on any agent runtime: it wraps the toolkit, so the tools the runtime sees keep their names and signatures.
"""

from __future__ import annotations

import contextvars
import math
import re
from collections import Counter
from pathlib import Path

import yaml

from . import bi_agent as bi

QUESTION = contextvars.ContextVar("desk_question", default="")
SESSION = contextvars.ContextVar("desk_session", default="")

_ALIAS = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_BASE = re.compile(r"^\s*(?:CALCULATE|TOTALYTD)\(\s*\[([^\]]+)\]", re.IGNORECASE)
_YOY = re.compile(r"^\s*DIVIDE\(\s*\[([^\]]+)\]\s*,.*\)\s*-\s*1\s*$", re.IGNORECASE)
_PRIOR = re.compile(r"SAMEPERIODLASTYEAR|PREVIOUSYEAR|PARALLELPERIOD|DATEADD|TOTALYTD|\bPY\b|YoY", re.IGNORECASE)
_LEGACY = re.compile(r"^(zz|_|old\b|tmp\b|test\b)|\b(bkp|backup|deprecated|old)\b", re.IGNORECASE)
_ASKS_PRIOR = re.compile(r"\b(last year|prior|previous|year before|year ago|py|yoy|year over year|ytd|year to date|versus|vs|"
                         r"compared?|change[ds]?|growth|grew|trend)\b", re.IGNORECASE)
_NAMES_PERIOD = re.compile(r"\b(20\d\d|q[1-4]|ytd|year to date|this year|last year|last month|this month|january|february|march|"
                           r"april|may|june|july|august|september|october|november|december)\b", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
SHOWN = 6


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
        self.docs = {k: Counter(_words(f"{m['label']} {m.get('description') or ''}")) for k, m in self.layer.captured().items()}
        self.df = Counter(w for d in self.docs.values() for w in d)
        self.ledger: dict[str, dict] = {}   # per conversation: the period in play
        self._latest: dict[str, str] = {}
        self._compute = kit.compute
        kit.compute = self.compute

    def find(self, query: str) -> str:
        """The metrics a question means, best first: an owner's precedent, then base metrics on the words asked."""
        words = _words(query)
        said = " ".join(words)
        fixed = [v for t, v in self.terms.items() if re.search(rf"\b{re.escape(t)}\b", said)]
        n = len(self.docs)

        def score(k: str) -> float:
            d, kind = self.docs[k], self.idx[k]["kind"]
            s = sum(math.log(1 + n / (1 + self.df[w])) for w in set(words) if w in d)
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
        best = list(dict.fromkeys(fixed + sorted(pool, key=lambda k: (-covers(k), -score(k), k))))[:SHOWN]
        if not best:
            return f"No metric matches {query!r}. Try other words, or say it is not in the model."
        lines = [f"- {k} ({self.layer.metrics[k]['label']}): {bi.describe(self.layer, k)}{bi.needs(self.layer, k)}" for k in best]
        cols = [c for c in self.layer.columns_for(best[:1]) if self.kit._type(c) != "dateTime"]
        dates = [c for c in self.layer.columns_for(best[:1]) if self.kit._type(c) == "dateTime"][:1]
        return ("Use the first that fits; it is the default for these words:\n" + "\n".join(lines)
                + f"\nGroup or filter it by: {', '.join(cols)}"
                + (f"\nDates: {dates[0]}:year, :quarter or :month, e.g. filters {{{dates[0]}:year: 2025}}" if dates else "")
                + "\nNo period in the question: leave it out, the period is applied for you.")

    def _date(self, metric: str) -> str | None:
        cols = [c for c in self.layer.columns_for([metric]) if self.kit._type(c) == "dateTime"]
        cols.sort(key=lambda c: (not re.search(r"calendar|date\b|dim.?date", c.split("[")[0], re.IGNORECASE), c))
        return f"{cols[0]}:month" if cols else None

    def _latest_month(self, metric: str, col: str) -> str | None:
        """The last month the metric has a number: a calendar table runs past the data as often as not."""
        if metric not in self._latest:
            kept = list(self.kit.results)
            out = self._compute([metric], [col], None, None, None, False, None)
            rows = (self.kit.results[-1].get("shown") or []) if self.kit.results[-1:] != kept[-1:] else []
            self.kit.results = kept  # a look at the calendar, not a result to show
            have = [str(r[0]) for r in rows if r[-1] is not None]
            if have:
                self._latest[metric] = max(have)[:7]
            elif out.startswith("Error"):
                return None
        return self._latest.get(metric)

    def _grains(self, filters: dict) -> dict:
        """A date column filtered to a year or a month takes that grain: on the bare column "2025" matches no day."""
        out = {}
        for c, v in filters.items():
            grain = None
            if ":" not in c and self.kit._type(c) == "dateTime" and not isinstance(v, list):
                grain = "year" if re.fullmatch(r"\d{4}", str(v)) else "month" if re.fullmatch(r"\d{4}-\d{2}", str(v)) else None
            out[f"{c}:{grain}" if grain else c] = int(v) if grain == "year" else v
        return out

    def compute(self, metrics, by=None, filters=None, top=None, order_by=None, ascending=False, params=None):
        question = QUESTION.get()
        if not question:  # a dashboard or a brief on the same toolkit: only an agent's turn is guarded
            return self._compute(metrics, by, filters, top, order_by, ascending, params)
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
        filters, notes = self._grains(filters or {}), []
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
        out = self._compute(metrics, by, filters, top, order_by, ascending, params)
        if out.startswith("Error"):
            return out
        scope = "; ".join(f"{k} = {v}" for k, v in filters.items()) or "everything, no filter"
        labels = ", ".join(self.layer.metrics[k]["label"] for k in metrics)
        return f"Plan (state it in the answer, plainly): {labels}; {scope}{' (' + ', '.join(notes) + ')' if notes else ''}.\n\n{out}"


def prompt(kit) -> str:
    """The toolkit's prompt with the two rules the harness has taken over."""
    text = kit.system_prompt()
    text = text.replace("1. Call list_metrics first in a conversation, and list_columns when you need a column.",
                        "1. Call find_metrics with the business words of the question, then compute what it puts first. "
                        "Call list_columns only for a column to group or filter by.")
    return text.replace("5. When a question is ambiguous but has a sensible default, pick it, state the assumption in one\n"
                        "   line, and answer. Ask only if there is no sensible default.",
                        "5. Defaults are decided for you: find_metrics puts the default metric first and compute applies the\n"
                        "   default period. Use them and state the plan compute returns.")


def install(kit, folder=None):
    """(tools, system prompt, wrap): build the agent from the first two and pass it through `wrap`."""
    h = Harness(kit, folder)

    async def find_metrics(ctx: bi.RunContext, query: str) -> str:
        """Find the metrics a question means, by its business words ("paid", "readmission rate"): the default first,
        with what each needs."""
        return h.find(query)

    find_metrics.__annotations__["ctx"] = bi.RunContext
    tools = [find_metrics, *[t for t in kit.tools() if t.__name__ != "list_metrics"]]

    def wrap(orch):
        inner = orch.stream

        async def stream(message, session_id="default", **kw):
            QUESTION.set(message)
            SESSION.set(session_id)
            async for line in inner(message, session_id=session_id, **kw):
                yield line

        orch.stream = stream
        return orch

    return tools, prompt(kit), wrap
