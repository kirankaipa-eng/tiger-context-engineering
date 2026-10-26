"""Compile unit mappings into governed SQL views.

Three layers of views are produced for a client:

  u_<unit>.<entity>   one per unit: the unit's source tables expressed in ontology
                      terms, typed, identity-qualified, with lineage
  ont.<entity>        group layer: UNION ALL of every unit that maps the entity,
                      restricted to group-schema columns (unit extensions stay
                      in the unit layer)
  ont_agent.<entity>  what agents see: `ont` with PII/PHI redacted per the client's
                      agent policy and agent_visible:false columns removed

Mappings are authored in DuckDB SQL. Each view is assembled per target dialect
from individually transpiled fragments (sqlglot), so one mapping serves DuckDB,
Snowflake, Databricks and Postgres. Where no faithful translation exists, a
mapping entry may carry a native override keyed by dialect name, e.g.

    requested_lines:
      expr: "list_transform(string_split(src.LOBS, ','), x -> upper(x))"
      postgres: "ARRAY(SELECT upper(x) FROM unnest(string_to_array(src.\"LOBS\", ',')) AS x)"

`portability_issues()` flags any function a target dialect does not provide, so a
view can never ship silently broken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import sqlglot
from linkml_runtime import SchemaView

from .client import Client, Unit
from .overlay import OverlayError
from .schema_utils import annotation, is_global, ordered_slots, slot_kind, sql_type, table_name

DIALECTS = ("duckdb", "snowflake", "databricks", "postgres")
GROUP_SCHEMA = "ont"
AGENT_SCHEMA = "ont_agent"
REDACTED = "'[REDACTED]'"


# Functions sqlglot passes through untranslated that the target does not provide.
MISSING_FUNCTIONS = {
    "postgres": ("TRANSFORM", "SPLIT", "REGEXP_EXTRACT", "LIST_TRANSFORM", "STRING_SPLIT", "LAST_DAY"),
    "snowflake": ("LIST_TRANSFORM", "STRING_SPLIT", "REGEXP_EXTRACT"),
    "databricks": ("LIST_TRANSFORM", "STRING_SPLIT"),
    "duckdb": (),
}


@dataclass
class View:
    schema: str
    name: str
    entity: str
    render: Callable[[str], str] = field(repr=False)  # dialect -> SELECT

    @property
    def select(self) -> str:
        return self.render("duckdb")

    def ddl(self, dialect: str = "duckdb") -> str:
        return f"CREATE OR REPLACE VIEW {self.schema}.{self.name} AS\n{self.render(dialect)}"


def tx(sql: str, dialect: str) -> str:
    """Transpile one DuckDB fragment (expression or query) to the target dialect."""
    if dialect == "duckdb":
        return sql
    return sqlglot.parse_one(sql, read="duckdb").sql(dialect=dialect)


def portability_issues(client: "Client", dialect: str) -> list[str]:
    issues = []
    missing = MISSING_FUNCTIONS.get(dialect, ())
    for v in all_views(client):
        sql = v.ddl(dialect)
        for fn in missing:
            if re.search(rf"\b{fn}\s*\(", sql, re.I):
                issues.append(f"{v.schema}.{v.name}: {fn}() is not available in {dialect}; "
                              f"add a `{dialect}:` override to the mapping")
    return issues


def _q(ident: str) -> str:
    """Quote only when needed, so each platform's native identifier case-folding applies."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", ident):
        return ident
    return '"' + ident.replace('"', '""') + '"'


def _source_expr(spec, slot_name: str, dialect: str = "duckdb") -> str:
    """The source expression for a slot, already in the target dialect."""
    if spec is None:
        raise OverlayError(f"empty mapping for {slot_name}")
    if isinstance(spec, str):
        return f"src.{_q(spec)}"
    if dialect in spec:
        return f"({spec[dialect]})"
    if "column" in spec:
        return f"src.{_q(spec['column'])}"
    if "expr" in spec:
        return f"({tx(spec['expr'], dialect)})"
    if "value" in spec:
        v = spec["value"]
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    raise OverlayError(f"mapping for {slot_name} needs column, expr or value")


def _source_relation(block: dict, source_schema: str, dialect: str = "duckdb") -> str:
    override = block.get(f"query_{dialect}")
    if override:
        return f"(\n{override.strip()}\n) AS src"
    if "query" in block:
        return f"(\n{tx(block['query'].strip(), dialect)}\n) AS src"
    if "table" in block:
        return f"{source_schema}.{_q(block['table'])} AS src"
    raise OverlayError("mapping block needs `table` or `query`")


