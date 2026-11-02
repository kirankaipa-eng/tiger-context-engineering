"""Guardrails for the runs that sweep a model (the Living Brief, the Team space's Scout): what they cost depends on
what someone chose to watch, never on how many measures the model has. A model with 700 measures costs the
warehouse no more than one with ten.

    watchlist(m, mine)   the measures a sweep looks at: a small model's every one, else the watched ones
    kit(m)               a toolkit of the run's own, on a warehouse that spends at most the run's budget of queries
    single(key, fn)      one run at a time per key: a second caller waits for the run under way and gets its result

Settings, in brief.yaml beside the model (all optional):

    watch: [paid, admits]     the measures to watch (beside the headline ones and those a person follows)
    budget: 3000              warehouse queries one run may spend; past it the run stops and says it is partial
    compare: [1]              the brief: months back each month is investigated against (1: the month before, which
                              finds a place that broke from its own last twelve months; [1, 12]: also a year before)
    scout_compare: [1, 12]    the same for the Scout, whose findings are the changes themselves

However many runs are going, no more than PARALLEL of their queries are on the warehouse at once; Ask and the
dashboards do not wait on them.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import yaml

from context_studio import pbi_team
from insurance_context.bi_agent import BIToolkit

SMALL = 15       # a model with this many measures to sweep, or fewer, has every one watched
WATCH = 10       # else at most this many
BUDGET = 3000    # warehouse queries per run
COMPARE = (1,)   # the brief: a month against the month before finds what broke from its own pattern
SCOUT = (1, 12)  # the Scout: against the month before and the same month a year before
PARALLEL = 4     # sweep queries on the warehouse at once, across every run
RESERVE = 0.3    # of a run's budget, kept for what it makes of its investigations

_SLOTS = threading.BoundedSemaphore(PARALLEL)


class OverBudget(Exception):
    pass


def settings(folder: Path) -> dict:
    p = folder / "brief.yaml"
    conf = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    back = lambda key, default: [int(k) for k in conf.get(key) or default if int(k) > 0] or list(default)
    return {"watch": [str(k) for k in conf.get("watch") or []], "budget": int(conf.get("budget") or BUDGET),
            "compare": back("compare", COMPARE), "scout_compare": back("scout_compare", SCOUT)}


def watchlist(m, mine: list[str] | None = None, headline: list[str] | None = None) -> list[str]:
    """The measures a sweep looks at: those `mine` names (a person's brief), else every one a small model has,
    else the headline ones (the brief's, unless given), then brief.yaml's watch list, then what people's briefs follow; at most WATCH."""
    every = [k for k in pbi_team._additive(m.layer) if pbi_team._period(m.layer, k)]
    if mine:
        return [k for k in mine if k in every][:WATCH]
    if len(every) <= SMALL:
        return every
    if headline is None:
        from context_studio.pbi_brief import (
            _headline,  # the brief's own choice: money measures first
        )
        headline = _headline(m)
    followed = [k for p in _briefs(m.folder) for k in p.get("prefs", {}).get("measures", [])]
    picked = [*headline, *settings(m.folder)["watch"], *followed]
    return [k for k in dict.fromkeys(picked) if k in every][:WATCH]


def _briefs(folder: Path) -> list[dict]:
    p = folder / "briefs.json"
    return list(json.loads(p.read_text(encoding="utf-8")).values()) if p.exists() else []


class Budgeted:
    """A warehouse that answers at most `budget` queries, PARALLEL at a time across every run; the rest raise
    OverBudget (which the toolkit's own error handling turns into a step that did not run)."""

    def __init__(self, wh, budget: int):
        self._wh, self.budget, self.spent, self.over = wh, budget, 0, False
        self._count = threading.Lock()

    def __getattr__(self, name):
        return getattr(self._wh, name)

    def searching(self) -> bool:
        """Budget left to look further, beyond what is kept for making sense of what was found (RESERVE)."""
        if self.spent >= self.budget * (1 - RESERVE):
            self.over = True
            return False
        return True

    def query(self, sql: str, limit: int | None = None):
        with self._count:
            if self.spent >= self.budget:
                self.over = True
                raise OverBudget(f"this run has used its {self.budget:,} warehouse queries")
            self.spent += 1
        with _SLOTS:
            return self._wh.query(sql, limit=limit)


def kit(m, budget: int | None = None, wh: Budgeted | None = None) -> BIToolkit:
    """A toolkit for one run: the model's warehouse and row security, a budget of its own (or `wh`'s, to share one
    run's budget between its parallel steps), results of its own (so a step reads back only its own investigation)."""
    shared = m.kit()
    own = BIToolkit(shared.layer, wh or Budgeted(shared.wh, budget or settings(m.folder)["budget"]),
                    row_filters=shared.row_filters, max_rows=shared.max_rows, max_top=shared.max_top)
    own.folder = shared.folder
    return own


def spent(k: BIToolkit) -> dict:
    wh = k.wh
    return {"queries": wh.spent, "budget": wh.budget, "partial": wh.over} if isinstance(wh, Budgeted) else {}


_FLIGHTS: dict[tuple, dict] = {}
_GATE = threading.Lock()


def single(key: tuple, fn):
    """Run `fn` unless a run for `key` is already under way; then wait for that one and return what it returned."""
    with _GATE:
        flight = _FLIGHTS.get(key)
        mine = flight is None
        if mine:
            flight = _FLIGHTS[key] = {"done": threading.Event(), "out": None, "err": None}
    if mine:
        try:
            flight["out"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed to every caller waiting on this run
            flight["err"] = exc
        finally:
            with _GATE:
                _FLIGHTS.pop(key, None)
            flight["done"].set()
    else:
        flight["done"].wait()
    if flight["err"] is not None:
        raise flight["err"]
    return flight["out"]
