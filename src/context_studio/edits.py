"""Edit operations: every save in the Studio and every curator draft is a list of these,
applied to a proposal's worktree. Round-trip YAML keeps comments and layout byte for byte
outside the edited node, so a diff shows exactly the change and nothing else.

    {"op": "add_enum_value", "enum": "CauseOfLossEnum", "value": "pfas", "description": "...",
     "layer": "pack" | "client", "client": "summit_group"}
    {"op": "set_label", "client": "...", "kind": "classes" | "slots", "name": "...", "label": "..."}
    {"op": "set_definition", "client": "...", "concept": "CommercialClaim", "text": "..."}
    {"op": "set_metric", "metric": "...", "fields": {...}, "client": "..." (override) | None (pack)}
    {"op": "set_mapping", "client": "...", "unit": "HPG", "entity": "...", "slot": "...",
     "spec": {"column": "..."} | {"expr": "..."}, "note": "..."}
    {"op": "write_file", "path": "clients/x/okf/...", "content": "..."}
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

import yaml as pyyaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


class EditError(ValueError):
    pass


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _load(path: Path):
    if not path.exists():
        raise EditError(f"{path.name} does not exist")
    return _yaml().load(path.read_text())


def _dump(path: Path, data) -> None:
    buf = io.StringIO()
    _yaml().dump(data, buf)
    path.write_text(buf.getvalue())


def _child(m: CommentedMap, key: str) -> CommentedMap:
    if key not in m or m[key] is None:
        m[key] = CommentedMap()
    return m[key]


def _client_file(root: Path, client: str) -> Path:
    return root / "clients" / client / "client.yaml"


def _pack_files(root: Path) -> list[Path]:
    return sorted((root / "ontology" / "lines").glob("*.yaml")) + [root / "ontology" / "core" / "core.yaml"]


def _client_line(root: Path, client: str) -> str:
    return pyyaml.safe_load(_client_file(root, client).read_text())["line"]


def _safe(root: Path, rel: str) -> Path:
    path = (root / rel).resolve()
    if root.resolve() not in path.parents or ".git" in path.parts:
        raise EditError(f"path outside the content repo: {rel}")
    return path


# ------------------------------------------------------------------ operations

def add_enum_value(root: Path, op: dict) -> list[str]:
    enum, value = op["enum"], op["value"]
    desc = op.get("description")
    if op.get("layer", "pack") == "client":
        path = _client_file(root, op["client"])
        data = _load(path)
        ev = _child(_child(data, "extend"), "enum_values")
        seq = ev.get(enum)
        if seq is None:
            ev[enum] = seq = CommentedSeq()
        if value in seq:
            raise EditError(f"{value} is already in {enum} for this client")
        seq.append(value)
        _dump(path, data)
        return [str(path.relative_to(root))]
    for path in _pack_files(root):
        data = _load(path)
        enums = data.get("enums") or {}
        if enum in enums:
            pv = _child(enums[enum], "permissible_values")
            if value in pv:
                raise EditError(f"{value} is already in {enum}")
            entry = CommentedMap()
            if desc:
                entry["description"] = desc
            pv[value] = entry if entry else None
            _dump(path, data)
            return [str(path.relative_to(root))]
    raise EditError(f"no pack defines {enum}")


def set_label(root: Path, op: dict) -> list[str]:
    if op["kind"] not in ("classes", "slots"):
        raise EditError("kind must be classes or slots")
    path = _client_file(root, op["client"])
    data = _load(path)
    _child(_child(data, "labels"), op["kind"])[op["name"]] = op["label"]
    _dump(path, data)
    return [str(path.relative_to(root))]


def set_definition(root: Path, op: dict) -> list[str]:
    """People's definition of a concept lives in the client's OKF entity file."""
    concept = op["concept"]
    path = root / "clients" / op["client"] / "okf" / "entities" / f"{concept}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = {"type": "Ontology Entity", "title": op.get("title") or concept, "canonical": concept,
              "layer": "client", "verified": None,
              "updated": datetime.now(timezone.utc).date().isoformat()}
    if path.exists():
        raw = path.read_text()
        if raw.startswith("---"):
            _, head, _ = raw.split("---", 2)
            header = {**(pyyaml.safe_load(head) or {}), "updated": header["updated"]}
    examples = op.get("examples") or []
    body = f"# {header['title']}\n\n## Business definition\n\n{op['text'].strip()}\n"
    if examples:
        body += "\n## Examples\n\n" + "\n".join(f"- {e}" for e in examples) + "\n"
    path.write_text("---\n" + pyyaml.safe_dump(header, sort_keys=False) + "---\n" + body)
    return [str(path.relative_to(root))]


def set_metric(root: Path, op: dict) -> list[str]:
    name, fields = op["metric"], op["fields"]
    if op.get("client"):
        path = _client_file(root, op["client"])
        data = _load(path)
        target = _child(_child(data, "metrics"), name)
    else:
        line = op.get("line") or (op.get("for_client") and _client_line(root, op["for_client"]))
        path = root / "ontology" / "metrics" / f"{line}.yaml"
        data = _load(path)
        metrics = data.get("metrics") or {}
        if name not in metrics:
            metrics[name] = CommentedMap()
        target = metrics[name]
    for k, v in fields.items():
        if v is None:
            target.pop(k, None)
        else:
            target[k] = v
    _dump(path, data)
    return [str(path.relative_to(root))]


def _unit_dir(root: Path, client: str, unit: str) -> Path:
    """A unit by its code (PLH) or its name (Plains Health Plan), as client.yaml lists it."""
    units = (pyyaml.safe_load(_client_file(root, client).read_text()) or {}).get("units") or []
    want = str(unit).strip().lower()
    for u in units:
        if want in (str(u.get("code", "")).lower(), str(u.get("name", "")).lower()):
            return root / "clients" / client / "units" / str(u["code"]).lower()
    codes = ", ".join(str(u.get("code")) for u in units) or "none"
    raise EditError(f"{client} has no unit {unit!r}; units are: {codes}")


def set_mapping(root: Path, op: dict) -> list[str]:
    path = _unit_dir(root, op["client"], op["unit"]) / "mappings.yaml"
    data = _load(path)
    cls = (data.get("classes") or {}).get(op["entity"])
    if cls is None:
        raise EditError(f"{op['entity']} is not mapped for unit {op['unit']}")
    slots = _child(cls, "slots")
    spec = CommentedMap()
    for k in ("column", "expr"):
        if op["spec"].get(k):
            spec[k] = op["spec"][k]
    if op.get("note"):
        spec["note"] = op["note"]
    if not spec:
        raise EditError("a mapping needs a column or an expr")
    slots[op["slot"]] = spec
    _dump(path, data)
    return [str(path.relative_to(root))]


def write_file(root: Path, op: dict) -> list[str]:
    path = _safe(root, op["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(op["content"])
    return [op["path"]]


OPS = {"add_enum_value": add_enum_value, "set_label": set_label, "set_definition": set_definition,
       "set_metric": set_metric, "set_mapping": set_mapping, "write_file": write_file}


def apply(root: Path, ops: list[dict]) -> list[str]:
    touched: list[str] = []
    for op in ops:
        fn = OPS.get(op.get("op", ""))
        if fn is None:
            raise EditError(f"unknown edit operation {op.get('op')!r}")
        try:
            touched += fn(root, op)
        except KeyError as exc:
            raise EditError(f"{op['op']}: missing {exc}") from exc
    return sorted(set(touched))
