"""Hello-world building blocks for the ontology curator.

Each function is one box in the spec's "how the curator finds a needle" flow:

    search -> extract_claim -> link_to_ontology -> corroborate -> decide -> draft -> verify

Deliberately simple (regex extraction, keyword search, a local git repo) so the whole
loop runs offline in seconds. Each piece is the seed of a real component; the spec
says what it grows into.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

import yaml

HERE = Path(__file__).resolve().parent
CORPUS = HERE / "corpus"
WORK = HERE / ".work"                      # scratch space; safe to delete
ENGINE_ROOT = HERE.parents[1]              # tiger-context-engineering
CLIENT = "summit_group"


# ------------------------------------------------------------------ 1. search

@dataclass
class Passage:
    doc: str
    title: str
    kind: str
    page: int
    para: int
    text: str

    def cite(self) -> str:
        return f"{self.doc} p.{self.page} para {self.para}"


def load_corpus(folder: Path = CORPUS) -> list[Passage]:
    """Documents are Markdown with a small header and '## Page N' headings."""
    passages = []
    for path in sorted(folder.glob("*.md")):
        raw = path.read_text()
        _, header, body = raw.split("---", 2)
        meta = yaml.safe_load(header)
        pages = re.split(r"^## Page (\d+)\s*$", body, flags=re.M)
        for page_no, page_text in zip(pages[1::2], pages[2::2]):
            paras = [p.strip() for p in page_text.strip().split("\n\n") if p.strip()]
            for i, text in enumerate(paras, 1):
                passages.append(Passage(path.name, meta["title"], meta["kind"], int(page_no), i, text))
    return passages


def search(passages: list[Passage], terms: list[str]) -> list[Passage]:
    """Keyword search. The real curator uses the enterprise search index."""
    scored = [(sum(p.text.lower().count(t.lower()) for t in terms), p) for p in passages]
    return [p for score, p in sorted(scored, key=lambda x: -x[0]) if score > 0]


# ---------------------------------------------------------- 2. extract a claim

@dataclass
class Claim:
    passage: Passage
    kind: str               # ontology_change | rule | noise
    subject: str | None     # e.g. "cause of loss"
    value: str | None       # e.g. "PFAS"
    why: str


_RULE = re.compile(r"\b(exclusion|endorsement is required|do not write|appetite|decline)\b", re.I)
_CODE = re.compile(r"\b(code|coded|record|reported)\b.*\b(separate|new)\b.*\b(cause[- ]of[- ]loss|status)\b", re.I)
_ACRONYM = re.compile(r"\(([A-Z]{2,8})\)")


def extract_claim(p: Passage) -> Claim:
    """Rule-based extraction: is this passage a change to what a concept means?"""
    if _RULE.search(p.text):
        return Claim(p, "rule", None, None, "an underwriting rule, not a change to the model")
    m = _CODE.search(p.text)
    if m:
        subject = "cause of loss" if "cause" in m.group(3).lower() else "claim status"
        acronym = _ACRONYM.search(p.text)
        return Claim(p, "ontology_change", subject, acronym.group(1) if acronym else None,
                     f"asks for a separate {subject} code")
    return Claim(p, "noise", None, None, "mentions the topic but asks for no change")


def extract_claim_llm(p: Passage, complete) -> Claim:
    """Same contract, extracted by an LLM (`complete(prompt) -> str`). Used with --llm."""
    prompt = (
        "You read one passage from an insurer's documents. Decide if it changes what an ontology concept "
        "means (a new code, a new status, a new definition), is a business rule (appetite, exclusions), "
        "or is noise. Reply JSON only: {\"kind\": \"ontology_change|rule|noise\", \"subject\": str|null, "
        "\"value\": str|null, \"why\": str}\n\nPassage:\n" + p.text)
    raw = complete(prompt)
    data = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    return Claim(p, data["kind"], data.get("subject"), data.get("value"), data.get("why", ""))


# ---------------------------------------------------------- 3. link to the model

_SUBJECT_TO_SLOT = {"cause of loss": ("CommercialClaim", "cause_of_loss"),
                    "claim status": ("CommercialClaim", "claim_status")}


def link_to_ontology(claim: Claim) -> dict:
    """Which concept and value set does the claim touch, and is the value already there?"""
    from insurance_context.client import Client
    sv = Client(CLIENT).resolved.view
    entity, slot = _SUBJECT_TO_SLOT[claim.subject]
    enum = sv.induced_slot(slot, entity).range
    value = claim.value.lower()
    known = list(sv.get_enum(enum).permissible_values)
    return {"entity": entity, "label": sv.get_class(entity).title or entity, "slot": slot,
            "enum": enum, "value": value, "exists": value in known}


# ---------------------------------------------------------- 4. corroborate

def warehouse() -> Path:
    """The Summit reference warehouse, built once with the ontology views installed."""
    path = ENGINE_ROOT / "build" / CLIENT / "warehouse.duckdb"
    if not path.exists():
        import duckdb
        from insurance_context import sqlgen
        from insurance_context.client import Client
        from insurance_context.fixtures import pc_warehouse
        pc_warehouse.build(path)
        con = duckdb.connect(str(path))
        sqlgen.install(Client(CLIENT), con)
        con.close()
    return path


def corroborate(link: dict) -> dict:
    """Does the data already show the value the document talks about?"""
    import duckdb
    from insurance_context.client import Client
    from insurance_context.validate import validate
    con = duckdb.connect(str(warehouse()), read_only=True)
    try:
        for f in validate(Client(CLIENT), con):
            if f.check == "enum" and f.slot == link["slot"] and link["value"] in [str(s) for s in f.sample]:
                return {"agrees": True, "unit": f.unit, "rows": f.violations,
                        "evidence": f"{f.violations} {f.unit} claims already carry {link['slot']} = '{link['value']}'"}
    finally:
        con.close()
    return {"agrees": False, "evidence": "no matching values in the data yet"}


# ---------------------------------------------------------- 5. decide layer + owners

def load_owners() -> dict:
    return yaml.safe_load((HERE / "owners.yaml").read_text())["areas"]


def decide(claim: Claim, corr: dict) -> dict:
    """Regulation applies to every carrier -> pack. Anything else -> this client's overlay."""
    layer = "pack" if claim.passage.kind == "regulatory bulletin" else "client"
    area = "pack-concepts" if layer == "pack" else "client-vocabulary"
    return {"layer": layer, "area": area, "owners": load_owners()[area]["owners"],
            "confidence": "high" if corr["agrees"] else "low"}


