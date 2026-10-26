"""CI for a repo of Power BI models: every merge request and every merge to main re-captures them.

    models/<name>/<name>.pbix              a model as Power BI Desktop saves it (first import), and/or
    models/<name>/<name>.SemanticModel/    the same model as TMDL (PBIP save; wins when both exist)
    models/<name>/warehouse.yaml           optional: catalog, schema, confirmed tables, column names, SQL
    models/<name>/access.yaml              optional: who is in each row-level security role

    ictx pbi ci models --out public        convert -> capture -> diff -> verify, per model
    ictx pbi publish public                on main: release capture-v<N> pointing at those files
    ictx pbi drift --release latest        scheduled: the warehouse still has every mapped table and column

Stages, per model:
  convert  .pbix -> TMDL (skipped when the repo already holds TMDL)
  capture  TMDL -> capture.yaml (metrics, tables, joins), with warehouse.yaml and access.yaml applied
  diff     against the capture last published from main: metrics added, removed, changed
  verify   every captured metric compiles for Databricks (sqlglot) and its total runs on each warehouse
           available: the local stand-in built from the .pbix's rows and, with DATABRICKS_* set,
           Databricks (one query per model per warehouse; per metric only to locate a failure).
           A metric is run as it must be asked (BILayer.needs: a period metric at its date grain, a
           rank by the column it ranks) with each what-if parameter at its default (the midpoint of
           its range when it has none); the report says which row and which values it used.
  version  version.json: v<N> on main (one past the newest release), commit, job, counts; what the MCP
           server stamps answers with

A captured metric that fails to compile or run fails the pipeline, so a published version always
answers. The report (report.md, kept as one note on the merge request, edited on every pipeline,
when GITLAB_TOKEN is set) is what a reviewer reads instead of the DAX. It counts the model's measures
as captured (agents can compute them), display-only (text for a visual, e.g. a FORMAT label; most
name the captured number they show) and not captured (with why).

GitLab is reached at GITLAB_API (default CI_API_V4_URL) with GITLAB_TOKEN, else the job token. Off
GitLab (Azure DevOps, a laptop) `ci` and `drift` run on folders and skip every GitLab call; `publish`
and `drift --release` need GitLab.
"""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
import yaml

from .bi_agent import _fmt, _num, metric_needs, param_range
from .bilayer import CAPTURE, BILayer, LayerError, Warehouse, build_spec, build_standin, parse_ref, ref, save, snake
from .pbimodel import read_pbix, read_tmdl, write_tmdl

VERSION = "version.json"
REPORT = "report.md"
NOTE_MARK = "<!-- ictx-pbi-capture -->"
DRIFT_LABEL = "warehouse-drift"
WAREHOUSE_KEYS = ("catalog", "schema", "tables", "columns", "sql")
DATABRICKS_VARS = "DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH and DATABRICKS_TOKEN (or DATABRICKS_CLIENT_ID + " \
                  "DATABRICKS_CLIENT_SECRET)"


def _ci() -> str | None:
    """Which CI this runs in: gitlab, azure, or None (a laptop, a test)."""
    if os.environ.get("GITLAB_CI"):
        return "gitlab"
    if os.environ.get("TF_BUILD"):
        return "azure"
    return None


# ------------------------------------------------------------------ convert + capture

def apply_warehouse(spec: dict, conf: dict, where: str | Path = "warehouse.yaml") -> dict:
    """warehouse.yaml onto a capture, every name checked against the model:

        catalog: pilot                      schema: sales            (defaults for assumed table names)
        tables:  {Sales: pilot.sales.fact_sales}                     confirmed warehouse table
        columns: {Sales: {Amount: net_amount}}                       model column -> warehouse column
        sql:     {Store: "SELECT ... FROM pilot.sales.store ..."}    the table's rows come from this query
    """
    unknown = sorted(set(conf) - set(WAREHOUSE_KEYS))
    if unknown:
        raise LayerError(f"{where}: unknown key {', '.join(map(repr, unknown))}; expected {', '.join(WAREHOUSE_KEYS)}")

    def table(name: str, key: str) -> dict:
        if name not in spec["tables"]:
            raise LayerError(f"{where}: {key}: no table {name!r} in the model; it has "
                             f"{', '.join(sorted(spec['tables']))}")
        t = spec["tables"][name]
        if not t.get("columns"):
            raise LayerError(f"{where}: {key}: {name} has no warehouse source ({t.get('why', 'not mapped')})")
        return t

    for name, target in (conf.get("tables") or {}).items():
        t = table(name, "tables")
        if not isinstance(target, str) or len(target.split(".")) != 3:
            raise LayerError(f"{where}: tables: {name}: {target!r} is not catalog.schema.table")
        t.update({"table": target, "from": "confirmed in warehouse.yaml"})
    for name, cols in (conf.get("columns") or {}).items():
        t = table(name, "columns")
        if not isinstance(cols, dict):
            raise LayerError(f"{where}: columns: {name}: expected {{ModelColumn: warehouse_column}}")
        for col, phys in cols.items():
            if col not in t["columns"]:
                raise LayerError(f"{where}: columns: no column {ref(name, col)} in the model; {name} has "
                                 f"{', '.join(t['columns'])}")
            t["columns"][col]["column"] = str(phys)
    for name, sql in (conf.get("sql") or {}).items():
        t = table(name, "sql")
        try:
            sqlglot.parse_one(str(sql), read="databricks")
        except sqlglot.errors.ParseError as exc:
            raise LayerError(f"{where}: sql: {name}: not valid Databricks SQL: {exc}") from None
        t.update({"sql": str(sql).strip(), "from": "SQL in warehouse.yaml"})  # "table" stays: the stand-in's copy
    return spec


