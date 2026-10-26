"""Agent harness: agent tools generated from a client's context layer.

    from insurance_context.agent_tools import ContextToolkit
    kit = ContextToolkit(Client("summit_group"), "warehouse.duckdb")
    Orchestrator(..., system_prompt=kit.system_prompt(), tools=kit.tools(),
                 tool_registry=kit.registry())

Guardrails are structural, not prompt-based:
  * SQL runs only against the redacted `ont_agent` schema, is parsed and must be a
    single SELECT whose every table lives in that schema (no table functions,
    no file readers, no DDL); the connection is read-only with external access
    disabled and configuration locked.
  * KPIs come from the governed semantic layer, and every answer carries the
    metric definitions and valuation date that produced it.
"""

from __future__ import annotations

import difflib
import json
import re
from datetime import date
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

from .agent_runtime import RunContext  # tool signatures are resolved against module globals

from .client import Client
from .exports import context_document
from .schema_utils import label, table_name
from .semantic import MetricError, SemanticLayer
from .sqlgen import AGENT_SCHEMA, group_views

MAX_ROWS = 200


class SQLRejected(ValueError):
    pass


def check_sql(sql: str) -> str:
    """Return the statement if it is a single read-only SELECT over ont_agent.*, else raise."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise SQLRejected(f"could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise SQLRejected("exactly one statement is allowed")
    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union, exp.With)) and not (
            isinstance(stmt, exp.Subquery)):
        raise SQLRejected("only SELECT queries are allowed")
    for node in stmt.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
                             exp.Command, exp.Copy, exp.Pragma)):
            raise SQLRejected("only SELECT queries are allowed")
    ctes = {c.alias_or_name.lower() for c in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise SQLRejected("table functions are not allowed")
        if table.db.lower() == AGENT_SCHEMA:
            continue
        if not table.db and table.name.lower() in ctes:
            continue
        raise SQLRejected(f"table {table.sql()} is outside the `{AGENT_SCHEMA}` schema")
    return sql


class FilterError(ValueError):
    """A filter value that matches nothing in the data."""


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", v.lower())


def _initials(v: str) -> str:
    return "".join(w[0] for w in re.split(r"[^A-Za-z0-9]+", v) if w).lower()


def _match_value(v: str, known: list) -> str | None:
    """The single known code a business spelling refers to, or None (no match, or ambiguous)."""
    strs = [k for k in known if isinstance(k, str)]
    for test in (lambda k: _norm(k) == _norm(v),                       # Medicare Advantage ~ medicare_advantage
                 lambda k: len(_norm(v)) >= 2 and _initials(k) == _norm(v)):  # MA ~ medicare_advantage
        hits = [k for k in strs if test(k)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


class ContextToolkit:
    def __init__(self, client: Client, warehouse: str | Path, as_of: date | None = None):
        self.client = client
        self.warehouse = str(warehouse)
        self.as_of = as_of
        self.semantic = SemanticLayer(client)
        self.sv = client.resolved.view
        self._entities = {v.entity: v.name for v in group_views(client)}

    # ------------------------------------------------------------ plumbing

    def _con(self):
        # Defense in depth under the SQL parser: read-only, no file/network/extension
        # access, and the agent cannot re-enable it.
        return duckdb.connect(self.warehouse, read_only=True,
                              config={"enable_external_access": False, "lock_configuration": True})

    def _as_of(self, as_of: str | None) -> tuple[date, str]:
        """Governed valuation date, and a note when the request was adjusted.

        A date after the latest valuation would silently mix fully-valued and partial
        data, so it is replaced by the governed valuation date, never passed through.
        """
        governed = self.as_of or date.today()
        if not as_of:
            return governed, ""
        try:
            asked = date.fromisoformat(as_of)
        except ValueError:
            return governed, f"(could not read as_of={as_of!r}; used the governed valuation date)"
        if asked > governed:
            return governed, (f"(requested {asked} is after the latest valuation; "
                              f"used the governed valuation date {governed})")
        return asked, ""

    def _resolve_entity(self, name: str) -> str | None:
        key = name.strip().lower().replace(" ", "_")
        index = {}
        for cls, table in self._entities.items():
            c = self.sv.get_class(cls)
            for alias in [cls, table, label(self.sv, c), *c.aliases]:
                index[alias.lower().replace(" ", "_")] = cls
        if key in index:
            return index[key]
        close = difflib.get_close_matches(key, index, n=1, cutoff=0.6)
        return index[close[0]] if close else None

    # ------------------------------------------------------------ tool bodies

    def list_concepts(self) -> str:
        con = self._con()
        try:
            rows = []
            for cls, table in self._entities.items():
                c = self.sv.get_class(cls)
                n = con.execute(f"SELECT count(*) FROM {AGENT_SCHEMA}.{table}").fetchone()[0]
                desc = (c.description or "").strip().split(". ")[0]
                rows.append(f"- {label(self.sv, c)} (`{AGENT_SCHEMA}.{table}`, {n:,} rows): {desc}")
        finally:
            con.close()
        return (f"Entities in {self.client.title}'s context layer "
                f"(units: {', '.join(u.code for u in self.client.units)}):\n" + "\n".join(rows))

    def describe_concept(self, name: str) -> str:
        cls = self._resolve_entity(name)
        if cls is None:
            return f"No concept matches {name!r}. Call list_concepts to see what exists."
        doc = context_document(self.client)
        heading = f"## {label(self.sv, self.sv.get_class(cls))} (`{table_name(cls)}`)"
        start = doc.index(heading)
        end = doc.find("\n## ", start + 1)
        return doc[start:end if end > 0 else None].strip()

    def list_metrics(self) -> str:
        lines = []
        for m in self.semantic.catalog():
            lines.append(f"- {m['name']} ({m['label']}, {m['format']}): {m.get('description', '')} "
                         f"dimensions: {', '.join(m['dimensions'])}")
        return "Governed metrics (use these for KPIs, not ad-hoc SQL):\n" + "\n".join(lines)

    def resolve_filters(self, con, metrics: list[str], filters: dict | None,
                        as_of: date | None = None) -> tuple[dict, list[str]]:
        """Match filter values to the codes the data actually holds.

        Agents write values the way people say them ("MA", "Medicare Advantage") while the
        data holds codes ("medicare_advantage"). An unmatched value used to filter everything
        out and come back as a blank or zero that looked like an answer. Each value now
        resolves to exactly one code (exact, then spelling-insensitive, then initials) or the
        call fails with the valid values listed. Returns (resolved filters, notes).
        """
        out, notes = {}, []
        for dim, value in (filters or {}).items():
            if dim not in self.semantic.dimensions:
                out[dim] = value  # compile() reports unknown dimensions with the known list
                continue
            wanted = value if isinstance(value, (list, tuple, set)) else [value]
            if not any(isinstance(v, str) for v in wanted):
                out[dim] = value
                continue
            known = self.semantic.dimension_values(con, metrics, dim, as_of)
            aliases = {_norm(k): a for k, a in self.semantic.dimensions[dim].aliases.items()}
            resolved = []
            for v in wanted:
                if not isinstance(v, str) or v in known:
                    resolved.append(v)
                    continue
                hit = aliases.get(_norm(v)) if aliases.get(_norm(v)) in known else _match_value(v, known)
                if hit is None:
                    shown = ", ".join(map(str, known[:40])) + (" ..." if len(known) > 40 else "")
                    raise FilterError(f"no {dim} value matches {v!r}. Values in the data: {shown}")
                resolved.append(hit)
                notes.append(f"read {dim}={v!r} as {hit!r}")
            out[dim] = resolved if isinstance(value, (list, tuple, set)) else resolved[0]
        return out, notes

    def compute_metrics(self, metrics: list[str], by: list[str] | None = None,
                        filters: dict | None = None, as_of: str | None = None) -> str:
        valuation, note = self._as_of(as_of)
        con = self._con()
        try:
            filters, read_as = self.resolve_filters(con, metrics, filters, valuation)
            res = self.semantic.query(con, metrics, by or [], filters, valuation)
        except FilterError as exc:
            return f"Filter error: {exc}"
        except MetricError as exc:
            return f"Metric error: {exc}"
        finally:
            con.close()
        fmt = {n: self.semantic.metrics[n].format for n in metrics}
        table = _markdown(res.columns, [[_fmt(v, fmt.get(c)) for c, v in zip(res.columns, r)] for r in res.rows])
        defs = "\n".join(f"- {n}: {d.get('description') or d['label']}"
                         + (f" [override: {d['source']}]" if d["source"] != "pack" else "")
                         for n, d in res.definitions.items() if n in metrics)
        notes = ""
        if read_as:
            notes += "\nFilters: " + "; ".join(read_as) + "."
        if filters and all(not v for r in res.rows for c, v in zip(res.columns, r) if c in metrics):
            notes += ("\nNo data matches these filters, so the result is empty or zero. Do not report it as the "
                      "answer; check the filters.")
        return (f"Valuation date: {res.as_of.isoformat()} {note}".rstrip() + notes
                + f"\n\n{table}\n\nDefinitions used:\n{defs}")

    def loss_triangle(self, metric: str = "reported_incurred_loss_alae", filters: dict | None = None,
                      as_of: str | None = None) -> str:
        valuation, _ = self._as_of(as_of)
        con = self._con()
        try:
            filters, _ = self.resolve_filters(con, [metric], filters, valuation)
            res = self.semantic.triangle(con, metric, where=filters, as_of=valuation)
        except FilterError as exc:
            return f"Filter error: {exc}"
        except MetricError as exc:
            return f"Metric error: {exc}"
        finally:
            con.close()
        rows = [[r[0], *[_fmt(v, "currency") for v in r[1:]]] for r in res.rows]
        return (f"{self.semantic.metrics[metric].label} development by accident year, valued at "
                f"12-month intervals through {res.as_of}:\n\n" + _markdown(res.columns, rows))

    def run_sql(self, sql: str) -> str:
        try:
            check_sql(sql)
        except SQLRejected as exc:
            return f"Rejected: {exc}. Query `{AGENT_SCHEMA}.<entity>` views with a single SELECT."
        con = self._con()
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(MAX_ROWS + 1)
        except duckdb.Error as exc:
            return f"SQL error: {exc}"
        finally:
            con.close()
        more = f"\n(truncated to {MAX_ROWS} rows)" if len(rows) > MAX_ROWS else ""
        return _markdown(cols, [[_fmt(v) for v in r] for r in rows[:MAX_ROWS]]) + more

    def trust_signals(self) -> tuple[list, list]:
        """Conformance findings and drift proposals, computed once per toolkit."""
        if not hasattr(self, "_trust"):
            from .drift import detect
            from .validate import validate
            con = self._con()
            try:
                self._trust = (validate(self.client, con), detect(self.client, con))
            finally:
                con.close()
        return self._trust

    def trace(self, metrics: list[str], by: list[str] | None = None, filters: dict | None = None,
              as_of: str | None = None) -> dict:
        """Layer-by-layer lineage for a governed metric request (see lineage.explain)."""
        from .lineage import explain
        valuation, _ = self._as_of(as_of)
        findings, proposals = self.trust_signals()
        con = self._con()
        try:
            return explain(self.semantic, metrics, by or [], filters or {}, valuation, con, findings, proposals)
        finally:
            con.close()

    def compare_to_peers(self, metric: str, entity: str, peers: str, volume_metric: str | None = None,
                         min_volume: float = 0, threshold: float = 2.0, periods: list[str] | None = None,
                         period_dimension: str = "quarter", filters: dict | None = None,
                         as_of: str | None = None, min_z: float | None = None) -> str:
        """Each `entity` (e.g. provider) against its `peers` group (e.g. peer cohort) on one governed
        metric. The peer value is the metric computed over the whole group (a ratio of sums, not an
        average of rates). Flags members at or above `threshold` x their group; with `periods`, only
        members above it in every listed period (e.g. two consecutive quarters). For a rate that is a
        count over a count, `min_z` also requires the excess to be statistically significant (a
        one-sided binomial z-score against the group rate), so small volumes do not flag by chance."""
        a = self.peer_analysis(metric, entity, peers, volume_metric, min_volume, threshold, periods,
                               period_dimension, filters, as_of, min_z)
        if "error" in a:
            return a["error"]
        windows, hits = a["windows"], [h for h in a["members"] if h["flagged"]]
        fmt = self.semantic.metrics[metric].format
        head = (f"Valuation date: {a['valuation']} {a['note']}".rstrip()
                + f"\n{len(hits)} of {len(a['members'])} {entity} values at or above {threshold:g}x their {peers} on {metric}"
                + (f" in every period of {', '.join(windows)}" if periods else "")
                + (f" (minimum {min_volume:g} {volume_metric} per period)" if volume_metric else "")
                + (f", significant at z >= {min_z:g}" if min_z is not None else "") + ".")
        lines = [head, "", f"| {peers} | {entity} | " + " | ".join(
            f"{w or 'period'}: rate / group / x" for w in windows) + (f" | {volume_metric} |" if volume_metric else " |"),
            "|" + "---|" * (2 + len(windows) + (1 if volume_metric else 0))]
        for h in hits[:25]:
            cells = [f"{_fmt(x['value'], fmt)} / {_fmt(x['group'], fmt)} / {x['value'] / x['group']:.1f}x"
                     + (f" (z {x['z']:.1f})" if x["z"] is not None else "") for x in h["periods"]]
            vol = f" | {', '.join(_fmt(x['volume'], 'count') for x in h['periods'])} |" if volume_metric else " |"
            lines.append(f"| {h['peers']} | {h['entity']} | " + " | ".join(cells) + vol)
        defs = self.semantic.metrics[metric]
        lines += ["", f"Definition used: {metric}: {defs.description or defs.label}"]
        return "\n".join(lines)

    def peer_analysis(self, metric: str, entity: str, peers: str, volume_metric: str | None = None,
                      min_volume: float = 0, threshold: float = 2.0, periods: list[str] | None = None,
                      period_dimension: str = "quarter", filters: dict | None = None,
                      as_of: str | None = None, min_z: float | None = None) -> dict:
        """compare_to_peers as data: every member present in all periods, with its value, its group's
        value, volume and z-score per period, and whether it is flagged. {"error": ...} on bad input."""
        valuation, note = self._as_of(as_of)
        con = self._con()
        try:
            base = dict(filters or {})
            m = self.semantic.metrics[metric]
            parts = list(m.ratio) if m.ratio and not m.scale else []
            if min_z is not None and not parts:
                return {"error": f"min_z needs a rate defined as a ratio of two counts; {metric} is not one"}
            metrics = [metric] + ([volume_metric] if volume_metric else []) + [x for x in parts if x != volume_metric]
            windows = [None] if not periods else list(periods)
            per_window = []
            for w in windows:
                where = {**base, **({period_dimension: w} if w else {})}
                where, read_as = self.resolve_filters(con, metrics, where, valuation)
                members = self.semantic.query(con, metrics, [peers, entity], where, valuation).records()
                groups = {r[peers]: r[metric] for r in self.semantic.query(con, [metric], [peers], where, valuation).records()}
                rows = {}
                for r in members:
                    group, value = groups.get(r[peers]), r[metric]
                    vol = r.get(volume_metric) if volume_metric else None
                    if not group or value is None or (volume_metric and (vol or 0) < min_volume):
                        continue
                    z = None
                    if parts:
                        k, n, p = float(r[parts[0]] or 0), float(r[parts[1]] or 0), float(group)
                        z = (k - n * p) / ((n * p * (1 - p)) ** 0.5) if n and 0 < p < 1 else None
                    rows[(r[peers], r[entity])] = (float(value), float(group), vol, z)
                per_window.append(rows)
        except FilterError as exc:
            return {"error": f"Filter error: {exc}"}
        except MetricError as exc:
            return {"error": f"Metric error: {exc}"}
        finally:
            con.close()
        keys = set.intersection(*(set(w) for w in per_window)) if per_window else set()
        members = []
        for k in keys:
            ratios = [w[k][0] / w[k][1] for w in per_window]
            zs = [w[k][3] for w in per_window]
            significant = min_z is None or all(z is not None and z >= min_z for z in zs)
            members.append({"peers": k[0], "entity": k[1], "ratio": min(ratios),
                            "flagged": all(x >= threshold for x in ratios) and significant,
                            "periods": [{"value": w[k][0], "group": w[k][1], "volume": w[k][2], "z": w[k][3]}
                                        for w in per_window]})
        members.sort(key=lambda h: -h["ratio"])
        return {"valuation": valuation.isoformat(), "note": note, "windows": windows, "members": members}

    def data_quality(self) -> str:
        from .validate import validate
        con = self._con()
        try:
            findings = validate(self.client, con)
        finally:
            con.close()
        if not findings:
            return "All conformance checks pass."
        return "Conformance findings:\n" + "\n".join(
            f"- {f.unit} {f.entity}.{f.slot} [{f.check}] {f.violations:,}/{f.rows:,}: {f.detail}; e.g. {f.sample}"
            for f in findings)

    def pending_changes(self) -> str:
        from .drift import detect, verify
        con = self._con()
        try:
            proposals = [verify(self.client, p) for p in detect(self.client, con)]
        finally:
            con.close()
        items = [p for p in proposals if p.kind != "coverage_gap"]
        if not items:
            return "No pending ontology change proposals."
        return "Ontology change proposals awaiting steward review:\n" + "\n".join(
            f"- {p.id} [{p.status}] {p.title} -> {p.layer}" for p in items)

    # ------------------------------------------------------------ agent wiring

    def system_prompt(self) -> str:
        units = "; ".join(f"{u.code} = {u.name} ({u.source_system})" for u in self.client.units)
        return f"""You are the enterprise context agent for {self.client.title}.
