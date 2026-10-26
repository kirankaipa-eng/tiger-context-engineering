"""Lineage: how a governed answer was built, layer by layer.

`explain()` returns, for one metric request, what each layer of the context layer
contributed. Everything is read from the same machinery that compiles the query
(the semantic layer's own join resolution, the resolved ontology, the unit
mappings), so the trace cannot drift from what actually ran.

    ontology   entities (client label, canonical IRI, is-a chain), relationships
               traversed, business groupings used (e.g. casualty -> coverage lines)
    semantic   metric formula tree, definitions and their source (pack / client
               override), dimensions, valuation date, grain rules
    context    per entity: which units and source systems feed it, source tables,
               transformation notes, rows contributed, identity and PII policy
    trust      data-quality findings and pending drift proposals on those entities
    sql        the compiled statement that produced the numbers
"""

from __future__ import annotations

import re
from datetime import date

from .schema_utils import annotation, is_global, ordered_slots, table_name
from .semantic import Metric, SemanticLayer, _Scope
from .sqlgen import GROUP_SCHEMA

_ONT_TABLE = re.compile(r"\{ont\}\.([a-z_]+)")


def _class_by_table(sv) -> dict[str, str]:
    return {table_name(c): c for c in sv.all_classes()}


def _entity_card(sl: SemanticLayer, cls: str) -> dict:
    sv = sl.sv
    c = sv.get_class(cls)
    chain = [a for a in sv.class_ancestors(cls) if a != cls]
    return {
        "name": cls,
        "label": c.title or cls,
        "iri": sv.get_uri(cls, expand=True),
        "curie": sv.get_uri(cls, expand=False),
        "is_a": [{"name": a, "curie": sv.get_uri(a, expand=False)} for a in chain],
        "description": (c.description or "").strip(),
        "aliases": [a for a in c.aliases if a != (c.title or cls)],
        "view": f"{GROUP_SCHEMA}.{table_name(cls)}",
    }


def _metric_tree(sl: SemanticLayer, name: str) -> dict:
    m: Metric = sl.metrics[name]
    node = {"name": name, "label": m.label, "description": m.description, "format": m.format,
            "source": m.source}
    if m.ratio:
        node.update(kind="ratio", formula=f"{m.ratio[0]} ÷ {m.ratio[1]}" + (f" × {m.scale:g}" if m.scale else ""),
                    children=[_metric_tree(sl, p) for p in m.ratio])
    elif m.sum_of:
        node.update(kind="sum", formula=" + ".join(m.sum_of), children=[_metric_tree(sl, p) for p in m.sum_of])
    else:
        node.update(kind="base", entity=m.entity, formula=" ".join(m.measure.split()),
                    as_of_field=m.as_of, filter=" ".join(m.filter.split()) if m.filter else None,
                    derived=m.entity in sl.derived)
    return node


def _hops(sl: SemanticLayer, entity: str, path: str) -> list[dict]:
    """Relationship hops for a dotted path, with slot IRIs."""
    hops, current = [], entity
    for hop in path.split("."):
        cols = sl._columns(current)
        target = cols.get(hop)
        if target is None:
            break
        slot = sl.sv.get_slot(hop) if hop in sl.sv.all_slots() else None
        hops.append({"from": current, "slot": hop, "to": target,
                     "curie": sl.sv.get_uri(slot, expand=False) if slot is not None else None})
        current = target
    return hops


def _sources(client, cls: str, con=None) -> list[dict]:
    """Which units feed an ontology entity, from which source relations, with what transformation."""
    out = []
    for unit in client.units:
        blocks = (unit.mappings.get("classes") or {}).get(cls)
        if not blocks:
            continue
        blocks = blocks if isinstance(blocks, list) else [blocks]
        schema = unit.mappings.get("source_schema", "main")
        tables, notes, kinds, mapped = [], [], [], set()
        for b in blocks:
            if "table" in b:
                tables.append(f"{schema}.{b['table']}")
                kinds.append("direct")
            if "query" in b:
                tables += sorted({f"{schema}.{t}" for t in re.findall(rf"{schema}\.([A-Za-z_][A-Za-z0-9_]*)",
                                                                      b["query"], re.I)})
                q = " ".join(b["query"].upper().split())
                kinds.append("snapshot deltas" if "LAG(" in q else
                             "latest snapshot" if "ROW_NUMBER()" in q and "RN = 1" in q else
                             "aggregated" if "GROUP BY" in q or "DISTINCT" in q else
                             "sequenced" if " OVER " in q else "query")
            if b.get("note"):
                notes.append(" ".join(b["note"].split()))
            mapped |= set((b.get("slots") or {}).keys())
        rows = None
        if con is not None:
            try:
                rows = con.execute(f"SELECT count(*) FROM {unit.schema_name}.{table_name(cls)}").fetchone()[0]
            except Exception:
                rows = None
        total = len(ordered_slots(unit.resolved.view, cls))
        out.append({"unit": unit.code, "unit_name": unit.name, "source_system": unit.source_system,
                    "tables": sorted(set(tables)), "transform": sorted(set(kinds)), "notes": notes,
                    "slots_mapped": len(mapped), "slots_total": total, "rows": rows,
                    "unit_view": f"{unit.schema_name}.{table_name(cls)}"})
    return out


