"""Insights: the business view's analysis tools.

Each tool computes from governed definitions and returns two things: a card for the canvas
(what the business user sees) and a short text for the agent (what it narrates). Cards carry
their provenance: the definitions used, the valuation date, the context version, the Power BI
measures that are the same quantity, and the dimensions a user can drill into next.

    trend      a metric over quarters, with the latest complete quarter as the headline
    breakdown  a metric split by one dimension, largest first
    change     why a metric moved between two quarters: utilization vs unit cost when the
               semantic layer knows the identity, and each segment's exact contribution
    outliers   members against their peer group (compare_to_peers as data)
    evidence   how a metric is defined, who owns it, where it comes from, what changed it
    gap        a question no governed metric answers yet, recorded for its owners
"""

from __future__ import annotations

import math
import re
from calendar import monthrange
from datetime import date

import yaml

from .engine_job import plain
from insurance_context.schema_utils import table_name

from .proposals import OPEN, Studio, fmt

PERIOD = "quarter"
# Dimensions a business user drills into, beyond the client's own (curated: the pack has many
# that are empty for a given client).
COMMON_DRILLS = ["line_of_business", "state"]


def label(value) -> str:
    """How a code reads to a person: medicare_advantage -> Medicare Advantage; IL stays IL."""
    if value is None:
        return "Not recorded"
    s = str(value)
    if re.fullmatch(r"[a-z0-9_]+", s) and not re.fullmatch(r"\d{4}(-Q\d)?", s):
        return s.replace("_", " ").title()
    return s


def quarter_end(q: str) -> date | None:
    m = re.fullmatch(r"(\d{4})-Q([1-4])", str(q))
    if not m:
        return None
    y, month = int(m.group(1)), 3 * int(m.group(2))
    return date(y, month, monthrange(y, month)[1])


def complete(q: str, valuation: date, runout_days: int = 90) -> bool:
    """A quarter is complete once a full claims runout has passed since it ended."""
    end = quarter_end(q)
    return end is not None and (valuation - end).days >= runout_days


def latest_complete_quarter(valuation: date) -> str:
    y, n = valuation.year, (valuation.month - 1) // 3 + 1
    for _ in range(8):
        q = f"{y}-Q{n}"
        if complete(q, valuation):
            return q
        y, n = (y - 1, 4) if n == 1 else (y, n - 1)
    return q


def _num(v) -> float | None:
    return None if v is None else float(v)