def apply_access(spec: dict, path: Path) -> bool:
    """access.yaml's role members into spec["security"]["members"]; False while bisecurity cannot read it."""
    from . import bisecurity
    if not hasattr(bisecurity, "load_access"):
        return False
    roles = (spec.get("security") or {}).get("roles") or {}
    try:
        members = bisecurity.load_access(path, roles)  # a role the model does not define is an error
    except ValueError as exc:
        raise LayerError(str(exc)) from None
    spec.setdefault("security", {"roles": roles})["members"] = members
    return True


def build_model(src: str | Path, out: str | Path, catalog: str = "pilot") -> dict:
    """One model folder of the repo -> out/ holding TMDL, capture.yaml and (with a .pbix) the stand-in."""
    src, out = Path(src), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    conf = (yaml.safe_load((src / "warehouse.yaml").read_text()) or {}) if (src / "warehouse.yaml").exists() else {}
    catalog = conf.get("catalog", catalog)
    pbix = next(iter(sorted(src.glob("*.pbix"))), None)
    tmdl = next(iter(sorted(src.glob("*.SemanticModel"))), None)
    if tmdl:
        shutil.copytree(tmdl, out / tmdl.name, dirs_exist_ok=True)
        converted = f"TMDL in the repo ({tmdl.name})"
    elif pbix:
        write_tmdl(read_pbix(pbix), out)
        converted = f"converted from {pbix.name}"
    else:
        raise LayerError(f"{src}: no .pbix and no .SemanticModel folder")
    model = read_tmdl(out)
    model.name = src.name
    spec = build_spec(model, catalog, conf.get("schema", snake(src.name)))
    apply_warehouse(spec, conf, src / "warehouse.yaml")
    spec["source"] = converted
    if (src / "access.yaml").exists() and not apply_access(spec, src / "access.yaml"):
        spec["source"] += " (access.yaml not applied: this build cannot read it yet)"
    save(spec, out)
    if pbix:
        build_standin(pbix, BILayer(spec), out / f"{catalog}.duckdb")
    return spec


# ------------------------------------------------------------------ counts

def _value_parts(metrics: dict) -> set[str]:
    """Metrics captured only as the number behind a display-only measure (`<key>_value`): not model measures."""
    return {m["value"] for k, m in metrics.items()
            if m.get("status") == "presentation" and m.get("value") == f"{k}_value" and m["value"] in metrics}


def measure_counts(spec: dict) -> dict:
    """The model's measures by status. presentation = display-only; presentation_with_value = those
    that name the captured metric whose number they show."""
    metrics = spec.get("metrics") or {}
    parts = _value_parts(metrics)
    ms = [m for k, m in metrics.items() if not m.get("hidden") and k not in parts]
    c = {"measures": len(ms), **{s: sum(1 for m in ms if m.get("status") == s)
                                 for s in ("captured", "presentation", "not_captured")}}
    c["presentation_with_value"] = sum(1 for m in ms if m.get("status") == "presentation" and m.get("value"))
    return c


def counts_line(c: dict, bold: bool = False) -> str:
    """"59 measures: 50 captured, 8 display-only (6 show a captured number), 1 not captured"."""
    shows = c.get("presentation_with_value") or 0
    disp = f"{c['presentation']} display-only" + (
        f" ({'all' if shows == c['presentation'] and shows > 1 else shows} show{'s' if shows == 1 else ''} "
        f"a captured number)" if shows else "")
    cap = f"{c['captured']} captured"
    return f"{c['measures']} measures: {'**' + cap + '**' if bold else cap}, {disp}, {c['not_captured']} not captured"


def _status(m: dict) -> str:
    s = m.get("status")
    if s == "presentation":
        return f"display-only, shows `{m['value']}`" if m.get("value") else "display-only"
    return {"not_captured": "not captured"}.get(s, s or "captured")


# ------------------------------------------------------------------ diff

_SHAPE = ("status", "dax", "table", "agg", "expr", "filter", "formula", "time", "context", "latest", "rank",
          "by_scope", "value")


def _definition(m: dict) -> dict:
    return {k: m.get(k) for k in _SHAPE}


