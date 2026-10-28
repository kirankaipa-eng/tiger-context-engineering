"""Resolve a line pack plus a chain of overlays into one standalone LinkML schema.

    line pack  ->  client (group) overlay  ->  operating-unit overlay

Each overlay is a small, reviewable YAML diff against what is beneath it. Resolution
never changes the IRI of an inherited element — a client may *label* ins:Claim
"Encounter Claim", but it is still ins:Claim in every export, which is what keeps
cross-client analytics and cross-platform exports interoperable. Only elements the
client adds get the client namespace.

Supported overlay operations:
  labels    — client vocabulary for classes/slots (-> title + aliases, IRI unchanged)
  extend    — new slots on existing classes, new enums, new classes, and new
              values on existing enums (extend.enum_values)
  restrict  — narrow an enum-valued slot on a class to a subset of values
  require   — make an optional slot required on a class
  hide      — keep a slot in the model but never expose it to agents
  global_identity — classes whose ids are shared across units in this client
                    (e.g. a group broker master) instead of unit-qualified
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from linkml_runtime import SchemaView
from linkml_runtime.linkml_model.meta import (
    Annotation,
    ClassDefinition,
    EnumDefinition,
    PermissibleValue,
    Prefix,
    SchemaDefinition,
    SlotDefinition,
)
from linkml_runtime.dumpers import yaml_dumper
from linkml_runtime.loaders import yaml_loader

REPO_ROOT = Path(__file__).resolve().parents[2]
# Ontology content (packs, metrics, clients) can live in its own git repo; the engine
# reads it from ICTX_CONTENT_DIR, defaulting to the copy bundled with the engine.
CONTENT_ROOT = Path(os.environ.get("ICTX_CONTENT_DIR") or REPO_ROOT).resolve()
LINES_DIR = CONTENT_ROOT / "ontology" / "lines"


class OverlayError(ValueError):
    pass


@dataclass
class ResolvedSchema:
    schema: SchemaDefinition
    overlay: dict | None

    @cached_property
    def view(self) -> SchemaView:
        return SchemaView(self.schema)

    def dump(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml_dumper.dumps(self.schema), encoding="utf-8")
        return path


def resolve(pack: str, overlays: list[dict] | None = None) -> ResolvedSchema:
    """Return the merged schema for a line pack with overlays applied in order."""
    path = LINES_DIR / f"{pack}.yaml"
    if not path.exists():
        raise OverlayError(f"Unknown line pack {pack!r} (no {path})")
    sv = SchemaView(str(path))
    _pin_uris(sv)
    sv.merge_imports()
    schema = copy.deepcopy(sv.schema)
    # linkml:types came in with the merge; import it instead of inlining 19 type defs.
    for type_name in list(schema.types):
        del schema.types[type_name]
    schema.imports = ["linkml:types"]

    overlays = overlays or []
    for ov in overlays:
        _apply_overlay(schema, ov)
    # Round-trip so the in-memory schema is exactly what exports will see.
    resolved = yaml_loader.loads(yaml_dumper.dumps(schema), SchemaDefinition)
    return ResolvedSchema(resolved, overlays[-1] if overlays else None)


def _pin_uris(sv: SchemaView) -> None:
    """Freeze each element's IRI so merging into another namespace cannot move it."""
    for name, cls in sv.all_classes().items():
        _owner(sv, "classes", name).class_uri = sv.get_uri(cls, expand=False)
    for name, slot in sv.all_slots().items():
        _owner(sv, "slots", name).slot_uri = sv.get_uri(slot, expand=False)
    for name, enum in sv.all_enums().items():
        _owner(sv, "enums", name).enum_uri = sv.get_uri(enum, expand=False)


def _owner(sv: SchemaView, kind: str, name: str):
    for schema in sv.all_schema():
        elements = getattr(schema, kind)
        if name in elements:
            return elements[name]
    raise KeyError(name)


