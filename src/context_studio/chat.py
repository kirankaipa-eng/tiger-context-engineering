"""Self-service chat and the curator, on the LangGraph agent runtime.

One Orchestrator per client. Its tools come in three groups:

    engine     governed metrics, concepts, data quality, read-only SQL (ContextToolkit)
    studio     proposals, questions, documents, history; propose / revise / comment / approve
    canvas     tools that stream a visual to the screen (metrics chart, proposal, trace, scan)

Who is asking travels in a context variable, so tools act as that person: an approval
is recorded under their name, and only for areas they own. The curator never approves
on its own; `approve_proposal` runs only when the signed-in owner says so.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
from datetime import date
from typing import AsyncIterator, Awaitable, Callable

from .engine_job import plain
from .proposals import CURATOR, Studio, fmt

from insurance_context.agent_runtime import RunContext

CURRENT_USER: contextvars.ContextVar[str] = contextvars.ContextVar("studio_user", default="")
SCREEN: contextvars.ContextVar[dict] = contextvars.ContextVar("studio_screen", default={})


def _event(type_: str, data: dict) -> str:
    return json.dumps({"type": type_, "data": data}, default=str) + "\n"


def _person(studio: Studio) -> dict:
    pid = CURRENT_USER.get() or "guest"
    p = studio.owners().person(pid)
    return {"id": pid, "name": p.name, "kind": "person"}


def _brief(r: dict) -> str:
    waiting = ", ".join(sorted({o for os_ in (r.get("reviewers") or {}).values() for o in os_}))
    checks = r.get("checks") or {}
    state = "running" if checks.get("running") else ("pass" if checks.get("passed") else "fail" if checks else "n/a")
    return (f"- {r['id']} [{r['status']}] {r['title']} (areas: {', '.join(r.get('areas', []))}; "
            f"owners: {waiting}; checks: {state})")


SYSTEM = """You are the Curator in Context Studio for {title} ({line}).
Context Studio is where people keep the company's ontology, metrics and source mappings correct.
Behind the scenes everything is stored in git, but NEVER mention git, branches, commits or merge
requests to users: say "proposal", "approve", "history", "undo".

The person you are talking to: {person} ({role}). Areas they own: {areas}.
What they are looking at right now: {screen}

You do two jobs.
1. Answer questions about the business with governed numbers. For any KPI call compute_metrics or,
   when a chart helps, show_metrics. Leave as_of empty unless a date is named. Never re-derive a
   governed metric in SQL. Name the definition and the valuation date you used. Use the client's
   own words for concepts. Use run_sql only for record-level detail, over ont_agent.* views.
   When someone asks how a number was built, call show_trace.
2. Keep the ontology current. You can search and read the company's documents (search_documents,
   scan_documents), explain proposals (show_proposal), draft proposals (propose_change), revise a
   proposal after an owner asks for changes (revise_proposal), and record comments.
   Every change you draft is a proposal that its owners approve; you never decide for them.
   Pick the right kind of change: when a document says how something should be classified or what a
   field holds (e.g. "observation stays are outpatient"), change the MAPPING (set_mapping), so every
   metric built on that field follows; call get_mapping first and reuse the source columns it shows.
   Change a metric (set_metric) only when the formula itself changes; to add one, read a similar metric
   with get_metric and follow its shape exactly.
   After drafting, read the proposal back (get_proposal) and compare its impact with what the change is
   meant to do. If a watched number did not move as intended (e.g. a category that should gain claims
   stays at zero), fix it with revise_proposal before telling the person it is ready. Name the governed numbers the
   change should move in watch_metrics so owners see before and after.
   Record an approval (approve_proposal) ONLY when the signed-in person explicitly tells you to
   approve that proposal; never approve on your own initiative.
   One weak source is not enough: if documents and data disagree, ask the owners instead of drafting.