def diff(old: dict | None, new: dict) -> list[str]:
    """What changed between two captures, as lines a reviewer reads."""
    if not old:
        return ["first capture of this model"]
    shown = lambda s: {k: m for k, m in (s.get("metrics") or {}).items() if not m.get("hidden")}  # noqa: E731
    a, b = shown(old), shown(new)
    out = []
    for k in sorted(b.keys() - a.keys()):
        out.append(f"+ metric `{k}` ({_status(b[k])}): `{b[k].get('dax', '')}`")
    for k in sorted(a.keys() - b.keys()):
        out.append(f"- metric `{k}` removed")
    for k in sorted(a.keys() & b.keys()):
        if _definition(a[k]) == _definition(b[k]):
            continue
        what = (f"{_status(a[k])} -> {_status(b[k])}" if _status(a[k]) != _status(b[k]) else "definition changed")
        out.append(f"~ metric `{k}`: {what}\n    was `{a[k].get('dax', '')}`\n    now `{b[k].get('dax', '')}`")
    pa, pb = old.get("parameters") or {}, new.get("parameters") or {}
    out += [f"+ what-if parameter {param_range(p, pb[p])}" for p in sorted(pb.keys() - pa.keys())]
    out += [f"- what-if parameter `{p}` removed" for p in sorted(pa.keys() - pb.keys())]
    out += [f"~ what-if parameter {param_range(p, pb[p])} (was {param_range(p, pa[p]).split(': ', 1)[-1]})"
            for p in sorted(pa.keys() & pb.keys()) if pa[p] != pb[p]]
    ta, tb = old.get("tables") or {}, new.get("tables") or {}
    for t in sorted(ta.keys() | tb.keys()):
        x, y = ta.get(t) or {}, tb.get(t) or {}
        if x.get("table") != y.get("table") or x.get("from") != y.get("from") or x.get("sql") != y.get("sql"):
            why = y.get("from", "removed")
            out.append(f"~ table `{t}`: {y.get('table')} ({why})" if x.get("table") == y.get("table")
                       else f"~ table `{t}`: {x.get('table')} -> {y.get('table')} ({why})")
        for c in sorted((x.get("columns") or {}).keys() & (y.get("columns") or {}).keys()):
            was, now = x["columns"][c].get("column"), y["columns"][c].get("column")
            if was != now:
                out.append(f"~ column `{ref(t, c)}`: warehouse column {was} -> {now}")
    ja = {(j["from"], j["to"]) for j in old.get("joins") or []}
    jb = {(j["from"], j["to"]) for j in new.get("joins") or []}
    out += [f"+ relationship {f} -> {t}" for f, t in sorted(jb - ja)]
    out += [f"- relationship {f} -> {t}" for f, t in sorted(ja - jb)]
    return out


# ------------------------------------------------------------------ verify

def _databricks_gap() -> str | None:
    e = os.environ.get
    gap = [] if e("DATABRICKS_HTTP_PATH") else ["DATABRICKS_HTTP_PATH"]
    if not e("DATABRICKS_TOKEN") and not (e("DATABRICKS_CLIENT_ID") and e("DATABRICKS_CLIENT_SECRET")):
        gap.append("DATABRICKS_TOKEN (or DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET)")
    return f"set {' and '.join(gap)}" if gap else None


def warehouses(folder: str | Path, layer: BILayer) -> tuple[list[Warehouse], list[str], bool]:
    """The warehouses a capture can be checked on, a line per warehouse saying checked or why not, and
    False when Databricks is half configured (a pipeline that thinks it checks Databricks must)."""
    found, lines, ok = [], [], True
    try:
        wh = Warehouse.open(folder, layer, prefer="local")
        found.append(wh)
        lines.append(f"local stand-in `{Path(wh.target).name}` (the .pbix's rows): checked")
    except LayerError:
        lines.append("local stand-in: none (no .pbix in the repo to copy rows from)")
    try:
        wh = Warehouse.open(folder, layer, prefer="databricks")
    except LayerError:
        lines.append(f"Databricks: not configured (set {DATABRICKS_VARS} as CI variables)")
    else:
        gap = _databricks_gap()
        if gap:
            ok = False
            lines.append(f"Databricks `{wh.target}`: **misconfigured**, {gap}")
        else:
            found.append(wh)
            lines.append(f"Databricks `{wh.target}`: checked")
    return found, lines, ok


def param_value(info: dict) -> tuple[float | None, str]:
    """The value verify runs a what-if parameter at: its default, else the midpoint of its range
    (on a step of the slider), and how it was chosen."""
    if info.get("default") is not None:
        return info["default"], "default"
    lo, hi, step = info.get("min"), info.get("max"), info.get("step")
    if lo is None or hi is None:
        return None, "no default and no range"
    mid = (lo + hi) / 2
    if step:
        mid = lo + math.floor((mid - lo) / step + 0.5) * step
    mid = round(mid, 10)
    return (int(mid) if float(mid).is_integer() and isinstance(lo, int) else mid), "midpoint, no default"


def _plan(layer: BILayer, key: str) -> tuple[list[str], dict, dict]:
    """How verify asks for a metric: the groupings it needs, the what-if values, and how each was chosen."""
    n = metric_needs(layer, key)
    by = list(n["by"])
    scope = layer.metrics[key].get("by_scope") or []
    if scope and not any("else" in b for b in scope):  # a scope switch with no else: ask by its first column
        cols = [b["column"] for b in scope if "column" in b]
        on = {parse_ref(d)[:2] for d in by}
        if cols and not any(parse_ref(c)[:2] in on for c in cols):
            by.append(cols[0])
    params, how = {}, {}
    for p, info in n["params"].items():
        v, why = param_value(info)
        if v is None:
            raise LayerError(f"what-if parameter {p}: {why}; give it a default in the model")
        params[p], how[p] = v, {"value": v, "chosen": why, "label": info.get("label") or p}
    return by, params, how


