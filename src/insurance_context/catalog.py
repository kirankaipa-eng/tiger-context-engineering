"""Catalog: the whole resolved ontology as one browsable model.

`ontology_model()` returns everything an ontology explorer needs, read from the same
resolved schemas, mappings and metric definitions the engine runs on:

  classes        every entity: client label, canonical IRI, layer (core / line pack /
                 client / unit), is-a chain, attributes (type, required, sensitivity,
                 layer), identity scope, which units feed it and from which source
                 tables, which governed metrics use it, and what the client customized
  relationships  class-ranged attributes as edges
  enums          value sets, including annotations such as line_group
  metrics        governed metric catalogue
  proposals      pending drift proposals, if supplied

"Customized" is computed by diffing the client's resolved schema against the base
line pack, so it reflects what the overlay actually changed.
"""

from __future__ import annotations

import re

from .client import Client
from .lineage import _ONT_TABLE, _class_by_table, _sources
from .overlay import resolve
from .schema_utils import annotation, is_global, slot_kind, table_name
from .semantic import SemanticLayer


def _layer_of(curie: str, client: Client) -> str:
    prefix = curie.split(":", 1)[0]
    if prefix == "ins":
        return "core"
    if prefix == client.prefix:
        return "client"
    if prefix.startswith(f"{client.prefix}_"):
        return f"unit:{prefix[len(client.prefix) + 1:].upper()}"
    return "line"


def _group(sv, cls: str) -> str:
    """Top-level family for navigation."""
    chain = [cls, *[a for a in sv.class_ancestors(cls) if a != cls]]
    families = {"Party": "Parties", "Coverage": "Policy & coverage", "Product": "Policy & coverage",
                "Claim": "Claims", "ClaimLine": "Claims", "Decision": "Decisions"}
    for a in chain:
        if a in families:
            return families[a]
    name = cls.lower()
    if "claim" in name or "catastrophe" in name:
        return "Claims"
    if any(k in name for k in ("policy", "coverage", "transaction", "location", "premium", "enrollment")):
        return "Policy & coverage"
    if any(k in name for k in ("submission", "quote", "loss", "program")):
        return "Underwriting"
    if any(k in name for k in ("treaty", "cession", "reserve", "accumulator")):
        return "Reinsurance & reserving"
    return "Other"