Never mention your tool names or ids of internal functions; offer actions in plain words
("I can show you the evidence", "I can draft that change").
Style: plain business language, short. Lead with the answer, then the numbers, then caveats.
When you show a canvas, do not repeat its whole table in text: summarize what it shows."""


INSIGHTS_SYSTEM = """You are the analyst on the medical economics desk of {title}. Business people (finance,
medical economics, payment integrity) ask you questions in plain language. You answer with EXHIBITS
on their canvas, computed from the company's governed definitions, and a short narration.

The person: {person}. Valuation date of the data: {valuation}. The latest complete quarter is {latest};
later quarters are still receiving claims and are not comparable yet. Asked to explain a change into or out
of one, answer in words that it is not comparable yet and offer the latest complete comparison; show no exhibit.
"Last quarter" means {latest}; "the quarter before" means the one before it.

How to answer:
- Every answer with new numbers shows an exhibit; never show again an exhibit already on the canvas (refer to
  it instead). Pick the one that fits the question:
  show_trend      "how is X trending", "X over time"
  explain_change  "why did X go up / down / change", "what drove X". Pass the two quarters. Let it pick
                  the split unless the person names one; it also separates utilization from unit cost.
  show_breakdown  "X by Y", "which Y", "where is X highest", and every drill-down
  find_outliers   "unusual / outlier providers", payment-integrity reviews (peer comparison)
  show_evidence   "how is X defined", "does X include ...", "can I trust this", "why is it different from ..."
  record_gap      when the metric the person names is not in the governed catalog. A named metric is a
                  definition its owners decide: do not stand in a filtered version of another metric (for
                  example "behavioral health PMPM" is not medical PMPM filtered to mental health until owners
                  define it). Never invent a metric, never estimate.
                  Its detail is one or two sentences: what they want measured and what is missing.
- Map the person's words to governed metric names and dimension values from the lists below. Filters
  take dimension names as keys and the person's words as values ("Medicare Advantage", "Illinois").
- Keep the conversation's scope for follow-ups on the same metric: "drill into endocrine" or "what about
  Texas" keeps the earlier filters and quarter and changes only what was named. A question about a
  different metric or topic starts fresh, with only the filters it names.
- Payment-integrity coding reviews (modifier 25 or 59): compare provider against peer_cohort, with
  volume_metric coded_professional_claims, min_volume 10 and min_z 1.645, over the two quarters named.
- Messages that start with "Drill:" come from the person clicking on an exhibit; do exactly what they say.
- Narration: 2 or 3 short sentences, under 60 words, never a list, plain prose with no labels or headings (never start
  with "Exhibit:", "Headline:" or "Narrative:"). Lead with the answer and its key numbers, in the business's
  words; the exhibit carries the detail. End with one useful next question, phrased as a question.
- When explaining a change in cost, split it by clinical_category unless the person names another split.
- Never mention tools, SQL, git or internal names. Never show code values like medicare_advantage; say
  Medicare Advantage.