def _compile(layer: BILayer, keys: list[str], by: list[str], params: dict, dialect: str) -> str:
    extra = {"params": params} if params else {}  # a compiler without what-if never sees the argument
    return layer.compile(keys, by=by, dialect=dialect, **extra)


def verify(layer: BILayer, folder: str | Path, targets: list[Warehouse] | None = None) -> list[dict]:
    """Each captured metric: its Databricks SQL compiles, and it runs on every warehouse given (default:
    every one available). Metrics that need no grouping run in one query per warehouse (alone only to
    say which one broke). A metric that needs a grouping (a period grain, a rank's column) runs alone
    at it; the value reported is the latest period's (dates only) or the first row's, and `at` says
    which. What-if parameters are set to their default, else their midpoint; `params` says which."""
    targets = warehouses(folder, layer)[0] if targets is None else targets
    rows, plans = {}, {}
    for key, m in layer.captured().items():
        row = {"metric": key, "label": m["label"], "ok": True}
        try:
            by, params, how = _plan(layer, key)
            plans[key] = (by, params)
            row.update({**({"by": by} if by else {}), **({"params": how} if how else {})})
            sqlglot.parse_one(_compile(layer, [key], by, params, "databricks"), read="databricks")
        except Exception as exc:  # noqa: BLE001 - a failure is a result here
            row.update(ok=False, error=f"compile: {exc}")
        rows[key] = row
    flat = [k for k, r in rows.items() if r["ok"] and not r.get("by")]
    flat_params = {p: v for k in flat for p, v in plans[k][1].items()}
    for wh in targets:
        alone = [k for k, r in rows.items() if r["ok"] and r.get("by")]
        try:
            if flat:
                _, got = wh.query(_compile(layer, flat, [], flat_params, wh.dialect))
                for k, v in zip(flat, got[0] if got else [None] * len(flat)):
                    rows[k][wh.kind] = v
        except Exception:  # noqa: BLE001 - find the metric that broke it
            alone += flat
        for k in alone:
            by, params = plans[k]
            try:
                _, got = wh.query(_compile(layer, [k], by, params, wh.dialect))
                if by:
                    dated = all(parse_ref(d)[2] for d in by)
                    has = [r for r in got if r[-1] is not None] or got
                    pick = (has[-1] if dated else has[0]) if has else None  # by period: the latest one
                    rows[k][wh.kind] = pick[-1] if pick else None
                    if pick and "at" not in rows[k]:  # the first warehouse's row names the cell
                        rows[k]["at"] = {"latest": dated, "row": dict(zip(by, pick[:len(by)]))}
                else:
                    rows[k][wh.kind] = got[0][0] if got else None
            except Exception as exc:  # noqa: BLE001
                rows[k].update(ok=False, error=f"{wh.kind}: {exc}")
    return list(rows.values())


def _used(c: dict) -> str:
    """What verify ran a metric at, for a reader: "latest Calendar[Date]:month = 2026-02; what-if ..."."""
    parts = []
    if c.get("at"):
        at = ", ".join(f"{d} = {v}" for d, v in c["at"]["row"].items())
        parts.append(("latest " if c["at"]["latest"] else "at ") + at)
    elif c.get("by"):
        parts.append(f"by {', '.join(c['by'])}")
    for p in (c.get("params") or {}).values():
        parts.append(f"{p['label']} = {_num(p['value'])} ({p['chosen']})")
    return "; ".join(parts)


# ------------------------------------------------------------------ GitLab

def _api(path: str, method: str = "GET", data: dict | None = None, raw: bool = False):
    base = os.environ.get("GITLAB_API") or os.environ.get("CI_API_V4_URL")
    if not base:
        raise LayerError("not on GitLab CI: set GITLAB_API (e.g. https://gitlab.example.com/api/v4) and GITLAB_TOKEN")
    token = os.environ.get("GITLAB_TOKEN")
    if token:
        headers = {"PRIVATE-TOKEN": token}
    elif os.environ.get("CI_JOB_TOKEN"):
        headers = {"JOB-TOKEN": os.environ["CI_JOB_TOKEN"]}
    else:
        raise LayerError("set GITLAB_TOKEN (a token with api scope on the models repo)")
    body = None
    if data is not None:
        body, headers["Content-Type"] = json.dumps(data).encode(), "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, body, headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310 - the GitLab API URL from CI
            out = r.read()
    except urllib.error.URLError as exc:
        if isinstance(exc, urllib.error.HTTPError):
            raise
        raise LayerError(f"GitLab API {base} is unreachable from here ({exc.reason}); set GITLAB_API to the "
                         "API URL as this job reaches it") from None
    return out if raw else json.loads(out or b"null")