# ---------------------------------------------------------- proposal

@dataclass
class Proposal:
    id: str
    title: str
    claim: dict
    link: dict
    corroboration: dict
    decision: dict
    branch: str = ""
    checks: dict = field(default_factory=dict)


def make_proposal(claim: Claim, link: dict, corr: dict, decision: dict) -> Proposal:
    key = f"{claim.passage.doc}|{claim.passage.page}|{link['enum']}|{link['value']}"
    pid = "cp-" + hashlib.sha1(key.encode()).hexdigest()[:6]
    title = f"Add {claim.value} as a {claim.subject}"       # business words; technical detail is in the body
    return Proposal(pid, title, {"text": claim.passage.text, "cite": claim.passage.cite(),
                                 "doc_title": claim.passage.title}, link, corr, decision)


def find_pfas_proposal(verbose: bool = False) -> Proposal:
    """Steps 1-5 on the PFAS needle, as scripts 01 and 02 walk through them."""
    claims = [extract_claim(p) for p in search(load_corpus(), ["PFAS"])]
    needle = next(c for c in claims if c.kind == "ontology_change")
    link = link_to_ontology(needle)
    corr = corroborate(link)
    return make_proposal(needle, link, corr, decide(needle, corr))


# ---------------------------------------------------------- 6. draft on a branch

def run(cmd: list[str], cwd: Path, check: bool = True) -> str:
    out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{out.stderr or out.stdout}")
    return out.stdout.strip()


def content_repo() -> Path:
    """A local content repo (ontology + clients + owners), created once from the engine's copy."""
    repo = WORK / "content"
    if (repo / ".git").exists():
        return repo
    repo.mkdir(parents=True, exist_ok=True)
    for part in ("ontology", "clients"):
        shutil.copytree(ENGINE_ROOT / part, repo / part,
                        ignore=shutil.ignore_patterns("proposals", "__pycache__"))
    shutil.copy(HERE / "owners.yaml", repo / "owners.yaml")
    run(["git", "init", "-q", "-b", "main"], repo)
    run(["git", "add", "-A"], repo)
    run(["git", "-c", "user.name=setup", "-c", "user.email=setup@example.com",
         "commit", "-q", "-m", "Initial ontology content"], repo)
    return repo


def add_enum_value(pack_file: Path, enum: str, value: str, description: str) -> None:
    """Insert a value at the top of an enum's permissible_values (keeps the rest byte for byte)."""
    text = pack_file.read_text()
    start = text.index(f"\n  {enum}:\n")
    anchor = text.index("    permissible_values:\n", start) + len("    permissible_values:\n")
    entry = f"      {value}:\n        description: {description}\n"
    pack_file.write_text(text[:anchor] + entry + text[anchor:])


