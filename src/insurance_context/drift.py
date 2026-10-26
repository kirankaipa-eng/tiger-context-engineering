"""Evolution loop, signal 1: data drift -> verified change proposals.

The ontology does not rewrite itself. It notices, drafts, verifies and asks:

  detect    profile each unit's source systems against the ontology + mappings
              * source columns nobody maps (new fields appearing upstream)
              * source tables nobody maps
              * values outside an enum domain (new categories in the business)
              * ontology entities no unit maps yet (coverage gaps)
  draft     turn each signal into a ChangeProposal with evidence and a concrete
            overlay / mapping change, scoped to a layer (pack, client, unit)
  enrich    optionally ask an LLM for industry context, better naming and the
            right scope (a trend seen across the industry belongs in the pack)
  verify    dry-run every suggested change by resolving the schema with it;
            proposals that do not resolve are marked invalid, never shown as ready
  review    a steward accepts or rejects; accepted changes ship as a new
            overlay/pack version

Other signal sources (regulatory feeds, standards releases, agent questions the
ontology could not answer) produce the same ChangeProposal shape.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .client import Client
from .overlay import OverlayError, resolve
from .schema_utils import annotation, concrete_classes, table_name
from .validate import validate

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class ChangeProposal:
    kind: str                 # new_attribute | new_enum_value | unmapped_table | coverage_gap
                              # | restriction_violation
    layer: str                # pack:<line> | client:<name> | unit:<code>
    target: str               # ontology element the change lands on
    title: str
    rationale: str
    evidence: dict
    overlay_change: dict = field(default_factory=dict)
    mapping_change: dict = field(default_factory=dict)
    signal: str = "data_drift"
    status: str = "proposed"  # proposed | verified | invalid | accepted | rejected
    verification: str = ""
    detected_on: str = field(default_factory=lambda: date.today().isoformat())
    enriched_by: str = ""

    @property
    def id(self) -> str:
        key = json.dumps([self.kind, self.target, self.evidence.get("source")], sort_keys=True, default=str)
        return "CP-" + hashlib.sha1(key.encode()).hexdigest()[:10]

    def as_dict(self) -> dict:
        return {"id": self.id, **asdict(self)}


# --------------------------------------------------------------------- detect

def _mapping_tokens(unit) -> set[str]:
    return {t.upper() for t in _TOKEN.findall(json.dumps(unit.mappings))}


def _mapped_tables(unit) -> dict[str, list[str]]:
    """source table (upper) -> ontology classes it feeds, row-grain classes first.

    A class that reads the table row-for-row (a plain `table:` or a query without
    GROUP BY / DISTINCT) is where a new source column most likely belongs.
    """
    ranked: dict[str, list[tuple[int, str]]] = {}
    source_schema = unit.mappings.get("source_schema", "main")
    for cls, blocks in (unit.mappings.get("classes") or {}).items():
        blocks = blocks if isinstance(blocks, list) else [blocks]
        for b in blocks:
            if "table" in b:
                ranked.setdefault(b["table"].upper(), []).append((0, cls))
            q = b.get("query")
            if q:
                grain = 1 if re.search(r"\bGROUP\s+BY\b|\bDISTINCT\b", q, re.I) else 0
                for m in re.finditer(rf"{source_schema}\.([A-Za-z_][A-Za-z0-9_]*)", q, re.I):
                    ranked.setdefault(m.group(1).upper(), []).append((grain, cls))
    return {t: [c for _, c in sorted(set(v), key=lambda x: x[0])] for t, v in ranked.items()}


def _profile(con, schema: str, table: str, column: str, dtype: str) -> dict:
    q = f"{schema}.{table}"
    total, nulls, distinct = con.execute(
        f"SELECT count(*), count(*) - count({column}), count(DISTINCT {column}) FROM {q}").fetchone()
    top = con.execute(
        f"SELECT CAST({column} AS VARCHAR) v, count(*) n FROM {q} WHERE {column} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT 5").fetchall()
    prof = {"rows": total, "null_rate": round(nulls / total, 4) if total else None, "distinct": distinct,
            "source_type": dtype, "top_values": [[v, n] for v, n in top]}
    if dtype.upper().startswith(("DECIMAL", "DOUBLE", "INTEGER", "BIGINT", "FLOAT")):
        mn, mx, sm = con.execute(f"SELECT min({column}), max({column}), sum({column}) FROM {q}").fetchone()
        prof.update({"min": float(mn) if mn is not None else None, "max": float(mx) if mx is not None else None,
                     "sum": float(sm) if sm is not None else None})
    return prof


def _infer(profile: dict) -> tuple[str, str]:
    """(LinkML range, mapping expression) guessed from a column profile."""
    values = {str(v).upper() for v, _ in profile["top_values"]}
    t = profile["source_type"].upper()
    if values and values <= {"Y", "N"}:
        return "boolean", "src.{col} = 'Y'"
    if t.startswith(("DECIMAL", "DOUBLE", "FLOAT")):
        return "decimal", "src.{col}"
    if t.startswith(("INTEGER", "BIGINT", "SMALLINT")):
        return "integer", "src.{col}"
    if t.startswith("DATE"):
        return "date", "src.{col}"
    return "string", "src.{col}"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return re.sub(r"_(ind|flg|cd)$", "", s)


def detect(client: Client, con) -> list[ChangeProposal]:
    proposals: list[ChangeProposal] = []
    for unit in client.units:
        schema = unit.mappings.get("source_schema", "main")
        tokens = _mapping_tokens(unit)
        mapped = _mapped_tables(unit)
        cols = con.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? ORDER BY table_name, ordinal_position", [schema]).fetchall()
        tables: dict[str, list[tuple[str, str]]] = {}
        for t, c, d in cols:
            tables.setdefault(t, []).append((c, d))

        for table, columns in tables.items():
            classes = mapped.get(table.upper())
            if not classes:
                proposals.append(ChangeProposal(
                    kind="unmapped_table", layer=f"unit:{unit.code}", target=f"{schema}.{table}",
                    title=f"{unit.code}: source table {schema}.{table} is not mapped",
                    rationale="A source table exists that no ontology entity reads from.",
                    evidence={"source": f"{schema}.{table}", "columns": [c for c, _ in columns]}))
                continue
            for column, dtype in columns:
                if column.upper() in tokens:
                    continue
                prof = _profile(con, schema, table, column, dtype)
                if prof["rows"] and prof["null_rate"] == 1.0:
                    continue
                rng, expr = _infer(prof)
                cls = classes[0]
                slot = f"{unit.client.prefix}_{unit.code.lower()}_{_slug(column)}"
                proposals.append(ChangeProposal(
                    kind="new_attribute", layer=f"unit:{unit.code}", target=cls,
                    title=f"{unit.code}: {schema}.{table}.{column} carries data the ontology does not model",
                    rationale=(f"Column is populated ({(1 - (prof['null_rate'] or 0)):.0%} of "
                               f"{prof['rows']:,} rows) but no mapping reads it."),
                    evidence={"source": f"{schema}.{table}.{column}", "feeds": classes, "profile": prof},
                    overlay_change={"extend": {"add_slots": {cls: {slot: {
                        "range": rng, "description": f"From {schema}.{table}.{column} (drafted from data drift)."}}}}},
                    mapping_change={"classes": {cls: {"slots": {slot: {"expr": expr.format(col=column)}}}}}))

    # Enum drift, from the conformance checks.
    for f in validate(client, con):
        if f.check != "enum":
            continue
        unit = client.unit(f.unit)
        sv = unit.resolved.view
        enum = sv.induced_slot(f.slot, f.entity).range
        new_values = [str(v) for v in f.sample]
        restricts = annotation(sv.get_enum(enum), "restricts")
        if restricts:
            # The client narrowed this enum on purpose: out-of-domain values are a
            # business-rule breach (or a decode gap in the mapping), not ontology drift.
            proposals.append(ChangeProposal(
                kind="restriction_violation", layer=f"unit:{f.unit}", target=enum,
                title=(f"{f.unit}: {f.violations} {f.entity}.{f.slot} record(s) break the client restriction "
                       f"on {restricts}: {', '.join(new_values)}"),
                rationale=("The client overlay deliberately restricts this value set. Route to the data "
                           "owner: either the records should not be here, or the mapping needs a decode."),
                evidence={"source": f"{unit.schema_name}.{table_name(f.entity)}.{f.slot}",
                          "violations": f.violations, "rows": f.rows, "values": new_values},
                signal="data_quality"))
            continue
        proposals.append(ChangeProposal(
            kind="new_enum_value", layer=f"client:{client.name}", target=enum,
            title=f"{f.unit}: {f.violations} {f.entity}.{f.slot} value(s) outside {enum}: {', '.join(new_values)}",
            rationale="The business is recording a category the ontology does not have.",
            evidence={"source": f"{unit.schema_name}.{table_name(f.entity)}.{f.slot}",
                      "violations": f.violations, "rows": f.rows, "values": new_values},
            overlay_change={"extend": {"enum_values": {enum: [
                {"value": _slug(v), "description": f"Observed in {f.unit} data (drafted from data drift)."}
                for v in new_values]}}}))

    # Coverage gaps: pack entities nobody maps.
    sv = client.resolved.view
    mapped_classes = {c for u in client.units for c in (u.mappings.get("classes") or {})}
    for cls in concrete_classes(sv):
        # A general class (Person, Product...) is covered when any specialization is mapped.
        if not ({cls, *sv.class_descendants(cls)} & mapped_classes):
            proposals.append(ChangeProposal(
                kind="coverage_gap", layer=f"client:{client.name}", target=cls,
                title=f"No unit maps {cls}", signal="coverage",
                rationale="Questions about this entity cannot be answered until a source is mapped.",
                evidence={"source": cls}))
    return proposals


# --------------------------------------------------------------------- verify

def _deep_merge(base: dict, extra: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = out[k] + v
        else:
            out[k] = copy.deepcopy(v)
    return out


def verify(client: Client, proposal: ChangeProposal) -> ChangeProposal:
    """Resolve the schema with the suggested change applied; record the outcome."""
    if not proposal.overlay_change:
        proposal.status, proposal.verification = "proposed", "informational; no schema change to verify"
        return proposal
    layer, _, name = proposal.layer.partition(":")
    try:
        if layer == "pack":
            probe = {"client": "probe", "prefix": "probe",
                     "namespace": "https://ontology.tigeranalytics.com/probe/", **proposal.overlay_change}
            resolve(client.line, [probe, client.config])
        elif layer == "client":
            resolve(client.line, [_deep_merge(client.config, proposal.overlay_change)])
        else:
            unit = client.unit(name)
            unit_ov = _deep_merge(unit.overlay or {"client": f"{client.name}_{unit.code.lower()}",
                                                   "prefix": f"{client.prefix}_{unit.code.lower()}",
                                                   "namespace": f"{client.namespace}units/{unit.code.lower()}/"},
                                  proposal.overlay_change)
            resolve(client.line, [client.config, unit_ov])
        proposal.status, proposal.verification = "verified", "schema resolves with the change applied"
    except (OverlayError, ValueError, KeyError) as exc:
        proposal.status, proposal.verification = "invalid", f"does not resolve: {exc}"
    return proposal


# --------------------------------------------------------------------- enrich

ENRICH_PROMPT = """You are the ontology steward for a commercial & specialty P&C insurance context layer.
A drift detector found the change proposal below. Using current industry knowledge:
1. Explain in 1-2 sentences what this data most likely represents and why it matters now
   (regulation, market trend, emerging risk).
