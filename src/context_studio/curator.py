"""The curator: from a client's documents and data to proposals, questions or a reasoned "set aside".

    read     each document once, with the LLM, against the ontology's vocabulary
    link     every claim to a concept / value set / metric that exists (or to nothing)
    prove    against the reference data (data-quality findings, drift)
    route    layer + owners, from what the change would touch
    act      propose (a branch), ask (a question to owners), or set aside (logged with why)

The curator drafts and asks. It never approves and never merges.
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .proposals import CURATOR, OPEN, Studio

REGULATORY = ("regulatory", "bulletin", "rule", "regulation", "cms", "statute", "standard", "federal", "state")
UNIT_DOCS = ("spec", "bordereau", "feed", "release notes", "interface")
# Documents that never change what data means (whatever they say), and ones that set business
# rules or rates: those may only propose a code the data already carries.
NOISE_DOCS = ("news", "press", "marketing", "brochure", "article", "blog", "advert")
RULE_DOCS = ("rate sheet", "rate notice", "guideline", "benefit rules", "benefit summary", "appetite")


@dataclass
class Passage:
    doc: str
    title: str
    kind: str
    page: int
    para: int
    text: str

    def cite(self) -> str:
        return f"{self.title}, page {self.page}"


@dataclass
class Claim:
    doc: str
    title: str
    doc_kind: str
    page: int
    quote: str
    kind: str                       # change | rule | noise | conflict
    change: dict = field(default_factory=dict)
    layer_hint: str | None = None
    unit: str | None = None
    why: str = ""
    confidence: str = "medium"


# ------------------------------------------------------------------ corpus

def corpus_dir(studio: Studio, client: str) -> Path:
    return studio.s.main / "clients" / client / "corpus"


def load_corpus(folder: Path) -> list[Passage]:
    out = []
    for path in sorted(folder.glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        if not raw.startswith("---"):
            continue
        _, header, body = raw.split("---", 2)
        meta = yaml.safe_load(header) or {}
        pages = re.split(r"^## Page (\d+)\s*$", body, flags=re.M)
        for page_no, text in zip(pages[1::2], pages[2::2]):
            paras = [p.strip() for p in text.strip().split("\n\n") if p.strip()]
            for i, para in enumerate(paras, 1):
                out.append(Passage(path.name, str(meta.get("title", path.stem)), str(meta.get("kind", "document")),
                                   int(page_no), i, para))
    return out


def documents(studio: Studio, client: str) -> list[dict]:
    folder = corpus_dir(studio, client)
    docs = []
    for path in sorted(folder.glob("*.md")) if folder.exists() else []:
        raw = path.read_text(encoding="utf-8")
        meta = yaml.safe_load(raw.split("---", 2)[1]) if raw.startswith("---") else {}
        docs.append({"doc": path.name, "title": meta.get("title", path.stem), "kind": meta.get("kind", ""),
                     "date": str(meta.get("date", "")), "source": meta.get("source", ""),
                     "pages": len(re.findall(r"^## Page \d+", raw, flags=re.M))})
    return docs


def search(passages: list[Passage], query: str, limit: int = 8) -> list[Passage]:
    terms = [t for t in re.findall(r"[a-z0-9-]+", query.lower()) if len(t) > 2]
    scored = []
    for p in passages:
        low = p.text.lower()
        score = sum(low.count(t) for t in terms) + (3 if query.lower() in low else 0)
        if score:
            scored.append((score, p))
    return [p for _, p in sorted(scored, key=lambda x: -x[0])[:limit]]


# ------------------------------------------------------------------ vocabulary

def vocabulary(studio: Studio, client: str) -> dict:
    """What the LLM may link to: value sets, concepts, metrics (names + labels)."""
    sv = studio.client(client).resolved.view
    enums = {n: list(e.permissible_values)[:40] for n, e in sv.all_enums().items()}
    slots_by_enum: dict[str, list[str]] = {}
    for cname in sv.all_classes():
        for s in sv.class_induced_slots(cname):
            if s.range in enums:
                slots_by_enum.setdefault(s.range, []).append(f"{cname}.{s.name}")
    concepts = {n: (c.title or n) for n, c in sv.all_classes().items() if not c.abstract}
    metrics = {m.name: m.label for m in studio.semantic(client).metrics.values()}
    return {"enums": enums, "used_by": slots_by_enum, "concepts": concepts, "metrics": metrics,
            "units": [u.code for u in studio.client(client).units]}


# ------------------------------------------------------------------ read

_PROMPT = """You are the ontology curator for an insurance company. Read the document below and find
the few passages that CHANGE what the company's data means, compared with the vocabulary below:
  - a new code or value that a value set does not have yet (type enum_value: give the enum and a snake_case value)
  - a changed definition of a concept, field or status (type definition)
  - a changed metric definition (type metric)
  - a source system that changes what a column holds, or adds a column (type mapping, give the unit)
  - two sources that define the same term differently (kind conflict)