def okf_proposal(p: Proposal) -> str:
    """The proposal as an OKF v0.2 concept: draft status, machine-generated, cited."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    header = {
        "type": "Change Proposal",
        "title": p.title,
        "description": f"Proposed {p.decision['layer']}-level change from {p.claim['doc_title']}.",
        "status": "draft",
        "proposal_id": p.id,
        "target": p.link["enum"],
        "layer": p.decision["layer"],
        "confidence": p.decision["confidence"],
        "owners": p.decision["owners"],
        "generated": {"by": "curator-hello-world/0.1", "at": now},
        "sources": [{"id": "doc", "resource": f"corpus/{p.claim['cite'].split(' ')[0]}",
                     "title": p.claim["doc_title"]},
                    {"id": "data", "resource": f"warehouse:{p.corroboration.get('unit', '')}",
                     "title": "Summit reference warehouse"}],
    }
    body = (f"# {p.title}\n\n"
            f"The source asks for {p.link['slot'].replace('_', ' ')} code `{p.link['value']}` [^doc]. "
            f"The data agrees: {p.corroboration['evidence']} [^data].\n\n"
            f"## Change\n\nAdd `{p.link['value']}` to `{p.link['enum']}` in the {p.decision['layer']} layer.\n\n"
            f"[^doc]: \"{p.claim['text']}\" ({p.claim['cite']})\n"
            f"[^data]: {p.corroboration['evidence']}\n")
    return "---\n" + yaml.safe_dump(header, sort_keys=False, allow_unicode=True) + "---\n" + body


def draft(p: Proposal) -> Proposal:
    """Create the branch, edit the pack, write the OKF proposal, commit as the curator bot."""
    repo = content_repo()
    p.branch = f"curator/{p.id}-{p.link['value']}"
    run(["git", "checkout", "-q", "main"], repo)
    run(["git", "branch", "-q", "-D", p.branch], repo, check=False)
    run(["git", "checkout", "-q", "-b", p.branch], repo)
    add_enum_value(repo / "ontology/lines/commercial_pc.yaml", p.link["enum"], p.link["value"],
                   f"Claims arising from {p.claim['text'].split('(')[1].split(')')[0]} exposure "
                   f"(regulatory coding requirement).")
    okf = repo / "okf" / CLIENT / "proposals" / f"{p.id}.md"
    okf.parent.mkdir(parents=True, exist_ok=True)
    okf.write_text(okf_proposal(p))
    run(["git", "add", "-A"], repo)
    run(["git", "-c", "user.name=curator-bot", "-c", "user.email=curator-bot@example.com",
         "commit", "-q", "-m", f"{p.title}\n\nProposal: {p.id}"], repo)
    return p


# ---------------------------------------------------------- 7. verify

_VERIFY = r"""
import json, shutil, sys, duckdb
from insurance_context.client import Client
from insurance_context import sqlgen
from insurance_context.semantic import SemanticLayer
from insurance_context.validate import validate
c = Client(sys.argv[1]); out = {}
c.resolved; [u.resolved for u in c.units]; out["resolves"] = True
out["metric_problems"] = SemanticLayer(c).validate()
out["portability_issues"] = sum(len(sqlgen.portability_issues(c, d)) for d in sqlgen.DIALECTS)
shutil.copy(sys.argv[2], sys.argv[3]); con = duckdb.connect(sys.argv[3]); sqlgen.install(c, con)
out["findings"] = [f"{f.unit} {f.entity}.{f.slot} {f.check}: {f.sample}" for f in validate(c, con)]
print(json.dumps(out))
"""


def verify(p: Proposal) -> Proposal:
    """Run the engine's checks against the branch's content (in a subprocess, so the engine
    reads the branch through ICTX_CONTENT_DIR)."""
    env = {**os.environ, "ICTX_CONTENT_DIR": str(content_repo())}
    out = subprocess.run([sys.executable, "-c", _VERIFY, CLIENT, str(warehouse()),
                          str(WORK / "verify.duckdb")], env=env, capture_output=True, text=True)
    if out.returncode != 0:
        p.checks = {"passed": False, "error": out.stderr.strip().splitlines()[-1]}
        return p
    result = json.loads(out.stdout.strip().splitlines()[-1])
    still_open = [f for f in result["findings"] if p.link["value"] in f]
    result["passed"] = (result["resolves"] and not result["metric_problems"]
                        and result["portability_issues"] == 0 and not still_open)
    result["needle_finding_cleared"] = not still_open
    p.checks = result
    return p


# ---------------------------------------------------------- 8. git backends

class GitBackend(Protocol):
    def push(self, repo: Path, branch: str) -> None: ...
    def open_change_request(self, repo: Path, branch: str, title: str, body: str,
                            reviewers: list[str]) -> dict: ...


def mr_body(p: Proposal) -> str:
    """The merge request description, following the spec's template."""
    c = p.checks
    return (f"## What changes\n{p.title}.\n\n"
            f"## Why (evidence)\n- \"{p.claim['text']}\" ({p.claim['cite']})\n"
            f"- Data: {p.corroboration['evidence']}.\n\n"
            f"## Impact\n- Concept: {p.link['label']} ({p.link['entity']}.{p.link['slot']})\n"
            f"- Clears the data-quality finding on this value.\n\n"
            f"## Confidence\n{p.decision['confidence'].title()}: document and data "
            f"{'agree' if p.corroboration['agrees'] else 'do not agree yet'}.\n\n"
            f"## Verification\nresolves: {c.get('resolves')} | metric problems: {len(c.get('metric_problems', []))} "
            f"| portability issues: {c.get('portability_issues')} | needle finding cleared: "
            f"{c.get('needle_finding_cleared')}\n\n"
            f"## Ask\n{' '.join(p.decision['owners'])}: approve, or reply with changes.\n")


