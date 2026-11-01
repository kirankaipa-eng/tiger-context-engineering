"""The analysis sandbox: ad-hoc analysis (a test, a forecast, outliers, a model) on the granular rows behind
the metrics, run from code the agent writes, in a container with no network that sees only a snapshot of
the rows the person asking may see.

    snap = snapshot(kit, columns, filters, conditions)   rows (row-level security applied) -> rows.parquet,
                                                         with the lock that reads them again
    run(folder)                                          analysis.py in the sandbox -> result.json
    Analyses(root).keep(question, snap, folder, ran)     question, lock, code and result, committed to git

The rows never outlive the run: the lock (the query, its hash, the capture it ran on) is what is kept, so
an analysis can be run again on today's data without the data sitting in a folder or in git. Columns named
in `sandbox.yaml` beside the capture (`deny:` then one `- Member[SSN]` line each) never leave the warehouse.

Where it runs (ICTX_SANDBOX): `docker` (the default) runs the IMAGE built by `ictx pbi sandbox build`,
with no network, a read-only root, no capabilities and memory, CPU and time limits. `local` runs this
Python in a subprocess: for tests and machines without Docker, and it says it is not isolated.
"""

from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from insurance_context import bistats

IMAGE = "ictx-analysis:1"
DOCKERFILE = """FROM python:3.12-slim
RUN pip install --no-cache-dir pandas==2.2.3 numpy==2.1.3 scipy==1.14.1 statsmodels==0.14.4 \\
    scikit-learn==1.5.2 pyarrow==18.1.0
WORKDIR /work
"""
LIBRARIES = "pandas, numpy, scipy, statsmodels, scikit-learn"
MAX_ROWS = int(os.environ.get("ICTX_SANDBOX_ROWS", "1000000"))
TIMEOUT = int(os.environ.get("ICTX_SANDBOX_TIMEOUT", "180"))