def _project(project: str | None = None) -> str:
    pid = project or os.environ.get("CI_PROJECT_ID")
    if not pid:
        raise LayerError("name the models repo (--project group/pbi-models) or run in its GitLab CI")
    return urllib.parse.quote(str(pid), safe="")


def _missing(path: str) -> bool:
    try:
        _api(path)
        return False
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return True
        raise


def published_files(job_id: int | None = None, project: str | None = None) -> zipfile.ZipFile | None:
    """The capture files of the newest capture-v* release (or of a given job), None before the first.
    Not "the latest artifacts on main": a nightly drift pipeline is newer and has no capture job."""
    pid = _project(project)
    if job_id is None:
        tags = capture_tags(project)
        job_id = _release_job(tags[0], project) if tags else None
        if job_id is None:
            return None
    try:
        return zipfile.ZipFile(io.BytesIO(_api(f"/projects/{pid}/jobs/{job_id}/artifacts", raw=True)))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _release_job(tag: str, project: str | None = None) -> int | None:
    """The CI job whose artifacts a release links as its capture."""
    rel = _api(f"/projects/{_project(project)}/releases/{urllib.parse.quote(tag)}")
    link = next((x["url"] for x in rel["assets"]["links"] if x["name"] == "capture"), "")
    job = re.search(r"/jobs/(\d+)/artifacts", link)
    return int(job.group(1)) if job else None


def capture_tags(project: str | None = None) -> list[str]:
    """capture-v<N> release tags, newest first."""
    rels = _api(f"/projects/{_project(project)}/releases?per_page=100") or []
    tags = [r["tag_name"] for r in rels if re.fullmatch(r"capture-v\d+", r["tag_name"])]
    return sorted(tags, key=lambda t: -int(t.removeprefix("capture-v")))


def fetch_release(tag: str, dest: str | Path, project: str | None = None) -> Path:
    """A release's capture files (the latest when tag is "latest") unpacked under dest; the folder
    holding version.json is returned."""
    if tag == "latest":
        tags = capture_tags(project)
        if not tags:
            raise LayerError("no capture-v* release yet; merge to main so CI publishes one")
        tag = tags[0]
    job = _release_job(tag, project)
    zf = published_files(job, project) if job else None
    if zf is None:
        raise LayerError(f"release {tag}: its capture files are gone (no capture link, or its job's artifacts expired)")
    dest = Path(dest)
    zf.extractall(dest)
    return next((p.parent for p in sorted(dest.rglob(VERSION))), dest)


def _previous(zf: zipfile.ZipFile | None, out: Path, name: str) -> dict | None:
    if zf is None:
        return None
    try:
        return yaml.safe_load(zf.read(f"{out.name}/{name}/{CAPTURE}"))
    except KeyError:
        return None


def note_on_merge_request(report: str) -> str:
    """The report as the pipeline's one note on the merge request: edited in place on every pipeline."""
    env = os.environ.get
    base = f"/projects/{env('CI_PROJECT_ID')}/merge_requests/{env('CI_MERGE_REQUEST_IID')}/notes"
    body = f"{report}\n\n{NOTE_MARK}"
    mine = [n for n in _api(f"{base}?per_page=100&sort=desc&order_by=updated_at") or [] if NOTE_MARK in n["body"]]
    if mine:
        _api(f"{base}/{mine[0]['id']}", "PUT", {"body": body})
        return f"updated note {mine[0]['id']}"
    return f"posted note {_api(base, 'POST', {'body': body})['id']}"


# ------------------------------------------------------------------ the CI entrypoints