Do NOT report passages that merely describe, define or restate something the vocabulary already covers
in the same way: that is not a change. Underwriting appetite, eligibility, exclusions, endorsements, benefit design
(copays, deductibles, limits) and rate or cap changes are business rules (kind rule), not changes to
data meaning: the data model already holds those values. News, marketing and minutes are noise.
Routine reference-data refreshes (new drug, procedure or diagnosis codes in an external code file, new
providers or products) are data, not vocabulary: noise, unless a value set listed below must change.
Report rules and noise only when they mention the same topic as a real change (so they can be set aside).

Vocabulary:
VALUE SETS (enum: current values): {enums}
WHERE THEY ARE USED (enum: Concept.field): {used_by}
CONCEPTS (name: label): {concepts}
METRICS (name: label): {metrics}
UNITS: {units}

Reply with JSON only, a list of objects:
[{{"page": int, "quote": "the exact sentence(s) from the document",
   "kind": "change" | "conflict" | "rule" | "noise",
   "change": {{"type": "enum_value" | "definition" | "metric" | "mapping" | "other",
              "enum": "EnumName or null", "value": "snake_case value or null", "value_label": "short words or null",
              "concept": "ConceptName or null", "field": "field or null", "metric": "metric name or null",
              "topic": "2-5 words naming the topic", "detail": "one sentence in plain business words"}},
   "layer_hint": "pack" (a regulator or standards body: every company) | "client" | "unit",
   "unit": "UNIT code if it concerns one unit's source system, else null",
   "why": "one short sentence", "confidence": "high" | "medium" | "low"}}]

