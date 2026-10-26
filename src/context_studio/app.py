"""Context Studio API.

Run: `ctx-studio serve` (or `uvicorn context_studio.app:app`). The web app calls these
endpoints; every write goes through a proposal. Who is calling comes from the
`X-Studio-User` header (a person id from owners.yaml; SSO replaces it in production).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import bootstrap

SETTINGS = bootstrap()  # must run before the engine is imported anywhere

from fastapi import Body, FastAPI, Header, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402

from . import curator, warehouse  # noqa: E402
from .chat import StudioChat  # noqa: E402
from .edits import EditError  # noqa: E402
from .proposals import OPEN, ProposalError, Studio, fmt  # noqa: E402

app = FastAPI(title="Context Studio", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STUDIO = Studio(SETTINGS)
CHAT = StudioChat(STUDIO)
for _r in STUDIO.list(status="checking"):  # checks interrupted by a restart run again
    STUDIO.check_async(_r["id"])


def _user(x_studio_user: str | None) -> str:
    uid = (x_studio_user or "").strip()
    if uid and uid not in STUDIO.owners().people:
        raise HTTPException(403, f"unknown person {uid}")
    return uid


def _need_user(x_studio_user: str | None) -> str:
    uid = _user(x_studio_user)
    if not uid:
        raise HTTPException(401, "sign in as a person to make changes")
    return uid


def _client(client: str | None) -> str:
    name = client or SETTINGS.default_client
    if not (SETTINGS.main / "clients" / name / "client.yaml").exists():
        raise HTTPException(404, f"unknown client {name}")
    return name


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, f"not found: {exc}") from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ProposalError, EditError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _summary(r: dict) -> dict:
    checks = r.get("checks") or {}
    return {k: r.get(k) for k in ("id", "title", "summary", "kind", "client", "status", "areas", "area_titles",
                                  "reviewers", "author", "confidence", "created_at", "updated_at", "approvals",
                                  "layer", "merged_sha")} | {
        "checks": {"running": checks.get("running"), "passed": checks.get("passed")},
        "evidence_count": len(r.get("evidence") or []),
        "changed": (r.get("impact") or {}).get("changed"),
    }


# ------------------------------------------------------------------ who and where

@app.get("/api/health")
def health():
    return {"ok": True, "head": STUDIO.head()[:10], "git": SETTINGS.git_backend}


@app.get("/api/people")
def people():
    o = STUDIO.owners()
    return {"people": [{"id": p.id, "name": p.name, "role": p.role,
                        "areas": [a.id for a in o.areas_of(p.id)]} for p in o.people.values()],
            "areas": [{"id": a.id, "title": a.title, "owners": a.owners, "paths": a.paths}
                      for a in o.areas.values()]}


@app.get("/api/clients")
def clients():
    return {"clients": STUDIO.clients(), "default": SETTINGS.default_client}


# ------------------------------------------------------------------ inbox + proposals

@app.get("/api/inbox")
def inbox(client: str | None = None, view: str = "mine", x_studio_user: str | None = Header(None)):
    uid = _user(x_studio_user)
    name = _client(client)
    records = STUDIO.list(client=name)
    counts = {
        "mine": sum(1 for r in records if uid and STUDIO.waiting_on(r, uid)),
        "open": sum(1 for r in records if r["status"] in OPEN and r["status"] != "question"),
        "questions": sum(1 for r in records if r["status"] == "question"),
        "done": sum(1 for r in records if r["status"] in ("merged", "closed", "answered")),
    }
    if view == "mine":
        records = [r for r in records if uid and STUDIO.waiting_on(r, uid)]
    elif view == "open":
        records = [r for r in records if r["status"] in OPEN and r["status"] != "question"]
    elif view == "questions":
        records = [r for r in records if r["status"] == "question"]
    elif view == "done":
        records = [r for r in records if r["status"] in ("merged", "closed", "answered")]
    return {"counts": counts, "items": [_summary(r) for r in records]}


@app.get("/api/proposals/{pid}")
def proposal(pid: str, x_studio_user: str | None = Header(None)):
    uid = _user(x_studio_user)
    r = _guard(STUDIO.get, pid)
    sat = STUDIO._satisfied(r)
    r["waiting_areas"] = [a for a in (r.get("reviewers") or {}) if a not in sat]
    r["can_approve"] = bool(uid) and r["status"] in ("in_review", "approved") and any(
        uid in STUDIO.owners().areas[a].owners for a in r["waiting_areas"] if a in STUDIO.owners().areas)
    r["can_review"] = bool(uid) and any(uid in o for o in (r.get("reviewers") or {}).values())
    return r


@app.post("/api/proposals")
def create_proposal(body: dict = Body(...), x_studio_user: str | None = Header(None)):
    uid = _need_user(x_studio_user)
    name = _client(body.get("client"))
    person = STUDIO.owners().person(uid)
    ops = [{**op, "client": op.get("client", name)} if op.get("op") != "set_metric" else op
           for op in body.get("ops", [])]
    return _guard(STUDIO.create, client=name, title=body["title"], summary=body.get("summary", ""), ops=ops,
                  author={"id": uid, "name": person.name, "kind": "person"}, kind=body.get("kind", "change"),
                  evidence=body.get("evidence"))


@app.post("/api/proposals/{pid}/approve")
def approve(pid: str, x_studio_user: str | None = Header(None)):
    return _guard(STUDIO.approve, pid, _need_user(x_studio_user))


@app.post("/api/proposals/{pid}/request-changes")
def request_changes(pid: str, body: dict = Body(...), x_studio_user: str | None = Header(None)):
    return _guard(STUDIO.comment, pid, _need_user(x_studio_user), body.get("text", ""), "request_changes")


@app.post("/api/proposals/{pid}/comments")
def comment(pid: str, body: dict = Body(...), x_studio_user: str | None = Header(None)):
    return _guard(STUDIO.comment, pid, _need_user(x_studio_user), body.get("text", ""))


@app.post("/api/proposals/{pid}/answer")
def answer(pid: str, body: dict = Body(...), x_studio_user: str | None = Header(None)):
    return _guard(STUDIO.answer, pid, _need_user(x_studio_user), body.get("text", ""))


@app.post("/api/proposals/{pid}/close")
def close(pid: str, body: dict = Body(default={}), x_studio_user: str | None = Header(None)):
    return _guard(STUDIO.close, pid, _need_user(x_studio_user), body.get("reason", ""))


@app.post("/api/proposals/{pid}/recheck")
def recheck(pid: str, x_studio_user: str | None = Header(None)):
    _need_user(x_studio_user)
    r = _guard(STUDIO.get, pid)
    r["checks"] = {"running": True}
    if r["status"] in ("in_review", "checks_failed"):
        r["status"] = "checking"
    STUDIO.reviews.save(r)
    STUDIO.check_async(pid)
    return r


# ------------------------------------------------------------------ the model

def _pending_by_concept(name: str) -> dict[str, list[dict]]:
    sv = STUDIO.client(name).resolved.view
    out: dict[str, list[dict]] = {}
    for r in STUDIO.list(status="open", client=name):
        targets = set()
        for op in r.get("ops", []):
            if op.get("entity"):
                targets.add(op["entity"])
            if op.get("concept"):
                targets.add(op["concept"])
            if op.get("op") == "set_label" and op.get("kind") == "classes":
                targets.add(op["name"])
            if op.get("op") == "add_enum_value":
                slots = {s for s, sl in sv.all_slots().items() if sl.range == op["enum"]}
                targets |= {c for c in sv.all_classes()
                            if slots & {s.name for s in sv.class_induced_slots(c)} and not sv.get_class(c).abstract}
        about = r.get("about") or {}
        if about.get("concept"):
            targets.add(about["concept"])
        for t in targets:
            out.setdefault(t, []).append({"id": r["id"], "title": r["title"], "status": r["status"]})
    return out


@app.get("/api/map")
def ontology_map(client: str | None = None):
    from insurance_context.catalog import ontology_model
    name = _client(client)

    def build():
        return ontology_model(STUDIO.client(name))
    model = STUDIO._cached(f"model:{name}", build)
    return {**model, "pending": _pending_by_concept(name)}


def _overlay_summary(ov: dict | None) -> dict:
    """What one overlay (client or unit) changes, in business terms."""
    ov = ov or {}
    ext = ov.get("extend") or {}
    labels = [{"kind": "concept" if k == "classes" else "field", "name": n, "label": v}
              for k in ("classes", "slots") for n, v in ((ov.get("labels") or {}).get(k) or {}).items()]
    added = [{"concept": c, "field": f, "description": (spec or {}).get("description", "")}
             for c, fields in (ext.get("add_slots") or {}).items() for f, spec in (fields or {}).items()]
    return {
        "labels": labels, "added_fields": added,
        "new_value_sets": [{"name": n, "values": v} for n, v in (ext.get("enums") or {}).items()],
        "added_values": [{"value_set": n, "values": v} for n, v in (ext.get("enum_values") or {}).items()],
        "restrictions": [{"concept": r.get("class"), "field": r.get("slot"), "allow": r.get("allow"),
                          "reason": r.get("reason", "")} for r in ov.get("restrict") or []],
        "required": [{"concept": r.get("class"), "field": r.get("slot"), "reason": r.get("reason", "")}
                     for r in ov.get("require") or []],
        "hidden": list(ov.get("hide") or []),
        "shared_identity": list(ov.get("global_identity") or []),
        "metric_overrides": [{"name": n, **{k: v for k, v in (m or {}).items() if k in ("label", "description")}}
                             for n, m in (ov.get("metrics") or {}).items()],
    }


@app.get("/api/layers")
def layers(client: str | None = None):
    """The four layers a client's ontology is built from, and what each one adds."""
    from insurance_context.overlay import resolve
    name = _client(client)
    c = STUDIO.client(name)
    base = resolve(c.line).view
    model = ontology_map(name)
    by_layer: dict[str, list[str]] = {}
    for k in model["classes"]:
        by_layer.setdefault(k["layer"], []).append(k["name"])
    import yaml as _y
    pack_file = SETTINGS.main / "ontology" / "lines" / f"{c.line}.yaml"
    pack = _y.safe_load(pack_file.read_text()) if pack_file.exists() else {}
    return {
        "kernel": {"title": "Core kernel", "concepts": sorted(by_layer.get("core", [])),
                   "says": "What every insurance line shares: party, product, coverage, claim, decision."},
        "pack": {"title": pack.get("title", c.line), "version": pack.get("version"),
                 "concepts": sorted(by_layer.get("line", [])),
                 "value_sets": len([e for e in model["enums"] if e["layer"] == "line"]),
                 "metrics": len([m for m in model["metrics"] if m.get("source") == "pack"]),
                 "says": "What this line of business means across the industry."},
        "client": {"title": c.title, "version": str(c.config.get("version", "")),
                   **_overlay_summary(c.config),
                   "says": "This company's words and rules, as a reviewable list of differences."},
        "units": [{"code": u.code, "name": u.name, "source_system": u.source_system,
                   "mapped": sorted((u.mappings.get("classes") or {}).keys()),
                   **_overlay_summary(u.overlay)} for u in c.units],
        "base_concepts": len(base.all_classes()),
    }