def _apply_overlay(schema: SchemaDefinition, ov: dict) -> None:
    client, prefix, ns = ov["client"], ov["prefix"], ov["namespace"]
    base_id = schema.id

    schema.id = ns.rstrip("/")
    schema.name = client
    schema.title = ov.get("title", client)
    schema.version = str(ov.get("version", "0"))
    schema.prefixes[prefix] = Prefix(prefix, ns)
    schema.default_prefix = prefix
    schema.annotations[f"base_{prefix}"] = Annotation(f"base_{prefix}", base_id)

    ext = ov.get("extend", {}) or {}

    for enum_name, values in (ext.get("enums") or {}).items():
        _new_element(schema.enums, enum_name, "enum")
        schema.enums[enum_name] = EnumDefinition(
            enum_name,
            enum_uri=f"{prefix}:{enum_name}",
            permissible_values={v: PermissibleValue(v) for v in values},
        )

    for enum_name, values in (ext.get("enum_values") or {}).items():
        if enum_name not in schema.enums:
            raise OverlayError(f"extend.enum_values references unknown enum {enum_name!r}")
        enum = schema.enums[enum_name]
        for v in values:
            spec = v if isinstance(v, dict) else {"value": v}
            if spec["value"] in enum.permissible_values:
                raise OverlayError(f"{enum_name} already has value {spec['value']!r}")
            pv = PermissibleValue(spec["value"], description=spec.get("description"))
            pv.annotations["added_by"] = Annotation("added_by", prefix)
            enum.permissible_values[spec["value"]] = pv

    for cls_name, spec in (ext.get("classes") or {}).items():
        _new_element(schema.classes, cls_name, "class")
        cls = ClassDefinition(
            cls_name,
            class_uri=f"{prefix}:{cls_name}",
            is_a=spec.get("is_a"),
            description=spec.get("description"),
        )
        schema.classes[cls_name] = cls
        for slot_name, slot_spec in (spec.get("slots") or {}).items():
            _add_client_slot(schema, cls, slot_name, slot_spec, prefix)

    for cls_name, spec in (ext.get("add_slots") or {}).items():
        cls = _class(schema, cls_name)
        for slot_name, slot_spec in spec.items():
            _add_client_slot(schema, cls, slot_name, slot_spec, prefix)

    for cls_name, label in (ov.get("labels", {}).get("classes") or {}).items():
        cls = _class(schema, cls_name)
        cls.title = label
        if label not in cls.aliases:
            cls.aliases.append(label)
    for slot_name, label in (ov.get("labels", {}).get("slots") or {}).items():
        slot = _slot(schema, slot_name)
        slot.title = label
        if label not in slot.aliases:
            slot.aliases.append(label)

    for r in ov.get("restrict") or []:
        _restrict(schema, r, prefix)

    for r in ov.get("require") or []:
        cls = _class(schema, r["class"])
        _slot(schema, r["slot"])
        usage = cls.slot_usage.get(r["slot"]) or SlotDefinition(r["slot"])
        usage.required = True
        cls.slot_usage[r["slot"]] = usage
        _note(usage, r)

    for cls_name in ov.get("global_identity") or []:
        cls = _class(schema, cls_name)
        cls.annotations["identity_scope"] = Annotation("identity_scope", "global")

    for ref in ov.get("hide") or []:
        cls_name, slot_name = ref.split(".")
        cls = _class(schema, cls_name)
        _slot(schema, slot_name)
        usage = cls.slot_usage.get(slot_name) or SlotDefinition(slot_name)
        usage.annotations["agent_visible"] = Annotation("agent_visible", False)
        cls.slot_usage[slot_name] = usage


def _restrict(schema: SchemaDefinition, r: dict, prefix: str) -> None:
    cls = _class(schema, r["class"])
    slot = _slot(schema, r["slot"])
    usage = cls.slot_usage.get(r["slot"])
    base_range = (usage.range if usage and usage.range else None) or slot.range
    base_enum = schema.enums.get(base_range)
    if base_enum is None:
        raise OverlayError(f"restrict {r['class']}.{r['slot']}: range {base_range!r} is not an enum")
    unknown = set(r["allow"]) - set(base_enum.permissible_values)
    if unknown:
        raise OverlayError(f"restrict {r['class']}.{r['slot']}: {sorted(unknown)} not in {base_range}")

    new_name = f"{_camel(prefix)}{r['class']}{_camel(r['slot'])}Enum"
    schema.enums[new_name] = EnumDefinition(
        new_name,
        enum_uri=f"{prefix}:{new_name}",
        description=f"Client subset of {base_range}. {r.get('reason', '')}".strip(),
        permissible_values={v: copy.deepcopy(base_enum.permissible_values[v]) for v in r["allow"]},
        annotations={"restricts": Annotation("restricts", base_range)},
    )
    usage = usage or SlotDefinition(r["slot"])
    usage.range = new_name
    cls.slot_usage[r["slot"]] = usage


def _add_client_slot(schema, cls, slot_name, spec, prefix) -> None:
    _new_element(schema.slots, slot_name, "slot")
    spec = dict(spec or {})
    annotations = spec.pop("annotations", {}) or {}
    slot = SlotDefinition(slot_name, slot_uri=f"{prefix}:{slot_name}", **spec)
    for k, v in annotations.items():
        slot.annotations[k] = Annotation(k, v)
    slot.annotations["client_extension"] = Annotation("client_extension", True)
    schema.slots[slot_name] = slot
    cls.slots.append(slot_name)


def _note(usage: SlotDefinition, r: dict) -> None:
    if r.get("reason"):
        usage.comments.append(r["reason"])


def _class(schema, name) -> ClassDefinition:
    if name not in schema.classes:
        raise OverlayError(f"Overlay references unknown class {name!r}")
    return schema.classes[name]


def _slot(schema, name) -> SlotDefinition:
    if name not in schema.slots:
        raise OverlayError(f"Overlay references unknown slot {name!r}")
    return schema.slots[name]


def _new_element(elements, name, kind) -> None:
    if name in elements:
        raise OverlayError(
            f"Overlay redefines existing {kind} {name!r}; use labels/restrict/require instead"
        )


def _camel(s: str) -> str:
    return "".join(p.capitalize() for p in s.split("_"))
