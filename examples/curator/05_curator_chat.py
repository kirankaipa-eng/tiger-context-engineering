"""Step 5: talk to the curator.

A LangGraph curator with four tools. By default it plays a short scripted conversation
with the pack steward; --interactive lets you type. Needs API_KEY / API_BASE_URL / CHAT_MODEL
(e.g. in a .env file).

    uv run python examples/curator/05_curator_chat.py [--interactive]
"""
import asyncio
import json
import os
import sys

from insurance_context.agent_runtime import Orchestrator, RunContext

from curatorlib import WORK, LocalGitBackend, content_repo, draft, find_pfas_proposal, mr_body, verify
from insurance_context import llm

llm.configured()  # loads .env
backend = LocalGitBackend()
if not backend.all():  # make sure there is something to review
    p = verify(draft(find_pfas_proposal()))
    backend.push(content_repo(), p.branch)
    backend.open_change_request(content_repo(), p.branch, p.title, mr_body(p), p.decision["owners"])


async def waiting_for(ctx: RunContext, owner: str) -> str:
    """Proposals waiting for an owner (e.g. '@pack-steward')."""
    items = [cr for cr in backend.all() if owner in cr["reviewers"] and cr["state"] == "open"]
    return json.dumps([{"id": c["id"], "title": c["title"], "opened_at": c["opened_at"]} for c in items])


async def explain(ctx: RunContext, cr_id: str) -> str:
    """The full evidence, impact and checks for a proposal."""
    return backend.get(cr_id)["body"]


async def request_changes(ctx: RunContext, cr_id: str, owner: str, note: str) -> str:
    """Record an owner's requested change as a review comment."""
    backend.comment(cr_id, owner, note)
    return f"Noted on {cr_id}. The curator will revise the branch and re-run the checks."


async def approve(ctx: RunContext, cr_id: str, owner: str) -> str:
    """Record the owner's approval. Only call when the owner explicitly says to approve."""
    try:
        cr = backend.approve(cr_id, owner)
    except PermissionError as exc:
        return f"Refused: {exc}"
    return f"Approved by {cr['approvals'][-1]['by']}. Ready for the owner to merge."


tools = [waiting_for, explain, request_changes, approve]
orch = Orchestrator(
    api_key=os.environ["API_KEY"], base_url=os.environ["API_BASE_URL"],
    model_name=os.environ.get("CHAT_MODEL", "gpt-4o"),
    system_prompt=(
        "You are the ontology curator. You help business owners review proposed changes to the "
        "insurance ontology. Call them 'proposals'. Owners are business users: never mention git, "
        "branches, merge requests or file names; say what changes in business terms. Quote the "
        "evidence (document, page) when asked why. Never approve unless the owner explicitly says so, "
        "and only for their own area. Keep answers short and plain."),
    tools=tools, tool_registry={t.__name__: (t, t.__doc__) for t in tools},
    data_dir=WORK / "agent", agent_name="Ontology curator",
)

SCRIPT = [
    "I'm @pack-steward. What's waiting for me?",
    "Why do you think PFAS needs its own code?",
    "Looks right. Approve it as @pack-steward.",
]


async def ask(text: str) -> None:
    print(f"\nOwner:   {text}")
    answer = []
    async for raw in orch.stream(text, session_id="hello-world"):
        for line in raw.splitlines():
            ev = json.loads(line) if line.strip() else {}
            if ev.get("type") == "result" and ev.get("data", {}).get("answer"):
                answer = [ev["data"]["answer"]]
            elif ev.get("type") == "chunk":
                answer.append(ev["data"]["text"])
    print("Curator: " + "".join(answer).strip().replace("\n", "\n         "))


async def main():
    if "--interactive" in sys.argv:
        while (text := input("\nOwner> ").strip()) not in ("", "quit", "exit"):
            await ask(text)
    else:
        for text in SCRIPT:
            await ask(text)

asyncio.run(main())