def _okf_definition(name: str, concept: str) -> dict | None:
    path = SETTINGS.main / "clients" / name / "okf" / "entities" / f"{concept}.md"
    if not path.exists():
        return None
    raw = path.read_text()
    _, head, body = raw.split("---", 2)
    m = re.search(r"## Business definition\s+(.+?)(\n## |\Z)", body, flags=re.S)
    import yaml
    meta = yaml.safe_load(head) or {}
    return {"text": m.group(1).strip() if m else body.strip(), "updated": str(meta.get("updated", "")),
            "verified": meta.get("verified")}


@app.get("/api/concepts")
def concepts(client: str | None = None):
    model = ontology_map(client)
    return {"concepts": [{k: c[k] for k in ("name", "label", "group", "layer", "description", "abstract",
                                           "customized", "metrics")} | {"pending": len(model["pending"].get(c["name"], []))}
                         for c in model["classes"]]}


@app.get("/api/concepts/{concept}")
def concept(concept: str, client: str | None = None):
    name = _client(client)
    model = ontology_map(name)
    c = next((x for x in model["classes"] if x["name"] == concept), None)
    if c is None:
        raise HTTPException(404, f"unknown concept {concept}")
    enums = {e["name"]: e for e in model["enums"]}
    for a in c["attributes"]:
        if a["range"] in enums:
            a["values"] = [v["value"] for v in enums[a["range"]]["values"]]
    rels = [r for r in model["relationships"] if r["from"] == concept or r["to"] == concept]
    owners = STUDIO.owners()
    probe = "ontology/lines/x.yaml" if c["layer"] in ("core", "line") else f"clients/{name}/client.yaml"
    vocab_owners = [p for a in owners.areas_for([f"clients/{name}/client.yaml"], {}, []) for p in a.owners]
    return {**c, "relationships": rels, "definition": _okf_definition(name, concept),
            "pending": model["pending"].get(concept, []),
            "owners": sorted({p for a in owners.areas_for([probe], {}, []) for p in a.owners}),
            "definition_owners": sorted(set(vocab_owners))}