# What an analysis can use; written into each run's folder as sandbox.py. Tested in test_sandbox.
KIT = '''"""What an analysis can use: load() the rows; the tested helpers for the common questions (unusual,
compare_periods, compare_rates, forecast, outliers), each checking its own inputs; report() the result. The checks the
helpers made travel with the result, so the reader sees what was verified."""
import json
import math
from pathlib import Path

_checks = []


def _check(text, passed=True):
    _checks.append({"check": text, "passed": bool(passed)})


_last = []  # the results the helpers returned, latest last: report() falls back on the latest


class Strict(dict):
    """Asking for a key it does not have is an error naming the ones it has, with get() and a default too:
    a guessed name must not quietly become a blank on the page."""

    def __getitem__(self, k):
        if k not in self:
            raise KeyError(f"{k!r} is not here; there is {', '.join(map(repr, self))}")
        return dict.__getitem__(self, k)

    def get(self, k, *default):
        return self[k]


class Result(Strict):
    """A helper's result; its numbers and method are strict as well."""

    def __init__(self, **kw):
        super().__init__(**{k: Strict(v) if k in ("numbers", "method") else v for k, v in kw.items()})
        _last.append(self)


def _name(period):
    """2026-09 as September 2026, 2026Q3 as Q3 2026; anything else as it is."""
    import pandas as pd
    t = str(period)
    try:
        if len(t) == 7 and t[4] == "-":
            return pd.Period(t, "M").strftime("%B %Y")
        if "Q" in t:
            y, q = t.split("Q")
            return f"Q{q} {y}"
    except ValueError:
        pass
    return t


def _unit(grain):
    return {"M": "months", "Q": "quarters", "Y": "years", "W": "weeks", "D": "days"}.get(grain, "periods")


def _verdict(p):
    return "a gap this size is very unlikely to be chance" if p < 0.05 else "a gap this size could well be chance"


def load():
    """The snapshot's rows as a pandas DataFrame, one column per column asked for."""
    import pandas as pd
    try:
        return pd.read_parquet("data/rows.parquet")
    except ImportError:  # no pyarrow (local runs): DuckDB reads Parquet too
        import duckdb
        return duckdb.sql("SELECT * FROM 'data/rows.parquet'").df()


def _days(df, date, value, statistic, where):
    """Per-day totals over every day the data covers, days with no rows as 0 (BLANK is 0 in a total)."""
    import pandas as pd
    d = df if where is None else df[where(df)]
    d = d[[date, value]].copy()
    d[date] = pd.to_datetime(d[date]).dt.normalize()
    full = pd.to_datetime(df[date]).dt.normalize()
    days = pd.date_range(full.min(), full.max(), freq="D")
    return d.groupby(date)[value].agg(statistic).reindex(days, fill_value=0)


def compare_periods(df, date, value, now, before, grain="M", statistic="sum", where=None):
    """Is `value` in period `now` different from `before` ("2026-09" vs "2025-09"; grain M, Q or Y)? Compares
    the per-day totals of each with Welch's t-test and the Mann-Whitney rank test. `where`: a function of the
    DataFrame selecting the rows that count (lambda d: d["Status"] == "Returned"). Returns a dict with
    now_total, before_total, now_per_day, before_per_day, days_now, days_before, p_value, p_value_rank, table."""
    import pandas as pd
    from scipy import stats
    if str(now) == str(before):
        raise ValueError(f"now and before are the same period ({now}); compare two different periods")
    daily = _days(df, date, value, statistic, where)
    label = daily.index.to_period(grain).astype(str)
    a, b = daily[label == str(now)], daily[label == str(before)]
    if len(a) == 0 or len(b) == 0:
        have = sorted(set(label))
        raise ValueError(f"no days in {now if len(a) == 0 else before}; the data covers {have[0]} to {have[-1]}")
    _check(f"{_name(now)} and {_name(before)} are different periods, with {len(a)} and {len(b)} days of data")
    if a.std() == 0 and b.std() == 0:
        p = p_rank = 1.0 if a.mean() == b.mean() else 0.0
    else:
        p = float(stats.ttest_ind(a, b, equal_var=False).pvalue)
        p_rank = float(stats.mannwhitneyu(a, b).pvalue)
    _check("days with no rows counted as 0, so quiet days pull the average down as they should")
    table = pd.DataFrame({"period": [str(before), str(now)], "per day": [round(b.mean(), 3), round(a.mean(), 3)]})
    return Result(now=str(now), before=str(before), now_total=float(a.sum()), before_total=float(b.sum()),
                  now_per_day=float(a.mean()), before_per_day=float(b.mean()), days_now=len(a), days_before=len(b),
                  p_value=p, p_value_rank=p_rank, table=table,
                  numbers={f"Per day, {_name(before)}": float(b.mean()), f"Per day, {_name(now)}": float(a.mean()),
                           "p-value": p},
                  method={"test": "a comparison of the daily totals in each period (Welch's t-test)", "n": len(a) + len(b),
                          "basis": f"{len(a) + len(b)} days"},
                  summary=f"Per day it went from {b.mean():,.0f} in {_name(before)} to {a.mean():,.0f} in {_name(now)}; "
                          f"{_verdict(p)}.")


def unusual(df, date, value, now, grain="M", history=12, statistic="sum", where=None):
    """Is period `now` unusual against its own history? Its total against the `history` periods before it
    (periods with no rows as 0): how many standard deviations away (z), and how many of those periods were
    as far out (a p-value that assumes nothing about the shape). The test for "is this jump real".
    Returns now_total, usual (the mean), z, p_value, periods, table (period, total)."""
    import pandas as pd
    from scipy import stats
    d = df if where is None else df[where(df)]
    s = d.groupby(pd.to_datetime(d[date]).dt.to_period(grain))[value].agg(statistic)
    dates = pd.to_datetime(df[date])
    full = pd.period_range(dates.min().to_period(grain), dates.max().to_period(grain), freq=grain)
    s = s.reindex(full, fill_value=0).astype(float)
    s.index = s.index.astype(str)
    if str(now) not in s.index:
        raise ValueError(f"no {now} in the data; it covers {s.index[0]} to {s.index[-1]}")
    at = list(s.index).index(str(now))
    past = s.iloc[max(0, at - history):at]
    if len(past) < 6:
        raise ValueError(f"only {len(past)} periods before {now}; at least 6 are needed to know what is usual")
    x, mu, sd = s.iloc[at], past.mean(), past.std()
    z = (x - mu) / sd if sd else (0.0 if x == mu else math.copysign(math.inf, x - mu))
    p = float(2 * stats.norm.sf(abs(z))) if math.isfinite(z) else 0.0
    _check(f"{_name(now)} compared with the {len(past)} {_unit(grain)} before it, any with no rows counted as 0")
    if s.index[-1] == str(now) and dates.max().normalize() < pd.Period(now, grain).end_time.normalize():
        _check(f"{_name(now)} is not over yet: the data stops on {dates.max():%d %B %Y}", False)
    table = s.iloc[max(0, at - history):at + 1].rename("total").rename_axis("period").reset_index()
    return Result(now_total=float(x), usual=float(mu), z=float(z), p_value=p, periods=len(past), table=table,
                  numbers={f"{_name(now)}": float(x), f"Usual (last {len(past)})": float(mu), "p-value": p},
                  method={"test": f"{_name(now)} against the {len(past)} {_unit(grain)} before it", "n": len(past) + 1,
                          "basis": f"{len(past) + 1} {_unit(grain)}"},
                  summary=f"{_name(now)} came to {x:,.0f} against a usual {mu:,.0f}; "
                          + ("that is far outside its own history." if p < 0.05 else "that is within its usual range."))


def compare_rates(df, date, now, before, hit, base=None, grain="M"):
    """Is a rate different between two periods? `hit(df)` marks the rows that count (returned), `base`
    (a column) weighs each row (units; default 1 per row); the rate is hits over all rows of the period.
    Two-proportion z-test. Returns rate_now, rate_before, n_now, n_before, p_value, table."""
    import pandas as pd
    from scipy import stats
    if str(now) == str(before):
        raise ValueError(f"now and before are the same period ({now}); compare two different periods")
    p_ = pd.to_datetime(df[date]).dt.to_period(grain).astype(str)
    w = df[base].abs() if base else pd.Series(1, index=df.index)
    h = hit(df)
    out = {}
    for k, per in (("now", str(now)), ("before", str(before))):
        m = p_ == per
        n, x = float(w[m].sum()), float(w[m & h].sum())
        if n == 0:
            raise ValueError(f"no rows in {per}")
        out[k] = (x, n)
    (x1, n1), (x2, n2) = out["now"], out["before"]
    pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(pool * (1 - pool) * (1 / n1 + 1 / n2)) if 0 < pool < 1 else 0
    p = 1.0 if se == 0 and x1 / n1 == x2 / n2 else (0.0 if se == 0 else float(2 * stats.norm.sf(abs((x1 / n1 - x2 / n2) / se))))
    _check(f"{_name(now)} and {_name(before)} are different periods, with {n1:,.0f} and {n2:,.0f} counted")
    table = pd.DataFrame({"period": [str(before), str(now)], "rate": [x2 / n2, x1 / n1]})
    return Result(rate_now=x1 / n1, rate_before=x2 / n2, n_now=n1, n_before=n2, p_value=p, table=table,
                  numbers={f"Rate in {_name(before)}": x2 / n2, f"Rate in {_name(now)}": x1 / n1, "p-value": p},
                  method={"test": "a two-proportion test on the rate in each period", "n": int(n1 + n2),
                          "basis": f"{n1 + n2:,.0f} {str(base).lower() + 's' if base else 'rows'} counted"},
                  summary=f"The rate went from {x2 / n2:.1%} in {_name(before)} to {x1 / n1:.1%} in {_name(now)}; "
                          f"{_verdict(p)}.")


def forecast(df, date, value, periods=6, grain="M", statistic="sum", where=None):
    """The next `periods` periods of `value`, with an 80% range (Holt-Winters exponential smoothing, seasonal
    when there are two years or more). A period the data stops part-way through is left out of the fit. Also
    backtests: refits without the last 3 periods and reports how far off it was (typical_miss, a share).
    Returns a dict with table (period, actual, forecast, low, high), typical_miss, model."""
    import pandas as pd
    d = df if where is None else df[where(df)]
    when = pd.to_datetime(d[date])
    s = d.groupby(when.dt.to_period(grain))[value].agg(statistic)
    last_day = pd.to_datetime(df[date]).max()
    full = pd.period_range(pd.to_datetime(df[date]).min().to_period(grain), last_day.to_period(grain), freq=grain)
    s = s.reindex(full, fill_value=0).astype(float)
    if last_day.normalize() < s.index[-1].end_time.normalize():
        _check(f"left out {_name(s.index[-1])}: the data stops part-way through it")
        s = s.iloc[:-1]
    if len(s) < 8:
        raise ValueError(f"only {len(s)} complete periods; a forecast needs at least 8")
    f = smooth(list(s.values), periods, {"M": 12, "Q": 4}.get(grain))
    miss = f["miss"]
    _check(f"backtested: fitted without the last 3 {_unit(grain)}, it was off by {miss:.0%} on average"
           if not math.isnan(miss) else f"backtest skipped: the last 3 {_unit(grain)} are zero")
    ahead = pd.period_range(s.index[-1] + 1, periods=periods, freq=grain)
    table = pd.concat([
        pd.DataFrame({"period": s.index.astype(str), "actual": s.values}),
        pd.DataFrame({"period": ahead.astype(str), "forecast": f["forecast"], "low": f["low"], "high": f["high"]})],
        ignore_index=True)
    nxt = float(sum(f["forecast"]))
    unit = _unit(grain)
    how = f"{f['method']}, backtested"
    return Result(table=table, typical_miss=miss, periods=periods, next_total=nxt, model=how,
                  numbers={f"Next {periods} {unit}": nxt, **({"Typical miss": miss} if not math.isnan(miss) else {})},
                  method={"test": how, "n": len(s), "basis": f"{len(s)} {unit} of history"},
                  summary=f"The next {periods} {unit} come to about {nxt:,.0f}"
                          + (f"; tested on the last 3 {unit}, it was off by {miss:.0%} on average."
                             if not math.isnan(miss) else "."))


def outliers(df, by, value, statistic="sum", threshold=3.5, where=None):
    """The values of `by` whose `value` stands far from the rest: the robust z-score (median and median absolute
    deviation, so the outliers do not hide themselves) beyond `threshold`. Returns table (by, value, z), n."""
    d = df if where is None else df[where(df)]
    g = d.groupby(by)[value].agg(statistic)
    if len(g) < 5:
        raise ValueError(f"only {len(g)} values of {by}; outliers need at least 5 to compare against")
    med = g.median()
    mad = (g - med).abs().median()
    z = 0.6745 * (g - med) / (mad if mad else (g - med).abs().mean() or 1)
    _check(f"compared {len(g)} values of {by} against their median, not their mean")
    out = g.to_frame(value).assign(z=z.round(1))
    far = out[out["z"].abs() >= threshold].sort_values("z", ascending=False).reset_index()
    return Result(table=far, n=len(g), numbers={"Stand out": len(far), "Compared": len(g)},
                  method={"test": "a robust z-score against the median (outliers cannot hide themselves)", "n": len(g)},
                  summary=(f"{len(far)} of {len(g)} stand out: {', '.join(map(str, far[by].head(3)))}." if len(far)
                           else f"None of the {len(g)} stands out."))


def _cell(v):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10] if getattr(v, "hour", 0) == 0 and getattr(v, "minute", 0) == 0 else v.isoformat()
    if hasattr(v, "item"):
        return _cell(v.item())
    return v


def report(answer, result=None, table=None, method=None, numbers=None):
    """answer: two or three plain sentences for a business reader. result: a helper's result, whose table,
    numbers and method are used unless given here. table: a DataFrame to show (and chart), at most 500 rows.
    method: {"assumptions": [...], "caveats": [...]} (and "test", "n" when no helper ran). numbers: up to four
    {plain name: value}."""
    if result is not None:  # the helper's numbers carry plain names and the p-value; they are the ones shown
        table = result["table"] if table is None else table
        numbers = result["numbers"]
        method = {**result["method"], **{k: v for k, v in (method or {}).items() if k not in ("test", "n")}}
    elif _last:  # a helper ran but its result was not passed: its plain method is the one shown
        method = {**dict(method or {}), **{k: v for k, v in _last[-1]["method"].items() if k in ("test", "n", "basis")}}
        if numbers is None:
            numbers = _last[-1]["numbers"]
    def plain(k):  # next_6_months_total -> Next 6 months total; plain names stay as written
        k = str(k)
        if "_" not in k:
            return k
        k = k.replace("_", " ").strip()
        return k[:1].upper() + k[1:]
    numbers = {plain(k): v for k, v in dict(numbers or {}).items()}
    empty = [k for k, v in numbers.items() if v is None or (isinstance(v, float) and math.isnan(v))]
    if empty:
        raise ValueError(f"these numbers have no value: {', '.join(empty)}; pass result=r to show the helper's "
                         "numbers, or compute them")
    out = {"answer": str(answer), "method": method or {}, "checks": _checks,
           "numbers": {k: _cell(v) for k, v in list((numbers or {}).items())[:4]}}
    if table is not None:
        t = table.reset_index() if table.index.name or getattr(table.index, "names", [None])[0] else table
        out["columns"] = [str(c) for c in t.columns]
        out["rows"] = [[_cell(v) for v in r] for r in t.head(500).itertuples(index=False, name=None)]
    Path("out").mkdir(exist_ok=True)
    Path("out/result.json").write_text(json.dumps(out, default=str), encoding="utf-8")
'''
# the shared statistics (smooth, standing) travel with the kit: the sandbox has no copy of this package
KIT += "\n\n" + inspect.getsource(bistats)