You answer business questions about underwriting, policies, premium, claims, reserves and
reinsurance using the governed context layer, not guesswork.

Units: {units}. Every record carries `source_system` (its unit); ids are prefixed with the
unit code unless the entity is group reference data.

How to work:
1. For any KPI (premium, loss ratio, severity, hit ratio, retention, IBNR...) call
   compute_metrics. Leave as_of empty unless the user names a date. Never re-derive a governed metric in SQL. Report the valuation date and
   name the definition you used.
2. If you are unsure which entity or column holds something, call list_concepts or
   describe_concept. Use the client's vocabulary in answers.
3. Use run_sql only for record-level detail the metrics do not cover. It accepts one SELECT
   over `{AGENT_SCHEMA}.*` views. Personal data is redacted by design; never try to recover it.
4. For reserving questions use loss_triangle; for "can I trust this data" use data_quality;
   for "what is changing in our data" use pending_changes.
5. If a dimension is not valid for a metric (e.g. IBNR is booked by unit x line x accident
   year, not by program), say so and offer the nearest valid cut. Do not allocate it yourself.
6. Business groupings like "casualty" or "property" are the `line_group` dimension. When a
   question is ambiguous but a reasonable default exists, pick it, state the assumption in
   one line, and answer. Ask a clarifying question only if no sensible default exists.