@app.get("/api/metrics")
def metrics(client: str | None = None):
    name = _client(client)
    sl = STUDIO.semantic(name)
    return {"metrics": sl.catalog(), "dimensions": [{"name": d.name, "label": d.label, "entities": sorted(d.paths)}
                                                    for d in sl.dimensions.values()]}


def _values(name: str, metric_names: list[str], by: list[str]) -> dict | None:
    return STUDIO._main_values(name, metric_names, by, warehouse.valuation(name))


@app.get("/api/metrics/{metric}")
def metric(metric: str, client: str | None = None, by: str | None = None):
    name = _client(client)
    sl = STUDIO.semantic(name)
    if metric not in sl.metrics:
        raise HTTPException(404, f"unknown metric {metric}")
    m = sl.metrics[metric]
    dims = sorted(sl.available_dimensions([metric]))
    group = [by] if by else [d for d in ("operating_unit_name", "operating_unit", "line_of_business", "unit") if d in dims][:1]
    values = _values(name, [metric], group)
    leaves = [sl.metrics[x].definition() for x in sl._leaves(metric) if x != metric]
    pending = [{"id": r["id"], "title": r["title"], "status": r["status"]} for r in STUDIO.list(status="open", client=name)
               if any(op.get("metric") == metric for op in r.get("ops", []))]
    return {"definition": m.definition(), "dimensions": dims, "by": group, "values": values, "parts": leaves,
            "as_of": str(warehouse.valuation(name) or ""), "pending": pending,
            "formatted": [[fmt(v, m.format) if i == len(group) else v for i, v in enumerate(row)]
                          for row in (values or {}).get("rows", [])]}