def unit_views(unit: Unit) -> list[View]:
    """One view per ontology class the unit maps."""
    sv = unit.resolved.view
    m = unit.mappings
    source_schema = m.get("source_schema", "main")
    views = []
    for cls_name, blocks in (m.get("classes") or {}).items():
        if cls_name not in sv.all_classes():
            raise OverlayError(f"{unit.code}: mapping references unknown class {cls_name!r}")
        blocks = blocks if isinstance(blocks, list) else [blocks]
        slots = ordered_slots(sv, cls_name)
        known = {sl.name for sl in slots}
        for block in blocks:
            mapped = block.get("slots") or {}
            unknown = set(mapped) - known
            if unknown:
                raise OverlayError(f"{unit.code}.{cls_name}: unknown slots {sorted(unknown)}")
            if "id" not in mapped:
                raise OverlayError(f"{unit.code}.{cls_name}: `id` must be mapped")

        def render(dialect, sv=sv, cls_name=cls_name, slots=slots, blocks=blocks):
            selects = []
            for block in blocks:
                mapped = block.get("slots") or {}
                cols = []
                for slot in slots:
                    typ = tx(f"CAST(NULL AS {sql_type(sv, slot)})", dialect)[len("CAST(NULL AS "):-1]
                    if slot.name == "source_system":
                        expr = f"'{unit.code}'"
                    elif slot.name in mapped:
                        expr = f"CAST({_source_expr(mapped[slot.name], slot.name, dialect)} AS {typ})"
                        expr = _qualify(sv, cls_name, slot, expr, unit.code)
                    else:
                        expr = f"CAST(NULL AS {typ})"
                    cols.append(f"  {expr} AS {slot.name}")
                where = f"\nWHERE {tx(block['where'], dialect)}" if block.get("where") else ""
                selects.append("SELECT\n" + ",\n".join(cols)
                               + f"\nFROM {_source_relation(block, source_schema, dialect)}{where}")
            return "\nUNION ALL\n".join(selects)

        views.append(View(unit.schema_name, table_name(cls_name), cls_name, render))
    return views


def _qualify(sv: SchemaView, cls: str, slot, expr: str, unit_code: str) -> str:
    """Prefix unit-scoped identifiers so records from different units never collide."""
    if slot.name == "id":
        return expr if is_global(sv, cls) else f"'{unit_code}:' || {expr}"
    if slot_kind(sv, slot) == "class" and not slot.multivalued:
        return expr if is_global(sv, slot.range) else f"'{unit_code}:' || {expr}"
    return expr


def group_views(client: Client) -> list[View]:
    sv = client.resolved.view
    by_entity: dict[str, list[Unit]] = {}
    for unit in client.units:
        for cls_name in unit.mappings.get("classes") or {}:
            by_entity.setdefault(cls_name, []).append(unit)
    views = []
    for cls_name, units in by_entity.items():
        cols = ", ".join(s.name for s in ordered_slots(sv, cls_name))
        select = "\nUNION ALL\n".join(
            f"SELECT {cols} FROM {u.schema_name}.{table_name(cls_name)}" for u in units)
        views.append(View(GROUP_SCHEMA, table_name(cls_name), cls_name, lambda d, q=select: q))
    return views


def agent_views(client: Client) -> list[View]:
    sv = client.resolved.view
    redact = set(client.redact)
    views = []
    for gv in group_views(client):
        cols = []
        for slot in ordered_slots(sv, gv.entity):
            if annotation(slot, "agent_visible", True) in (False, "false", "False"):
                continue
            if annotation(slot, "sensitivity") in redact:
                cols.append(f"  {_redaction(sv, slot)} AS {slot.name}")
            else:
                cols.append(f"  {slot.name}")
        select = "SELECT\n" + ",\n".join(cols) + f"\nFROM {GROUP_SCHEMA}.{gv.name}"
        views.append(View(AGENT_SCHEMA, gv.name, gv.entity, lambda d, q=select: tx(q, d)))
    return views


def _redaction(sv: SchemaView, slot) -> str:
    typ = sql_type(sv, slot)
    if typ == "DATE":
        # HIPAA safe-harbor style: keep the year only.
        return f"CAST(date_trunc('year', {slot.name}) AS DATE)"
    if typ == "VARCHAR":
        return f"CASE WHEN {slot.name} IS NULL THEN NULL ELSE {REDACTED} END"
    return f"CAST(NULL AS {typ})"


def all_views(client: Client) -> list[View]:
    views = []
    for unit in client.units:
        views += unit_views(unit)
    return views + group_views(client) + agent_views(client)


def schemas(client: Client) -> list[str]:
    return [u.schema_name for u in client.units] + [GROUP_SCHEMA, AGENT_SCHEMA]


def compile_ddl(client: Client, dialect: str = "duckdb") -> str:
    if dialect not in DIALECTS:
        raise ValueError(f"dialect must be one of {DIALECTS}")
    out = [f"-- {client.title}: ontology views ({dialect})",
           "-- Generated by insurance-context. Do not edit; edit mappings.yaml and rebuild.", ""]
    for s in schemas(client):
        out.append(f"CREATE SCHEMA IF NOT EXISTS {s};")
    for issue in portability_issues(client, dialect):
        out.append(f"-- PORTABILITY WARNING: {issue}")
    for v in all_views(client):
        out += ["", f"-- {v.entity}", v.ddl(dialect) + ";"]
    return "\n".join(out) + "\n"


def install(client: Client, con) -> list[View]:
    """Create all views on a live DuckDB connection."""
    for s in schemas(client):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {s}")
    views = all_views(client)
    for v in views:
        try:
            con.execute(v.ddl("duckdb"))
        except Exception as exc:  # surface which mapping broke
            raise OverlayError(f"{v.schema}.{v.name} ({v.entity}): {exc}") from exc
    return views