class SandboxError(Exception):
    """The analysis could not run; the message says why in a sentence the agent can act on."""


def _plain(columns: list[str]) -> list[str]:
    """Table[Column] as `Column`, or `Table Column` where two tables share a column name."""
    from insurance_context.bilayer import parse_ref
    parts = [parse_ref(c) for c in columns]
    names = [p[1] + (f" ({p[2]})" if p[2] else "") for p in parts]
    return [f"{p[0]} {n}" if names.count(n) > 1 else n for p, n in zip(parts, names)]


def denied(folder: Path | None) -> set[tuple[str, str]]:
    if folder is None or not (folder / "sandbox.yaml").exists():
        return set()
    import yaml
    from insurance_context.bilayer import parse_ref
    conf = yaml.safe_load((folder / "sandbox.yaml").read_text(encoding="utf-8")) or {}
    return {tuple(x.lower() for x in parse_ref(c)[:2]) for c in conf.get("deny") or []}


def snapshot(kit, columns: list[str], filters: dict | None = None, conditions: list[str] | None = None,
             table: str | None = None, folder: Path | None = None, into: Path | None = None) -> dict:
    """The granular rows for an analysis, read under the caller's row-level security, written to
    `into`/data/rows.parquet; returns the lock: what was read, how, and a hash of what came back."""
    import duckdb
    import pandas as pd
    from insurance_context.bilayer import parse_ref
    if not columns:
        raise SandboxError("name the columns the analysis needs, as Table[Column]")
    blocked = [c for c in columns + list(filters or {}) if tuple(x.lower() for x in parse_ref(c)[:2]) in denied(folder)]
    if blocked:
        raise SandboxError(f"{', '.join(blocked)} may not leave the warehouse (sandbox.yaml); analyse without it")
    where, notes = kit.resolve(filters)
    sql = kit.layer.explore_sql(columns, None, where, conditions, None, False, MAX_ROWS + 1, False, table,
                                row_filters=kit.row_filters, dialect=kit.wh.dialect)
    t = time.monotonic()
    _, rows = kit.wh.query(sql, limit=MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        raise SandboxError(f"more than {MAX_ROWS:,} rows; narrow it with filters or conditions")
    names = _plain(columns)
    frame = pd.DataFrame(rows, columns=names)
    for c in frame.columns:  # warehouse decimals (money) as floats: pandas and scipy compute on those
        if frame[c].map(lambda v: isinstance(v, Decimal)).any():
            frame[c] = frame[c].astype(float)
    data = (into or Path(".")) / "data"
    data.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.register("frame", frame)
        con.execute(f"COPY frame TO '{(data / 'rows.parquet').as_posix()}' (FORMAT parquet)")
    finally:
        con.close()
    digest = hashlib.sha256()
    for r in rows:
        digest.update(repr(r).encode("utf-8"))
    return {"columns": dict(zip(names, columns)), "filters": {k: str(v) for k, v in (where or {}).items()},
            "conditions": list(conditions or []), "table": table, "rows": len(rows), "sql": sql,
            "rows_sha256": digest.hexdigest(), "read_ms": int((time.monotonic() - t) * 1000),
            "warehouse": kit.wh.describe(), "row_security": bool(kit.row_filters), "notes": notes,
            "capture": (kit.layer.spec.get("imported") or {}) if hasattr(kit.layer, "spec") else {}}


def mode() -> str:
    return os.environ.get("ICTX_SANDBOX", "docker").lower()


def image_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0


def build_image() -> None:
    """Build the sandbox image (once; a few minutes, it downloads the analysis libraries)."""
    if shutil.which("docker") is None:
        raise SandboxError("Docker is not installed; install it, or set ICTX_SANDBOX=local (not isolated)")
    subprocess.run(["docker", "build", "-t", IMAGE, "-"], input=DOCKERFILE.encode("utf-8"), check=True)


def run(folder: Path, timeout: int = TIMEOUT) -> dict:
    """Run folder/analysis.py against folder/data in the sandbox; its result.json, output and how it ran."""
    folder = folder.resolve()
    (folder / "sandbox.py").write_text(KIT, encoding="utf-8")
    (folder / "out").mkdir(exist_ok=True)
    how = mode()
    t = time.monotonic()
    if how == "local":
        # isolated mode (-I) keeps the user's site and environment out; sandbox.py sits beside analysis.py
        cmd = [sys.executable, "-I", "-c", "import sys, runpy; sys.path.insert(0, '.'); "
               "runpy.run_path('analysis.py', run_name='__main__')"]
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(folder), "PYTHONUTF8": "1",
               "PYTHONDONTWRITEBYTECODE": "1",
               **({"SYSTEMROOT": os.environ["SYSTEMROOT"]} if "SYSTEMROOT" in os.environ else {})}
        cwd, isolated = folder, False
    elif how == "docker":
        if not image_ready():
            raise SandboxError("the sandbox image is not built: run `ictx pbi sandbox build` (once)")
        # nothing is mounted: the folder goes in on stdin as a tar and `out` comes back on stdout, so it runs
        # alike wherever Docker runs (Docker Desktop shares only some folders) and the host's disk stays out
        name = f"ictx-analysis-{uuid.uuid4().hex[:10]}"
        cmd = ["docker", "run", "--rm", "-i", "--name", name, "--network", "none", "--read-only",
               "--tmpfs", "/tmp:rw,size=1g", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "--memory", "2g", "--cpus", "2", "--pids-limit", "256", "--user", "65534:65534",
               "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "MPLCONFIGDIR=/tmp", IMAGE, "sh", "-c",
               "mkdir /tmp/w && cd /tmp/w && tar -xf - && python analysis.py 1>&2; s=$?; tar -cf - out; exit $s"]
        box = io.BytesIO()
        with tarfile.open(fileobj=box, mode="w") as tar:
            for f in ("analysis.py", "sandbox.py", "data", "out"):
                tar.add(folder / f, arcname=f)
        try:
            p = subprocess.run(cmd, input=box.getvalue(), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            raise SandboxError(f"the analysis ran past {timeout} seconds; sample the rows or simplify it") from None
        if p.stdout:
            with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
                for m in tar.getmembers():  # only out/result.json, and only as a plain file
                    if m.isfile() and m.name in ("out/result.json", "./out/result.json"):
                        (folder / "out" / "result.json").write_bytes(tar.extractfile(m).read())
        p = subprocess.CompletedProcess(cmd, p.returncode, "", p.stderr.decode("utf-8", "replace"))
        isolated = True
    else:
        raise SandboxError(f"ICTX_SANDBOX={how!r}: use docker or local")
    if how == "local":
        try:
            p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise SandboxError(f"the analysis ran past {timeout} seconds; sample the rows or simplify it") from None
    seconds = round(time.monotonic() - t, 1)
    result = folder / "out" / "result.json"
    ran = {"ok": p.returncode == 0 and result.exists(), "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:],
           "seconds": seconds, "isolated": isolated, "where": f"container {IMAGE}, no network" if isolated
           else "a local Python process (not isolated)"}
    if p.returncode == 0 and not result.exists():
        ran["stderr"] += "\nThe analysis finished without calling report(...); end it with report(answer=...)."
    if ran["ok"]:
        ran["result"] = json.loads(result.read_text(encoding="utf-8"))
    return ran


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "analysis"


class Analyses:
    """Each analysis kept as a folder in a git repository: question.md, data.lock.json, analysis.py,
    result.json and log.txt, one commit each. The rows themselves are deleted after the run."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def start(self, question: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        folder = self.root / f"{stamp}-{_slug(question)}-{uuid.uuid4().hex[:6]}"
        folder.mkdir(parents=True)
        return folder

    def keep(self, folder: Path, question: str, who: str, lock: dict, ran: dict) -> str | None:
        """Write the record and commit it; the commit id, or None where git is not installed."""
        shutil.rmtree(folder / "data", ignore_errors=True)
        shutil.rmtree(folder / "out", ignore_errors=True)
        shutil.rmtree(folder / "__pycache__", ignore_errors=True)
        (folder / "sandbox.py").unlink(missing_ok=True)
        (folder / "question.md").write_text(f"# {question}\n\nAsked by {who} on {date.today().isoformat()}.\n",
                                            encoding="utf-8")
        (folder / "data.lock.json").write_text(json.dumps(lock, indent=2, default=str), encoding="utf-8")
        (folder / "result.json").write_text(json.dumps(ran.get("result") or {}, indent=2), encoding="utf-8")
        (folder / "log.txt").write_text(f"ran in {ran['where']} in {ran['seconds']}s\n\n{ran['stdout']}\n{ran['stderr']}",
                                        encoding="utf-8")
        if shutil.which("git") is None:
            return None
        git = ["git", "-C", str(self.root), "-c", "user.name=Context Studio", "-c", "user.email=studio@localhost"]
        if not (self.root / ".git").exists():
            subprocess.run([*git, "init", "-q"], check=True, capture_output=True)
            (self.root / ".gitignore").write_text("*/data/\n*/out/\n", encoding="utf-8")
            subprocess.run([*git, "add", ".gitignore"], check=True, capture_output=True)
        subprocess.run([*git, "add", folder.name], check=True, capture_output=True)
        subprocess.run([*git, "commit", "-q", "-m", f"analysis: {question[:72]}", "-m", f"Asked by {who}."],
                       check=True, capture_output=True)
        return subprocess.run([*git, "rev-parse", "--short", "HEAD"], check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def list(self) -> list[dict]:
        out = []
        for f in sorted(self.root.glob("*/question.md"), reverse=True):
            result = json.loads((f.parent / "result.json").read_text(encoding="utf-8") or "{}")
            out.append({"id": f.parent.name, "question": f.read_text(encoding="utf-8").splitlines()[0].lstrip("# "),
                        "answer": result.get("answer", "")})
        return out
