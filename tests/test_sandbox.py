"""The analysis sandbox: the agent's code runs on a snapshot of the granular rows, under the caller's row-level
security, never with a denied column, and the analysis (not the rows) is kept in git. Over test_explain's
claims, whose last month holds one planted spike (Lakeview inpatient, five times over). Runs the local
sandbox; the container is checked when its image is built."""

import json
import shutil
import subprocess

import pytest

from insurance_context import bisandbox
from insurance_context.bi_agent import BIToolkit
from test_explain import kit as claims_kit  # noqa: F401 - the fixture

SPIKE = """
from sandbox import load, report
df = load()
df["month"] = df["ServiceDate"].astype("datetime64[ns]").dt.to_period("M").astype(str)
m = df[df["Setting"] == "IP"].groupby(["Provider", "month"])["Paid"].sum().reset_index()
last = m["month"].max()
hist = m[m["month"] < last].groupby("Provider")["Paid"].agg(["mean", "std"])
now = m[m["month"] == last].set_index("Provider")["Paid"]
z = ((now - hist["mean"]) / hist["std"]).sort_values(ascending=False)
top = z.index[0]
report(answer=f"{top} inpatient is far above its own history in {last}.",
       table=z.rename("z").reset_index(),
       method={"test": "z-score against each provider's own months", "n": int(len(m))},
       numbers={"z": round(float(z.iloc[0]), 1)})
"""