class Insights:
    def __init__(self, studio: Studio, client: str, kit):
        self.studio, self.client, self.kit = studio, client, kit
        self.sl = kit.semantic
        self.valuation, _ = kit._as_of(None)  # noqa: SLF001

    # ------------------------------------------------------------ shared

    def _resolve(self, con, metrics: list[str], filters: dict | None) -> dict:
        """Business spellings to data codes, each dimension checked on a metric that carries it."""
        leaves = [x for m in metrics for x in self.sl._leaves(m)]  # noqa: SLF001
        out = {}
        for dim, value in (filters or {}).items():
            carrier = next((x for x in metrics + leaves if dim in self.sl.available_dimensions([x])), None)
            if carrier is None:
                raise ValueError(f"{self.sl.metrics[metrics[0]].label} cannot be filtered by {dim}")
            where, _ = self.kit.resolve_filters(con, [carrier], {dim: value}, self.valuation)
            out.update(where)
        return out

    def _query(self, con, metrics, by, where):
        return self.sl.query(con, metrics, by, where, self.valuation)

    def split_dims(self, metric: str) -> set[str]:
        """Dimensions a metric can be cut by: its own, or for a ratio, its numerator's (each segment's
        numerator over the whole denominator, the medical economics convention for PMPM)."""
        m = self.sl.metrics[metric]
        return self.sl.available_dimensions([m.ratio[0] if m.ratio else metric]) | self.sl.available_dimensions([metric])

    def _values(self, con, metric: str, by: list[str], where: dict) -> tuple[dict, bool]:
        """{by-values tuple: value}, and whether segments were taken over the whole denominator."""
        m = self.sl.metrics[metric]
        need = set(by) | set(where)
        n = len(by)
        if need <= self.sl.available_dimensions([metric]) or not m.ratio:
            res = self._query(con, [metric], by, where)
            return {tuple(r[:n]): _num(r[n]) for r in res.rows}, False
        a, b = m.ratio
        if not need <= self.sl.available_dimensions([a]):
            raise ValueError(f"{m.label} cannot be split by {', '.join(sorted(need - self.sl.available_dimensions([a])))}")
        bavail = self.sl.available_dimensions([b])
        bby = [d for d in by if d in bavail]
        bwhere = {k: v for k, v in where.items() if k in bavail}
        den = {tuple(r[:len(bby)]): _num(r[len(bby)]) for r in self._query(con, [b], bby, bwhere).rows}
        out = {}
        for r in self._query(con, [a], by, where).rows:
            key = tuple(r[:n])
            d = den.get(tuple(key[by.index(x)] for x in bby))
            out[key] = (m.scale or 1) * (_num(r[n]) or 0) / d if d else None
        return out, True

    def drills(self, metrics: list[str]) -> list[str]:
        own = list((self.studio.client(self.client).config.get("dimensions") or {}))
        avail = set.intersection(*(self.split_dims(m) for m in metrics)) if metrics else set()
        return [d for d in dict.fromkeys(COMMON_DRILLS + own) if d in avail and d != PERIOD]

    def powerbi(self, metric: str) -> list[dict]:
        folder = self.studio.s.main / "clients" / self.client / "powerbi"
        out = []
        for path in sorted(folder.glob("*.crosswalk.yaml")) if folder.exists() else []:
            doc = yaml.safe_load(path.read_text()) or {}
            out += [{"model": doc.get("model"), "measure": m["name"]} for m in doc.get("measures", [])
                    if m.get("metric") == metric and m.get("status") == "governed"]
        return out

    def meta(self, metrics: list[str], where: dict, by: list[str] | None = None) -> dict:
        by = by or []
        scope = [{"dim": d, "label": self.sl.dimensions[d].label if d in self.sl.dimensions else d,
                  "value": v, "value_label": ", ".join(label(x) for x in v) if isinstance(v, list) else label(v)}
                 for d, v in where.items()]
        return {
            "as_of": self.valuation.isoformat(), "version": self.studio.head()[:7],
            "client_title": self.studio.client(self.client).title,
            "metrics": [{"name": m, "label": self.sl.metrics[m].label, "format": self.sl.metrics[m].format,
                         "description": self.sl.metrics[m].description} for m in metrics],
            "scope": scope, "filters": where,
            "drill": [{"dim": d, "label": self.sl.dimensions[d].label} for d in self.drills(metrics)
                      if d not in by and d not in where],
            "powerbi": self.powerbi(metrics[0]) if metrics else [],
        }

    def _fmt(self, metric: str, v) -> str:
        return fmt(v, self.sl.metrics[metric].format)

    # ------------------------------------------------------------ trend

    def trend(self, metric: str, filters: dict | None = None, series_by: str | None = None,
              title: str = "") -> tuple[dict, str]:
        con = self.kit._con()  # noqa: SLF001
        try:
            where = self._resolve(con, [metric], filters)
            where.pop(PERIOD, None)
            by = [PERIOD] + ([series_by] if series_by else [])
            vals, share = self._values(con, metric, by, where)
        finally:
            con.close()
        periods = sorted({k[0] for k in vals if k[0]})
        series: dict = {}
        for k, v in vals.items():
            if k[0]:
                series.setdefault(k[1] if series_by else "_all", {})[k[0]] = v
        lines = [{"key": k, "label": label(k) if series_by else self.sl.metrics[metric].label,
                  "values": [series[k].get(p) for p in periods]} for k in sorted(series, key=str)]
        incomplete = [p for p in periods if not complete(p, self.valuation)]
        done = [p for p in periods if p not in incomplete]
        headline = None
        if done and not series_by:
            v = series["_all"]
            last = done[-1]
            prior = done[-2] if len(done) > 1 else None
            yoy = f"{int(last[:4]) - 1}{last[4:]}"
            headline = {"period": last, "value": v.get(last), "prior_period": prior,
                        "prior_value": v.get(prior) if prior else None,
                        "yoy_period": yoy if yoy in v else None, "yoy_value": v.get(yoy)}
        card = {"kind": "trend", "title": title or f"{self.sl.metrics[metric].label} by quarter",
                "periods": periods, "series": lines, "incomplete": incomplete, "headline": headline,
                "series_by": series_by, "series_by_label": self.sl.dimensions[series_by].label if series_by else None,
                "share_of_total": share, **self.meta([metric], where, by)}
        text = (f"{self.sl.metrics[metric].label} by quarter (valuation {self.valuation}; quarters still "
                f"receiving claims, not comparable yet: {', '.join(incomplete) or 'none'}): "
                + "; ".join(f"{ln['label']}: " + ", ".join(f"{p} {self._fmt(metric, v)}" for p, v in zip(periods, ln["values"]))
                            for ln in lines[:8]))
        return card, text

    # ------------------------------------------------------------ breakdown

    def breakdown(self, metric: str, by: str, filters: dict | None = None, title: str = "",
                  top: int = 12) -> tuple[dict, str]:
        con = self.kit._con()  # noqa: SLF001
        try:
            where = self._resolve(con, [metric], filters)
            vals, share = self._values(con, metric, [by], where)
            total = self._values(con, metric, [], where)[0].get((), None)
        finally:
            con.close()
        rows = sorted(({"key": k[0], "label": label(k[0]), "value": v} for k, v in vals.items()),
                      key=lambda x: -(x["value"] or 0))
        shown = rows[:top]
        name = self.sl.metrics[metric].label
        card = {"kind": "breakdown", "title": title or f"{name} by {self.sl.dimensions[by].label.lower()}",
                "by": by, "by_label": self.sl.dimensions[by].label, "rows": shown, "more": max(0, len(rows) - top),
                "total": total, "share_of_total": share, **self.meta([metric], where, [by])}
        text = (f"{name} by {by} (valuation {self.valuation}), overall {self._fmt(metric, total)}"
                + (" (each segment's paid over all member months, so segments add up to the total)" if share else "")
                + ": " + ", ".join(f"{x['label']} {self._fmt(metric, x['value'])}" for x in shown))
        return card, text

    # ------------------------------------------------------------ change

    def _identity(self, metric: str) -> dict | None:
        """utilization x unit cost, when the catalog defines both halves of a ratio metric."""
        m = self.sl.metrics[metric]
        if not m.ratio:
            return None
        a, b = m.ratio
        unavailable = self.sl.unavailable()
        for x in self.sl.metrics.values():
            if x.ratio and x.ratio[0] == a and x.ratio[1] != b and not x.scale and x.name not in unavailable:
                for y in self.sl.metrics.values():
                    if y.ratio and list(y.ratio) == [x.ratio[1], b] and y.name not in unavailable:
                        return {"unit_cost": x.name, "utilization": y.name}
        return None

    def change(self, metric: str, from_period: str, to_period: str, by: str | None = None,
               filters: dict | None = None, title: str = "") -> tuple[dict, str]:
        m = self.sl.metrics[metric]
        ident = self._identity(metric)
        if by is None:
            by = next((d for d in self.drills([metric]) if d not in (filters or {})), None)
        if by and by not in self.split_dims(metric):
            raise ValueError(f"{m.label} cannot be split by {by}")
        pq = [from_period, to_period]
        open_ = [q for q in pq if quarter_end(q) and not complete(q, self.valuation)]
        if open_:  # a quarter still receiving claims would show runout as a fall in cost
            raise ValueError(f"{', '.join(open_)} is still receiving claims (data valued {self.valuation}), so it is "
                             f"not comparable yet; the latest complete quarter is "
                             f"{latest_complete_quarter(self.valuation)}. Do not show another exhibit: say so")
        con = self.kit._con()  # noqa: SLF001
        try:
            extra = [ident["unit_cost"], ident["utilization"]] if ident else []
            where = self._resolve(con, [metric] + extra, filters)
            where.pop(PERIOD, None)
            both = {**where, PERIOD: pq}
            tot = {k: self._values(con, x, [PERIOD], both)[0] for k, x in
                   [("m", metric)] + ([("c", ident["unit_cost"]), ("u", ident["utilization"])] if ident else [])}
            v1, v2 = tot["m"].get((from_period,)), tot["m"].get((to_period,))
            if v1 is None or v2 is None:
                raise ValueError(f"no data for {from_period} or {to_period}; quarters look like 2025-Q4")
            steps, part = [], None
            if by:
                if m.ratio:
                    a, b = m.ratio
                    bwhere = {k: v for k, v in both.items() if k in self.sl.available_dimensions([b])}
                    den = {r[0]: _num(r[1]) for r in self._query(con, [b], [PERIOD], bwhere).rows}
                    seg = self._query(con, [a], [PERIOD, by], both).rows
                    scale = m.scale or 1
                    part = {"name": a, "label": self.sl.metrics[a].label, "format": self.sl.metrics[a].format}
                else:
                    seg = self._query(con, [metric], [PERIOD, by], both).rows
                    den, scale = None, 1
                    part = {"name": metric, "label": m.label, "format": m.format}
                vals = {}
                for p, k, x in seg:
                    vals.setdefault(k, {})[p] = _num(x) or 0.0
                for k, pv in vals.items():
                    f, t = pv.get(from_period, 0.0), pv.get(to_period, 0.0)
                    c = scale * (t / den[to_period] - f / den[from_period]) if den else t - f
                    steps.append({"key": k, "label": label(k), "contribution": c, "from": f, "to": t})
        finally:
            con.close()
        steps.sort(key=lambda s: -abs(s["contribution"]))
        if len(steps) > 7:
            rest = steps[7:]
            steps = steps[:7] + [{"key": None, "label": f"Other ({len(rest)})", "other": True,
                                  "contribution": sum(s["contribution"] for s in rest),
                                  "from": sum(s["from"] for s in rest), "to": sum(s["to"] for s in rest)}]
        drivers = None
        if ident and v1 and v2 and v1 > 0 and v2 > 0:
            u1, u2 = tot["u"].get((from_period,)), tot["u"].get((to_period,))
            c1, c2 = tot["c"].get((from_period,)), tot["c"].get((to_period,))
            if u1 and u2 and c1 and c2:
                lm = math.log(v2 / v1) or 1e-12
                dv = v2 - v1
                drivers = {
                    key: {"metric": ident[name], "label": self.sl.metrics[ident[name]].label,
                          "format": self.sl.metrics[ident[name]].format, "from": f, "to": t,
                          "pct": t / f - 1, "contribution": dv * math.log(t / f) / lm}
                    for key, name, f, t in (("utilization", "utilization", u1, u2), ("unit_cost", "unit_cost", c1, c2))}
        card = {"kind": "change", "title": title or f"Why {m.label} moved from {from_period} to {to_period}",
                "from_period": from_period, "to_period": to_period, "start": v1, "end": v2,
                "pct": (v2 / v1 - 1) if v1 else None, "drivers": drivers, "by": by,
                "by_label": self.sl.dimensions[by].label if by else None, "steps": steps, "part": part,
                **self.meta([metric], where, [by] if by else [])}
        text = f"{m.label} {from_period} {self._fmt(metric, v1)} -> {to_period} {self._fmt(metric, v2)}"
        if v1:
            text += f" ({(v2 / v1 - 1) * 100:+.1f}%)"
        if drivers:
            d = drivers
            text += (f". Utilization ({d['utilization']['label']}) {d['utilization']['from']:.0f} -> {d['utilization']['to']:.0f} "
                     f"({d['utilization']['pct'] * 100:+.1f}%), unit cost ({d['unit_cost']['label']}) "
                     f"{self._fmt(ident['unit_cost'], d['unit_cost']['from'])} -> {self._fmt(ident['unit_cost'], d['unit_cost']['to'])} "
                     f"({d['unit_cost']['pct'] * 100:+.1f}%)")
        if steps:
            text += (f". Contribution to the change by {by}: "
                     + ", ".join(f"{s['label']} {self._fmt(metric, s['contribution'])}" for s in steps))
        return card, text

    # ------------------------------------------------------------ outliers

    def outliers(self, metric: str, entity: str, peers: str, volume_metric: str | None = None,
                 min_volume: float = 0, threshold: float = 2.0, periods: list[str] | None = None,
                 filters: dict | None = None, min_z: float | None = None, title: str = "") -> tuple[dict, str]:
        a = self.kit.peer_analysis(metric, entity, peers, volume_metric, min_volume, threshold, periods,
                                   PERIOD, filters, None, min_z)
        if "error" in a:
            raise ValueError(a["error"])
        pts = [{"entity": x["entity"], "peers": x["peers"], "ratio": x["ratio"], "flagged": x["flagged"],
                "value": x["periods"][-1]["value"], "group": x["periods"][-1]["group"],
                "volume": sum((p["volume"] or 0) for p in x["periods"]) if volume_metric else None,
                "z": min((p["z"] for p in x["periods"] if p["z"] is not None), default=None)}
               for x in a["members"]]
        flagged = [p for p in pts if p["flagged"]]
        card = {"kind": "outliers", "title": title or f"{self.sl.metrics[metric].label}: {entity} against {peers}",
                "entity": entity, "entity_label": self.sl.dimensions[entity].label,
                "peers": peers, "peers_label": self.sl.dimensions[peers].label,
                "volume_metric": volume_metric, "volume_label": self.sl.metrics[volume_metric].label if volume_metric else None,
                "threshold": threshold, "periods": a["windows"] if periods else [], "min_z": min_z,
                "min_volume": min_volume, "points": pts, "flagged": len(flagged),
                **self.meta([metric], dict(filters or {}), [entity, peers])}
        text = (f"{len(flagged)} of {len(pts)} {entity} values at or above {threshold:g}x their {peers} on {metric}"
                + (f" in every period of {', '.join(a['windows'])}" if periods else "") + ": "
                + "; ".join(f"{p['entity']} ({p['peers']}) {p['ratio']:.1f}x" for p in flagged[:12]))
        return card, text

    # ------------------------------------------------------------ evidence

    def evidence(self, metric: str, title: str = "") -> tuple[dict, str]:
        trace = self.kit.trace([metric])
        definition = (trace.get("semantic") or {}).get("metrics", [{}])[0]
        owners = self.studio.owners()
        path = f"clients/{self.client}/client.yaml"
        areas = owners.areas_for([path], {path: {"metrics"}}, [])
        own = [{"area": a.title, "people": [owners.person(p).name for p in a.owners]} for a in areas]
        lineage = []
        for c in trace.get("context") or []:
            for u in c.get("units", []):
                lineage.append({"entity": c.get("label") or c.get("entity"), "unit": u.get("unit_name"),
                                "system": u.get("source_system"), "tables": u.get("tables", []),
                                "view": u.get("unit_view"), "rows": u.get("rows"),
                                "agent_view": f"ont_agent.{table_name(c.get('entity', ''))}",
                                "redacted": c.get("redacted_for_agents", [])})
        label_ = self.sl.metrics[metric].label
        changes, pending = [], []
        for r in self.studio.list(status=None, client=self.client):
            hits = [x for x in (r.get("impact") or {}).get("rows", []) if x.get("metric") == metric and x.get("changed")]
            defines = any(op.get("op") == "set_metric" and op.get("metric") == metric for op in r.get("ops", []))
            if not hits and not defines:
                continue
            ev = (r.get("evidence") or [None])[0]
            item = {"id": r["id"], "title": r["title"], "status": r["status"],
                    "at": r.get("merged_at") or (r.get("approvals") or [{}])[-1].get("at"),
                    "approved_by": [a.get("name") for a in r.get("approvals", [])],
                    "impact": [{"group": " / ".join(label(g) for g in x.get("group") or []) or "All", "before": x["before_fmt"],
                                "after": x["after_fmt"]} for x in hits][:4],
                    "source": {"title": ev.get("title"), "page": ev.get("page"), "quote": ev.get("quote")} if ev else None}
            (changes if r["status"] == "merged" else pending if r["status"] in OPEN else []).append(item)
        changes.sort(key=lambda x: x.get("at") or "", reverse=True)
        card = {"kind": "evidence", "title": title or f"How {label_} is defined, and why you can trust it",
                "definition": definition, "owners": own, "lineage": lineage, "changes": changes[:6],
                "pending": pending[:4], "ontology_view": next((e.get("view") for e in (trace.get("ontology") or {}).get("entities", [])), None),
                **self.meta([metric], {})}
        text = (f"{label_}: {definition.get('description')} Formula: {definition.get('formula')}. "
                + " ".join(f"Part {c['label']}: {c.get('description')} (filter: {c.get('filter') or 'none'})."
                           for c in definition.get("children", []))
                + f" Owners: {', '.join(a['area'] for a in own) or 'none'}. Approved changes that moved it: "
                + ("; ".join(f"{c['title']} ({c['at'] and c['at'][:10]}, approved by {', '.join(c['approved_by'])}: "
                             + ", ".join(f"{i['group']} {i['before']} -> {i['after']}" for i in c["impact"]) + ")"
                             for c in changes[:4]) or "none")
                + (f". Under review: {'; '.join(p['title'] for p in pending)}" if pending else "")
                + f". Same quantity in Power BI: {', '.join(p['measure'] for p in card['powerbi']) or 'none recorded'}.")
        return card, text

    # ------------------------------------------------------------ gap

    def gap(self, question: str, detail: str, author: dict) -> tuple[dict, str]:
        r = self.studio.ask(client=self.client, title=question[:120], summary=detail, evidence=[],
                            about={"type": "metric", "metric": True}, author=author)
        areas = list((r.get("area_titles") or {}).values())
        people = sorted({self.studio.owners().person(p).name for ps in (r.get("reviewers") or {}).values() for p in ps})
        card = {"kind": "gap", "title": question, "detail": detail, "id": r["id"], "areas": areas, "owners": people,
                "as_of": self.valuation.isoformat(), "version": self.studio.head()[:7]}
        return card, (f"Recorded as question {r['id']} for {', '.join(areas) or 'the owners'} "
                      f"({', '.join(people)}). No governed number was given.")


