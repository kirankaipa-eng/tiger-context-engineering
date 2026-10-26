"""Step 4: open the change request through a git backend.

    uv run python examples/curator/04_open_change_request.py                      # local, offline
    uv run python examples/curator/04_open_change_request.py --remote <ssh-url>   # prints the push (dry run)
    uv run python examples/curator/04_open_change_request.py --remote <ssh-url> --execute
"""
import sys

from curatorlib import GitLabPushBackend, LocalGitBackend, content_repo, draft, find_pfas_proposal, mr_body, verify

p = verify(draft(find_pfas_proposal()))
if not p.checks.get("passed"):
    sys.exit(f"Checks failed, sending a question instead of a merge request: {p.checks}")

repo = content_repo()
if "--remote" in sys.argv:
    backend = GitLabPushBackend(sys.argv[sys.argv.index("--remote") + 1], execute="--execute" in sys.argv)
else:
    backend = LocalGitBackend()

backend.push(repo, p.branch)
cr = backend.open_change_request(repo, p.branch, p.title, mr_body(p), p.decision["owners"])

if cr.get("dry_run"):
    print("Dry run. This command pushes the branch and opens the change request on the git host:\n")
    print(" \\\n  ".join(a if " " not in a else repr(a) for a in cr["command"]))
else:
    print(f"Opened change request {cr.get('id', '')} for {', '.join(p.decision['owners'])}\n")
    print(cr.get("body", cr.get("output", "")))