Be concise: lead with the answer, then the numbers, then caveats."""

    def tools(self) -> list:
        kit = self

        async def list_concepts(ctx: RunContext) -> str:
            """List every business entity in the context layer with row counts."""
            return kit.list_concepts()

        async def describe_concept(ctx: RunContext, name: str) -> str:
            """Describe an entity: meaning, columns, allowed values, relationships. Accepts business names."""
            return kit.describe_concept(name)

        async def list_metrics(ctx: RunContext) -> str:
            """List governed metrics with definitions and the dimensions each supports."""
            return kit.list_metrics()

        async def compute_metrics(ctx: RunContext, metrics: list[str], by: list[str] | None = None,
                                  filters: dict[str, str | int | list[str]] | None = None,
                                  as_of: str | None = None) -> str:
            """Compute governed metrics, optionally grouped `by` dimensions and filtered
            ({dimension: value or [values]}). Filter values may be business names or codes; the
            reply says how each was read, and an unknown value returns the valid values. Leave
            `as_of` empty to use the governed valuation date; set it (YYYY-MM-DD) only when the
            user asks for a specific date."""
            return kit.compute_metrics(metrics, by, filters, as_of)

        async def loss_triangle(ctx: RunContext, metric: str = "reported_incurred_loss_alae",
                                filters: dict[str, str | int | list[str]] | None = None) -> str:
            """Accident-year development triangle for a claims metric (12-month valuations)."""
            return kit.loss_triangle(metric, filters)

        async def run_sql(ctx: RunContext, sql: str) -> str:
            """Run one read-only SELECT over ont_agent.* views for record-level detail."""
            return kit.run_sql(sql)

        async def compare_to_peers(ctx: RunContext, metric: str, entity: str, peers: str,
                                   volume_metric: str | None = None, min_volume: float = 0,
                                   threshold: float = 2.0, periods: list[str] | None = None,
                                   filters: dict[str, str | int | list[str]] | None = None,
                                   min_z: float | None = None) -> str:
            """Compare each member of a group with its peers on a governed metric, e.g. each provider
            (entity="provider") against its peer cohort (peers="peer_cohort") on modifier_25_rate. Flags
            members at or above `threshold` x their group; `periods` (e.g. ["2025-Q4","2026-Q1"]) requires
            it in every period; `volume_metric`/`min_volume` skip members with too little activity; `min_z`
            (e.g. 2) keeps only statistically significant excesses for count-over-count rates."""
            return kit.compare_to_peers(metric, entity, peers, volume_metric, min_volume, threshold, periods,
                                        filters=filters, min_z=min_z)

        async def data_quality(ctx: RunContext) -> str:
            """Conformance of each unit's data to the ontology (required, enums, references...)."""
            return kit.data_quality()

        async def pending_changes(ctx: RunContext) -> str:
            """Ontology change proposals detected from source-data drift, awaiting review."""
            return kit.pending_changes()

        return [list_concepts, describe_concept, list_metrics, compute_metrics, compare_to_peers, loss_triangle,
                run_sql, data_quality, pending_changes]

    def registry(self) -> dict:
        return {fn.__name__: (fn, (fn.__doc__ or "").strip().split("\n")[0]) for fn in self.tools()}


def _fmt(v, kind: str | None = None) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float) or hasattr(v, "as_integer_ratio") and not isinstance(v, int):
        v = float(v)
        if kind == "percent":
            return f"{v:.1%}"
        if kind == "count":
            return f"{v:,.0f}"
        return f"{v:,.0f}" if abs(v) >= 1000 or kind == "currency" else f"{v:,.4g}"
    if isinstance(v, int):
        return f"{v:,}" if kind in ("count", "currency", None) and abs(v) >= 10000 else str(v)
    if isinstance(v, (list, tuple)):
        return ", ".join(map(str, v))
    return str(v)


def _markdown(cols: list[str], rows: list[list]) -> str:
    if not rows:
        return "(no rows)"
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols)
    return head + "\n" + "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