def run_ci(models: str | Path, out: str | Path, previous: str | Path | None = None) -> tuple[bool, str]:
    """Every model folder under `models`: build, diff against what main last published, verify.
    `previous` is a folder of an earlier `out` (tests, local runs, Azure DevOps); on GitLab it is
    fetched from the API."""
    models, out = Path(models), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    ci = _ci()
    zf = published_files() if ci == "gitlab" and previous is None else None
    lines, ok, built = [], True, {}
    for src in sorted(p for p in models.iterdir() if p.is_dir()):
        dest = out / src.name
        try:
            spec = build_model(src, dest)
        except (LayerError, OSError) as exc:
            ok = False
            built[src.name] = {"error": str(exc)}
            lines += [f"## {src.name}", "", f"**Not captured**: {exc}", ""]
            continue
        old = (yaml.safe_load((Path(previous) / src.name / CAPTURE).read_text())
               if previous and (Path(previous) / src.name / CAPTURE).exists() else _previous(zf, out, src.name))
        layer = BILayer(spec)
        targets, checked, wh_ok = warehouses(dest, layer)
        checks = verify(layer, dest, targets)
        bad = [c for c in checks if not c["ok"]]
        ok &= not bad and wh_ok
        count = measure_counts(spec)
        assumed = sorted(t for t, v in spec["tables"].items() if str(v.get("from", "")).startswith("assumed"))
        kinds = [k for k in ("local", "databricks") if any(w.kind == k for w in targets)]
        shown = kinds or ["total"]
        built[src.name] = {**count, "source": spec["source"], "verified_on": kinds}
        lines += [f"## {src.name}", "", f"{spec['source']}. {counts_line(count, bold=True)}.", "",
                  "**Changes since the published version**", ""]
        lines += [f"- {d}" for d in diff(old, spec)] or ["- no metric changes"]
        lines += ["", "**Warehouses checked**", ""] + [f"- {x}" for x in checked]
        lines += ["", "**Verify** (Databricks SQL compiles; " + ("totals on each warehouse checked)" if kinds
                                                              else "no warehouse to run on: compile only)"), "",
                  "| metric | " + "".join(f"{k} | " for k in shown) + " |", "|---|" + "---|" * (1 + len(shown))]
        for c in checks:
            vals = "".join(f"{_fmt(c.get(k))} | " for k in shown)
            used = _used(c)
            status = (f"ok ({used})" if used else "ok") if c["ok"] else c["error"]
            lines.append(f"| `{c['metric']}` | {vals}{status} |")
        lines += _measure_lines(spec)
        sec = spec.get("security") or {}
        if sec.get("roles"):
            n, members = len(sec["roles"]), sec.get("members") or {}
            done = sum(1 for r in sec["roles"].values() if r.get("status") == "captured")
            lines += ["", f"Row-level security: {n} roles ({done} captured)" + (
                f", members for {len(members)} of them from access.yaml; a caller in no role is denied."
                if (src / "access.yaml").exists() else
                ". **No access.yaml: every caller will be denied** (add models/"
                f"{src.name}/access.yaml naming each role's members).")]
            empty = sorted(set(sec["roles"]) - {r for r, m in members.items() if m})
            if (src / "access.yaml").exists() and empty:
                lines.append(f"Roles with no members (nobody gets their rows): {', '.join(empty)}")
        if assumed:
            lines += ["", f"{len(assumed)} tables still have an assumed warehouse name "
                          f"(confirm in warehouse.yaml): {', '.join(assumed)}"]
        lines.append("")
    version = _version(ci)
    version.update(built_at=datetime.now(timezone.utc).isoformat(timespec="seconds"), models=built, ok=ok)
    (out / VERSION).write_text(json.dumps(version, indent=2))
    report = f"# Power BI capture {version['version']} ({version['commit'] or 'local'})\n\n" + "\n".join(lines)
    (out / REPORT).write_text(report)
    env = os.environ.get
    if ci == "gitlab" and env("CI_MERGE_REQUEST_IID"):
        if not env("GITLAB_TOKEN"):
            print("note: set GITLAB_TOKEN (api scope) to keep the report as a note on the merge request",
                  file=sys.stderr)
        else:
            try:
                print(f"merge request !{env('CI_MERGE_REQUEST_IID')}: {note_on_merge_request(report)}",
                      file=sys.stderr)
            except (LayerError, urllib.error.HTTPError) as exc:  # the report is in the artifacts regardless
                print(f"warning: could not note the merge request: {exc}", file=sys.stderr)
    return ok, report


def _measure_lines(spec: dict) -> list[str]:
    """What-if parameters, display-only measures (and the number each shows) and measures not captured."""
    metrics, out = spec.get("metrics") or {}, []
    params = spec.get("parameters") or {}
    if params:
        out += ["", "**What-if parameters** (agents pass a value; verify used the one shown above)", ""]
        out += [f"- {param_range(p, i)}" for p, i in params.items()]
    shown = [m for m in metrics.values() if not m.get("hidden") and m.get("status") == "presentation"]
    if shown:
        out += ["", "**Display-only** (text for a visual; agents compute the number it shows)", ""]
        out += [f"- {m['label']}: shows `{m['value']}` as text" if m.get("value")
                else f"- {m['label']}: no captured number behind it ({m.get('why') or 'a constant for display'})"
                for m in shown]
    missing = [m for m in metrics.values() if not m.get("hidden") and m.get("status") == "not_captured"]
    if missing:
        out += ["", "**Not captured** (agents cannot compute these)", ""]
        out += [f"- {m['label']}: {m.get('why', 'not captured')}" for m in missing]
    return out


def _version(ci: str | None) -> dict:
    env = os.environ.get
    if ci == "gitlab":
        return {"version": _version_name(), "commit": env("CI_COMMIT_SHORT_SHA") or "",
                "ref": env("CI_COMMIT_REF_NAME") or "", "job": int(env("CI_JOB_ID")) if env("CI_JOB_ID") else None,
                "pipeline_url": env("CI_PIPELINE_URL") or ""}
    if ci == "azure":
        url = f"{env('SYSTEM_COLLECTIONURI', '')}{env('SYSTEM_TEAMPROJECT', '')}/_build/results?buildId={env('BUILD_BUILDID')}"
        return {"version": f"build {env('BUILD_BUILDNUMBER') or env('BUILD_BUILDID')}",
                "commit": (env("BUILD_SOURCEVERSION") or "")[:8], "ref": env("BUILD_SOURCEBRANCHNAME") or "",
                "job": None, "pipeline_url": url}
    return {"version": "local", "commit": "", "ref": "", "job": None, "pipeline_url": ""}