def _metric_usage(sl: SemanticLayer) -> dict[str, list[str]]:
    """class -> governed metrics that read it (directly, via a derived entity, or via a join)."""
    by_table = _class_by_table(sl.sv)
    usage: dict[str, set[str]] = {}
    for name, m in sl.metrics.items():
        if not m.is_base:
            continue
        classes = set()
        if m.entity in sl.derived:
            classes |= {by_table[t] for t in _ONT_TABLE.findall(sl.derived[m.entity]["sql"]) if t in by_table}
        else:
            classes.add(m.entity)
            for text in filter(None, [m.measure, m.filter]):
                for path in re.findall(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", text):
                    cur = m.entity
                    for hop in path.split(".")[:-1]:
                        cur = sl._columns(cur).get(hop) or cur
                        classes.add(cur)
        for c in classes:
            usage.setdefault(c, set()).add(name)
    for name, m in sl.metrics.items():
        if not m.is_base:
            for leaf in sl._leaves(name):
                for ms in usage.values():
                    if leaf in ms:
                        ms.add(name)
    return {c: sorted(ms) for c, ms in usage.items()}


def ontology_model(client: Client, con=None, proposals: list | None = None) -> dict:
    sv = client.resolved.view
    base = resolve(client.line).view
    sl = SemanticLayer(client)
    usage = _metric_usage(sl)
    unit_views = {u.code: u.resolved.view for u in client.units}

    classes, relationships = [], []
    for name, c in sorted(sv.all_classes().items()):
        curie = sv.get_uri(c, expand=False)
        attrs, customized = [], []
        own = {s.name for s in sv.class_induced_slots(name)}
        base_slots = {s.name: s for s in base.class_induced_slots(name)} if name in base.all_classes() else {}
        for s in sv.class_induced_slots(name):
            kind = slot_kind(sv, s)
            s_curie = sv.get_uri(sv.get_slot(s.name), expand=False) if s.name in sv.all_slots() else s.name
            layer = _layer_of(s_curie, client)
            attr = {"name": s.name, "label": s.title or s.name, "range": s.range or "string", "kind": kind,
                    "required": bool(s.required), "multivalued": bool(s.multivalued),
                    "description": (s.description or "").strip(), "curie": s_curie, "layer": layer,
                    "sensitivity": annotation(s, "sensitivity"),
                    "agent_visible": annotation(s, "agent_visible", True) not in (False, "false", "False")}
            b = base_slots.get(s.name)
            if b is None and layer not in ("core", "line"):
                attr["change"] = "added by client"
            elif b is not None:
                diffs = []
                if bool(b.required) != bool(s.required):
                    diffs.append("made required")
                if (b.range or "string") != (s.range or "string"):
                    diffs.append(f"restricted to {s.range}")
                if (s.title or s.name) != (b.title or b.name):
                    diffs.append(f"relabelled '{s.title}'")
                if not attr["agent_visible"]:
                    diffs.append("hidden from agents")
                if diffs:
                    attr["change"] = "; ".join(diffs)
            if attr.get("change"):
                customized.append(f"{s.name}: {attr['change']}")
            attrs.append(attr)
            if kind == "class" and s.name != "id":
                relationships.append({"from": name, "slot": s.name, "to": s.range, "label": s.title or s.name,
                                      "curie": s_curie, "layer": layer, "multivalued": bool(s.multivalued)})
        for code, uv in unit_views.items():
            if name not in uv.all_classes():
                continue
            for s in uv.class_induced_slots(name):
                if s.name in own:
                    continue
                s_curie = uv.get_uri(uv.get_slot(s.name), expand=False)
                attrs.append({"name": s.name, "label": s.title or s.name, "range": s.range or "string",
                              "kind": slot_kind(uv, s), "required": bool(s.required),
                              "multivalued": bool(s.multivalued), "description": (s.description or "").strip(),
                              "curie": s_curie, "layer": f"unit:{code}", "sensitivity": None,
                              "agent_visible": True, "change": f"added by unit {code}"})
                customized.append(f"{s.name}: added by unit {code}")
        label = c.title or name
        if name in base.all_classes() and label != name:
            customized.insert(0, f"relabelled '{label}'")
        classes.append({
            "name": name, "label": label, "curie": curie, "iri": sv.get_uri(c, expand=True),
            "layer": _layer_of(curie, client), "group": _group(sv, name), "abstract": bool(c.abstract),
            "is_a": [a for a in sv.class_ancestors(name) if a != name],
            "parent": c.is_a, "description": (c.description or "").strip(),
            "aliases": [a for a in c.aliases if a != label],
            "identity": "global" if is_global(sv, name) else "unit-qualified",
            "attributes": attrs, "sources": _sources(client, name, con),
            "metrics": usage.get(name, []), "customized": customized,
            "view": f"ont.{table_name(name)}",
        })

    enums = []
    for name, e in sorted(sv.all_enums().items()):
        curie = sv.get_uri(e, expand=False)
        enums.append({
            "name": name, "curie": curie, "layer": _layer_of(curie, client),
            "description": (e.description or "").strip(),
            "values": [{"value": v, "description": (pv.description or "").strip(),
                        "annotations": {k: annotation(pv, k) for k in (pv.annotations or {})}}
                       for v, pv in e.permissible_values.items()],
        })

    return {
        "client": {"name": client.name, "title": client.title, "line": client.line,
                   "line_version": client.line_version, "client_version": str(client.config.get("version", "")),
                   "prefix": client.prefix,
                   "units": [{"code": u.code, "name": u.name, "source_system": u.source_system}
                             for u in client.units]},
        "classes": classes,
        "relationships": relationships,
        "enums": enums,
        "metrics": sl.catalog(),
        "proposals": [{"id": p.id, "kind": p.kind, "target": p.target, "title": p.title,
                       "status": p.status, "layer": p.layer} for p in (proposals or [])],
    }
