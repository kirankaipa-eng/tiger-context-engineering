"""How a proposal reads: the merge-request body and the OKF proposal file."""

from __future__ import annotations

import yaml


def mr_body(r: dict) -> str:
    """The spec's description template, in business words."""
    lines = [f"## What changes\n{r.get('summary') or r['title']}"]
    if r.get("evidence"):
        lines.append("## Why (evidence)")
        for e in r["evidence"]:
            lines.append(f"- \"{e['quote']}\" ({e.get('title') or e['doc']}, p.{e.get('page')})")
    if r.get("data_proof"):
        lines.append(f"- Data: {r['data_proof'].get('summary', '')}")
    impact = r.get("impact") or {}
    if impact.get("rows"):
        lines.append("## Impact")
        for row in impact["rows"][:12]:
            lines.append(f"- {row['label']}: {row.get('before_fmt', '')} -> {row.get('after_fmt', '')}")
    if r.get("confidence"):
        lines.append(f"## Confidence\n{r['confidence'].title()}")
    checks = r.get("checks") or {}
    if checks:
        status = "pass" if checks.get("passed") else ("running" if checks.get("running") else "fail")
        lines.append(f"## Verification\n{status}: " + ", ".join(
            f"{c['label']} {'ok' if c['ok'] else 'FAILED'}" for c in checks.get("items", [])))
    owners = sorted({p for ps in (r.get("reviewers") or {}).values() for p in ps})
    if owners:
        lines.append("## Ask\n" + " ".join(f"@{o}" for o in owners) + ": approve, or reply with changes.")
    return "\n\n".join(lines)


def okf_proposal(r: dict) -> str:
    """The proposal as an OKF v0.2 Change Proposal (draft until merged)."""
    header = {
        "type": "Change Proposal",
        "title": r["title"],
        "description": r.get("summary", ""),
        "status": "draft",
        "proposal_id": r["id"],
        "client": r.get("client"),
        "areas": r.get("areas", []),
        "confidence": r.get("confidence"),
        "generated": {"by": r.get("author", {}).get("id"), "at": r.get("created_at")},
        "sources": [{"id": f"e{i + 1}", "resource": f"corpus/{e['doc']}#page={e.get('page')}",
                     "title": e.get("title") or e["doc"]} for i, e in enumerate(r.get("evidence", []))],
    }
    body = [f"# {r['title']}", "", r.get("summary", "")]
    for i, e in enumerate(r.get("evidence", [])):
        body.append(f"\n[^e{i + 1}]: \"{e['quote']}\" ({e['doc']} p.{e.get('page')})")
    return "---\n" + yaml.safe_dump(header, sort_keys=False, allow_unicode=True) + "---\n" + "\n".join(body) + "\n"
