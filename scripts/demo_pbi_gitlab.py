"""Git as the source of Power BI models, CI as the converter, MCP as what agents call: on a local GitLab.

    GITLAB_TOKEN=... uv run python scripts/demo_pbi_gitlab.py all [--pbix model.pbix]

Steps (each can run alone):
  image    build pbi-capture:dev (the image the models repo's CI runs in) and pbi-metrics:dev (the MCP
           server), both from this repo's code
  setup    create the models repo, push the .pbix with the CI file to main -> pipeline -> capture-v1
  change   an owner's merge request: the model as TMDL, one measure added, two tables confirmed;
           the pipeline posts what changed and the verified totals on the MR
  merge    merge it -> pipeline on main -> capture-v2, which the MCP server serves within a refresh
  ci       push the template's .gitlab-ci.yml to main (-> a new release) and run the drift job
           through a nightly pipeline schedule, played now

The token needs `api` scope on the local GitLab (an admin's, for creating the project).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "examples" / "pbi-models"
IMAGE = "pbi-capture:dev"
SERVER_IMAGE = "pbi-metrics:dev"
JOB_API = "http://gitlab:8929/api/v4"  # the API as job containers on the gitlab-ci network reach it
BRANCH = "add-external-net-sales"
from pbi_sample import MEASURE, WAREHOUSE, fetch  # noqa: E402 - the owner's change, shared with the tests' sample


class GitLab:
    def __init__(self, url: str, token: str, project: str):
        self.url, self.token, self.project = url.rstrip("/"), token, project
        self.pid = urllib.parse.quote(project, safe="")

    def api(self, path: str, method: str = "GET", data: dict | None = None):
        req = urllib.request.Request(self.url + "/api/v4" + path, json.dumps(data).encode() if data else None,
                                     {"PRIVATE-TOKEN": self.token, "Content-Type": "application/json"},
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - the local GitLab
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{method} {path}: {exc.code} {exc.read().decode()[:300]}") from None

    def git(self, work: Path, *args: str) -> str:
        auth = base64.b64encode(f"oauth2:{self.token}".encode()).decode()
        out = subprocess.run(["git", "-c", f"http.extraHeader=Authorization: Basic {auth}", *args], cwd=work,
                             check=True, capture_output=True, text=True)
        return out.stdout.strip()

    def remote(self) -> str:
        return f"{self.url}/{self.project}.git"

    def wait_pipeline(self, sha: str, source: str | None = None) -> dict:
        print(f"  waiting for the pipeline on {sha[:8]}", end="", flush=True)
        for _ in range(240):
            pipes = [p for p in self.api(f"/projects/{self.pid}/pipelines?sha={sha}") if
                     source is None or p["source"] == source]
            if pipes and pipes[0]["status"] in ("success", "failed", "canceled"):
                p = pipes[0]
                print(f" {p['status']}\n  {p['web_url']}")
                for j in self.api(f"/projects/{self.pid}/pipelines/{p['id']}/jobs"):
                    print(f"    {j['stage']:<8} {j['name']:<8} {j['status']}")
                if p["status"] != "success":
                    raise SystemExit("pipeline did not pass")
                return p
            print(".", end="", flush=True)
            time.sleep(5)
        raise SystemExit("timed out")


def step_image(_gl, _args) -> None:
    print(f"== image: building {IMAGE} and {SERVER_IMAGE} from this repo")
    with tempfile.TemporaryDirectory() as ctx:
        for name in ("pyproject.toml", "README.md"):
            shutil.copy(ROOT / name, ctx)
        shutil.copytree(ROOT / "src", Path(ctx) / "src", ignore=shutil.ignore_patterns("__pycache__"))
        import duckdb
        import sqlglot
        pins = f"duckdb=={duckdb.__version__}\nsqlglot=={sqlglot.__version__}\n"
        try:  # the MCP SDK the server was tested with (2.x renamed FastMCP)
            from importlib.metadata import version
            pins += f"mcp=={version('mcp')}\n"
        except Exception:  # noqa: BLE001 - no mcp extra here: pyproject's <2 bound still holds
            pass
        (Path(ctx) / "constraints.txt").write_text(pins)
        for tag, dockerfile in ((IMAGE, "pbi-capture.Dockerfile"), (SERVER_IMAGE, "metrics-server.Dockerfile")):
            subprocess.run(["docker", "build", "-q", "-t", tag, "-f", str(ROOT / "ci" / dockerfile), ctx], check=True)
            size = subprocess.run(["docker", "image", "inspect", "-f", "{{.Size}}", tag], check=True,
                                  capture_output=True, text=True).stdout.strip()
            print(f"  {tag}: {int(size) / 1e6:.0f} MB")


def step_setup(gl: GitLab, args) -> None:
    print(f"== setup: {gl.project} on {gl.url}")
    group, _, name = gl.project.rpartition("/")
    ns = gl.api(f"/namespaces/{urllib.parse.quote(group, safe='')}")
    gl.api("/projects", "POST", {"name": name, "path": name, "namespace_id": ns["id"], "visibility": "internal",
                                 "initialize_with_readme": False, "only_allow_merge_if_pipeline_succeeds": True,
                                 "remove_source_branch_after_merge": True})
    # a project token as a masked CI variable, so the pipeline can post its report on merge requests
    tok = gl.api(f"/projects/{gl.pid}/access_tokens", "POST", {
        "name": "capture-ci", "scopes": ["api"], "access_level": 40,
        "expires_at": time.strftime("%Y-%m-%d", time.gmtime(time.time() + 300 * 86400))})
    gl.api(f"/projects/{gl.pid}/variables", "POST", {"key": "GITLAB_TOKEN", "value": tok["token"],
                                                     "masked": True, "protected": False})
    gl.api(f"/projects/{gl.pid}/variables", "POST", {"key": "GITLAB_API", "value": JOB_API, "protected": False})
    work = Path(args.work)
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(TEMPLATE, work)
    model = work / "models" / "sales-returns"
    model.mkdir(parents=True)
    shutil.copy(args.pbix or fetch(ROOT / "build" / "pbi-sample" / "models" / "sales-returns" / "sales-returns.pbix"),
                model / "sales-returns.pbix")
    (work / ".gitattributes").write_text("*.pbix binary\n")
    gl.git(work, "init", "-q", "-b", "main")
    gl.git(work, "add", "-A")
    gl.git(work, "-c", "user.name=Model Owner", "-c", "user.email=owner@example.com", "commit", "-q", "-m",
           "Add the Sales & Returns model (.pbix as saved from Power BI Desktop)")
    gl.git(work, "remote", "add", "origin", gl.remote())
    gl.git(work, "push", "-q", "-u", "origin", "main")
    gl.wait_pipeline(gl.git(work, "rev-parse", "HEAD"))
    print_release(gl)


def step_change(gl: GitLab, args) -> None:
    print(f"== change: merge request {BRANCH}")
    sys.path.insert(0, str(ROOT / "src"))
    from insurance_context.pbimodel import read_pbix, write_tmdl
    work = Path(args.work)
    gl.git(work, "checkout", "-q", "-B", BRANCH, "main")
    model = work / "models" / "sales-returns"
    write_tmdl(read_pbix(model / "sales-returns.pbix"), model)  # the owner switches to saving as PBIP (TMDL)
    f = model / "sales-returns.SemanticModel" / "definition" / "tables" / "Analysis DAX.tmdl"
    text = f.read_text()
    f.write_text(text.replace("\tmeasure 'Net Sales PM'", MEASURE + "\tmeasure 'Net Sales PM'", 1))
    (model / "warehouse.yaml").write_text(WAREHOUSE)
    gl.git(work, "add", "-A")
    gl.git(work, "-c", "user.name=Model Owner", "-c", "user.email=owner@example.com", "commit", "-q", "-m",
           "Add External Net Sales; save the model as TMDL; confirm the Sales and Store tables")
    gl.git(work, "push", "-q", "-f", "-u", "origin", BRANCH)
    mr = gl.api(f"/projects/{gl.pid}/merge_requests", "POST", {
        "source_branch": BRANCH, "target_branch": "main", "title": "Add External Net Sales",
        "description": "Net sales through external stores, for the channel review."})
    print(f"  merge request !{mr['iid']}: {mr['web_url']}")
    gl.wait_pipeline(gl.git(work, "rev-parse", "HEAD"), source="merge_request_event")
    for _ in range(12):
        notes = [n for n in gl.api(f"/projects/{gl.pid}/merge_requests/{mr['iid']}/notes")
                 if n["body"].startswith("# Power BI capture")]
        if notes:
            print("\n  the pipeline's note on the merge request:\n")
            print("    " + notes[0]["body"].replace("\n", "\n    "))
            return
        time.sleep(2)


def step_merge(gl: GitLab, args) -> None:
    print("== merge")
    mr = gl.api(f"/projects/{gl.pid}/merge_requests?state=opened&source_branch={BRANCH}")[0]
    for _ in range(30):  # mergeability is computed asynchronously
        if gl.api(f"/projects/{gl.pid}/merge_requests/{mr['iid']}")["detailed_merge_status"] == "mergeable":
            break
        time.sleep(2)
    merged = gl.api(f"/projects/{gl.pid}/merge_requests/{mr['iid']}/merge", "PUT", {})
    print(f"  merged !{mr['iid']} as {merged['merge_commit_sha'][:8]}")
    gl.wait_pipeline(merged["merge_commit_sha"])
    print_release(gl)


def step_ci(gl: GitLab, args) -> None:
    print("== ci: the template's .gitlab-ci.yml on main, then the drift job")
    work = Path(args.work)
    gl.git(work, "fetch", "-q", "origin")
    gl.git(work, "checkout", "-q", "-B", "main", "origin/main")
    vars_ = {v["key"] for v in gl.api(f"/projects/{gl.pid}/variables")}
    if "GITLAB_API" not in vars_:  # the template no longer names the job-side API URL itself
        gl.api(f"/projects/{gl.pid}/variables", "POST", {"key": "GITLAB_API", "value": JOB_API, "protected": False})
    shutil.copy(TEMPLATE / ".gitlab-ci.yml", work / ".gitlab-ci.yml")
    shutil.copy(TEMPLATE / "README.md", work / "README.md")
    if gl.git(work, "status", "--porcelain"):
        gl.git(work, "add", "-A")
        gl.git(work, "-c", "user.name=Platform", "-c", "user.email=platform@example.com", "commit", "-q", "-m",
               "CI: release resource group, kept artifacts, MR note edited in place, nightly drift check")
        gl.git(work, "push", "-q", "origin", "main")
        gl.wait_pipeline(gl.git(work, "rev-parse", "HEAD"), source="push")
        print_release(gl)
    sched = next((s for s in gl.api(f"/projects/{gl.pid}/pipeline_schedules")
                  if s["description"] == "nightly warehouse drift"), None) or \
        gl.api(f"/projects/{gl.pid}/pipeline_schedules", "POST", {
            "description": "nightly warehouse drift", "ref": "main", "cron": "0 5 * * *",
            "cron_timezone": "UTC", "active": True})
    before = {p["id"] for p in gl.api(f"/projects/{gl.pid}/pipelines?source=schedule")}
    gl.api(f"/projects/{gl.pid}/pipeline_schedules/{sched['id']}/play", "POST", {})
    for _ in range(30):
        new = [p for p in gl.api(f"/projects/{gl.pid}/pipelines?source=schedule") if p["id"] not in before]
        if new:
            gl.wait_pipeline(new[0]["sha"], source="schedule")
            return
        time.sleep(2)
    raise SystemExit("the schedule did not start a pipeline")


def print_release(gl: GitLab) -> None:
    rels = gl.api(f"/projects/{gl.pid}/releases")
    if rels:
        r = rels[0]
        print(f"  released {r['tag_name']}: {r['_links']['self']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("step", choices=["image", "setup", "change", "merge", "ci", "all"])
    p.add_argument("--pbix", help="the .pbix to import (setup; default: Microsoft's sample, downloaded)")
    p.add_argument("--url", default="http://localhost:8929")
    p.add_argument("--project", default="root/pbi-models")
    p.add_argument("--work", default=str(ROOT / "build" / "pbi-models"), help="local clone of the models repo")
    args = p.parse_args()
    token = os.environ.get("GITLAB_TOKEN") or ("" if args.step == "image" else
                                              sys.exit("set GITLAB_TOKEN (api scope on the local GitLab)"))
    gl = GitLab(args.url, token, args.project)
    steps = {"image": step_image, "setup": step_setup, "change": step_change, "merge": step_merge, "ci": step_ci}
    for name in (steps if args.step == "all" else [args.step]):
        steps[name](gl, args)
        print()


if __name__ == "__main__":
    main()
