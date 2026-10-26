"""Helpers for reading a resolved schema the way the compilers need it."""

from __future__ import annotations

import re

from linkml_runtime import SchemaView
from linkml_runtime.linkml_model.meta import SlotDefinition

_TYPE_SQL = {
    "string": "VARCHAR",
    "integer": "BIGINT",
    "decimal": "DECIMAL(38,6)",  # money and rates alike: 2 places would truncate cap rates and risk scores
    "float": "DOUBLE",
    "double": "DOUBLE",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "datetime": "TIMESTAMP",
}


def table_name(class_name: str) -> str:
    """CommercialClaim -> commercial_claim."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower()


def concrete_classes(sv: SchemaView) -> list[str]:
    return [c.name for c in sv.all_classes().values() if not c.abstract and not c.mixin]


def ordered_slots(sv: SchemaView, cls: str) -> list[SlotDefinition]:
    """Induced slots with `id` first and lineage (`source_system`) last."""
    slots = {s.name: s for s in sv.class_induced_slots(cls)}
    order = ["id"]
    for ancestor in reversed(sv.class_ancestors(cls)):
        order += [s for s in sv.get_class(ancestor).slots if s not in order]
    order += [s for s in slots if s not in order]
    order.remove("source_system")
    order.append("source_system")
    return [slots[n] for n in order if n in slots]


def slot_kind(sv: SchemaView, slot: SlotDefinition) -> str:
    """'class' | 'enum' | 'type'."""
    r = slot.range or sv.schema.default_range or "string"
    if r in sv.all_classes():
        return "class"
    if r in sv.all_enums():
        return "enum"
    return "type"


def sql_type(sv: SchemaView, slot: SlotDefinition) -> str:
    base = "VARCHAR"
    if slot_kind(sv, slot) == "type":
        types = sv.all_types()
        t = types.get(slot.range or sv.schema.default_range or "string")
        while t is not None and t.typeof:
            t = types.get(t.typeof)
        base = _TYPE_SQL.get(t.name if t else "string", "VARCHAR")
    return f"{base}[]" if slot.multivalued else base


def _ann(annotations, tag: str):
    """Annotation lookup that works for dicts and LinkML JsonObj alike."""
    if not annotations:
        return None
    try:
        ann = annotations[tag]
    except (KeyError, TypeError):
        ann = getattr(annotations, tag, None)
    if ann is None:
        return None
    if isinstance(ann, dict):
        return ann.get("value")
    return getattr(ann, "value", ann)


def annotation(element, tag: str, default=None):
    value = _ann(element.annotations, tag)
    return default if value is None else value


def is_global(sv: SchemaView, cls: str) -> bool:
    """Classes whose identifiers are shared across units (reference data)."""
    return any(annotation(sv.get_class(c), "identity_scope") == "global"
               for c in sv.class_ancestors(cls))


def label(sv: SchemaView, element) -> str:
    return element.title or element.name
