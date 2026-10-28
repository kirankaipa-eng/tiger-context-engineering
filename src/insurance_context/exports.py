"""Build: compile a client deployment into every export target.

    build/<client>/
      manifest.json             versions + content hashes of every artifact (provenance)
      schema/<client>.yaml      resolved group schema (LinkML)
      schema/units/<unit>.yaml  resolved unit schemas
      owl/<client>.ttl          OWL 2 (Turtle), canonical IRIs: open in Protege, load in a KG
      shacl/<client>.ttl        SHACL shapes for RDF data validation
      jsonschema/<client>.json  JSON Schema (APIs, LLM tool schemas)
      pydantic/<client>.py      Pydantic models (services, agents)
      r2rml/<client>.ttl        W3C R2RML over the ont.* views (virtual knowledge graph)
      sql/<dialect>.sql         governed views for duckdb, snowflake, databricks, postgres
      context/<client>.md       LLM-ready data dictionary in the client's vocabulary
      metrics/catalog.json      governed metric definitions and valid dimensions
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .client import Client
from .overlay import REPO_ROOT
from .schema_utils import annotation, label, ordered_slots, slot_kind, table_name
from .semantic import SemanticLayer
from .sqlgen import DIALECTS, GROUP_SCHEMA, compile_ddl, group_views

BUILD_DIR = REPO_ROOT / "build"


def build(client: Client, out: Path | None = None) -> dict:
    # Generator stack is the optional `export` extra.
    from linkml.generators.jsonschemagen import JsonSchemaGenerator
    from linkml.generators.owlgen import OwlSchemaGenerator
    from linkml.generators.pydanticgen import PydanticGenerator
    from linkml.generators.shaclgen import ShaclGenerator

    out = out or BUILD_DIR / client.name
    out.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, Path] = {}

    schema_path = client.resolved.dump(out / "schema" / f"{client.name}.yaml")
    artifacts["schema"] = schema_path
    for unit in client.units:
        artifacts[f"schema:{unit.code}"] = unit.resolved.dump(out / "schema" / "units" / f"{unit.code.lower()}.yaml")

    artifacts["owl"] = _write(out / "owl" / f"{client.name}.ttl",
                              OwlSchemaGenerator(str(schema_path), use_native_uris=False,
                                                 consolidate_cardinality_axioms=True,
                                                 skip_vacuous_min_zero_cardinality_axioms=True,
                                                 skip_vacuous_local_range_axioms=True).serialize())
    artifacts["shacl"] = _write(out / "shacl" / f"{client.name}.ttl",
                                ShaclGenerator(str(schema_path)).serialize())
    artifacts["jsonschema"] = _write(out / "jsonschema" / f"{client.name}.json",
                                     JsonSchemaGenerator(str(schema_path)).serialize())
    artifacts["pydantic"] = _write(out / "pydantic" / f"{client.name}.py",
                                   PydanticGenerator(str(schema_path)).serialize())
    artifacts["r2rml"] = _write(out / "r2rml" / f"{client.name}.ttl", r2rml(client))
    for d in DIALECTS:
        artifacts[f"sql:{d}"] = _write(out / "sql" / f"{d}.sql", compile_ddl(client, d))
    artifacts["context"] = _write(out / "context" / f"{client.name}.md", context_document(client))
    sl = SemanticLayer(client)
    artifacts["metrics"] = _write(out / "metrics" / "catalog.json",
                                  json.dumps({"version": sl.version, "metrics": sl.catalog(),
                                              "dimensions": {n: {"label": d.label, "entities": sorted(d.paths)}
                                                             for n, d in sl.dimensions.items()}}, indent=2))

    manifest = {
        "client": client.name,
        "title": client.title,
        "line": client.line,
        "line_version": client.line_version,
        "client_version": client.config.get("version"),
        "units": [u.code for u in client.units],
        "engine_version": __version__,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "artifacts": {k: {"path": str(p.relative_to(out)), "sha256": _sha(p)} for k, p in artifacts.items()},
    }
    _write(out / "manifest.json", json.dumps(manifest, indent=2))
    return manifest


def r2rml(client: Client) -> str:
    """R2RML TriplesMaps over the group views, using canonical ontology IRIs."""
    from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
    RR = Namespace("http://www.w3.org/ns/r2rml#")
    sv = client.resolved.view
    g = Graph()
    g.bind("rr", RR)
    data_ns = client.namespace + "data/"
    for view in group_views(client):
        cls = view.entity
        tm = URIRef(f"{client.namespace}mapping/{cls}")
        g.add((tm, RDF.type, RR.TriplesMap))
        lt = BNode()
        g.add((tm, RR.logicalTable, lt))
        g.add((lt, RR.tableName, Literal(f"{GROUP_SCHEMA}.{view.name}")))
        sm = BNode()
        g.add((tm, RR.subjectMap, sm))
        g.add((sm, RR.template, Literal(f"{data_ns}{cls}/{{id}}")))
        g.add((sm, RR["class"], URIRef(sv.get_uri(cls, expand=True))))
        for slot in ordered_slots(sv, cls):
            if slot.name == "id" or slot.multivalued:
                continue  # R2RML has no array semantics; multivalued slots need a child table
            pom, om = BNode(), BNode()
            g.add((tm, RR.predicateObjectMap, pom))
            g.add((pom, RR.predicate, URIRef(sv.get_uri(slot, expand=True))))
            g.add((pom, RR.objectMap, om))
            if slot_kind(sv, slot) == "class":
                g.add((om, RR.template, Literal(f"{data_ns}{slot.range}/{{{slot.name}}}")))
            else:
                g.add((om, RR.column, Literal(slot.name)))
    return g.serialize(format="turtle")


def context_document(client: Client) -> str:
    """Data dictionary written for an LLM: client vocabulary, meanings, relationships, sensitivity."""
    sv = client.resolved.view
    lines = [f"# {client.title}: enterprise context", "",
             f"Line of business pack: `{client.line}` v{client.line_version}. "
             f"Units: {', '.join(f'{u.code} ({u.name})' for u in client.units)}.", "",
             "Query the `ont_agent` schema (PII/PHI redacted). Each entity below is a view "
             "`ont_agent.<table>`. `source_system` gives the unit a row came from; ids are prefixed "
             "with the unit code unless the entity is shared group reference data.", ""]
    for view in group_views(client):
        cls = sv.get_class(view.entity)
        head = label(sv, cls)
        also = [a for a in cls.aliases if a != head]
        lines.append(f"## {head} (`{view.name}`)")
        if cls.title and cls.title != cls.name:
            lines.append(f"Canonical concept: `{cls.name}`.")
        if cls.description:
            lines.append(cls.description.strip())
        if also:
            lines.append(f"Also called: {', '.join(also)}.")
        lines.append("")
        lines.append("| column | type | meaning |")
        lines.append("|---|---|---|")
        for slot in ordered_slots(sv, view.entity):
            if annotation(slot, "agent_visible", True) in (False, "false", "False"):
                continue
            kind = slot_kind(sv, slot)
            if kind == "class":
                typ = f"-> {table_name(slot.range)}.id"
            elif kind == "enum":
                values = list(sv.get_enum(slot.range).permissible_values)
                typ = "one of: " + ", ".join(values[:14]) + (" ..." if len(values) > 14 else "")
            else:
                typ = slot.range or "string"
            if slot.multivalued:
                typ = f"list of {typ}"
            meaning = (slot.description or "").replace("\n", " ").strip()
            if slot.title and slot.title != slot.name:
                meaning = f"({slot.title}) {meaning}".strip()
            sens = annotation(slot, "sensitivity")
            if sens in client.redact:
                meaning += f" [{sens.upper()}: redacted for agents]"
            lines.append(f"| {slot.name} | {typ} | {meaning} |")
        lines.append("")
    return "\n".join(lines)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