2. Decide the right layer: "pack" if every carrier writing this line should model it
   (an industry-wide concept), "client" if it is this carrier's own practice, or keep "unit".
3. Propose a clean snake_case name (no client prefix for pack-level changes) and, for new
   attributes, the LinkML range (string, boolean, decimal, integer, date).
Reply with JSON only: {"rationale": str, "layer": "pack"|"client"|"unit", "name": str, "range": str|null}

Proposal:
"""


def enrich(client: Client, proposals: list[ChangeProposal], complete) -> list[ChangeProposal]:
    """Use an LLM (`complete(prompt) -> str`) to add industry context, naming and scope.

    Only new_attribute and new_enum_value proposals are enriched; everything the
    model suggests is re-verified, so a bad suggestion cannot reach a reviewer as valid.
    """
    for p in proposals:
        if p.kind not in ("new_attribute", "new_enum_value"):
            continue
        payload = {k: v for k, v in p.as_dict().items() if k in ("kind", "target", "title", "evidence")}
        try:
            raw = complete(ENRICH_PROMPT + json.dumps(payload, default=str, indent=2))
            data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        except Exception as exc:  # enrichment is best-effort
            p.enriched_by = f"enrichment failed: {exc}"
            continue
        name = _slug(data.get("name") or "") or None
        layer = data.get("layer") or "unit"
        p.rationale = f"{data.get('rationale', '').strip()} {p.rationale}".strip()
        p.enriched_by = "llm"
        if layer == "pack":
            p.layer = f"pack:{client.line}"
        elif layer == "client":
            p.layer = f"client:{client.name}"
        if p.kind == "new_attribute" and name:
            (cls, slots), = p.overlay_change["extend"]["add_slots"].items()
            (old, spec), = slots.items()
            new = name if p.layer.startswith("pack:") else f"{client.prefix}_{name}"
            if data.get("range") in ("string", "boolean", "decimal", "integer", "date"):
                spec["range"] = data["range"]
            p.overlay_change = {"extend": {"add_slots": {cls: {new: spec}}}}
            m = p.mapping_change["classes"][cls]["slots"].pop(old)
            p.mapping_change["classes"][cls]["slots"][new] = m
        elif p.kind == "new_enum_value" and name:
            (enum, values), = p.overlay_change["extend"]["enum_values"].items()
            if len(values) == 1:
                values[0]["value"] = name
    return proposals


def run(client: Client, con, complete=None) -> list[ChangeProposal]:
    proposals = detect(client, con)
    if complete is not None:
        enrich(client, proposals, complete)
    return [verify(client, p) for p in proposals]