class LocalGitBackend:
    """A bare repo on disk plus JSON review records. Offline stand-in for GitLab."""

    def __init__(self, remote: Path = WORK / "remote.git"):
        self.remote = remote
        if not remote.exists():
            run(["git", "init", "-q", "--bare", str(remote)], WORK)
        self.reviews = remote / "change_requests"
        self.reviews.mkdir(exist_ok=True)

    def push(self, repo: Path, branch: str) -> None:
        if "local" not in run(["git", "remote"], repo).split():
            run(["git", "remote", "add", "local", str(self.remote)], repo)
        run(["git", "push", "-q", "local", "main", branch, "--force"], repo)

    def open_change_request(self, repo, branch, title, body, reviewers) -> dict:
        cr = {"id": branch.split("/", 1)[1].rsplit("-", 1)[0],     # curator/cp-1a2b3c-pfas -> cp-1a2b3c
              "branch": branch, "title": title, "body": body, "reviewers": reviewers,
              "state": "open", "comments": [], "approvals": [],
              "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        self.save(cr)
        return cr

    def save(self, cr: dict) -> None:
        (self.reviews / f"{cr['id']}.json").write_text(json.dumps(cr, indent=2))

    def get(self, cr_id: str) -> dict:
        return json.loads((self.reviews / f"{cr_id}.json").read_text())

    def all(self) -> list[dict]:
        return [json.loads(f.read_text()) for f in sorted(self.reviews.glob("*.json"))]

    def comment(self, cr_id: str, author: str, text: str) -> dict:
        cr = self.get(cr_id)
        cr["comments"].append({"by": author, "text": text,
                               "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        self.save(cr)
        return cr

    def approve(self, cr_id: str, owner: str) -> dict:
        """A human action. The curator's tools may record it on the owner's behalf only when the
        owner asks in chat; in GitLab the owner clicks Approve themselves."""
        cr = self.get(cr_id)
        if owner not in cr["reviewers"]:
            raise PermissionError(f"{owner} does not own this area; owners are {cr['reviewers']}")
        cr["approvals"].append({"by": f"human:{owner.lstrip('@')}",
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        cr["state"] = "approved"
        self.save(cr)
        return cr


class GitLabPushBackend:
    """Opens a GitLab merge request with plain `git push` + push options (SSH, no API token).
    Dry run by default: prints the command instead of running it."""

    def __init__(self, remote_url: str, execute: bool = False):
        self.remote_url, self.execute = remote_url, execute

    def push(self, repo: Path, branch: str) -> None:
        pass  # the push below both uploads the branch and opens the MR

    def open_change_request(self, repo, branch, title, body, reviewers) -> dict:
        cmd = ["git", "push", self.remote_url, f"{branch}:{branch}",
               "-o", "merge_request.create", "-o", "merge_request.target=main",
               "-o", f"merge_request.title={title}",
               "-o", f"merge_request.description={body}",
               "-o", "merge_request.label=curator"]
        if not self.execute:
            return {"dry_run": True, "command": cmd}
        return {"dry_run": False, "output": run(cmd, repo)}