def _version_name() -> str:
    """v<N> on the default branch, one past the newest capture release; a preview name elsewhere."""
    env = os.environ.get
    if env("CI_MERGE_REQUEST_IID"):
        return f"preview of !{env('CI_MERGE_REQUEST_IID')}"
    if env("CI_COMMIT_BRANCH") != env("CI_DEFAULT_BRANCH", "main"):
        return f"preview of {env('CI_COMMIT_REF_NAME')}"
    done = capture_tags()
    return f"v{int(done[0].removeprefix('capture-v')) + 1 if done else 1}"


def publish(out: str | Path) -> str:
    """On main: a release capture-v<N> whose link is the capture job's files. The MCP server serves
    the latest release (or one it is pinned to), so a rollback is pinning an earlier tag. Refuses a
    preview capture and a tag that already exists (two merges raced: re-run the later pipeline)."""
    if _ci() != "gitlab":
        raise LayerError("publish makes a GitLab release and runs in the models repo's GitLab CI; elsewhere the "
                         "`ictx pbi ci` output folder is the release (serve it with `ictx pbi serve --dir`)")
    v = json.loads((Path(out) / VERSION).read_text())
    if not re.fullmatch(r"v\d+", str(v.get("version"))):
        raise LayerError(f"{out} holds capture {v.get('version')!r}, not a release from the default branch")
    if not v.get("ok"):
        raise LayerError(f"{out}: capture {v['version']} did not verify; not publishing it")
    pid, tag = _project(), f"capture-{v['version']}"
    if not _missing(f"/projects/{pid}/releases/{urllib.parse.quote(tag)}") or \
            not _missing(f"/projects/{pid}/repository/tags/{urllib.parse.quote(tag)}"):
        raise LayerError(f"{tag} already exists: a release was made outside this job's resource group "
                         "(capture-release). Re-run this pipeline; its capture takes the next version")
    report = (Path(out) / REPORT).read_text()
    link = f"{os.environ['CI_API_V4_URL']}/projects/{os.environ['CI_PROJECT_ID']}/jobs/{v['job']}/artifacts"
    _api(f"/projects/{pid}/releases", "POST", {
        "tag_name": tag, "ref": os.environ["CI_COMMIT_SHA"], "name": f"Power BI capture {v['version']}",
        "description": report[:100_000],
        "assets": {"links": [{"name": "capture", "url": link, "link_type": "package"}]}})
    return tag


# ------------------------------------------------------------------ drift

_FAMILIES = {"number": ("bigint", "int", "integer", "smallint", "tinyint", "hugeint", "ubigint", "uinteger",
                        "usmallint", "utinyint", "uhugeint", "long", "short", "byte", "decimal", "numeric", "double",
                        "float", "real"),
             "string": ("varchar", "string", "char", "text", "uuid"),
             "datetime": ("date", "timestamp", "datetime", "time"),
             "boolean": ("boolean", "bool"),
             "binary": ("binary", "blob", "varbinary", "bytea")}
_MODEL_TYPES = {"int64": "number", "double": "number", "decimal": "number", "string": "string",
                "dateTime": "datetime", "boolean": "boolean", "binary": "binary"}


def _family(warehouse_type: str) -> str | None:
    word = (re.match(r"[a-z_0-9]+", str(warehouse_type).strip().lower()) or [""])[0]
    for fam, names in _FAMILIES.items():
        if word in names or (fam == "datetime" and word.startswith("timestamp")):
            return fam
    return None


def _described(wh: Warehouse, relation: str, is_sql: bool) -> dict[str, str]:
    """{lower-cased column: type} of a warehouse table or query, from DESCRIBE (no rows are read)."""
    if wh.kind == "databricks":
        sql = (f"DESCRIBE QUERY {relation}" if is_sql
               else "DESCRIBE TABLE " + ".".join(f"`{p.replace('`', '``')}`" for p in relation.split(".")))
    else:
        sql = f"DESCRIBE {relation}" if is_sql else "DESCRIBE " + ".".join(
            '"' + p.replace('"', '""') + '"' for p in relation.split("."))
    out = {}
    for row in wh.query(sql)[1]:
        name = str(row[0] or "").strip()
        if not name or name.startswith("#"):  # Databricks: partition and detail sections follow the columns
            break
        out.setdefault(name.lower(), str(row[1]))
    return out


def drift_check(layer: BILayer, wh: Warehouse) -> list[dict]:
    """Every mapped table and column of a capture against the warehouse: missing table, missing
    column, or a type of another kind (a number column that became a string). [] when none."""
    found = []
    for name, t in sorted(layer.tables.items()):
        if not t.get("columns") or not (t.get("sql") or t.get("table")):
            continue
        relation = t.get("sql") or t["table"]
        where = f"`{name}` ({'its SQL in warehouse.yaml' if t.get('sql') else t['table']})"
        try:
            cols = _described(wh, relation, bool(t.get("sql")))
        except Exception as exc:  # noqa: BLE001 - the table is gone, renamed, or not visible to this principal
            found.append({"table": name, "column": None,
                          "problem": f"{where}: not readable: {str(exc).strip().splitlines()[0][:300]}"})
            continue
        if not cols:
            found.append({"table": name, "column": None, "problem": f"{where}: no such table"})
            continue
        for c, info in t["columns"].items():
            phys = str(info.get("column") or c)
            got = cols.get(phys.lower())
            if got is None:
                found.append({"table": name, "column": c,
                              "problem": f"{where}: no column `{phys}` (model column {ref(name, c)})"})
                continue
            want, have = _MODEL_TYPES.get(info.get("type") or ""), _family(got)
            if want and have and want != have:
                found.append({"table": name, "column": c,
                              "problem": f"{where}: `{phys}` is {got}; the model reads {ref(name, c)} as "
                                         f"{info.get('type')}"})
    return found