def catalog_brief(ins: Insights) -> str:
    """The metric catalog and drillable dimensions with their values, for the agent's prompt."""
    sl = ins.sl
    catalog = sl.catalog()
    lines = ["Governed metrics (name: label - meaning):"]
    for m in catalog:
        lines.append(f"- {m['name']}: {m['label']} - {m.get('description') or ''}".rstrip(" -"))
    own = list((ins.studio.client(ins.client).config.get("dimensions") or {}))
    dims = [d for d in dict.fromkeys([PERIOD] + COMMON_DRILLS + own) if d in sl.dimensions]
    con = ins.kit._con()  # noqa: SLF001
    try:
        lines.append("Dimensions and the values in the data:")
        for d in dims:
            dim = sl.dimensions[d]
            metric = next((m["name"] for m in catalog if d in m["dimensions"] and m.get("entity")), None)
            if metric is None:
                continue
            try:
                vals = [v for v in sl.dimension_values(con, [metric], d, ins.valuation)]
            except Exception:  # noqa: BLE001
                vals = []
            shown = ", ".join(map(str, vals[:14])) + (f" ... ({len(vals)} in all)" if len(vals) > 14 else "")
            lines.append(f"- {d} ({dim.label}): {shown}")
    finally:
        con.close()
    return "\n".join(lines)


def plain_card(card: dict) -> dict:
    """JSON-safe copy of a card (Decimal, dates)."""
    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return plain(x)
    return walk(card)
