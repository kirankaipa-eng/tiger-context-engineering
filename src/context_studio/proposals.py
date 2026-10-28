"""The proposal lifecycle: draft on a branch -> checks -> owners review -> merge -> live.

    create()   edits on a fresh branch, routed to the owning areas, checks started
    revise()   more edits on the same branch (after "request changes"); checks rerun
    approve()  an owner of an area; when every area is satisfied and checks pass, merges
    revert()   undo a merged change: a new proposal, approved like any other

Nothing here writes to main except `_merge`, and only after approvals.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import edits, warehouse
from .config import Settings
from .gitrepo import ContentRepo, git
from .owners import Owners
from .render import okf_proposal
from .review import make_backend, now

CURATOR = {"id": "curator", "name": "Curator", "kind": "curator"}
OPEN = ("checking", "in_review", "changes_requested", "checks_failed", "approved", "question")


class ProposalError(ValueError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "change"


def fmt(value, kind: str | None) -> str:
    if value is None:
        return "—"
    if kind == "percent":
        return f"{value * 100:.1f}%"
    if kind == "currency":
        v = float(value)
        for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if abs(v) >= div:
                return f"${v / div:.1f}{suffix}"
        return f"${v:,.0f}"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}" if isinstance(value, (int, float)) else str(value)


class Studio:
    """One per process. Thread-safe enough for a team-sized app (git calls are serialized)."""

    def __init__(self, s: Settings):
        self.s = s
        self.repo = ContentRepo(s)
        self.reviews = make_backend(s)
        self._head = None
        self._cache: dict = {}

    # ------------------------------------------------------------ reading main

    def owners(self) -> Owners:
        return Owners.load(self.s.main)

    def head(self) -> str:
        return self.repo.head()

    def _cached(self, key: str, build):
        head = self.head()
        if self._head != head:
            self._cache.clear()
            self._head = head
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    def client(self, name: str):
        from insurance_context.client import Client
        return self._cached(f"client:{name}", lambda: Client(name))

    def semantic(self, name: str):
        from insurance_context.semantic import SemanticLayer
        return self._cached(f"sl:{name}", lambda: SemanticLayer(self.client(name)))

    def clients(self) -> list[dict]:
        from insurance_context.client import list_clients
        out = []
        for name in list_clients(self.s.main / "clients"):
            cfg = yaml.safe_load((self.s.main / "clients" / name / "client.yaml").read_text(encoding="utf-8"))
            out.append({"name": name, "title": cfg.get("title", name), "line": cfg.get("line"),
                        "units": [u["code"] for u in cfg.get("units", [])],
                        "has_data": warehouse.supported(name)})
        return out

    def served_warehouse(self, client: str) -> Path | None:
        if not (self.s.main / "clients" / client / "client.yaml").exists():
            return None  # a client being onboarded has no live content to serve yet
        return warehouse.served_path(self.s.warehouses, client, self.head())

    # ------------------------------------------------------------ proposals

    def list(self, status: str | None = None, person: str | None = None, client: str | None = None) -> list[dict]:
        records = self.reviews.list()
        if client:
            # A client being onboarded has no screen of its own yet: its proposals show with every client.
            live = {p.name for p in (self.s.main / "clients").iterdir() if (p / "client.yaml").exists()}
            records = [r for r in records if r.get("client") == client or r.get("client") not in live]
        if status == "open":
            records = [r for r in records if r["status"] in OPEN]
        elif status:
            records = [r for r in records if r["status"] == status]
        if person:
            records = [r for r in records if self.waiting_on(r, person)]
        return records

    def get(self, pid: str) -> dict:
        return self.reviews.get(pid)

    def waiting_on(self, r: dict, person: str) -> bool:
        if r["status"] == "question":
            return any(person in owners for owners in (r.get("reviewers") or {}).values())
        if r["status"] not in ("in_review", "approved"):
            return False
        return any(person in owners and area not in self._satisfied(r)
                   for area, owners in (r.get("reviewers") or {}).items())

    def _satisfied(self, r: dict) -> set[str]:
        owners = self.owners()
        done = set()
        for area_id in r.get("reviewers") or {}:
            need = owners.areas[area_id].min_approvals if area_id in owners.areas else 1
            got = {a["by"] for a in r.get("approvals", []) if area_id in a.get("areas", [])}
            if len(got) >= need:
                done.add(area_id)
        return done

    def create(self, *, client: str, title: str, ops: list[dict], author: dict, summary: str = "",
               kind: str = "change", evidence: list | None = None, data_proof: dict | None = None,
               set_aside: list | None = None, confidence: str | None = None, extra: dict | None = None,
               run_checks: bool = True) -> dict:
        pid = "cp-" + hashlib.sha1(f"{title}|{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:8]
        prefix = "curator" if author.get("kind") == "curator" else "proposals"
        branch = f"{prefix}/{pid}-{_slug(title)}"
        wt = self.repo.start_branch(pid, branch)
        try:
            touched = edits.apply(wt, ops)
        except edits.EditError:
            self.repo.drop_worktree(pid)
            raise
        record = {
            "id": pid, "title": title, "summary": summary or title, "kind": kind, "client": client,
            "branch": branch, "author": author, "status": "checking", "ops": ops,
            "evidence": evidence or [], "data_proof": data_proof, "set_aside": set_aside or [],
            "confidence": confidence, "comments": [], "approvals": [], "checks": {"running": True},
            "impact": {}, "touched": touched, **(extra or {}),
        }
        self._write_okf(wt, record)
        self._commit_push(wt, record, f"{title}\n\nProposal: {pid}", author)
        self._route(record)
        self.reviews.create(record)
        if run_checks:
            self.check_async(pid)
        return record

    def revise(self, pid: str, ops: list[dict], author: dict, note: str = "") -> dict:
        r = self.get(pid)
        if r["status"] not in OPEN:
            raise ProposalError(f"{pid} is {r['status']}; it cannot change")
        wt = self.repo.open_worktree(pid, r["branch"])
        r["touched"] = sorted(set(r.get("touched", [])) | set(edits.apply(wt, ops)))
        r["ops"] = r.get("ops", []) + ops
        self._commit_push(wt, r, f"Revise: {r['title']}\n\n{note}\n\nProposal: {pid}".strip(), author)
        r["approvals"] = []
        r["status"] = "checking"
        r["checks"] = {"running": True}
        r["comments"].append({"by": author["id"], "name": author.get("name"), "kind": "revision",
                              "text": note or "Revised the draft.", "at": now()})
        self._route(r)
        self.reviews.save(r)
        self.check_async(pid)
        return r

    def comment(self, pid: str, person: str, text: str, kind: str = "comment") -> dict:
        r = self.get(pid)
        name = "Curator" if person == "curator" else self.owners().person(person).name
        r["comments"].append({"by": person, "name": name, "kind": kind, "text": text, "at": now()})
        if kind == "request_changes":
            if not self._is_reviewer(r, person):
                raise PermissionError(f"{person} is not an owner of this proposal")
            r["status"] = "changes_requested"
        return self.reviews.save(r)

    def approve(self, pid: str, person: str) -> dict:
        r = self.get(pid)
        if r["status"] not in ("in_review", "approved") or not (r.get("checks") or {}).get("passed"):
            raise ProposalError(f"{pid} is {r['status'].replace('_', ' ')}; it can be approved once checks pass")
        areas = [a for a, owners in (r.get("reviewers") or {}).items()
                 if person in owners and a not in self._satisfied(r)]
        if not areas:
            raise PermissionError(f"{person} owns none of the areas still waiting on this proposal")
        r["approvals"].append({"by": person, "name": self.owners().person(person).name,
                               "areas": areas, "at": now()})
        if set(r["reviewers"]) <= self._satisfied(r):
            r["status"] = "approved"
            self.reviews.save(r)
            return self._merge(r, person)
        return self.reviews.save(r)

    def close(self, pid: str, person: str, reason: str = "") -> dict:
        r = self.get(pid)
        r["status"] = "closed"
        r["comments"].append({"by": person, "name": self.owners().person(person).name, "kind": "close",
                              "text": reason or "Closed without merging.", "at": now()})
        self.repo.drop_worktree(pid)
        return self.reviews.save(r)

    def revert(self, sha: str, person: str) -> dict:
        commit = next((c for c in self.repo.log(500) if c.sha.startswith(sha)), None)
        if commit is None:
            raise ProposalError(f"{sha} is not a change on main")
        original = re.search(r"Proposal: (cp-[0-9a-f]+)", commit.body)
        client = None
        if original:
            try:
                client = self.get(original.group(1)).get("client")
            except KeyError:
                pass
        title = f"Undo: {commit.subject}"
        pid = "cp-" + hashlib.sha1(f"{title}|{now()}".encode()).hexdigest()[:8]
        branch = f"proposals/{pid}-undo"
        wt = self.repo.start_branch(pid, branch)
        self.repo.revert_into(wt, commit.sha)
        author = {"id": person, "name": self.owners().person(person).name, "kind": "person"}
        record = {"id": pid, "title": title, "summary": f"Reverses \"{commit.subject}\" ({commit.sha[:8]}).",
                  "kind": "revert", "client": client or self.s.default_client, "branch": branch,
                  "author": author, "status": "checking", "ops": [], "evidence": [], "data_proof": None,
                  "set_aside": [], "confidence": None, "comments": [], "approvals": [],
                  "checks": {"running": True}, "impact": {}, "revert_of": commit.sha,
                  "touched": self.repo.changed_files(commit.sha)}
        self._write_okf(wt, record)
        self._commit_push(wt, record, f"{title}\n\nProposal: {pid}\nReverts: {commit.sha}", author)
        self._route(record)
        self.reviews.create(record)
        self.check_async(pid)
        return record

    def ask(self, *, client: str, title: str, summary: str, evidence: list, about: dict, author: dict) -> dict:
        """A question to owners instead of a draft: no branch, answered in comments."""
        for r in self.list(status="question", client=client):
            if r["title"] == title:
                return r
        layer, unit = about.get("layer"), about.get("unit")
        probe = []
        if unit:
            probe.append(f"clients/{client}/units/{str(unit).lower()}/mappings.yaml")
        if about.get("type") == "metric" or about.get("metric"):
            probe.append("ontology/metrics/x.yaml")
        if layer == "pack":
            probe.append("ontology/lines/x.yaml")
        if not probe or about.get("conflict") or about.get("type") == "definition":
            probe.append(f"clients/{client}/client.yaml")
        areas = self.owners().areas_for(probe, {}, [])
        pid = "cq-" + hashlib.sha1(f"{title}|{now()}".encode()).hexdigest()[:8]
        record = {"id": pid, "title": title, "summary": summary, "kind": "question", "client": client,
                  "branch": None, "author": author, "status": "question", "ops": [], "evidence": evidence,
                  "about": about, "data_proof": None, "set_aside": [], "confidence": None, "comments": [],
                  "approvals": [], "checks": {}, "impact": {}, "areas": [a.id for a in areas],
                  "area_titles": {a.id: a.title for a in areas}, "reviewers": {a.id: a.owners for a in areas}}
        return self.reviews.create(record)

    def answer(self, pid: str, person: str, text: str) -> dict:
        r = self.get(pid)
        r["comments"].append({"by": person, "name": self.owners().person(person).name, "kind": "answer",
                              "text": text, "at": now()})
        r["status"] = "answered"
        return self.reviews.save(r)

    # ------------------------------------------------------------ internals

    def _is_reviewer(self, r: dict, person: str) -> bool:
        return any(person in owners for owners in (r.get("reviewers") or {}).values())

    def _write_okf(self, wt: Path, r: dict) -> None:
        path = wt / "okf" / "proposals" / f"{r['id']}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(okf_proposal(r), encoding="utf-8")

    def _commit_push(self, wt: Path, r: dict, message: str, author: dict) -> None:
        email = f"{author['id']}@studio.local"
        name = "curator-bot" if author.get("kind") == "curator" else author.get("name", author["id"])
        self.repo.commit(wt, message, name, email)
        self.repo.push(wt, r["branch"])

    def _route(self, r: dict) -> None:
        """Which areas the diff touches, and so who must approve."""
        d = self.repo.diff(r["branch"])
        files = [f["path"] for f in d["files"] if not f["path"].startswith("okf/proposals/")]
        keys = {}
        for f in files:
            if f.endswith((".yaml", ".yml")):
                before = yaml.safe_load(self.repo.show(f) or "") or {}
                after = yaml.safe_load(self.repo.show(f, f"origin/{r['branch']}") or "") or {}
                if isinstance(before, dict) and isinstance(after, dict):
                    keys[f] = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        lines = [ln[1:] for ln in d["patch"].splitlines()
                 if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
        owners = self.owners()
        areas = owners.areas_for(files, keys, lines)
        r["areas"] = [a.id for a in areas]
        r["area_titles"] = {a.id: a.title for a in areas}
        r["reviewers"] = {a.id: a.owners for a in areas}
        r["diff"] = {"files": [f for f in d["files"] if not f["path"].startswith("okf/proposals/")],
                     "patch": d["patch"], "behind_main": d["behind_main"]}

    def check_async(self, pid: str) -> threading.Thread:
        t = threading.Thread(target=self._check_safely, args=(pid,), daemon=True)
        t.start()
        return t

    def _check_safely(self, pid: str) -> None:
        try:
            self.check(pid)
        except Exception as exc:  # a crashed check must surface on the proposal, not vanish
            r = self.get(pid)
            r["checks"] = {"running": False, "passed": False,
                           "items": [{"id": "error", "label": "Checks", "ok": False, "detail": str(exc)}]}
            r["status"] = "checks_failed"
            self.reviews.save(r)

    def _impact_plan(self, r: dict) -> tuple[list[str], list[str]]:
        """Which governed metrics to recompute, and by which dimension."""
        try:
            sl = self.semantic(r["client"])
        except Exception:
            return [], []
        unavailable = sl.unavailable()
        # metrics a proposal sets, new ones included: the branch computes them, main shows them as new
        names = [op["metric"] for op in r.get("ops", []) if op.get("op") == "set_metric"]
        # The author may name the governed numbers the change should move.
        names += [m for m in r.get("watch") or [] if m in sl.metrics and m not in names]
        names = [m for m in names if m not in unavailable]
        by: list[str] = list(r.get("watch_by") or [])
        for op in r.get("ops", []):
            if op.get("op") == "add_enum_value":
                for d in sl.dimensions.values():
                    if any(p.split(".")[-1] in self._slots_with_range(r["client"], op["enum"]) for p in d.paths.values()):
                        by = [d.name]
        if not names:
            entities = self._touched_entities(r)
            fields = {op["slot"] for op in r.get("ops", []) if op.get("op") == "set_mapping" and op.get("slot")}
            scored = []
            for i, m in enumerate(sl.metrics.values()):
                if m.name in unavailable:
                    continue
                leaves = [sl.metrics[x] for x in sl._leaves(m.name)]
                if not any(x.entity in entities for x in leaves):
                    continue
                text = " ".join(f"{x.measure or ''} {x.filter or ''}" for x in leaves)
                uses_field = any("{" + f + "}" in text for f in fields)
                # ratios people read (PMPM, per 1,000, unit cost) built on the changed field come first
                scored.append((-(2 * uses_field + (not m.is_base)), i, m.name))
            names = [n for *_, n in sorted(scored)][:6]
        if not names:
            names = [m.name for m in sl.metrics.values()
                     if m.format in ("currency", "percent") and m.name not in unavailable][:4]
        if not by:
            known = [n for n in names if n in sl.metrics]
            avail = sl.available_dimensions(known) if known else set(sl.dimensions)
            by = [d for d in ("operating_unit", "line_of_business", "unit") if d in avail][:1]
        return names, by

    def _slots_with_range(self, client: str, enum: str) -> set[str]:
        sv = self.client(client).resolved.view
        return {s for s, slot in sv.all_slots().items() if slot.range == enum}

    def _touched_entities(self, r: dict) -> set[str]:
        out = set()
        sv = self.client(r["client"]).resolved.view
        for op in r.get("ops", []):
            if op.get("entity"):
                out.add(op["entity"])
            if op.get("concept"):
                out.add(op["concept"])
            if op.get("op") == "add_enum_value":
                slots = self._slots_with_range(r["client"], op["enum"])
                out |= {c for c in sv.all_classes() if slots & {s.name for s in sv.class_induced_slots(c)}}
        return out

    def check(self, pid: str) -> dict:
        r = self.get(pid)
        wt = self.repo.open_worktree(pid, r["branch"])
        stale = self.repo.catch_up(wt, r["branch"])
        if stale and r.get("ops"):
            # The proposal is a list of edits, so replay them on the latest main instead of merging lines.
            try:
                wt = self.repo.start_branch(pid, r["branch"])
                edits.apply(wt, r["ops"])
                self._write_okf(wt, r)
                self._commit_push(wt, r, f"{r['title']}\n\nProposal: {pid}\nReplayed on the latest main.",
                                  r.get("author") or CURATOR)
                self._route(r)
                self.reviews.save(r)
                stale = None
            except edits.EditError as exc:
                stale = f"no longer applies to live content: {exc}"
        if stale:
            r["checks"] = {"running": False, "passed": False, "at": now(), "items": [
                {"id": "current", "label": "Up to date with live content", "ok": False, "detail": stale}]}
            r["status"] = "checks_failed"
            return self.reviews.save(r)
        metrics, by = self._impact_plan(r)
        as_of = warehouse.valuation(r["client"])
        raw = warehouse.raw_path(self.s.warehouses, r["client"])
        with tempfile.TemporaryDirectory(dir=self.s.home) as tmp:
            tmdl = f"clients/{r['client']}/powerbi/governed_measures.tmdl"
            set_metrics = [op["metric"] for op in r.get("ops", []) if op.get("op") == "set_metric"]
            job = {"client": r["client"], "raw": str(raw) if raw else None,
                   "scratch": str(Path(tmp) / "branch.duckdb"), "metrics": metrics, "by": by,
                   "as_of": as_of.isoformat() if as_of else None,
                   "tmdl": tmdl if (wt / tmdl).exists() and set_metrics else None, "set_metrics": set_metrics}
            env = {**os.environ, "ICTX_CONTENT_DIR": str(wt), "PYTHONUTF8": "1"}
            proc = subprocess.run([sys.executable, "-m", "context_studio.engine_job"], input=json.dumps(job),
                                  capture_output=True, text=True, encoding="utf-8", env=env, timeout=900)
        if proc.returncode != 0 or not proc.stdout.strip():
            raise RuntimeError((proc.stderr or "engine check produced no output").strip().splitlines()[-1])
        branch = json.loads(proc.stdout)
        if branch.get("tmdl") and job.get("tmdl") and (wt / job["tmdl"]).read_text(encoding="utf-8") != branch["tmdl"]:
            (wt / job["tmdl"]).write_text(branch["tmdl"], encoding="utf-8")
            self._commit_push(wt, r, f"Regenerate governed Power BI measures\n\nProposal: {pid}",
                              {"id": "studio", "name": "Context Studio", "kind": "curator"})
            r = self.get(pid)
            r["touched"] = sorted(set(r.get("touched", [])) | {job["tmdl"]})
            self._route(r)  # the regenerated file is part of the change: refresh diff and owners
            self.reviews.save(r)
        main = self._main_values(r["client"], metrics, by, as_of) if branch.get("values") else None
        r = self.get(pid)  # reload: comments may have arrived while checks ran
        r["checks"] = self._summarize_checks(r, branch)
        labels = {op["metric"]: op.get("fields") or {} for op in r.get("ops", []) if op.get("op") == "set_metric"}
        r["impact"] = self._impact(r["client"], metrics, by, as_of, main, branch.get("values"), labels)
        if r["status"] in ("checking", "in_review", "checks_failed"):
            r["status"] = "in_review" if r["checks"]["passed"] else "checks_failed"
        return self.reviews.save(r)

    def _main_values(self, client: str, metrics: list[str], by: list[str], as_of) -> dict | None:
        import duckdb
        path = self.served_warehouse(client)
        sl = self.semantic(client)
        metrics = [m for m in metrics if m in sl.metrics and m not in sl.unavailable()]  # new ones: no "before"
        if path is None or not metrics:
            return None
        con = duckdb.connect(str(path), read_only=True)
        try:
            res = sl.query(con, metrics, by=by, as_of=as_of)
            from .engine_job import plain
            return {"columns": list(res.columns), "rows": [[plain(v) for v in x] for x in res.rows]}
        except Exception:
            return None
        finally:
            con.close()

    def _summarize_checks(self, r: dict, branch: dict) -> dict:
        items = branch.get("items", [])
        main_findings = self._main_findings(r["client"])
        key = lambda f: (f["unit"], f["entity"], f["slot"], f["check"])
        before = {key(f): f for f in main_findings}
        after = {key(f): f for f in branch.get("findings", [])}
        cleared = [before[k] for k in before if k not in after]
        new = [after[k] for k in after if k not in before]
        onboarding = not (self.s.main / "clients" / r["client"] / "client.yaml").exists()
        if new and onboarding:
            # A client being onboarded brings its data as it is: report its findings, do not block on them.
            items.append({"id": "quality", "label": "Data-quality findings recorded", "ok": True,
                          "detail": f"{len(new)} finding(s) in the client's data at onboarding: " +
                                    "; ".join(f"{f['unit']} {f['entity']}.{f['slot']} {f['check']}" for f in new[:5])})
        elif new:
            items.append({"id": "quality", "label": "No new data-quality findings", "ok": False,
                          "detail": "; ".join(f"{f['unit']} {f['entity']}.{f['slot']} {f['check']}" for f in new[:5])})
        else:
            items.append({"id": "quality", "label": "No new data-quality findings", "ok": True,
                          "detail": f"{len(cleared)} finding(s) cleared" if cleared else ""})
        items.append({"id": "evidence", "label": "Evidence cited",
                      "ok": bool(r.get("evidence")) or r.get("author", {}).get("kind") != "curator", "detail": ""})
        return {"running": False, "passed": all(i["ok"] for i in items), "items": items,
                "findings_cleared": cleared, "findings_new": new, "at": now()}

    def _main_findings(self, client: str) -> list[dict]:
        def build():
            import duckdb
            from insurance_context.validate import validate
            path = self.served_warehouse(client)
            if path is None:
                return []
            con = duckdb.connect(str(path), read_only=True)
            try:
                return [f.as_dict() for f in validate(self.client(client), con)]
            finally:
                con.close()
        return self._cached(f"findings:{client}", build)

    def _impact(self, client, metrics, by, as_of, main, branch, labels: dict | None = None) -> dict:
        if not (metrics and branch):
            return {"metrics": metrics, "by": by, "rows": []}
        sl = self.semantic(client)
        nby = len(by)

        def index(values):
            out = {}
            for row in (values or {}).get("rows", []):
                cols = (values or {}).get("columns", [])
                group = tuple(row[:nby])
                for c, v in zip(cols[nby:], row[nby:]):
                    out[(group, c)] = v
            return out

        b, a = index(main), index(branch)
        rows = []
        for (group, metric) in sorted(set(b) | set(a), key=lambda k: (k[1], [str(g) for g in k[0]])):
            kind = sl.metrics[metric].format if metric in sl.metrics else (labels or {}).get(metric, {}).get("format")
            before, after = b.get((group, metric)), a.get((group, metric))
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                changed = abs(after - before) > 1e-9 * max(1.0, abs(before))  # float noise is not a change
            else:
                changed = before != after
            label = sl.metrics[metric].label if metric in sl.metrics else ((labels or {}).get(metric, {}).get("label") or metric)
            rows.append({"metric": metric, "label": label, "new": metric not in sl.metrics,
                         "group": [str(g) for g in group], "format": kind, "before": before, "after": after,
                         "before_fmt": fmt(before, kind), "after_fmt": fmt(after, kind), "changed": changed})
        return {"metrics": metrics, "by": by, "as_of": as_of.isoformat() if as_of else None, "rows": rows,
                "changed": sum(1 for x in rows if x["changed"])}

    def _merge(self, r: dict, person: str) -> dict:
        wt = self.repo.open_worktree(r["id"], r["branch"])
        okf = wt / "okf" / "proposals" / f"{r['id']}.md"
        if okf.exists():
            text = okf.read_text(encoding="utf-8").replace("status: draft", "status: accepted", 1)
            verified = "".join(f"\n- human:{a['by']}" for a in r["approvals"])
            okf.write_text(text.replace("---\n", f"---\nverified:{verified}\n", 1) if verified else text, encoding="utf-8")
        approver = self.owners().person(person)
        self.repo.commit(wt, f"Record approval\n\nProposal: {r['id']}", approver.name, f"{person}@studio.local")
        self.repo.push(wt, r["branch"])
        trailers = "\n".join(f"Approved-by: {a['name']} <{a['by']}>" for a in r["approvals"])
        sha = self.repo.merge(r["branch"], f"{r['title']}\n\nProposal: {r['id']}\n{trailers}",
                              approver.name, f"{person}@studio.local")
        self.repo.drop_worktree(r["id"])
        r["status"] = "merged"
        r["merged_sha"] = sha
        r["merged_at"] = now()
        return self.reviews.save(r)

    # ------------------------------------------------------------ history

    def history(self, limit: int = 100) -> list[dict]:
        owners = self.owners()
        out = []
        for c in self.repo.log(limit):
            pid = re.search(r"Proposal: (cp-[0-9a-f]+)", c.body)
            record = None
            if pid:
                try:
                    record = self.get(pid.group(1))
                except KeyError:
                    record = None
            files = self.repo.changed_files(c.sha)
            areas = owners.areas_for([f for f in files if not f.startswith("okf/proposals/")], {}, [])
            out.append({"sha": c.sha, "subject": c.subject, "author": c.author, "at": c.at,
                        "proposal": pid.group(1) if pid else None,
                        "client": record.get("client") if record else None,
                        "approvals": record.get("approvals", []) if record else [],
                        "evidence": (record.get("evidence") or [])[:1] if record else [],
                        "areas": [{"id": a.id, "title": a.title} for a in areas],
                        "files": files, "is_revert": c.subject.startswith("Undo:"),
                        "initial": len(c.parents) == 0})
        return out