@pytest.fixture()
def kit(claims_kit, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ICTX_SANDBOX", "local")
    k = BIToolkit(claims_kit.layer, claims_kit.wh)
    k.analyses, k.folder, k.who = tmp_path / "analyses", tmp_path, "Priya"
    return k


COLS = ["Provider[Provider]", "Claims[ServiceDate]", "Claims[Setting]", "Claims[Paid]"]


def test_an_analysis_runs_on_the_rows_and_is_kept_in_git(kit):
    out = kit.analyze("Is Lakeview's inpatient jump real?", SPIKE, COLS)
    assert out.startswith("Answer (from the analysis): Lakeview inpatient is far above"), out
    a = kit.results[-1]["analysis"]
    assert a["rows"] == 540 and a["numbers"]["z"] > 10 and not a["isolated"]
    assert kit.results[-1]["columns"] == ["Provider", "z"] and kit.results[-1]["shown"][0][0] == "Lakeview"
    folder = kit.analyses / a["id"]
    assert sorted(p.name for p in folder.iterdir()) == ["analysis.py", "data.lock.json", "log.txt", "question.md",
                                                         "result.json"]  # the rows are gone
    lock = json.loads((folder / "data.lock.json").read_text(encoding="utf-8"))
    assert lock["rows"] == 540 and len(lock["rows_sha256"]) == 64 and "SELECT" in lock["sql"]
    if shutil.which("git"):
        log = subprocess.run(["git", "-C", str(kit.analyses), "log", "--format=%s"], capture_output=True, text=True)
        assert "analysis: Is Lakeview's inpatient jump real?" in log.stdout and a["commit"]
    assert bisandbox.Analyses(kit.analyses).list()[0]["answer"].startswith("Lakeview")


def test_a_failing_analysis_comes_back_to_be_fixed_and_reuses_the_rows(kit):
    out = kit.analyze("Broken", "from sandbox import load\nload()['nope']\n", COLS)
    assert out.startswith("Error: the analysis failed") and "KeyError" in out
    assert not list(kit.analyses.glob("*/question.md"))  # nothing kept
    out = kit.analyze("Fixed", "from sandbox import load, report\nreport(answer=str(len(load())))\n", COLS)
    assert "Answer (from the analysis): 540" in out
    out = kit.analyze("No report", "x = 1\n", COLS)
    assert "without calling report" in out


def test_the_rows_are_the_callers_and_denied_columns_stay_put(kit):
    kit.row_filters = {"Provider": "{Provider[Specialty]} = 'Ortho'"}
    out = kit.analyze("How many?", "from sandbox import load, report\nreport(answer=str(len(load())))\n", COLS)
    assert "Answer (from the analysis): 180" in out and kit.results[-1]["analysis"]["row_security"]
    (kit.folder / "sandbox.yaml").write_text("deny:\n  - Claims[Paid]\n", encoding="utf-8")
    out = kit.analyze("Paid?", "pass", COLS)
    assert out.startswith("Error: Claims[Paid] may not leave the warehouse")


def test_analyze_is_offered_only_where_analyses_are_kept(claims_kit, tmp_path):  # noqa: F811
    k = BIToolkit(claims_kit.layer, claims_kit.wh)
    assert "analyze" not in [t.__name__ for t in k.tools()] and "(Not available here.)" in k.system_prompt()
    k.analyses = tmp_path
    assert "analyze" in [t.__name__ for t in k.tools()] and "(Not available here.)" not in k.system_prompt()


def test_the_helpers_find_the_spike_and_refuse_a_period_against_itself(kit):
    code = """
from sandbox import load, report, unusual, compare_periods, compare_rates, forecast, outliers
df = load()
ip = lambda d: (d["Setting"] == "IP") & (d["Provider"] == "Lakeview")
u = unusual(df, "ServiceDate", "Paid", now="2026-03", where=ip)
calm = unusual(df, "ServiceDate", "Paid", now="2026-02", where=ip)
r = compare_periods(df, "ServiceDate", "Paid", now="2026-03", before="2026-02", where=ip)
q = compare_rates(df, "ServiceDate", now="2026-03", before="2026-02", hit=lambda d: d["Setting"] == "IP", base="Paid")
f = forecast(df, "ServiceDate", "Paid", periods=3)
o = outliers(df[df["ServiceDate"].astype(str) >= "2026-03"], "Provider", "Paid")
import json
report(answer=json.dumps({"p": u["p_value"], "calm": calm["p_value"], "z": u["z"],
       "few days": r["p_value"], "rate now": q["rate_now"],
       "rate before": q["rate_before"], "miss": f["typical_miss"], "top": o["table"]["Provider"].iloc[0]}), table=f["table"])
"""
    out = kit.analyze("helpers", code, COLS)
    assert out.startswith("Answer"), out
    a = kit.results[-1]["analysis"]
    a["numbers"] = json.loads(a["answer"])
    assert a["numbers"]["p"] < 0.001 and a["numbers"]["z"] > 20 and a["numbers"]["calm"] > 0.05  # the spike; not wiggle
    assert a["numbers"]["few days"] > 0.05  # three claims a month: two months of days alone cannot tell
    assert a["numbers"]["rate now"] > a["numbers"]["rate before"]
    assert a["numbers"]["top"] == "Lakeview" and a["numbers"]["miss"] < 0.3
    assert any("different periods" in c["check"] for c in a["checks"])
    assert any("backtested" in c["check"] for c in a["checks"])
    cols = kit.results[-1]["columns"]
    # March 2026 stops on the 12th: left out of the fit, and said so
    assert cols == ["period", "actual", "forecast", "low", "high"] and len(kit.results[-1]["shown"]) == 14 + 3
    assert any(c["check"] == "left out March 2026: the data stops part-way through it" for c in a["checks"])
    guessed = ("from sandbox import load, compare_rates\n"
               "r = compare_rates(load(), 'ServiceDate', '2026-03', '2026-02', hit=lambda d: d['Setting'] == 'IP')\n"
               "r.get('now_rate')\n")
    assert "'now_rate' is not here; there is 'rate_now', 'rate_before'" in kit.analyze("guess", guessed, COLS)
    whole = ("from sandbox import load, report, unusual\n"
             "r = unusual(load(), 'ServiceDate', 'Paid', now='2026-03', where=lambda d: d['Provider'] == 'Lakeview')\n"
             "report(answer=r['summary'], result=r, method={'caveats': ['one month']})\n")
    assert kit.analyze("whole", whole, COLS).startswith("Answer (from the analysis): March 2026 came to")
    a = kit.results[-1]["analysis"]
    assert list(a["numbers"]) == ["March 2026", "Usual (last 12)", "p-value"] and a["method"]["caveats"] == ["one month"]
    assert a["method"]["test"] == "March 2026 against the 12 months before it"
    own = ("from sandbox import load, report, forecast\n"
           "f = forecast(load(), 'ServiceDate', 'Paid', periods=3)\n"
           "report(answer='x', table=f['table'], numbers={'next_3_months_total': f['next_total']}, method={'caveats': ['c']})\n")
    kit.analyze("own", own, COLS)
    a = kit.results[-1]["analysis"]
    assert list(a["numbers"]) == ["Next 3 months total"] and a["method"]["test"].startswith("Holt-Winters")
    assert a["method"]["caveats"] == ["c"]
    # the run in the screenshot: a guessed name inside numbers, with a default, must not become a blank tile
    nested = ("from sandbox import load, report, compare_periods\n"
              "r = compare_periods(load(), 'ServiceDate', 'Paid', now='2026-03', before='2026-02')\n"
              "report(answer='x', numbers={'before': r['numbers'].get('before_total', None)})\n")
    assert "'before_total' is not here; there is 'Per day, February 2026'" in kit.analyze("nested", nested, COLS)
    blank = "from sandbox import report\nreport(answer='x', numbers={'Returns': None})\n"
    assert "these numbers have no value: Returns" in kit.analyze("blank", blank, COLS)
    labelled = ("from sandbox import load, report, compare_periods\n"
                "r = compare_periods(load(), 'ServiceDate', 'Paid', now='2026-03', before='2026-02')\n"
                "report(answer='x', table=r['table'], numbers={'p': r['p_value']}, method={'test': 'Compare_periods'})\n")
    kit.analyze("labelled", labelled, COLS)
    assert kit.results[-1]["analysis"]["method"]["test"].startswith("a comparison of the daily totals")
    assert kit.results[-1]["columns"] == ["period", "per day"]
    same = ("from sandbox import load, compare_periods\n"
            "compare_periods(load(), 'ServiceDate', 'Paid', now='2026-03', before='2026-03')\n")
    assert "the same period (2026-03)" in kit.analyze("same", same, COLS)
    gone = "from sandbox import load, compare_periods\ncompare_periods(load(), 'ServiceDate', 'Paid', '2030-01', '2026-03')\n"
    assert "no days in 2030-01; the data covers 2025-01 to 2026-03" in kit.analyze("gone", gone, COLS)


@pytest.mark.skipif(not bisandbox.image_ready(), reason="the sandbox image is not built (ictx pbi sandbox build)")
def test_the_container_has_no_network(kit, monkeypatch):
    monkeypatch.setenv("ICTX_SANDBOX", "docker")
    code = ("import socket\nfrom sandbox import load, report\n"
            "try:\n    socket.create_connection(('1.1.1.1', 53), timeout=3); net = 'open'\n"
            "except OSError:\n    net = 'closed'\n"
            "import scipy, statsmodels\nreport(answer=net, numbers={'rows': len(load())})\n")
    out = kit.analyze("Network?", code, COLS)
    assert "Answer (from the analysis): closed" in out and kit.results[-1]["analysis"]["isolated"]