@app.post("/api/preview")
def preview(body: dict = Body(...)):
    """Numbers before and after a draft, without saving anything (a scratch copy of main)."""
    name = _client(body.get("client"))
    from . import edits
    ops = [{**op, "client": op.get("client", name)} if op.get("op") != "set_metric" else op for op in body["ops"]]
    metric_names, by = body.get("metrics", []), body.get("by", [])
    as_of = warehouse.valuation(name)
    raw = warehouse.raw_path(SETTINGS.warehouses, name)
    with tempfile.TemporaryDirectory(dir=SETTINGS.home) as tmp:
        root = Path(tmp) / "content"
        for part in ("ontology", "clients"):
            shutil.copytree(SETTINGS.main / part, root / part, ignore=shutil.ignore_patterns("corpus", "okf"))
        try:
            edits.apply(root, ops)
        except EditError as exc:
            raise HTTPException(400, str(exc)) from exc
        job = {"client": name, "raw": str(raw) if raw else None, "scratch": str(Path(tmp) / "p.duckdb"),
               "metrics": metric_names, "by": by, "as_of": as_of.isoformat() if as_of else None}
        import os
        proc = subprocess.run([sys.executable, "-m", "context_studio.engine_job"], input=json.dumps(job),
                              capture_output=True, text=True, env={**os.environ, "ICTX_CONTENT_DIR": str(root)},
                              timeout=600)
    after = json.loads(proc.stdout or "{}")
    before = _values(name, metric_names, by) if after.get("values") else None
    impact = STUDIO._impact(name, metric_names, by, as_of, before, after.get("values"))
    return {"checks": after.get("items", []), "impact": impact}


