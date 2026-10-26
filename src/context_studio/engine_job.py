"""Engine checks on a proposal branch, run in a subprocess.

The engine reads its content at import, so a branch is checked in a fresh process
with ICTX_CONTENT_DIR pointing at the branch's worktree:

    ICTX_CONTENT_DIR=<worktree> python -m context_studio.engine_job < job.json > result.json

job: {"client", "raw": raw warehouse or null, "scratch": temp db path,
      "metrics": [...], "by": [...], "as_of": "YYYY-MM-DD" | null}
"""

from __future__ import annotations

import json
import shutil
import sys
import traceback
from datetime import date


def plain(v):
    """Warehouse values as JSON numbers (DuckDB returns Decimal for money)."""
    from decimal import Decimal
    return float(v) if isinstance(v, Decimal) else v


def _item(cid: str, label: str, ok: bool, detail: str = "") -> dict:
    return {"id": cid, "label": label, "ok": bool(ok), "detail": detail}


def run(job: dict) -> dict:
    from insurance_context import sqlgen
    from insurance_context.client import Client
    from insurance_context.semantic import SemanticLayer

    out: dict = {"items": [], "findings": [], "values": None}
    items = out["items"]
    try:
        client = Client(job["client"])
        client.resolved
        for u in client.units:
            u.resolved
        items.append(_item("builds", "Ontology builds", True))
    except Exception as exc:  # the whole point is to report, not to crash
        items.append(_item("builds", "Ontology builds", False, str(exc)))
        return out

    sl = SemanticLayer(client)
    problems = sl.validate()
    items.append(_item("metrics", "Metrics resolve", not problems, "; ".join(problems[:5])))
    if job.get("tmdl") and not problems:
        # Power BI measures generated from governed metrics follow the definitions on this branch.
        import re
        from insurance_context import powerbi
        from insurance_context.overlay import CONTENT_ROOT
        current = (CONTENT_ROOT / job["tmdl"]).read_text()
        names = [n for n in re.findall(r"annotation GovernedMetric = (\w+)", current) if n in sl.metrics]
        names += [n for n in job.get("set_metrics") or [] if n in sl.metrics and n not in names]
        out["tmdl"] = powerbi.to_tmdl(sl, names)[0] + "\n"
    issues = [i for d in sqlgen.DIALECTS for i in sqlgen.portability_issues(client, d)]
    items.append(_item("portable", "SQL compiles on all platforms", not issues, "; ".join(issues[:5])))

    if job.get("raw"):
        import duckdb
        from insurance_context.validate import validate
        shutil.copy(job["raw"], job["scratch"])
        con = duckdb.connect(job["scratch"])
        try:
            sqlgen.install(client, con)
            items.append(_item("views", "Views install on the reference data", True))
            findings = validate(client, con)
            out["findings"] = [f.as_dict() for f in findings]
            if job.get("metrics"):
                as_of = date.fromisoformat(job["as_of"]) if job.get("as_of") else None
                res = sl.query(con, job["metrics"], by=job.get("by") or [], as_of=as_of)
                out["values"] = {"columns": list(res.columns), "rows": [[plain(v) for v in r] for r in res.rows]}
            items.append(_item("reconcile", "Totals reconcile", True))
        except Exception as exc:
            items.append(_item("views", "Views install on the reference data", False, str(exc)))
        finally:
            con.close()
    return out


def main() -> None:
    job = json.loads(sys.stdin.read())
    try:
        result = run(job)
    except Exception:
        result = {"items": [_item("error", "Engine check", False, traceback.format_exc(limit=3))],
                  "findings": [], "values": None}
    sys.stdout.write(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
