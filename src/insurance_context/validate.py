"""Data conformance: check mapped client data against the ontology's own constraints.

Every check is generated from the resolved schema (nothing hand-written) and runs
as SQL inside the warehouse against the unit views, so it scales to production
volumes and reports per unit, the level at which someone can fix the source.

Checks: identifier uniqueness, required slots, enum domains (incl. client
restrictions), regex patterns, numeric bounds, and referential integrity of
every relationship.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .client import Client
from .schema_utils import ordered_slots, slot_kind, sql_type, table_name
from .sqlgen import GROUP_SCHEMA, unit_views


@dataclass
class Finding:
    unit: str
    entity: str
    slot: str
    check: str
    violations: int
    rows: int
    detail: str
    sample: list

    @property
    def rate(self) -> float:
        return self.violations / self.rows if self.rows else 0.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["rate"] = round(self.rate, 6)
        return d


def _lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def checks_for(client: Client, unit) -> list[tuple[str, str, str, str, str]]:
    """(entity, slot, check, detail, violation predicate) for one unit."""
    sv = unit.resolved.view
    out = []
    for view in unit_views(unit):
        cls = view.entity
        for slot in ordered_slots(sv, cls):
            col = slot.name
            if col == "source_system":
                continue
            if slot.required or col == "id":
                out.append((cls, col, "required", "value is required", f"{col} IS NULL"))
            kind = slot_kind(sv, slot)
            if kind == "enum":
                values = list(sv.get_enum(slot.range).permissible_values)
                domain = ", ".join(_lit(v) for v in values)
                if slot.multivalued:
                    pred = (f"{col} IS NOT NULL AND EXISTS (SELECT 1 FROM unnest({col}) AS u(v) "
                            f"WHERE v IS NULL OR v NOT IN ({domain}))")
                else:
                    pred = f"{col} IS NOT NULL AND {col} NOT IN ({domain})"
                out.append((cls, col, "enum", f"must be one of {slot.range}", pred))
            if slot.pattern and not slot.multivalued and sql_type(sv, slot) == "VARCHAR":
                out.append((cls, col, "pattern", f"must match {slot.pattern}",
                            f"{col} IS NOT NULL AND NOT regexp_matches({col}, {_lit(slot.pattern)})"))
            if slot.minimum_value is not None and not slot.multivalued:
                out.append((cls, col, "minimum", f">= {slot.minimum_value}",
                            f"{col} < {slot.minimum_value}"))
            if slot.maximum_value is not None and not slot.multivalued:
                out.append((cls, col, "maximum", f"<= {slot.maximum_value}",
                            f"{col} > {slot.maximum_value}"))
            if kind == "class" and not slot.multivalued and col != "id":
                target = table_name(slot.range)
                out.append((cls, col, "reference", f"must reference an existing {slot.range}",
                            f"{col} IS NOT NULL AND {col} NOT IN "
                            f"(SELECT id FROM {GROUP_SCHEMA}.{target} WHERE id IS NOT NULL)"))
    return out


def mapped_slots(unit, cls: str) -> set[str]:
    blocks = (unit.mappings.get("classes") or {}).get(cls) or []
    blocks = blocks if isinstance(blocks, list) else [blocks]
    return {s for b in blocks for s in (b.get("slots") or {})}


def validate(client: Client, con, sample_size: int = 3) -> list[Finding]:
    """Run all checks; return only failing ones."""
    findings = []
    group_tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", [GROUP_SCHEMA]).fetchall()}
    for unit in client.units:
        totals: dict[str, int] = {}
        for cls, col, check, detail, pred in checks_for(client, unit):
            view = f"{unit.schema_name}.{table_name(cls)}"
            if check == "reference" and pred.split(f"{GROUP_SCHEMA}.")[1].split()[0] not in group_tables:
                if col in mapped_slots(unit, cls):
                    findings.append(Finding(unit.code, cls, col, "reference", 0, 0,
                                            "slot is mapped but its target entity is not mapped by any unit", []))
                continue
            if cls not in totals:
                totals[cls] = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
            n = con.execute(f"SELECT count(*) FROM {view} WHERE {pred}").fetchone()[0]
            if n:
                sample = con.execute(
                    f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM {view} WHERE {pred} LIMIT {sample_size}"
                ).fetchall() if check != "required" else con.execute(
                    f"SELECT id FROM {view} WHERE {pred} LIMIT {sample_size}").fetchall()
                findings.append(Finding(unit.code, cls, col, check, n, totals[cls], detail,
                                        [r[0] for r in sample]))
        for cls in totals:
            view = f"{unit.schema_name}.{table_name(cls)}"
            dupes = con.execute(
                f"SELECT count(*) FROM (SELECT id FROM {view} GROUP BY id HAVING count(*) > 1)").fetchone()[0]
            if dupes:
                sample = con.execute(
                    f"SELECT id FROM {view} GROUP BY id HAVING count(*) > 1 LIMIT {sample_size}").fetchall()
                findings.append(Finding(unit.code, cls, "id", "unique", dupes, totals[cls],
                                        "identifier must be unique", [r[0] for r in sample]))
    return findings