DOCUMENT "{title}" ({kind}):
{text}"""


def _doc_text(folder: Path, doc: str) -> tuple[str, dict]:
    raw = (folder / doc).read_text(encoding="utf-8")
    _, header, body = raw.split("---", 2)
    return body.strip(), yaml.safe_load(header) or {}


def read_document(folder: Path, doc: str, vocab: dict, complete, cache: Path | None = None) -> list[Claim]:
    body, meta = _doc_text(folder, doc)
    key = hashlib.sha1((body + json.dumps(vocab, sort_keys=True)).encode()).hexdigest()[:16]
    hit = cache / f"{doc}.{key}.json" if cache else None
    if hit and hit.exists():
        items = json.loads(hit.read_text(encoding="utf-8"))
    else:
        prompt = _PROMPT.format(enums=json.dumps(vocab["enums"]), used_by=json.dumps(vocab["used_by"]),
                                concepts=json.dumps(vocab["concepts"]), metrics=json.dumps(vocab["metrics"]),
                                units=", ".join(vocab["units"]), title=meta.get("title", doc),
                                kind=meta.get("kind", ""), text=body)
        items = None
        for attempt in range(3):  # the gateway sheds load under parallel reads; back off and retry
            try:
                raw = complete(prompt, system="You extract ontology changes from documents. Reply with JSON only.")
                start, end = raw.find("["), raw.rfind("]")
                items = json.loads(raw[start: end + 1]) if start >= 0 else []
                break
            except Exception:
                if attempt == 2:
                    raise
                import time
                time.sleep(3 * (attempt + 1))
        if hit:
            hit.parent.mkdir(parents=True, exist_ok=True)
            hit.write_text(json.dumps(items), encoding="utf-8")
    claims = []
    for it in items:
        claims.append(Claim(doc, str(meta.get("title", doc)), str(meta.get("kind", "")), int(it.get("page") or 1),
                            str(it.get("quote", "")).strip(), it.get("kind", "noise"), it.get("change") or {},
                            it.get("layer_hint"), it.get("unit"), it.get("why", ""), it.get("confidence", "medium")))
    return claims


_RULE = re.compile(r"\b(exclusion|endorsement is required|do not write|appetite|decline|eligib)\w*", re.I)
_CODE = re.compile(r"\b(code|coded|record|report)\w*\b.*\b(separate|new|distinct)\b", re.I)
_ACRONYM = re.compile(r"\(([A-Z][A-Z0-9]{1,9})\)")


def read_document_rules(folder: Path, doc: str, vocab: dict) -> list[Claim]:
    """No-LLM fallback: catches 'code X separately' needles and business-rule decoys."""
    body, meta = _doc_text(folder, doc)
    claims = []
    for p in load_corpus(folder):
        if p.doc != doc:
            continue
        base = dict(doc=doc, title=p.title, doc_kind=p.kind, page=p.page, quote=p.text)
        if _RULE.search(p.text):
            claims.append(Claim(**base, kind="rule", why="a business rule, not a change to what data means"))
            continue
        m = _CODE.search(p.text)
        acr = _ACRONYM.search(p.text)
        if m and acr:
            target = None
            low = p.text.lower()
            for enum, used in vocab["used_by"].items():
                words = {w for u in used for w in u.split(".")[1].split("_")}
                if words and all(w in low for w in words):
                    target = enum
                    break
            if target:
                claims.append(Claim(**base, kind="change", change={
                    "type": "enum_value", "enum": target, "value": acr.group(1).lower(),
                    "value_label": acr.group(1), "detail": f"Code {acr.group(1)} separately"},
                    why="asks for a separate code", confidence="medium"))
    return claims


def link(claim: Claim, vocab: dict) -> Claim:
    """Fill in the value set and value when the reader named the change but not the target."""
    ch = claim.change or {}
    if ch.get("type") != "enum_value":
        return claim
    if ch.get("enum") not in vocab["enums"]:
        text = " ".join(str(x) for x in (claim.quote, ch.get("field"), ch.get("topic"), ch.get("detail"))).lower()
        best, score = None, 0
        for enum, used in vocab["used_by"].items():
            for u in used:
                words = u.split(".")[1].split("_")
                hits = sum(1 for w in words if w in text)
                if hits == len(words) and hits > score:
                    best, score = enum, hits
        ch["enum"] = best
    acr = _ACRONYM.search(claim.quote)
    if acr and ch.get("value") and acr.group(1).lower() in f"{ch.get('value')} {ch.get('value_label') or ''}".lower():
        # A document that defines an acronym codes by the acronym ("... substances (PFAS)").
        ch["value"], ch["value_label"] = acr.group(1).lower(), acr.group(1)
    if not ch.get("value"):
        label = ch.get("value_label") or (acr.group(1) if acr else None)
        if label:
            ch["value"] = re.sub(r"[^a-z0-9]+", "_", str(label).lower()).strip("_")
            ch.setdefault("value_label", label)
    claim.change = ch
    return claim


# ------------------------------------------------------------------ prove + route + act

def _findings(studio: Studio, client: str) -> list[dict]:
    try:
        return studio._main_findings(client)
    except Exception:
        return []


def corroborate(studio: Studio, client: str, claim: Claim) -> dict:
    ch = claim.change
    if ch.get("type") == "enum_value" and ch.get("enum") and ch.get("value"):
        sv = studio.client(client).resolved.view
        slots = {s for s, slot in sv.all_slots().items() if slot.range == ch["enum"]}
        flat = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())
        val, label = flat(ch["value"]), flat(ch.get("value_label") or "")
        words = re.findall(r"[a-z0-9]+", str(ch.get("value_label") or ch["value"]).lower().replace("_", " "))
        initials = "".join(w[0] for w in words if w not in ("of", "and", "the", "a")) if len(words) > 1 else ""
        for f in _findings(studio, client):
            if f["check"] == "enum" and f["slot"] in slots:
                for raw in f.get("sample", []):
                    code = flat(raw)
                    if code and (code == val or code == label or code == initials or code in val or val in code):
                        return {"agrees": True, "unit": f["unit"], "rows": f["violations"], "data_value": str(raw),
                                "summary": f"{f['violations']:,} {f['unit']} {f['entity']} rows already carry "
                                           f"{f['slot'].replace('_', ' ')} = {raw}"}
        return {"agrees": False, "summary": "No matching values in the data yet."}
    return {"agrees": None, "summary": "Not checkable against the data automatically."}


def _infer_unit(claim: Claim, units: list[str]) -> str | None:
    if claim.unit and str(claim.unit).upper() in units:
        return str(claim.unit).upper()
    hay = f"{claim.doc} {claim.title}".upper()
    for u in units:
        if re.search(rf"(^|[^A-Z]){re.escape(u)}([^A-Z]|$)", hay):
            return u
    return None


def _layer(claim: Claim) -> str:
    kind = (claim.doc_kind or "").lower()
    if claim.unit and any(k in f"{kind} {claim.title.lower()}" for k in UNIT_DOCS):
        return "unit"  # a source system's own spec changes that unit's mapping
    if claim.layer_hint in ("pack", "client", "unit"):
        if claim.layer_hint == "pack" and not any(k in kind for k in REGULATORY):
            return "client"  # only regulators and standards bodies change the industry pack
        return claim.layer_hint
    if any(k in kind for k in REGULATORY):
        return "pack"
    if claim.unit or any(k in kind for k in UNIT_DOCS):
        return "unit"
    return "client"


def _known(studio: Studio, client: str, ch: dict) -> bool:
    if ch.get("type") != "enum_value":
        return False
    sv = studio.client(client).resolved.view
    e = sv.all_enums().get(ch.get("enum") or "")
    return bool(e and str(ch.get("value", "")).lower() in {v.lower() for v in e.permissible_values})


def _open_duplicate(studio: Studio, client: str, ch: dict) -> dict | None:
    for r in studio.list(status="open", client=client):
        for op in r.get("ops", []):
            if op.get("op") == "add_enum_value" and op.get("enum") == ch.get("enum") \
                    and op.get("value") == str(ch.get("value", "")).lower():
                return r
    return None


def _title(ch: dict, vocab: dict) -> str:
    if ch.get("type") == "enum_value":
        used = vocab["used_by"].get(ch.get("enum"), [""])
        what = used[0].split(".")[-1].replace("_", " ") if used and used[0] else ch.get("enum", "value")
        return f"Add {ch.get('value_label') or ch.get('value')} as a {what}"
    return (ch.get("detail") or "Proposed change").rstrip(".")


def act(studio: Studio, client: str, claim: Claim, vocab: dict) -> dict:
    """Propose, ask or set aside. Returns what happened, for the scan report."""
    ch = claim.change or {}
    evidence = [{"quote": claim.quote, "doc": claim.doc, "title": claim.title, "page": claim.page}]
    if claim.kind in ("rule", "noise"):
        return {"action": "set_aside", "doc": claim.doc, "title": claim.title, "page": claim.page,
                "quote": claim.quote, "why": claim.why or ("a business rule" if claim.kind == "rule" else "no change asked")}
    if claim.kind == "conflict" or ch.get("type") != "enum_value" or not ch.get("enum") or not ch.get("value"):
        # Definitions, metrics, mappings and conflicts need a person's judgement: ask, don't draft.
        q = studio.ask(client=client, title=(ch.get("detail") or claim.why or "Question for owners").rstrip("."),
                       summary=f"{claim.why} The curator did not draft a change: an owner should decide.".strip(),
                       evidence=evidence, about={**ch, "layer": _layer(claim), "unit": claim.unit},
                       author=CURATOR)
        return {"action": "asked", "id": q["id"], "title": q["title"]}
    if _known(studio, client, ch):
        return {"action": "already_known", "doc": claim.doc, "page": claim.page,
                "why": f"{ch.get('value')} is already in {ch.get('enum')}"}
    proof = corroborate(studio, client, claim)
    if proof.get("data_value"):
        ch = {**ch, "value": str(proof["data_value"]).lower()}
    if _known(studio, client, ch):
        return {"action": "already_known", "doc": claim.doc, "page": claim.page,
                "why": f"{ch.get('value')} is already in {ch.get('enum')}"}
    dup = _open_duplicate(studio, client, ch)
    if dup:
        if claim.doc not in {e["doc"] for e in dup.get("evidence", [])}:
            studio.comment(dup["id"], "curator", f"Also supported by {claim.title}, p.{claim.page}: \"{claim.quote[:300]}\"",
                           kind="curator")
        return {"action": "already_proposed", "id": dup["id"], "title": dup["title"]}
    layer = _layer(claim)
    rule_doc = any(k in (claim.doc_kind or "").lower() for k in RULE_DOCS)
    if rule_doc and not proof.get("agrees"):
        return {"action": "set_aside", "doc": claim.doc, "title": claim.title, "page": claim.page,
                "quote": claim.quote, "why": "a rate or rule document, and the data does not show this value"}
    if proof.get("agrees") is False and claim.confidence != "high":
        q = studio.ask(client=client, title=_title(ch, vocab) + "?",
                       summary="One source asks for this, but the data does not show it yet. Should we add it now?",
                       evidence=evidence, about=ch, author=CURATOR)
        return {"action": "asked", "id": q["id"], "title": q["title"]}
    # When the data already carries a code for this, the value set must use that exact code.
    value = str(proof.get("data_value") or ch["value"]).lower()
    label = ch.get("value_label") or ch["value"]
    op = {"op": "add_enum_value", "enum": ch["enum"], "value": value,
          "description": f"{label}: {ch.get('detail') or claim.why}".strip(),
          "layer": "pack" if layer == "pack" else "client", "client": client}
    confidence = "high" if proof.get("agrees") else claim.confidence
    r = studio.create(client=client, title=_title(ch, vocab), author=CURATOR, ops=[op],
                      summary=f"{ch.get('detail') or claim.why} Applies at the {layer} level.",
                      kind="enum_value", evidence=evidence, data_proof=proof, confidence=confidence,
                      extra={"layer": layer})
    return {"action": "proposed", "id": r["id"], "title": r["title"]}


def scan(studio: Studio, client: str, docs: list[str] | None = None, complete=None,
         progress=None) -> dict:
    """Read the client's documents and act on every claim. `progress(msg)` gets live updates."""
    folder = corpus_dir(studio, client)
    if not folder.exists():
        return {"client": client, "documents": 0, "results": [], "note": "this client has no documents yet"}
    vocab = vocabulary(studio, client)
    names = docs or [d["doc"] for d in documents(studio, client)]
    cache = studio.s.home / "curator" / client
    say = progress or (lambda m: None)

    def read(doc: str) -> list[Claim]:
        if complete is None:
            return read_document_rules(folder, doc, vocab)
        try:
            return read_document(folder, doc, vocab, complete, cache)
        except Exception as exc:
            say(f"could not read {doc} with the LLM ({exc}); using rules")
            return read_document_rules(folder, doc, vocab)

    claims: list[Claim] = []
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        for doc, found in zip(names, pool.map(read, names)):
            say(f"read {doc}: {sum(1 for c in found if c.kind in ('change', 'conflict'))} candidate change(s)")
            claims += found
    for c in claims:
        kind = (c.doc_kind or "").lower()  # the document's type, not its title ("Actuarial Guideline 49-A memo")
        if any(k in kind for k in NOISE_DOCS):
            # News and marketing never define anything, conflicts included: a newsletter contradicting a
            # definition is not a reason to open a question for owners.
            c.kind, c.why = "noise", c.why or "news or marketing: not a source of definitions"
        elif any(k in kind for k in RULE_DOCS) and c.kind == "change" and c.change.get("type") != "enum_value":
            c.kind, c.why = "rule", c.why or "rates and rules are data, not the model"
    claims = [link(c, vocab) for c in claims]
    for c in claims:
        c.unit = _infer_unit(c, vocab["units"])
    results, set_aside = [], []
    drafts = [c for c in claims if c.kind == "change" and c.change.get("type") == "enum_value"
              and c.change.get("enum") and c.change.get("value")]
    asks = [c for c in claims if c.kind in ("change", "conflict") and c not in drafts and c.confidence != "low"]
    for c in drafts + [c for c in claims if c.kind in ("rule", "noise")]:
        res = act(studio, client, c, vocab)
        (set_aside if res["action"] == "set_aside" else results).append(res)
    # Related questions from one document go to owners as one question, not a flood.
    by_doc: dict[str, list[Claim]] = {}
    for c in asks:
        by_doc.setdefault(c.doc, []).append(c)
    drafted = {}  # doc -> proposal id drafted from it in this scan
    for c, res in zip(drafts, results[:len(drafts)]):
        if res.get("id") and res["action"] in ("proposed", "already_proposed"):
            drafted.setdefault(c.doc, res["id"])
    for doc, group in list(by_doc.items()):
        # Follow-ups from a document that already produced a proposal (e.g. "recode open claims")
        # belong on that proposal, not in a separate question. Conflicts still go to owners.
        if doc in drafted and not any(c.kind == "conflict" for c in group):
            notes = "\n".join(f"- p.{c.page}: {(c.change.get('detail') or c.why).rstrip('.')}." for c in group)
            studio.comment(drafted[doc], "curator", f"Also in {group[0].title}:\n{notes}", kind="curator")
            results.append({"action": "noted_on_proposal", "id": drafted[doc], "doc": doc,
                            "title": f"{len(group)} related point(s) added to the proposal"})
            del by_doc[doc]
    for doc, group in by_doc.items():
        lead = max(group, key=lambda c: (c.kind == "conflict", {"high": 2, "medium": 1}.get(c.confidence, 0),
                                         c.change.get("type") in ("mapping", "metric")))
        detail = (lead.change.get("detail") or lead.why or "").rstrip(".")
        topic = str(lead.change.get("topic") or "").strip().rstrip(".")
        title = (topic[:1].upper() + topic[1:] + f" ({lead.title.split(',')[0].replace(' (fictional)', '')})"
                 if topic else detail or "Question for owners")
        layer = _layer(lead)
        q = studio.ask(client=client, title=title,
                       summary=(f"{detail}. " if detail else "") +
                               " ".join(dict.fromkeys(c.why for c in group if c.why and c.why not in detail)) +
                               " The curator did not draft a change: an owner should decide.",
                       evidence=[{"quote": c.quote, "doc": c.doc, "title": c.title, "page": c.page} for c in group],
                       about={**lead.change, "layer": layer, "unit": lead.unit,
                              "conflict": any(c.kind == "conflict" for c in group)}, author=CURATOR)
        results.append({"action": "asked", "id": q["id"], "title": q["title"], "doc": doc})
    for res in results:
        say(f"{res['action'].replace('_', ' ')}: {res.get('title') or res.get('why')}")
    report = {"client": client, "documents": len(names), "claims": len(claims), "results": results,
              "set_aside": set_aside, "llm": complete is not None}
    out = studio.s.home / "curator" / client / "last_scan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def last_scan(studio: Studio, client: str) -> dict | None:
    path = studio.s.home / "curator" / client / "last_scan.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def evaluate(studio: Studio, client: str, report: dict) -> dict:
    """Score a scan against the client's answer key: needles found, decoys set aside."""
    key_path = corpus_dir(studio, client) / "answer_key.yaml"
    if not key_path.exists():
        return {}
    key = yaml.safe_load(key_path.read_text(encoding="utf-8"))
    acted = {(r.get("doc"), r.get("page")) for r in report.get("results", [])}
    for r in report.get("results", []):
        if r.get("id"):
            try:
                rec = studio.get(r["id"])
                for e in rec.get("evidence", []):
                    acted.add((e["doc"], e.get("page")))
            except KeyError:
                pass
    aside = {(r["doc"], r.get("page")) for r in report.get("set_aside", [])}
    needles = key.get("needles", [])
    found = [n for n in needles if (n.get("doc"), n.get("page")) in acted]
    decoys = key.get("decoys", [])
    wrongly = [d for d in decoys if any(doc == d.get("doc") for doc, _ in acted)]
    return {"needles": len(needles), "found": len(found), "decoys": len(decoys),
            "decoys_acted_on": len(wrongly), "set_aside": len(aside)}
