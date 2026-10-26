"""Step 3: draft the change on a branch of the content repo, then run the checks.

Creates examples/curator/.work/content (a local git repo) on first run.

    uv run python examples/curator/03_draft_and_verify.py
"""
from curatorlib import content_repo, draft, find_pfas_proposal, run, verify

p = find_pfas_proposal()
p = draft(p)
repo = content_repo()
print(f"Branch {p.branch} in {repo}\n")
print(run(["git", "show", "--stat", "--format=%an: %s", "HEAD"], repo))
print("\nDiff to the pack:\n" + run(["git", "diff", "main", "--", "ontology/"], repo))

p = verify(p)
c = p.checks
print("Checks:")
print(f"  ontology resolves ............ {c.get('resolves')}")
print(f"  metric definitions ok ........ {not c.get('metric_problems')}")
print(f"  SQL portable (4 platforms) ... {c.get('portability_issues') == 0}")
print(f"  PFAS data finding cleared .... {c.get('needle_finding_cleared')}")
print(f"\n{'PASS: ready for a merge request' if c.get('passed') else 'FAIL: ' + str(c)}")