@app.get("/api/mappings")
def mappings(client: str | None = None):
    name = _client(client)
    c = STUDIO.client(name)
    return {"units": [{"code": u.code, "name": u.name, "source_system": u.source_system,
                       "entities": [{"entity": e, "table": spec.get("table"),
                                     "slots": len(spec.get("slots") or {})}
                                    for e, spec in (u.mappings.get("classes") or {}).items()]}
                      for u in c.units]}


@app.get("/api/mappings/{unit}/{entity}")
def mapping(unit: str, entity: str, client: str | None = None):
    import duckdb
    name = _client(client)
    c = STUDIO.client(name)
    try:
        u = c.unit(unit)
    except Exception as exc:
        raise HTTPException(404, str(exc)) from exc
    spec = (u.mappings.get("classes") or {}).get(entity)
    if spec is None:
        raise HTTPException(404, f"{entity} is not mapped for {unit}")
    sv = c.resolved.view
    from insurance_context.schema_utils import annotation
    sensitive = {s.name for s in sv.class_induced_slots(entity)
                 if annotation(s, "sensitivity")} if entity in sv.all_classes() else set()
    slots = []
    for slot, m in (spec.get("slots") or {}).items():
        m = m if isinstance(m, dict) else {"column": m}
        slots.append({"slot": slot, "column": m.get("column"), "expr": m.get("expr"), "note": m.get("note"),
                      "sensitive": slot in sensitive})
    preview_rows, columns = [], []
    path = STUDIO.served_warehouse(name)
    from insurance_context.schema_utils import table_name
    view = f"{u.schema_name}.{table_name(entity)}"
    if path:
        con = duckdb.connect(str(path), read_only=True)
        try:
            cur = con.execute(f"SELECT * FROM {view} LIMIT 8")
            columns = [d[0] for d in cur.description]
            from .engine_job import plain
            preview_rows = [["[hidden]" if col in sensitive else plain(v) for col, v in zip(columns, r)]
                            for r in cur.fetchall()]
        except Exception:
            pass
        finally:
            con.close()
    findings = [f for f in STUDIO._main_findings(name) if f["unit"] == u.code and f["entity"] == entity]
    pending = [{"id": r["id"], "title": r["title"], "status": r["status"]} for r in STUDIO.list(status="open", client=name)
               if any(op.get("entity") == entity and str(op.get("unit", "")).upper() == u.code for op in r.get("ops", []))
               or ((r.get("about") or {}).get("unit") == u.code)]
    return {"unit": {"code": u.code, "name": u.name, "source_system": u.source_system},
            "entity": entity, "label": sv.get_class(entity).title or entity if entity in sv.all_classes() else entity,
            "table": spec.get("table"), "query": spec.get("query"), "note": spec.get("note"),
            "source_schema": u.mappings.get("source_schema"), "view": view,
            "slots": slots, "preview": {"columns": columns, "rows": preview_rows}, "findings": findings,
            "pending": pending}


# ------------------------------------------------------------------ history

@app.get("/api/history")
def history(client: str | None = None, limit: int = 100):
    rows = STUDIO.history(limit)
    if client:
        rows = [h for h in rows if h["client"] in (None, client)]
    return {"items": rows}