def explain(sl: SemanticLayer, metrics: list[str], by: list[str] | None = None,
            where: dict | None = None, as_of: date | None = None, con=None,
            findings: list | None = None, proposals: list | None = None) -> dict:
    by, where = list(by or []), dict(where or {})
    as_of = as_of or date.today()
    client, sv = sl.client, sl.sv
    sql = sl.compile(metrics, by, where, as_of)
    by_table = _class_by_table(sv)

    leaves: list[str] = []
    for n in metrics:
        leaves += [x for x in sl._leaves(n) if x not in leaves]

    # --- ontology: entities and relationships, via the compiler's own join resolution
    entities: dict[str, dict] = {}
    relationships: list[dict] = []
    derived: list[dict] = []
    seen_hops: set[tuple] = set()

    def add_entity(cls: str):
        if cls in sv.all_classes() and cls not in entities:
            entities[cls] = _entity_card(sl, cls)

    for leaf in leaves:
        m = sl.metrics[leaf]
        entity = m.entity
        paths = []
        if entity in sl.derived:
            spec = sl.derived[entity]
            base = sorted({by_table[t] for t in _ONT_TABLE.findall(spec["sql"]) if t in by_table})
            if not any(d["name"] == entity for d in derived):
                derived.append({"name": entity, "description": " ".join(spec.get("description", "").split()),
                                "built_from": base})
            for b in base:
                add_entity(b)
        else:
            add_entity(entity)
        for text in filter(None, [m.measure, m.filter]):
            paths += [p for p in re.findall(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", text) if "." in p]
        for d in [*by, *where]:
            spec = sl.dimensions[d].paths.get(entity)
            if spec:
                paths += (re.findall(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", spec) if "{" in spec else [spec])
        for p in paths:
            if "." not in p:
                continue
            # Full path, including a final reference hop the compiler reads as a key
            # (e.g. policy.operating_unit) without needing a join.
            for h in _hops(sl, entity, p):
                key = (h["from"], h["slot"], h["to"])
                if key not in seen_hops:
                    seen_hops.add(key)
                    relationships.append(h)
                add_entity(h["from"])
                add_entity(h["to"])

    groupings = []
    for d in [*by, *where]:
        dim = sl.dimensions[d]
        if dim.enum_group:
            enum = sv.get_enum(dim.enum_group["enum"])
            groups: dict[str, list[str]] = {}
            for v, pv in enum.permissible_values.items():
                g = annotation(pv, dim.enum_group["annotation"])
                if g:
                    groups.setdefault(g, []).append(v)
            selected = where.get(d)
            selected = selected if isinstance(selected, list) else [selected] if selected else list(groups)
            groupings.append({"dimension": d, "label": dim.label, "enum": dim.enum_group["enum"],
                              "groups": {g: groups.get(g, []) for g in selected if g in groups}})

    # --- semantic
    dims = []
    involved = {sl.metrics[x].entity for x in leaves}
    for d in [*by, *where]:
        dim = sl.dimensions[d]
        dims.append({"name": d, "label": dim.label, "role": "group by" if d in by else "filter",
                     "value": where.get(d), "paths": {e: dim.paths[e] for e in involved if e in dim.paths}})
    grain = []
    for leaf in leaves:
        m = sl.metrics[leaf]
        valid = sorted(n for n, dd in sl.dimensions.items() if m.entity in dd.paths)
        if len(valid) < len(sl.dimensions):
            grain.append({"metric": leaf, "entity": m.entity, "valid_dimensions": valid})

    # --- context
    context = []
    redact = set(client.redact)
    for cls in entities:
        srcs = _sources(client, cls, con)
        if not srcs:
            continue
        slots = ordered_slots(sv, cls)
        context.append({
            "entity": cls, "label": entities[cls]["label"], "units": srcs,
            "identity": "global (shared across units)" if is_global(sv, cls) else "unit-qualified (UNIT:id)",
            "redacted_for_agents": [s.name for s in slots if annotation(s, "sensitivity") in redact],
            "hidden_from_agents": [s.name for s in slots
                                   if annotation(s, "agent_visible", True) in (False, "false", "False")],
        })

    names = set(entities)
    trust = {
        "findings": [f.as_dict() for f in (findings or []) if f.entity in names],
        "proposals": [{"id": p.id, "kind": p.kind, "title": p.title, "status": p.status, "layer": p.layer}
                      for p in (proposals or []) if p.target in names or p.kind == "new_enum_value"
                      and any(ordered_slot_range(sv, e, p.target) for e in names)],
    }

    return {
        "request": {"metrics": metrics, "by": by, "filters": where, "as_of": as_of.isoformat()},
        "client": {"name": client.name, "title": client.title, "line": client.line,
                   "line_version": client.line_version, "client_version": str(client.config.get("version", ""))},
        "ontology": {"entities": list(entities.values()), "relationships": relationships,
                     "groupings": groupings, "derived": derived},
        "semantic": {"metrics": [_metric_tree(sl, n) for n in metrics], "dimensions": dims,
                     "valuation_date": as_of.isoformat(), "grain": grain,
                     "catalog_version": sl.version},
        "context": context,
        "trust": trust,
        "sql": sql,
    }


def ordered_slot_range(sv, cls: str, enum: str) -> bool:
    """Does any slot of `cls` use `enum` as its range?"""
    return any(s.range == enum for s in sv.class_induced_slots(cls))