{catalog}"""


class Transcripts:
    """What each conversation said and showed, so people can reopen it (canvases included)."""

    def __init__(self, folder):
        self.folder = folder

    def _path(self, client: str, session_id: str):
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            raise KeyError(session_id)
        return self.folder / client / f"{session_id}.json"

    def append(self, client: str, session_id: str, user: str, turn: dict) -> None:
        from datetime import datetime, timezone
        path = self._path(client, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "id": session_id, "user": user, "title": turn["user"][:80], "turns": []}
        turn["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data["turns"].append(turn)
        data["updated_at"] = turn["at"]
        path.write_text(json.dumps(data, default=str), encoding="utf-8")

    def list(self, client: str, user: str) -> list[dict]:
        folder = self.folder / client
        out = []
        for p in folder.glob("*.json") if folder.exists() else []:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("user") == user:
                out.append({"id": d["id"], "title": d["title"], "turns": len(d["turns"]),
                            "updated_at": d.get("updated_at")})
        return sorted(out, key=lambda x: x.get("updated_at") or "", reverse=True)

    def get(self, client: str, session_id: str) -> dict:
        path = self._path(client, session_id)
        if not path.exists():
            raise KeyError(session_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def delete(self, client: str, session_id: str, user: str) -> None:
        d = self.get(client, session_id)
        if d.get("user") != user:
            raise PermissionError("only the person who had this conversation can delete it")
        self._path(client, session_id).unlink()


class StudioChat:
    def __init__(self, studio: Studio):
        self.studio = studio
        self._orchs: dict[str, object] = {}
        self.transcripts = Transcripts(studio.s.agent_dir / "transcripts")

    # ------------------------------------------------------------ tools

    def _engine_kit(self, client: str):
        from insurance_context.agent_tools import ContextToolkit
        from . import warehouse
        path = self.studio.served_warehouse(client)
        if path is None:
            return None
        return ContextToolkit(self.studio.client(client), path, as_of=warehouse.valuation(client))

    def _studio_tools(self, client: str) -> list:
        studio = self.studio

        async def list_proposals(ctx: RunContext, status: str = "open", waiting_on_me: bool = False) -> str:
            """List proposals and questions. status: open | merged | closed | question | all.
            waiting_on_me=true lists only those waiting on the signed-in person."""
            person = CURRENT_USER.get() if waiting_on_me else None
            rows = studio.list(status=None if status == "all" else status, person=person, client=client)
            return "\n".join(_brief(r) for r in rows[:40]) or "Nothing matches."

        async def get_proposal(ctx: RunContext, proposal_id: str) -> str:
            """Full detail of one proposal or question: evidence, data proof, checks, impact, comments."""
            r = studio.get(proposal_id)
            keep = {k: r.get(k) for k in ("id", "title", "summary", "status", "kind", "areas", "reviewers",
                                          "evidence", "data_proof", "set_aside", "confidence", "approvals",
                                          "comments", "about", "layer")}
            checks = r.get("checks") or {}
            keep["checks"] = [f"{i['label']}: {'ok' if i['ok'] else 'FAILED ' + i.get('detail', '')}"
                              for i in checks.get("items", [])]
            keep["findings_cleared"] = [f"{f['unit']} {f['entity']}.{f['slot']} {f['check']}"
                                        for f in checks.get("findings_cleared", [])]
            impact = r.get("impact") or {}
            keep["impact"] = [f"{x['label']} {'/'.join(x['group'])}: {x['before_fmt']} -> {x['after_fmt']}"
                              for x in impact.get("rows", []) if x.get("changed")][:20] or "no governed number changes"
            keep["changed_files"] = [f["path"] for f in (r.get("diff") or {}).get("files", [])]
            return json.dumps(keep, default=str)[:12000]

        async def propose_change(ctx: RunContext, title: str, summary: str, operations: list[dict],
                                 evidence: list[dict] | None = None, watch_metrics: list[str] | None = None) -> str:
            """Draft a proposal from the conversation, as the signed-in person. `operations` is a list of
            edits, each one of:
              {"op":"add_enum_value","enum":"...","value":"snake_case","description":"...","layer":"pack"|"client"}
              {"op":"set_label","kind":"classes"|"slots","name":"CanonicalName","label":"Client words"}
              {"op":"set_definition","concept":"CanonicalName","text":"what it means here","examples":[...]}
              {"op":"set_metric","metric":"name","fields":{...},
               "client": true for a client override (default) or false for the industry pack}
               fields for a base metric: {"label","description","entity":"Concept","measure":"sum({field})" or
               "count(DISTINCT {id})","filter":"{field} = 'value' AND ...","as_of":"date field","format":
               "currency"|"count"|"percent"|"number"}; for a composite: {"label","description","ratio":["num",
               "den"],"scale":12000 (optional),"format":..} or {"sum_of":[...]}. A new rate is usually TWO
               metrics: a base metric and a ratio over it. Read a similar metric with get_metric first.
              {"op":"set_mapping","unit":"CODE","entity":"Concept","slot":"field","spec":{"column":"COL"} or
               {"expr":"SQL over src (src.<column> as get_mapping shows)"},"note":"why"}
            Classification rules (what a field holds) are set_mapping; set_metric is for formula changes.
            `evidence`: [{"quote","doc","page","title"}] when the change comes from a document.
            `watch_metrics`: governed metrics this change should move (e.g. ["ip_pmpm","op_pmpm"]); owners
            see their before/after values on the proposal."""
            ops = []
            for op in operations:
                op = dict(op)
                if op.get("op") == "set_metric":
                    is_client = op.pop("client", True)
                    if is_client is True or is_client == client:
                        op["client"] = client
                    else:
                        op["client"] = None
                        op["for_client"] = client
                else:
                    op.setdefault("client", client)
                ops.append(op)
            author = _person(studio)
            try:
                r = await asyncio.to_thread(studio.create, client=client, title=title, summary=summary, ops=ops,
                                            author=author, evidence=evidence or [], kind="change",
                                            extra={"watch": watch_metrics} if watch_metrics else None)
            except ValueError as exc:  # an edit that does not apply: the agent reads why and fixes the ops
                return f"Not drafted: {exc}"
            return (f"Proposal {r['id']} drafted: \"{r['title']}\". It goes to: "
                    f"{', '.join(r['area_titles'].values())}. Checks are running.")

        async def revise_proposal(ctx: RunContext, proposal_id: str, operations: list[dict], note: str) -> str:
            """Revise an open proposal with more edits (same shapes as propose_change), e.g. after an owner
            asked for changes. Approvals reset and checks rerun."""
            ops = [{**op, "client": op.get("client", client)} for op in operations]
            try:
                r = await asyncio.to_thread(studio.revise, proposal_id, ops, CURATOR, note)
            except ValueError as exc:
                return f"Not revised: {exc}"
            return f"Revised {r['id']}; checks are running again."

        async def comment_on_proposal(ctx: RunContext, proposal_id: str, text: str,
                                      request_changes: bool = False) -> str:
            """Add the signed-in person's comment; request_changes=true marks the proposal as needing changes."""
            r = studio.comment(proposal_id, CURRENT_USER.get(), text,
                               "request_changes" if request_changes else "comment")
            return f"Comment recorded on {r['id']} ({r['status']})."

        async def answer_question(ctx: RunContext, question_id: str, answer: str) -> str:
            """Record the signed-in owner's answer to a curator question."""
            r = studio.answer(question_id, CURRENT_USER.get(), answer)
            return f"Answer recorded on {r['id']}."

        async def approve_proposal(ctx: RunContext, proposal_id: str) -> str:
            """Record the signed-in person's approval. ONLY when they explicitly asked to approve it."""
            try:
                r = await asyncio.to_thread(studio.approve, proposal_id, CURRENT_USER.get())
            except (PermissionError, ValueError) as exc:
                return f"Not approved: {exc}"
            if r["status"] == "merged":
                return f"Approved by {r['approvals'][-1]['name']}. Every owner has approved, so it is now live."
            return f"Approval recorded. Still waiting on: {', '.join(a for a in r['reviewers'] if a not in studio._satisfied(r))}."

        async def get_mapping(ctx: RunContext, concept: str) -> str:
            """How each unit's source data maps onto a concept today (source table or query, notes,
            and the column or expression behind every field). Read this before proposing a mapping change."""
            import yaml as _yaml
            out = []
            for path in sorted((studio.s.main / "clients" / client / "units").glob("*/mappings.yaml")):
                spec = (_yaml.safe_load(path.read_text(encoding="utf-8")) or {})
                cls = (spec.get("classes") or {}).get(concept)
                if cls is not None:
                    out.append(f"Unit {spec.get('unit')} (source schema {spec.get('source_schema')}):\n"
                               + _yaml.safe_dump({concept: cls}, sort_keys=False, width=110))
            return "\n".join(out) or f"No unit maps {concept}."

        async def get_metric(ctx: RunContext, name: str) -> str:
            """A governed metric's full definition and the metrics it is built from, in the exact shape
            set_metric expects. Read a similar one before proposing a new metric."""
            import yaml as _yaml
            sl = studio.semantic(client)
            if name not in sl.metrics:
                return f"No metric {name!r}. Known: {', '.join(sorted(sl.metrics))}"
            out = {}
            for n in [name, *sl._leaves(name)]:
                d = {k: v for k, v in sl.metrics[n].definition().items() if v not in (None, "", [])}
                d.pop("name", None)
                out[n] = d
            return _yaml.safe_dump(out, sort_keys=False, width=110)

        async def crosswalk_powerbi_model(ctx: RunContext, model: str = "") -> str:
            """Read a Power BI semantic model kept with this client (its TMDL) and draft a proposal that
            crosswalks every measure to a governed metric, presentation, or gap, and generates the governed
            metrics back as TMDL. Leave `model` empty to list the models."""
            from .cli import harvest_powerbi
            models = studio.s.main / "clients" / client / "powerbi" / "models"
            names = sorted(p.name for p in models.iterdir()
                           if p.is_dir() or p.suffix.lower() in (".pbix", ".pbit", ".bim")) if models.exists() else []
            if not model or model not in names:
                return f"Power BI models kept for this client: {', '.join(names) or 'none'}."
            r = await asyncio.to_thread(harvest_powerbi, studio, client, str(models / model), _person(studio))
            return (f"Proposal {r['id']} drafted: \"{r['title']}\". {r['summary']} It goes to: "
                    f"{', '.join(r['area_titles'].values())}. Checks are running.")

        async def search_documents(ctx: RunContext, query: str) -> str:
            """Search the company's documents; returns passages with document and page."""
            from .curator import corpus_dir, load_corpus, search
            hits = search(load_corpus(corpus_dir(studio, client)), query)
            return "\n".join(f"- {p.title} (p.{p.page}, {p.doc}): {p.text[:400]}" for p in hits) or "No passages found."

        async def list_documents(ctx: RunContext) -> str:
            """List the documents the curator reads for this client."""
            from .curator import documents
            return "\n".join(f"- {d['doc']}: {d['title']} ({d['kind']}, {d['date']})" for d in documents(studio, client))

        async def history(ctx: RunContext, limit: int = 15) -> str:
            """Recent approved changes, newest first, with who approved them."""
            rows = studio.history(limit)
            return "\n".join(f"- {h['at'][:10]} {h['subject']} ({', '.join(a['title'] for a in h['areas'])}; "
                             f"approved by {', '.join(a['name'] for a in h['approvals']) or 'setup'}) [{h['sha'][:8]}]"
                             for h in rows)

        async def undo_change(ctx: RunContext, change_id: str) -> str:
            """Propose undoing an approved change (id from history). Opens a proposal; it does not undo directly."""
            r = await asyncio.to_thread(studio.revert, change_id, CURRENT_USER.get())
            return f"Opened {r['id']}: \"{r['title']}\". Its owners approve it like any other change."

        return [list_proposals, get_proposal, propose_change, revise_proposal, comment_on_proposal,
                answer_question, approve_proposal, get_mapping, get_metric, crosswalk_powerbi_model, search_documents,
                list_documents, history,
                undo_change]

    def _canvas_factories(self, client: str) -> list[Callable]:
        from insurance_context.agent_runtime import OrchestratorEvent
        studio, chat = self.studio, self

        def make_show_metrics(emit: Callable[[OrchestratorEvent], Awaitable[None]], agent_id: str):
            async def show_metrics(ctx: RunContext, metrics: list[str], by: list[str] | None = None,
                                   filters: dict[str, str | int | list[str]] | None = None,
                                   chart: str = "bar", title: str = "") -> str:
                """Compute governed metrics and SHOW them on screen as a chart + table.
                chart: bar | line | table. `by` dimensions as in compute_metrics."""
                from . import warehouse
                import duckdb
                sl = studio.semantic(client)
                path = studio.served_warehouse(client)
                if path is None:
                    return "This client has no reference data to compute on."
                con = duckdb.connect(str(path), read_only=True)
                try:
                    res = sl.query(con, metrics, by or [], filters or {}, warehouse.valuation(client))
                except Exception as exc:
                    return f"Metric error: {exc}"
                finally:
                    con.close()
                payload = {
                    "kind": "metrics", "title": title or ", ".join(sl.metrics[m].label for m in metrics),
                    "metrics": [{"name": m, "label": sl.metrics[m].label, "format": sl.metrics[m].format}
                                for m in metrics],
                    "by": by or [], "filters": filters or {}, "chart": chart, "as_of": res.as_of.isoformat(),
                    "columns": res.columns, "rows": [[plain(v) for v in r] for r in res.rows], "sql": res.sql,
                    "definitions": res.definitions,
                }
                await emit(OrchestratorEvent(type="studio_canvas", data=payload))
                kinds = {m: sl.metrics[m].format for m in metrics}
                preview = [[fmt(v, kinds.get(c)) if c in kinds else v for c, v in zip(res.columns, r)]
                           for r in res.rows[:25]]
                return f"Shown on screen (valuation {res.as_of}). Columns {res.columns}; rows: {preview}"
            return show_metrics

        def make_show_trace(emit, agent_id: str):
            async def show_trace(ctx: RunContext, metrics: list[str], by: list[str] | None = None,
                                 filters: dict[str, str | int | list[str]] | None = None) -> str:
                """SHOW how governed numbers are built: metric definitions, ontology joins, unit mappings,
                source tables and data-quality signals."""
                kit = chat._engine_kit(client)
                if kit is None:
                    return "No reference data for this client."
                trace = await asyncio.to_thread(kit.trace, metrics, by, filters)
                await emit(OrchestratorEvent(type="studio_canvas", data={"kind": "trace", "trace": trace}))
                return "Shown on screen: the trace from metric definition to source tables."
            return show_trace

        def make_show_proposal(emit, agent_id: str):
            async def show_proposal(ctx: RunContext, proposal_id: str) -> str:
                """SHOW a proposal or question card on screen (evidence, checks, impact, actions)."""
                r = studio.get(proposal_id)
                await emit(OrchestratorEvent(type="studio_canvas", data={"kind": "proposal", "proposal": r}))
                return f"Shown on screen: {r['title']} ({r['status']})."
            return show_proposal

        def make_scan(emit, agent_id: str):
            async def scan_documents(ctx: RunContext, documents: list[str] | None = None) -> str:
                """Run the curator over the company's documents (all, or the named ones): find changes,
                check them against the data, draft proposals, ask owners, set the rest aside."""
                from insurance_context import llm
                from . import curator
                loop = asyncio.get_running_loop()

                def progress(msg: str):
                    asyncio.run_coroutine_threadsafe(
                        emit(OrchestratorEvent.agent_status(agent_id, "active", msg)), loop)

                rep = await asyncio.to_thread(curator.scan, studio, client, documents,
                                              llm.complete if llm.configured() else None, progress)
                rep["evaluation"] = curator.evaluate(studio, client, rep)
                await emit(OrchestratorEvent(type="studio_canvas", data={"kind": "scan", "report": rep}))
                acts = {}
                for r in rep["results"]:
                    acts[r["action"]] = acts.get(r["action"], 0) + 1
                return (f"Read {rep['documents']} documents. " + ", ".join(f"{v} {k.replace('_', ' ')}"
                        for k, v in acts.items()) + f"; {len(rep['set_aside'])} passages set aside. "
                        f"Results: {json.dumps(rep['results'])[:3000]}")
            return scan_documents

        return [make_show_metrics, make_show_trace, make_show_proposal, make_scan]

    def _insights(self, client: str):
        from .insights import Insights
        kit = self._engine_kit(client)
        return Insights(self.studio, client, kit) if kit else None

    def _insight_factories(self, client: str) -> list[Callable]:
        """The business view's exhibits: each computes from governed definitions and shows a card."""
        from insurance_context.agent_runtime import OrchestratorEvent
        from .insights import plain_card
        chat = self

        def exhibit(emit, build) -> Awaitable[str]:
            async def run():
                ins = chat._insights(client)
                if ins is None:
                    return "This client has no data to compute on."
                try:
                    card, text = await asyncio.to_thread(build, ins)
                except Exception as exc:  # noqa: BLE001 - the agent reads the reason and recovers
                    return f"Could not build that exhibit: {exc}"
                await emit(OrchestratorEvent(type="studio_canvas", data=plain_card(card)))
                return "Shown on the canvas. " + text
            return run()

        def make_trend(emit, agent_id: str):
            async def show_trend(ctx: RunContext, metric: str, filters: dict[str, str | list[str]] | None = None,
                                 series_by: str | None = None, title: str = "") -> str:
                """Exhibit: a governed metric by quarter, with the latest complete quarter as the headline.
                `series_by` draws one line per value of a dimension. `title`: the question, in the person's words."""
                return await exhibit(emit, lambda ins: ins.trend(metric, filters, series_by, title))
            return show_trend

        def make_change(emit, agent_id: str):
            async def explain_change(ctx: RunContext, metric: str, from_period: str, to_period: str,
                                     by: str | None = None, filters: dict[str, str | list[str]] | None = None,
                                     title: str = "") -> str:
                """Exhibit: why a governed metric moved between two quarters (e.g. "2025-Q3" to "2025-Q4"):
                utilization vs unit cost, and each segment's exact contribution, split by `by`."""
                return await exhibit(emit, lambda ins: ins.change(metric, from_period, to_period, by, filters, title))
            return explain_change

        def make_breakdown(emit, agent_id: str):
            async def show_breakdown(ctx: RunContext, metric: str, by: str,
                                     filters: dict[str, str | list[str]] | None = None, title: str = "") -> str:
                """Exhibit: a governed metric split by one dimension, largest first. Put the quarter in filters."""
                return await exhibit(emit, lambda ins: ins.breakdown(metric, by, filters, title))
            return show_breakdown

        def make_outliers(emit, agent_id: str):
            async def find_outliers(ctx: RunContext, metric: str, entity: str, peers: str,
                                    volume_metric: str | None = None, min_volume: float = 0,
                                    threshold: float = 2.0, periods: list[str] | None = None,
                                    filters: dict[str, str | list[str]] | None = None,
                                    min_z: float | None = None, title: str = "") -> str:
                """Exhibit: each member (entity, e.g. provider) against its peer group (peers, e.g.
                peer_cohort) on a governed rate; flags those at or above `threshold` x their group in every
                period listed, with at least `min_volume` of `volume_metric`; `min_z` (e.g. 1.645) keeps
                only statistically significant excesses."""
                return await exhibit(emit, lambda ins: ins.outliers(metric, entity, peers, volume_metric, min_volume,
                                                                    threshold, periods, filters, min_z, title))
            return find_outliers

        def make_evidence(emit, agent_id: str):
            async def show_evidence(ctx: RunContext, metric: str, title: str = "") -> str:
                """Exhibit: how a governed metric is defined, who owns it, where its data comes from, which
                approved changes moved it (with before and after), and its Power BI equivalent."""
                return await exhibit(emit, lambda ins: ins.evidence(metric, title))
            return show_evidence

        def make_gap(emit, agent_id: str):
            async def record_gap(ctx: RunContext, question: str, detail: str) -> str:
                """Record a question no governed metric answers, for the owners to define. `question`: short,
                in the person's words; `detail`: what they want measured and why it is not available."""
                return await exhibit(emit, lambda ins: ins.gap(question, detail, _person(chat.studio)))
            return record_gap

        return [make_trend, make_change, make_breakdown, make_outliers, make_evidence, make_gap]

    # ------------------------------------------------------------ orchestrator

    def orchestrator(self, client: str, mode: str = "studio"):
        key = f"{client}:{mode}@{self.studio.head()}"
        if key in self._orchs:
            return self._orchs[key]
        from insurance_context.agent_runtime import Orchestrator
        if mode == "insights":
            self.studio.s.agent_dir.mkdir(parents=True, exist_ok=True)
            orch = Orchestrator(
                api_key=os.environ.get("API_KEY"), base_url=os.environ.get("API_BASE_URL"),
                model_name=os.environ.get("CHAT_MODEL", "gpt-4o"), system_prompt="{dynamic}", tools=[],
                tool_registry={}, data_dir=self.studio.s.agent_dir,
                storage=str(self.studio.s.agent_dir / "insights.db"), agent_name="Analyst",
                runtime_tool_factories=self._insight_factories(client))
            self._orchs = {k: v for k, v in self._orchs.items() if not k.startswith(f"{client}:{mode}@")}
            self._orchs[key] = orch
            return orch
        kit = self._engine_kit(client)
        engine_tools = kit.tools() if kit else []
        studio_tools = self._studio_tools(client)
        tools = engine_tools + studio_tools
        self.studio.s.agent_dir.mkdir(parents=True, exist_ok=True)
        orch = Orchestrator(
            api_key=os.environ.get("API_KEY"), base_url=os.environ.get("API_BASE_URL"),
            model_name=os.environ.get("CHAT_MODEL", "gpt-4o"),
            system_prompt="{dynamic}",
            tools=tools,
            tool_registry={fn.__name__: (fn, (fn.__doc__ or "").strip().split("\n")[0]) for fn in tools},
            data_dir=self.studio.s.agent_dir,
            storage=str(self.studio.s.agent_dir / "studio.db"),
            agent_name="Curator",
            runtime_tool_factories=self._canvas_factories(client),
        )
        self._orchs = {k: v for k, v in self._orchs.items() if not k.startswith(f"{client}:{mode}@")}
        self._orchs[key] = orch
        return orch

    def _insights_prompt(self, client: str) -> str:
        from .insights import catalog_brief, latest_complete_quarter
        ins = self._insights(client)
        c = self.studio.client(client)
        if ins is None:
            return f"You are the analyst for {c.title}. There is no data to compute on yet; say so."
        key = f"{client}@{self.studio.head()}"
        if getattr(self, "_briefs", {}).get("key") != key:
            self._briefs = {"key": key, "text": catalog_brief(ins)}
        return INSIGHTS_SYSTEM.format(title=c.title, person=_person(self.studio)["name"],
                                      valuation=ins.valuation.isoformat(),
                                      latest=latest_complete_quarter(ins.valuation), catalog=self._briefs["text"])

    def _system_prompt(self, client: str) -> str:
        c = self.studio.client(client)
        person = _person(self.studio)
        owners = self.studio.owners()
        areas = ", ".join(a.title for a in owners.areas_of(person["id"])) or "none"
        return SYSTEM.format(title=c.title, line=c.line, person=person["name"],
                             role=owners.person(person["id"]).role or "viewer", areas=areas,
                             screen=json.dumps(SCREEN.get() or {"screen": "chat"}))

    async def stream(self, message: str, session_id: str, client: str, user: str,
                     screen: dict | None = None, mode: str = "studio") -> AsyncIterator[str]:
        CURRENT_USER.set(user)
        SCREEN.set(screen or {})
        mode = "insights" if mode == "insights" else "studio"
        try:
            orch = self.orchestrator(client, mode)
            prompt = self._insights_prompt(client) if mode == "insights" else self._system_prompt(client)
        except Exception as exc:
            yield _event("error", {"message": f"Chat is not available: {exc}"})
            return
        canvases: list[dict] = []
        steps: list[str] = []
        text = ""
        # The orchestrator is shared per client; the prompt carries who is asking and what they see.
        sid = f"{client}:{session_id}" if mode == "studio" else f"{client}:{mode}:{session_id}"
        async for raw in orch.stream(message, session_id=sid, system_prompt=prompt):
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "studio_canvas":
                canvases.append(ev["data"])
            elif kind == "chunk":
                text += (ev.get("data") or {}).get("text", "")
            elif kind == "agent_tool_call" and (ev.get("data") or {}).get("status") == "started":
                steps.append(ev["data"].get("tool", ""))
            if kind == "result":
                ev["data"] = {**(ev.get("data") or {}), "canvases": canvases}
                self.transcripts.append(client, session_id, user, {
                    "user": message, "answer": text or (ev["data"].get("answer") or ""),
                    "steps": steps, "canvases": canvases, "screen": screen or {}})
            yield json.dumps(ev, default=str) + "\n"