@app.post("/api/history/{sha}/undo")
def undo(sha: str, x_studio_user: str | None = Header(None)):
    return _guard(STUDIO.revert, sha, _need_user(x_studio_user))


# ------------------------------------------------------------------ documents + curator

@app.get("/api/documents")
def docs(client: str | None = None):
    name = _client(client)
    return {"documents": curator.documents(STUDIO, name), "last_scan": curator.last_scan(STUDIO, name)}


@app.get("/api/documents/{doc}")
def document(doc: str, client: str | None = None):
    name = _client(client)
    path = curator.corpus_dir(STUDIO, name) / doc
    if not re.fullmatch(r"[\w.-]+\.md", doc) or not path.exists():
        raise HTTPException(404, "unknown document")
    passages = [p for p in curator.load_corpus(path.parent) if p.doc == doc]
    pages: dict[int, list[str]] = {}
    for p in passages:
        pages.setdefault(p.page, []).append(p.text)
    meta = next((d for d in curator.documents(STUDIO, name) if d["doc"] == doc), {})
    return {**meta, "pages": [{"page": k, "paragraphs": v} for k, v in sorted(pages.items())]}


@app.post("/api/curator/scan")
async def scan(body: dict = Body(default={}), x_studio_user: str | None = Header(None)):
    _need_user(x_studio_user)
    name = _client(body.get("client"))
    from insurance_context import llm
    use_llm = body.get("llm", True) and llm.configured()
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def progress(msg: str):
        loop.call_soon_threadsafe(queue.put_nowait, {"type": "progress", "data": {"message": msg}})

    async def run():
        try:
            rep = await asyncio.to_thread(curator.scan, STUDIO, name, body.get("documents"),
                                          llm.complete if use_llm else None, progress)
            rep["evaluation"] = curator.evaluate(STUDIO, name, rep)
            await queue.put({"type": "result", "data": rep})
        except Exception as exc:
            await queue.put({"type": "error", "data": {"message": str(exc)}})
        await queue.put(None)

    async def gen():
        task = asyncio.create_task(run())
        while True:
            item = await queue.get()
            if item is None:
                break
            yield json.dumps(item, default=str) + "\n"
        await task

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ------------------------------------------------------------------ consistency lab

@app.post("/api/consistency")
async def consistency(body: dict = Body(...)):
    from .consistency import Lab
    name = _client(body.get("client"))
    n = max(1, min(int(body.get("n", 5)), 10))

    async def gen():
        async for item in Lab(STUDIO).run(name, body["question"], n):
            yield json.dumps(item, default=str) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# ------------------------------------------------------------------ chat

@app.post("/api/chat")
async def chat(body: dict = Body(...), x_studio_user: str | None = Header(None)):
    uid = _user(x_studio_user) or "guest"
    name = _client(body.get("client"))

    async def gen():
        async for line in CHAT.stream(body["message"], body.get("session_id") or "default", name, uid,
                                      body.get("screen"), body.get("mode") or "studio"):
            yield line

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/chat/sessions")
def chat_sessions(client: str | None = None, x_studio_user: str | None = Header(None)):
    return {"sessions": CHAT.transcripts.list(_client(client), _user(x_studio_user) or "guest")}


@app.get("/api/chat/sessions/{session_id}")
def chat_session(session_id: str, client: str | None = None, x_studio_user: str | None = Header(None)):
    d = _guard(CHAT.transcripts.get, _client(client), session_id)
    if d.get("user") != (_user(x_studio_user) or "guest"):
        raise HTTPException(403, "this conversation belongs to someone else")
    return d


@app.delete("/api/chat/sessions/{session_id}")
def delete_chat_session(session_id: str, client: str | None = None, x_studio_user: str | None = Header(None)):
    _guard(CHAT.transcripts.delete, _client(client), session_id, _user(x_studio_user) or "guest")
    return {"deleted": session_id}