def _capture_folders(path: Path, work: Path) -> list[Path]:
    """Model capture folders under a published folder, a single capture, or a repo's models/ (built)."""
    if (path / CAPTURE).exists():
        return [path]
    subs = sorted(p for p in path.iterdir() if p.is_dir())
    if any((p / CAPTURE).exists() for p in subs):
        return [p for p in subs if (p / CAPTURE).exists()]
    out = []
    for src in subs:
        if any(src.glob("*.pbix")) or any(src.glob("*.SemanticModel")):
            build_model(src, work / src.name)
            out.append(work / src.name)
    if not out:
        raise LayerError(f"{path}: no capture.yaml and no model folders (.pbix / .SemanticModel) under it")
    return out


def run_drift(path: str | Path, prefer: str = "auto") -> tuple[bool, str]:
    """Drift between what each model captured and the warehouse it runs on (Databricks when DATABRICKS_*
    is set, else the capture's local stand-in). Returns (no drift, markdown report)."""
    path = Path(path)
    v = json.loads((path / VERSION).read_text()) if (path / VERSION).exists() else {}
    lines, ok = [], True
    with tempfile.TemporaryDirectory() as work:
        for folder in _capture_folders(path, Path(work)):
            layer = BILayer.load(folder)
            try:
                wh = Warehouse.open(folder, layer, prefer=prefer)
                gap = _databricks_gap() if wh.kind == "databricks" else None
                found = [{"table": None, "column": None, "problem": f"Databricks: {gap}"}] if gap else \
                    drift_check(layer, wh)
                on = wh.describe()
            except LayerError as exc:
                found, on = [{"table": None, "column": None, "problem": str(exc)}], "no warehouse"
            mapped = [t for t in layer.tables.values() if t.get("columns") and (t.get("sql") or t.get("table"))]
            ok &= not found
            lines += [f"## {folder.name}", "", f"Checked on {on}: {len(mapped)} tables, "
                      f"{sum(len(t['columns']) for t in mapped)} columns.", ""]
            lines += [f"- {f['problem']}" for f in found] or ["- no drift"]
            lines.append("")
    head = f"# Warehouse drift: {'none' if ok else 'found'}"
    if v:
        head += f" (capture {v.get('version')}" + (f", commit {v['commit']}" if v.get("commit") else "") + ")"
    when = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tail = ["", f"Checked {when}" + (f" by {os.environ['CI_PIPELINE_URL']}" if os.environ.get("CI_PIPELINE_URL")
                                     else "") + "."]
    if not ok:
        tail += ["", "Fix the warehouse, or map the model to it in warehouse.yaml (`tables:`, `columns:`, `sql:`) "
                     "and merge: the next capture is verified against the warehouse as it is now."]
    return ok, head + "\n\n" + "\n".join(lines + tail) + "\n"


def drift_issue(ok: bool, report: str, project: str | None = None) -> str:
    """Drift as one open GitLab issue labelled warehouse-drift: opened on the first failure, updated on
    each later one, closed by the first clean run after it."""
    if not os.environ.get("GITLAB_TOKEN"):
        return "no issue: set GITLAB_TOKEN (api scope) for the drift job to open one"
    pid = _project(project)
    issues = _api(f"/projects/{pid}/issues?state=opened&labels={DRIFT_LABEL}&per_page=20") or []
    if ok:
        for i in issues:
            _api(f"/projects/{pid}/issues/{i['iid']}/notes", "POST", {"body": "No drift any more.\n\n" + report})
            _api(f"/projects/{pid}/issues/{i['iid']}", "PUT", {"state_event": "close"})
        return f"closed {', '.join(i['web_url'] for i in issues)}" if issues else "no drift, no issue open"
    if issues:
        i = issues[0]
        _api(f"/projects/{pid}/issues/{i['iid']}", "PUT", {"description": report[:1_000_000]})
        _api(f"/projects/{pid}/issues/{i['iid']}/notes", "POST",
             {"body": "Still drifting" + (f": {os.environ['CI_PIPELINE_URL']}" if os.environ.get("CI_PIPELINE_URL")
                                          else ".")})
        return f"updated {i['web_url']}"
    i = _api(f"/projects/{pid}/issues", "POST", {
        "title": "Warehouse drift: a published Power BI capture no longer matches the warehouse",
        "description": report[:1_000_000], "labels": DRIFT_LABEL})
    return f"opened {i['web_url']}"
