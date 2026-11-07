"""What a question means is settled in code: one metric for a word, one period when none is said, no prior-year
measure unasked, and nothing changes for what is not an agent's turn."""

import asyncio
import datetime
import inspect
import json

import pytest
import yaml

from insurance_context import desk_harness as dh


def m(label, dax):
    return {"label": label, "dax": dax, "status": "captured"}


METRICS = {
    "total_pmpm": m("Total PMPM", "DIVIDE([Total Paid], [Member Months])"),
    "medical_pmpm": m("Medical PMPM", "DIVIDE([Medical Paid], [Member Months])"),
    "total_pmpm_final": m("Total PMPM (Final)", "[Total PMPM]"),
    "zz_old_total_pmpm": m("zz_old Total PMPM", "[Total PMPM]"),
    "total_pmpm_py": m("Total PMPM PY", "CALCULATE([Total PMPM], SAMEPERIODLASTYEAR('Calendar'[Date]))"),
    "total_pmpm_py_commercial": m("Total PMPM PY - Commercial", "CALCULATE([Total PMPM], SAMEPERIODLASTYEAR('Calendar'[Date]), Member[LOB] = \"Commercial\")"),
    "total_pmpm_commercial": m("Total PMPM - Commercial", "CALCULATE([Total PMPM], Member[LOB] = \"Commercial\")"),
    "total_pmpm_yoy": m("Total PMPM YoY %", "DIVIDE([Total PMPM], CALCULATE([Total PMPM], SAMEPERIODLASTYEAR('Calendar'[Date]))) - 1"),
}
DATE, MONTH = "Calendar[Date]", "Calendar[Date]:month"


class Layer:
    metrics = METRICS

    def captured(self):
        return METRICS

    def columns_for(self, metrics):
        return [DATE, "Member[LOB]"]

    def compile(self, metrics, by, filters=None, *rest, **kw):
        return f"sql {by}"


class Months:
    dialect = "duckdb"

    def __init__(self):
        self.ran = 0

    def query(self, sql, limit=None):
        self.ran += 1
        return [], [("2026-08", 1.0), ("2026-09", 2.0), ("2026-10", None)]


class Kit:
    layer, folder, row_filters, said = Layer(), None, None, "Data: x"

    def __init__(self):
        self.calls, self.results, self.wh, self._seq = [], [], Months(), 0

    def _keep(self, last):
        self._seq += 1
        self.results = [*self.results, {**last, "seq": self._seq}]

    def resolve_params(self, metrics, given):
        return given or {}, []

    def tools(self):
        async def list_metrics(ctx): ...
        async def compute(ctx): ...
        return [list_metrics, compute]

    def system_prompt(self):
        return "1. Call list_metrics first in a conversation, and list_columns when you need a column.\n"

    def _type(self, c):
        return "dateTime" if c == DATE else "string"

    def compute(self, metrics, by=None, filters=None, top=None, order_by=None, ascending=False, params=None):
        self.calls.append((metrics, by, filters))
        self._seq += 1
        self.results = [*self.results, {"seq": self._seq, "metrics": metrics, "kind": "compute", "columns": metrics,
                                        "shown": [tuple(0.5 for _ in metrics)]}]
        return self.said


@pytest.fixture
def kit(tmp_path, monkeypatch):
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    k = Kit()
    k.folder = tmp_path
    k.tools_, k.prompt, k.wrap = dh.install(k, tmp_path)
    dh.QUESTION.set("")
    return k


def ask(q, session="s"):
    dh.QUESTION.set(q)
    dh.SESSION.set(session)


def found(k, q):
    return [x.split(" (")[0][2:] for x in k.compute.__self__.find(q).splitlines() if x.startswith("- ")]


def test_measures_fall_into_families():
    idx = dh.index(Layer())
    assert idx["total_pmpm"]["kind"] == "base" and idx["total_pmpm_final"]["kind"] == "alias"
    assert idx["zz_old_total_pmpm"]["kind"] == "legacy" and idx["total_pmpm_py"]["kind"] == "prior"
    assert idx["total_pmpm_commercial"] == {"base": "total_pmpm", "kind": "variant"}
    assert idx["total_pmpm_yoy"]["kind"] == "prior"


def test_a_bare_word_finds_the_total_and_a_variant_only_when_named(kit):
    assert found(kit, "PMPM")[0] == "total_pmpm"
    assert "total_pmpm_commercial" not in found(kit, "PMPM")
    assert found(kit, "total PMPM Commercial")[0] == "total_pmpm_commercial"
    assert not {"total_pmpm_final", "zz_old_total_pmpm"} & set(found(kit, "total PMPM"))


def test_an_owners_precedent_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    (tmp_path / "precedents.yaml").write_text(yaml.safe_dump({"terms": {"pmpm": "medical_pmpm"}}))
    k = Kit()
    dh.install(k, tmp_path)
    assert found(k, "our PMPM")[0] == "medical_pmpm"


def test_no_period_means_the_last_month_with_data_not_the_calendars_end(kit):
    ask("What's our PMPM?")
    out = kit.compute(["total_pmpm"])
    assert kit.calls[-1][2] == {MONTH: "2026-09"} and out.startswith("Plan") and "2026-09" in out
    assert len(kit.results) == 1  # the look at the calendar is not an exhibit


def test_a_metric_that_refuses_the_default_period_runs_without_it(kit):
    plain = kit.compute.__self__._compute

    def refuses(metrics, by=None, filters=None, *rest):
        return "Error: not supported by its contract" if filters else plain(metrics, by, filters, *rest)

    kit.compute.__self__._compute = refuses
    ask("What is this month's PMPM?")
    out = kit.compute(["total_pmpm"])
    assert out.startswith("Plan") and "sets its own period" in out and kit.calls[-1][2] == {}


def test_the_latest_month_is_asked_once_per_date_column(kit):
    h = kit.compute.__self__
    h._latest.clear()
    kit.wh.ran = 0
    assert h._latest_month("total_pmpm", MONTH) == h._latest_month("medical_pmpm", MONTH) == "2026-09"
    assert kit.wh.ran == 1


def test_an_alias_runs_as_its_base_and_a_named_period_stands(kit):
    ask("What was total PMPM in 2025?")
    kit.compute(["total_pmpm_final"], None, {f"{DATE}:year": 2025})
    assert kit.calls[-1] == (["total_pmpm"], None, {f"{DATE}:year": 2025})


def test_a_prior_year_measure_is_refused_unless_asked(kit):
    ask("What was PMPM for Commercial in 2025?")
    assert kit.compute(["total_pmpm_py_commercial"]).startswith("Error") and not kit.calls
    ask("How does PMPM compare with last year?")
    assert kit.compute(["total_pmpm_py"], None, {f"{DATE}:year": 2025}).startswith("Plan")


def test_a_follow_up_keeps_the_conversations_period(kit):
    ask("What was total PMPM in 2025?", "conv")
    kit.compute(["total_pmpm"], None, {f"{DATE}:year": 2025})
    ask("And for Commercial?", "conv")
    kit.compute(["total_pmpm"], None, {"Member[LOB]": "Commercial"})
    assert kit.calls[-1][2] == {"Member[LOB]": "Commercial", f"{DATE}:year": 2025}


def test_a_year_on_the_bare_date_column_takes_the_year_grain(kit):
    ask("What was total PMPM in 2025?")
    kit.compute(["total_pmpm"], None, {DATE: "2025"})
    assert kit.calls[-1][2] == {f"{DATE}:year": 2025}


def test_an_internal_helper_is_refused_not_a_crash(kit):
    ask("What was total PMPM in 2025?")
    assert kit.compute(["total_pmpm__3"]).startswith("Error") and not kit.calls


def test_this_month_is_the_datas_latest_and_last_month_the_one_before(kit):
    ask("What is this month's PMPM?")
    kit.compute(["total_pmpm"], None, {MONTH: "2026-11"})  # the calendar's month, which has nothing in it yet
    assert kit.calls[-1][2] == {MONTH: "2026-09"}
    ask("What was last month's PMPM?", "other")
    kit.compute(["total_pmpm"])
    assert kit.calls[-1][2] == {MONTH: "2026-08"}


def test_a_metric_is_found_by_what_it_is_made_of(kit):
    METRICS["rx_pmpm"] = m("Rx PMPM", "DIVIDE([Pharmacy Paid], [Member Months])")
    METRICS["pharmacy_paid"] = m("Pharmacy Paid", "SUM(x)")
    METRICS["member_months"] = m("Member Months", "SUM(y)")
    try:
        k = Kit()
        dh.install(k, kit.folder)
        assert found(k, "what is this month's PMPM for pharmacy?")[0] == "rx_pmpm"
    finally:
        for key in ("rx_pmpm", "pharmacy_paid", "member_months"):
            del METRICS[key]


class Slow:
    dialect = "duckdb"

    def __init__(self):
        self.ran = 0

    def query(self, sql, limit=None):
        self.ran += 1
        return [], []


def looked(monkeypatch):
    seen = {}

    def endless(self, metric, period, now, before, filters=None, depth=3):
        def split():
            try:
                return bool(self.wh.query("select 1"))
            except TimeoutError:  # as the investigation treats a split it cannot make
                return False

        seen.update(depth=depth, cols=self._splits(metric, period))
        return f"found {sum(split() for _ in range(500))}"

    monkeypatch.setattr(dh.bi.BIToolkit, "explain", endless)
    return seen


@pytest.fixture
def investigating(kit):
    kit.wh, kit._seq = Slow(), 0
    kit._splits = lambda *_: ["Member[Member ID]", "Member[Plan]", "Claims[Setting]", "Claims[Service Date]"]
    kit.resolve = lambda f: (f, [])
    kit.layer.compile = lambda m, by, f, **k: f"sql {by}"
    yield kit
    del kit.layer.compile


def test_a_first_look_is_one_level_of_columns_that_can_drive_it_and_offers_more(investigating, monkeypatch):
    seen = looked(monkeypatch)
    ask("What are the drivers of medical cost?")
    out = investigating.compute.__self__.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    assert seen == {"depth": 1, "cols": ["Member[Plan]", "Claims[Setting]"]}
    assert "first look" in out or "partial" in out


def test_asking_for_more_is_one_more_layer_never_the_whole_descent(investigating, monkeypatch):
    seen = looked(monkeypatch)
    ask("Dig deeper into inpatient")
    investigating.compute.__self__.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    assert seen["depth"] == 1


def test_a_layer_looks_at_a_few_columns_across_the_tables(investigating, monkeypatch):
    seen = looked(monkeypatch)
    investigating._splits = lambda *_: [f"A[a{i}]" for i in range(6)] + ["B[b0]", "C[c0]"]
    ask("What drove medical cost?")
    investigating.compute.__self__.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    assert seen["cols"] == ["A[a0]", "B[b0]", "C[c0]", "A[a1]", "A[a2]"]


def test_an_investigation_stops_at_its_limit_and_says_it_is_partial(investigating, monkeypatch):
    looked(monkeypatch)
    ask("What drove medical cost?")
    out = investigating.compute.__self__.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    together = 2 + 2  # the totals and each column, fetched ahead, then what the layer may still ask
    assert investigating.wh.ran == together + dh.EXPLAIN_QUERIES and "partial" in out


def test_one_question_looks_at_a_metric_once_however_often_the_agent_asks(investigating, monkeypatch):
    looked(monkeypatch)
    ask("What drove medical cost?")
    dh.TRACE.set({"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()})
    h = investigating.compute.__self__
    first = h.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    assert first != dh.LOOKED and h.explain("total_pmpm", MONTH, "2026-09", "2025-09") == dh.LOOKED
    dh.TRACE.set(None)


def test_peeling_goes_inside_what_led_on_the_columns_not_yet_looked_at(kit, monkeypatch):
    h = kit.compute.__self__
    ask("What drove total pmpm?")
    h.ledger["s"] = {"peel": {"total_pmpm": {"metric": "total_pmpm", "period": MONTH, "now": "2026-09", "before": "2025-09",
                                             "filters": {}, "used": ["Member[Age]"], "lead": ("Member[Age]", "65+")}}}
    ran = []
    monkeypatch.setattr(h, "explain", lambda m, p, n, b, f, used, pinned=False: ran.append((f, used)) or "found")
    assert h.next_layer("what's the weather") is None
    ask("Dig deeper")
    form = h.next_layer("Dig deeper")
    assert form["route"] == "layer" and h.run(form, "Dig deeper") == ("found", "explain")
    assert ran == [({"Member[Age]": "65+"}, ["Member[Age]"])]
    assert h.next_layer("what is driving Total PMPM in 65+")["metrics"] == ["total_pmpm"]
    assert h.layer_offer().strip() == "Would you like what is driving Total PMPM in 65+?"


def test_no_more_queries_run_at_once_than_the_limit_whoever_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    (tmp_path / "precedents.yaml").write_text("parallel: 3")
    k = Kit()
    k.folder = tmp_path
    peak, live, lock = [0], [0], dh.threading.Lock()

    class Wide(Months):
        def query(self, sql, limit=None):
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            dh.time.sleep(0.02)
            with lock:
                live[0] -= 1
            return [], []

    k.wh = Wide()
    dh.install(k, tmp_path)
    dh.QUESTION.set("")
    with dh.ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda i: k.wh.query(f"q{i}"), range(12)))
    assert peak[0] == 3


@pytest.mark.parametrize("asked,held", [(1, 3), (9, 5), (None, 5)])
def test_the_limit_stays_between_three_and_five(tmp_path, monkeypatch, asked, held):
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    (tmp_path / "precedents.yaml").write_text(f"parallel: {asked}" if asked else "")
    k = Kit()
    k.folder = tmp_path
    dh.install(k, tmp_path)
    assert k.compute.__self__.at_once == held


def test_a_turn_asks_the_warehouse_nothing_twice_and_a_dashboard_always_asks(kit):
    h = kit.compute.__self__
    ask("q")
    kit.wh.query("same")
    kit.wh.query("same")
    assert h._memo and len([k for k in h._memo if k[0] == "same"]) == 1
    before = kit.wh.__class__.query
    ran = kit.wh.ran
    kit.wh.query("same")
    assert kit.wh.ran == ran
    dh.QUESTION.set("")
    kit.wh.query("same")
    assert kit.wh.ran == ran + 1 and before


def test_a_turn_that_goes_round_and_round_is_told_to_answer(kit):
    calls = []

    async def fake(ctx=None):
        calls.append(1)
        return "ok"

    fake.__name__ = "explore"
    kit.tools = lambda: [fake]
    tools, _, _ = dh.install(kit, kit.folder)
    tool = next(t for t in tools if t.__name__ == "explore")
    dh.TRACE.set({"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()})
    got = [asyncio.run(tool()) for _ in range(dh.TURN_CALLS + 1)]
    assert got[-2] == "ok" and got[-1] == dh.OUT_OF_TURN and len(calls) == dh.TURN_CALLS


def test_a_question_is_followed_to_its_answer_however_long_it_takes(kit):
    class Slow:
        async def stream(self, message, session_id="default"):
            await asyncio.sleep(0.1)
            yield json.dumps({"type": "result", "data": {"answer": "done"}}) + "\n"

    _, _, wrap = dh.install(kit, kit.folder)
    orch = wrap(Slow())

    async def run():
        return [json.loads(x) async for x in orch.stream("why?")]

    assert [x["data"]["answer"] for x in asyncio.run(asyncio.wait_for(run(), 5))] == ["done"]


def test_a_job_outlives_the_chat_that_started_it(kit):
    ran = []

    class Slow:
        async def stream(self, message, session_id="default"):
            await asyncio.sleep(0.1)
            ran.append(message)
            yield json.dumps({"type": "result", "data": {"answer": "done"}}) + "\n"

    _, _, wrap = dh.install(kit, kit.folder)
    orch = wrap(Slow())

    async def run():
        chat = orch.stream("why?").__aiter__()
        watching = asyncio.ensure_future(chat.__anext__())
        await asyncio.sleep(0.02)
        watching.cancel()  # the user went to another chat
        await asyncio.sleep(0.2)
        return ran, [json.loads(x) async for x in orch.stream("why?")]

    done, back = asyncio.run(asyncio.wait_for(run(), 5))
    assert done == ["why?"] and back[-1]["data"]["answer"] == "done"  # it ran once, and the answer was waiting


def test_an_agent_that_never_answers_ends_in_an_answer_not_an_error(kit, monkeypatch):
    class Hangs:
        async def stream(self, message, session_id="default"):
            yield "first\n"
            await asyncio.sleep(30)

    monkeypatch.setattr(dh, "JOB_SECONDS", 0.05)
    _, _, wrap = dh.install(kit, kit.folder)
    orch = wrap(Hangs())

    async def run():
        return [x async for x in orch.stream("nothing the model holds")]

    got = [json.loads(x) for x in asyncio.run(asyncio.wait_for(run(), 5))[1:]]
    assert not [x for x in got if x["type"] == "error"] and got[-1]["data"]["answer"] == dh.GAVE_UP


def test_an_agent_that_reports_an_error_is_answered_for(kit):
    class Fails:
        async def stream(self, message, session_id="default"):
            yield json.dumps({"type": "error", "data": {"message": "boom"}}) + "\n"

    _, _, wrap = dh.install(kit, kit.folder)
    orch = wrap(Fails())

    async def run():
        return [json.loads(x) async for x in orch.stream("nothing the model holds")]

    got = asyncio.run(asyncio.wait_for(run(), 5))
    assert not [x for x in got if x["type"] == "error"] and got[-1]["type"] == "result"


def test_a_dashboard_on_the_same_toolkit_is_untouched(kit):
    assert kit.compute(["total_pmpm_final"]) == "Data: x"
    assert kit.calls[-1] == (["total_pmpm_final"], None, None)


def test_the_list_is_replaced_and_the_prompt_says_so(kit):
    names = [t.__name__ for t in kit.tools_]
    assert "find_metrics" in names and "list_metrics" not in names and "find_metrics" in kit.prompt


def turn():
    dh.TRACE.set({"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()})


def test_a_plan_runs_every_step_once_and_the_tables_reach_the_screen_in_order(kit):
    h = kit.compute.__self__
    kit.said = "Data: x\n\nDefinitions (long)\n\nSQL: select"
    ask("PMPM and medical PMPM this month?")
    turn()
    out = h.plan([{"metrics": ["total_pmpm"]}, {"metrics": ["medical_pmpm"]}])
    assert out.startswith("Plan") and "1. Total PMPM" in out and "2. Medical PMPM" in out
    assert "Definitions" not in out and "SQL" not in out
    assert [r["metrics"] for r in kit.results] == [["total_pmpm"], ["medical_pmpm"]]
    assert {str(c[2]) for c in kit.calls} == {str({MONTH: "2026-09"})}


def test_a_plan_that_cannot_run_runs_nothing_and_says_all_that_is_wrong(kit):
    h = kit.compute.__self__
    ask("What is PMPM?")
    turn()
    out = h.plan([{"metrics": ["total_pmpm"]}, {"metrics": ["total_pmpm_py"]}, {"metrics": ["nope"]}])
    assert out.startswith("Error: the plan was not run") and "Step 2:" in out and "Step 3:" in out
    assert kit.calls == []
    assert h.plan([{"metrics": ["total_pmpm"]}]).startswith("Plan")  # a refused plan can be sent again


def test_what_the_model_refuses_is_caught_before_the_warehouse(kit, monkeypatch):
    h = kit.compute.__self__

    def strict(metrics, by, filters=None, *a, **kw):
        if by is None:
            raise ValueError("Total PMPM needs by=Member[LOB]")
        return "sql"

    monkeypatch.setattr(kit.layer, "compile", strict, raising=False)
    ask("What is PMPM in 2026?")
    turn()
    out = h.plan([{"metrics": ["total_pmpm"]}])
    assert "not run" in out and "needs by=Member[LOB]" in out and kit.calls == []


def test_a_contract_that_refuses_the_default_period_is_settled_in_the_check(kit, monkeypatch):
    h = kit.compute.__self__

    def strict(metrics, by, filters=None, *a, **kw):
        if filters:
            raise ValueError("this ratio contract takes no month filter")
        return "sql"

    h._latest_month("total_pmpm", MONTH)
    monkeypatch.setattr(kit.layer, "compile", strict, raising=False)
    ask("What is PMPM?")
    turn()
    out = h.plan([{"metrics": ["total_pmpm"]}])
    assert out.startswith("Plan") and "sets its own period" in out and kit.calls[-1][2] == {}


def test_once_a_plan_has_run_the_question_is_answered_from_it(kit):
    h = kit.compute.__self__
    ask("What is PMPM?")
    turn()
    h.plan([{"metrics": ["total_pmpm"]}])
    assert h.plan([{"metrics": ["medical_pmpm"]}]) == dh.PLAN_RAN and len(kit.calls) == 1


def test_a_failed_step_keeps_the_others_and_gets_one_more_plan(kit, monkeypatch):
    h = kit.compute.__self__
    plain = Kit.compute

    def half(self, metrics, by=None, filters=None, *rest):
        return "Error: warehouse said no" if metrics == ["medical_pmpm"] else plain(self, metrics, by, filters, *rest)

    monkeypatch.setattr(Kit, "compute", half)
    ask("PMPM and medical PMPM in 2026?")
    turn()
    out = h.plan([{"metrics": ["total_pmpm"]}, {"metrics": ["medical_pmpm"]}])
    assert "Step 1: Data: x" in out and "Step 2: Error: warehouse said no" in out
    assert h.plan([{"metrics": ["medical_pmpm"]}]).startswith("Plan")
    assert h.plan([{"metrics": ["medical_pmpm"]}]) == dh.PLAN_RAN


def test_a_step_that_outlasts_the_turn_says_so(kit, monkeypatch):
    h = kit.compute.__self__
    release = dh.threading.Event()
    plain = Kit.compute

    def slow(self, metrics, by=None, filters=None, *rest):
        if metrics == ["medical_pmpm"]:
            release.wait(5)
        return plain(self, metrics, by, filters, *rest)

    monkeypatch.setattr(Kit, "compute", slow)
    h.turn_seconds = 0.2
    monkeypatch.setattr(dh, "PLAN_SECONDS", 0.2)
    ask("PMPM and medical PMPM in 2026?")
    turn()
    out = h.plan([{"metrics": ["total_pmpm"]}, {"metrics": ["medical_pmpm"]}])
    release.set()
    assert "Step 1: Data: x" in out and "Step 2: did not finish" in out


def test_the_agent_gets_compute_as_a_plan_under_the_same_name(kit):
    tool = next(t for t in kit.tools_ if t.__name__ == "compute")
    assert "steps" in inspect.signature(tool).parameters
    assert [t.__name__ for t in kit.tools_].count("compute") == 1 and "whole plan" in kit.prompt
    ask("What is PMPM?")
    turn()
    assert asyncio.run(tool(None, [{"metrics": ["total_pmpm"]}])).startswith("Plan")


def test_a_plan_with_a_sibling_of_the_metric_named_is_sent_back_once(kit):
    h = kit.compute.__self__
    ask("Show Total PMPM by LOB in 2025")
    turn()
    out = h.plan([{"metrics": ["medical_pmpm"]}])
    assert "not run" in out and "Total PMPM (total_pmpm)" in out and kit.calls == []
    assert h.plan([{"metrics": ["total_pmpm"]}]).startswith("Plan")


def test_the_longest_name_is_the_one_meant_and_the_agent_has_the_last_word(kit):
    h = kit.compute.__self__
    assert h._named("Total PMPM - Commercial in 2025") == ["total_pmpm_commercial"]
    ask("Total PMPM in 2025")
    turn()
    h.plan([{"metrics": ["medical_pmpm"]}])
    assert h.plan([{"metrics": ["medical_pmpm"]}]).startswith("Plan")


def test_a_slow_model_does_not_cost_the_question_its_plan(kit):
    tool = next(t for t in kit.tools_ if t.__name__ == "compute")
    ask("What is PMPM?")
    turn()
    dh.TRACE.get()["start"] -= 1000
    assert asyncio.run(tool(None, [{"metrics": ["total_pmpm"]}])).startswith("Plan")
    assert asyncio.run(tool(None, [{"metrics": ["total_pmpm"]}])) == dh.OUT_OF_TURN


class Reply:
    def __init__(self, content):
        self.content = content


class Model:
    def __init__(self, form=None, words="It held steady."):
        self.form, self.words, self.asked = form, words, 0

    async def ainvoke(self, sent):
        self.asked += 1
        return Reply(self.form)

    async def astream(self, sent):
        yield Reply(self.words)


class Orch:
    _model_name, _api_key, _base_url = "m", "k", "u"

    def __init__(self, model):
        self.seen, self.model = [], model
        self._chat_model = lambda http: model

    async def stream(self, message, session_id="default", **kw):
        self.seen.append(message)
        yield json.dumps({"type": "result", "data": {"answer": "from the agent"}}) + "\n"


def told(k, model, q, session="s"):
    orch = k.wrap(Orch(model))

    async def go():
        return [json.loads(x) async for x in orch.stream(q, session_id=session)]
    return asyncio.run(go()), orch


def test_a_question_code_can_read_needs_no_model_before_its_number(kit):
    model = Model()
    lines, orch = told(kit, model, "Total PMPM in 2025")
    kinds = [x["type"] for x in lines]
    assert model.asked == 0 and orch.seen == [] and kit.calls[-1][0] == ["total_pmpm"]
    assert kinds.index("chunk") < kinds.index("result") and "**" in next(x for x in lines if x["type"] == "chunk")["data"]["text"]
    done = [x for x in lines if x["type"] == "agent_tool_call" and x["data"]["status"] == "completed"]
    assert done[0]["data"]["tool"] == "compute"  # what brings the rows to the screen
    assert lines[-1]["data"]["answer"].endswith("It held steady.")


def test_a_word_code_cannot_place_goes_to_one_small_call(kit):
    model = Model('{"route": "compute", "metrics": ["total_pmpm"], "filters": {"Calendar[Date]:year": 2025}}')
    lines, orch = told(kit, model, "How is PMPM looking across the book in 2025?")
    assert model.asked == 1 and orch.seen == [] and lines[-1]["type"] == "result"


def test_what_the_small_call_will_not_settle_goes_to_the_agent(kit):
    lines, orch = told(kit, Model('{"route": "agent"}'), "Forecast PMPM")
    assert orch.seen == ["Forecast PMPM"] and lines[-1]["data"]["answer"] == "from the agent"
    lines, orch = told(kit, Model("not json at all"), "Forecast PMPM")
    assert lines[-1]["data"]["answer"] == "from the agent"


def test_a_form_the_model_refuses_goes_to_the_agent_having_run_nothing(kit):
    lines, orch = told(kit, Model('{"route": "compute", "metrics": ["nope"]}'), "PMPM looking odd?")
    assert orch.seen and kit.calls == []


def test_a_sentence_that_fails_leaves_the_number_standing(kit):
    class Mute(Model):
        async def astream(self, sent):
            raise RuntimeError("model down")
            yield
    lines, orch = told(kit, Mute(), "Total PMPM in 2025")
    assert lines[-1]["type"] == "result" and "**" in lines[-1]["data"]["answer"] and orch.seen == []


def test_the_agent_hears_what_was_answered_without_it(kit):
    told(kit, Model(), "Total PMPM in 2025")
    _, orch = told(kit, Model('{"route": "agent"}'), "Forecast it")
    assert "Total PMPM in 2025" in orch.seen[0] and orch.seen[0].endswith("Now: Forecast it")


def test_a_follow_up_reads_from_the_last_form(kit):
    told(kit, Model(), "Total PMPM in 2025")
    h = kit.compute.__self__
    sent = h.form_for("And for Commercial?", "s")
    assert "Previous form" in sent[1][1] and "total_pmpm" in sent[1][1]


def test_direct_can_be_turned_off(kit):
    kit.compute.__self__.direct = False
    _, orch = told(kit, Model(), "Total PMPM in 2025")
    assert orch.seen == ["Total PMPM in 2025"]


def test_a_number_reads_as_the_report_shows_it(kit, monkeypatch):
    h = kit.compute.__self__
    monkeypatch.setitem(METRICS["total_pmpm"], "format", "0.0%;-0.0%;0.0%")
    monkeypatch.setitem(METRICS["medical_pmpm"], "format", "\\$#,0;-\\$#,0")
    assert h._show("total_pmpm", 0.9036) == "90.4%" and h._show("medical_pmpm", 1358.2) == "$1,358"


def test_a_reasoning_models_thinking_is_not_part_of_what_it_said():
    blocks = [{"type": "reasoning", "summary": [{"type": "summary_text", "text": "hmm", "signature": "x"}]},
              {"type": "text", "text": '{"route": "agent"}'}]
    assert dh._text(blocks) == '{"route": "agent"}' and dh._text("plain") == "plain"


def test_a_cell_with_nothing_in_it_is_left_out_of_the_number(kit):
    h = kit.compute.__self__
    kit.results = [{"seq": 1, "kind": "compute", "metrics": ["total_pmpm", "medical_pmpm"], "columns": ["Member[LOB]", "total_pmpm", "medical_pmpm"],
                    "shown": [("Commercial", 5.0, None), ("Medicaid", None, None)]}]
    assert h.number(0).strip() == "Commercial: Total PMPM **5**"


def months_kit(kit):
    """Kit.compute answering by month, as the warehouse would for the pair an open question asks for."""
    kit.said = "Data: x"

    def by_month(self, metrics, by=None, filters=None, *rest):
        self.calls.append((metrics, by, filters))
        self._seq += 1
        shown = [("2025-09", 100.0), ("2026-09", 120.0)] if by else [(5.0,)]
        self.results = [*self.results, {"seq": self._seq, "metrics": metrics, "kind": "compute", "shown": shown,
                                        "columns": [*(by or []), *metrics], "filters": filters or {}}]
        return "Data: x"
    return by_month


def test_an_open_question_is_answered_with_the_headlines_not_a_question(kit, monkeypatch):
    monkeypatch.setattr(Kit, "compute", months_kit(kit))
    h = kit.compute.__self__
    h.headline = ["total_pmpm", "medical_pmpm"]
    lines, orch = told(kit, Model('{"route": "overview", "metrics": []}'), "How is the business doing?")
    answer = lines[-1]["data"]["answer"]
    assert orch.seen == [] and "| Total PMPM |" in answer and "| Medical PMPM |" in answer
    assert "September 2026" in answer and "September 2025" in answer and "+20.0%" in answer
    assert answer.strip().endswith("?") and "what drove" in answer.splitlines()[-1]
    assert "by lob or what drove" in answer
    assert kit.calls[-1][0] == ["total_pmpm"]  # the first headline is what the page draws
    assert h.ledger["s"]["period"] == {MONTH: "2026-09"}


def test_a_broad_word_shows_the_measures_it_could_mean(kit, monkeypatch):
    monkeypatch.setattr(Kit, "compute", months_kit(kit))
    lines, orch = told(kit, Model('{"route": "overview", "metrics": ["total_pmpm", "medical_pmpm"]}'), "How are we on cost?")
    assert "| Total PMPM |" in lines[-1]["data"]["answer"] and orch.seen == []


def test_a_headline_that_refuses_the_pair_shows_its_own_period_and_one_refused_is_left_out(kit, monkeypatch):
    monkeypatch.setattr(Kit, "compute", months_kit(kit))
    h = kit.compute.__self__
    h._latest_month("total_pmpm", MONTH)

    def strict(metrics, by, filters=None, *a, **kw):
        if metrics == ["medical_pmpm"]:
            raise ValueError("refused")
        if by:
            raise ValueError("this ratio contract takes no month filter")
        return "sql"

    monkeypatch.setattr(kit.layer, "compile", strict, raising=False)
    form = {"route": "overview", "metrics": ["total_pmpm", "medical_pmpm"]}
    ask("How is the business doing?")
    turn()
    assert h.overview(form, "How is the business doing?") and [c[0] for c in kit.calls] == [["total_pmpm"]]


def test_the_number_says_its_period_as_people_do(kit):
    h = kit.compute.__self__
    kit.results = [{"seq": 1, "kind": "compute", "metrics": ["total_pmpm"], "columns": ["total_pmpm"], "shown": [(5.0,)], "filters": {MONTH: "2026-09"}}]
    assert h.number(0).strip() == "Total PMPM **5** · September 2026"
    assert dh._period("2026-Q1") == "Q1 2026" and dh._period(2025) == "2025"


def test_the_number_comes_before_the_rows_the_page_draws_from(kit):
    lines, _ = told(kit, Model(), "Total PMPM in 2025")
    kinds = [(x["type"], x["data"].get("status")) for x in lines]
    assert kinds.index(("chunk", None)) < kinds.index(("agent_tool_call", "completed"))


class Dates(Months):
    """A warehouse as it is: a date column comes back as dates, not text."""

    def __init__(self, days):
        super().__init__()
        self.days = days

    def query(self, sql, limit=None):
        return ([], [(d,) for d in self.days]) if sql == "values" else super().query(sql, limit)


def dated(kit, days, refused=False):
    """The toolkit's own check, which meets text with dates: whatever text is left for it is refused, as it is today."""
    kit.wh = Dates(days)
    def sql(col, where=None):
        if col != DATE:
            raise ValueError("not read here")
        return "values"
    kit._values_sql = sql
    h = kit.compute.__self__
    h._resolve = lambda filters: (filters or {}, [])
    return h


def test_a_date_written_as_text_finds_the_date_the_warehouse_holds(kit):
    h = dated(kit, [datetime.datetime(2026, 8, 31), datetime.datetime(2026, 9, 30)])
    assert h.resolve({DATE: "2026-09-30"})[0] == {DATE: "2026-09-30 00:00:00"}
    assert h.resolve({DATE: ["2026-08-31", "9/30/2026"]})[0] == {DATE: ["2026-08-31 00:00:00", "2026-09-30 00:00:00"]}


@pytest.mark.parametrize("said", ["2026-09", "September 2026", "Sep 2026", "2026-09-01", "2026-09-30T00:00:00"])
def test_a_column_of_one_day_a_month_takes_any_way_of_writing_that_month(kit, said):
    h = dated(kit, [datetime.date(2026, 8, 31), datetime.date(2026, 9, 30)])
    assert h.resolve({DATE: said})[0] == {DATE: "2026-09-30"}


def test_a_month_of_many_days_is_read_at_month_grain(kit):
    h = dated(kit, [datetime.date(2026, 9, 1), datetime.date(2026, 9, 2)])
    assert h.resolve({DATE: "September 2026"})[0] == {f"{DATE}:month": "2026-09"}


def test_a_date_the_column_does_not_hold_says_what_it_does_hold(kit):
    h = dated(kit, [datetime.date(2026, 8, 31), datetime.date(2026, 9, 30)])
    with pytest.raises(dh.bi.LayerError, match="runs from 2026-08-31 to 2026-09-30"):
        h.resolve({DATE: "2027-01-31"})


def test_text_that_only_looks_like_a_date_is_left_to_the_toolkit(kit):
    h = dated(kit, ["2026-09-30"])  # a text column: the toolkit compares text with text and needs no help
    assert h.resolve({DATE: "2026-09-30"})[0] == {DATE: "2026-09-30"}
    assert h.resolve({"Member[LOB]": "Commercial"})[0] == {"Member[LOB]": "Commercial"}


def test_a_month_end_on_monthly_data_comes_back_as_its_month_and_leaves_no_empty_result(kit):
    h = dated(kit, [datetime.date(2026, 9, 1), datetime.date(2026, 9, 30), datetime.date(2026, 10, 1)])
    seen = []

    def compute(metrics, by, filters, *rest):
        seen.append(filters)
        kit._keep({"filters": filters})
        return "Data: x" if ":month" in next(iter(filters)) else "Data: x\nNo data matches, so the result is empty"

    step = {"metrics": ["total_pmpm"], "filters": {DATE: "2026-09-30"}, "given": {DATE: "2026-09-30"}, "notes": []}
    assert h._run(step, compute, None, None, None, False, None) == "Data: x"
    assert seen == [{DATE: "2026-09-30"}, {f"{DATE}:month": "2026-09"}]
    assert [r["filters"] for r in kit.results] == [{f"{DATE}:month": "2026-09"}]
    assert step["notes"] == ["that date read as its month"]


def test_a_day_in_the_middle_of_a_month_stays_that_day_when_it_finds_nothing(kit):
    h = dated(kit, [datetime.date(2026, 9, 14), datetime.date(2026, 9, 15)])
    assert h._months({DATE: "2026-09-14"}) == {DATE: "2026-09-14"}


def test_the_same_refusal_twice_ends_the_trying(tmp_path, monkeypatch):
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    calls = []

    async def column_values(ctx):
        calls.append(1)
        return f"Error: no Calendar[Date] value matches '2026-09-{29 + len(calls)}'. Values in the data: 2026-09-30"

    class Refusing(Kit):
        def tools(self):
            return [*super().tools(), column_values]

    monkeypatch.setattr(dh, "REFUSALS", 2)  # the mechanism, whatever the number
    k = Refusing()
    k.folder = tmp_path
    tool = next(t for t in dh.install(k, tmp_path)[0] if t.__name__ == "column_values")
    dh.TRACE.set({"calls": 0, "start": dh.time.monotonic(), "n": 0, "secs": 0.0, "slowest": 0.0})
    first, second, third = (asyncio.run(tool(None)) for _ in range(3))
    assert first.startswith("Error: no Calendar") and dh.SAME_REFUSAL not in first  # a first refusal may be fixed
    assert dh.SAME_REFUSAL in second                                                # another value, the same refusal
    assert third == dh.OUT_OF_TURN and len(calls) == 2                              # and no third try reaches the tool
    dh.TRACE.set(None)


def test_what_is_driving_looks_into_each_measure_named_and_says_which_it_could_not(kit, monkeypatch):
    h = kit.compute.__self__
    monkeypatch.setattr(h, "_why_one", lambda k, steps, q: f"found {k}" if k == "medical_pmpm" else "")
    out = h._why({"metrics": ["medical_pmpm", "total_pmpm"]}, "what drives medical and total")
    assert "found medical_pmpm" in out and "Could not be looked into: Total PMPM." in out
    assert h._why({"metrics": ["total_pmpm"]}, "q") == ""  # nothing found: the agent takes it


class Tables(Layer):
    tables = {"Calendar": {"columns": {"Date": {"type": "dateTime"}}}, "Member": {"columns": {"Line of Business": {"type": "string"}, "Plan Year": {"type": "int64"}}},
              "Member Months": {"columns": {"Month": {"type": "dateTime"}}}}

    def columns_for(self, metrics):
        return ["Calendar[Date]", "Member[Line of Business]"]


@pytest.fixture
def real(tmp_path, monkeypatch):
    """A model with tables and a warehouse that answers from them, as the harness sees one."""
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    held = {"Calendar[Date]": [datetime.date(2026, 8, 31), datetime.date(2026, 9, 1), datetime.date(2026, 9, 30)],
            "Calendar[Date]:month": ["2026-08", "2026-09"], "Calendar[Date]:quarter": ["2026-Q3"], "Calendar[Date]:year": [2026],
            "Member[Line of Business]": ["Commercial", "Medicare Advantage", "Medicaid"], "Member[Plan Year]": [2025, 2026]}

    class Held(Months):
        def query(self, sql, limit=None):
            return ([], [(v,) for v in held[sql]]) if sql in held else super().query(sql, limit)

    k = Kit()
    k.layer, k.folder, k.wh = Tables(), tmp_path, Held()
    k._values_sql = lambda col, where=None: col
    k.resolve = lambda filters: (filters or {}, [])
    k._type = lambda c: "dateTime" if c.endswith(("[Date]", "[Month]")) else "string"
    dh.install(k, tmp_path)
    dh.QUESTION.set("")
    return k.compute.__self__


@pytest.mark.parametrize("said,means", [
    ({"LOB": "Commercial"}, {"Member[Line of Business]": "Commercial"}),
    ({"line of business": "commercial"}, {"Member[Line of Business]": "Commercial"}),
    ({"Member[Line of Business]": "MA"}, {"Member[Line of Business]": "Medicare Advantage"}),
    ({"Member[Line of Business]": "Comercial"}, {"Member[Line of Business]": "Commercial"}),
    ({"Member[Plan Year]": "2026"}, {"Member[Plan Year]": 2026}),
    ({"Date": "2026-09-30"}, {"Calendar[Date]": "2026-09-30"}),
    ({"calendar[date]:Month": "2026-09"}, {"Calendar[Date]:month": "2026-09"}),
    ({"Calendar[Date]:monthly": "September 2026"}, {"Calendar[Date]:month": "2026-09"}),
    ({"Calendar[Date]:month": "2026-09-30"}, {"Calendar[Date]:month": "2026-09"}),
    ({"Calendar[Date]:year": "2026"}, {"Calendar[Date]:year": 2026}),
    ({"Calendar[Date]:year": "2026-09-30"}, {"Calendar[Date]:year": 2026}),
    ({"Calendar[Date]": "2026"}, {"Calendar[Date]:year": 2026}),
    ({"Calendar[Date]:quarter": "Q3 2026"}, {"Calendar[Date]:quarter": "2026-Q3"}),
    ({"Calendar[Date]": "Q3 2026"}, {"Calendar[Date]:quarter": "2026-Q3"}),
    ({"Calendar[Date]:quarter": "2026-09-30"}, {"Calendar[Date]:quarter": "2026-Q3"}),
    ({"Calendar[Date]": "2026-08 to 2026-09"}, {"Calendar[Date]:month": ["2026-08", "2026-09"]}),
    ({"Calendar[Date]": {"from": "2026-08-01", "to": "2026-09-30"}}, {"Calendar[Date]:month": ["2026-08", "2026-09"]}),
])
def test_a_filter_is_written_the_way_the_tables_hold_it(real, said, means):
    assert real.resolve(said)[0] == means


def test_a_value_nothing_in_the_column_can_mean_is_refused_with_what_it_does_hold(real):
    with pytest.raises(dh.bi.LayerError, match="Commercial"):
        real.resolve({"Member[Line of Business]": "Comerzzzz cial plan"})
    with pytest.raises(dh.bi.LayerError, match="not a date.*2026-08-31 to 2026-09-30"):
        real.resolve({"Calendar[Date]": "soon"})


def test_a_column_read_once_is_not_read_again(real):
    runs = []
    inner = real.kit.wh.query
    real.kit.wh.query = lambda sql, limit=None: runs.append(sql) or inner(sql, limit)
    real.resolve({"Member[Line of Business]": "Commercial"})
    real.resolve({"Member[Line of Business]": "Medicaid"})
    assert len(runs) <= 1


def test_a_period_on_part_of_a_measure_moves_to_a_date_all_of_it_reaches(real, monkeypatch):
    monkeypatch.setattr(dh.bi, "metric_needs", lambda layer, k: {"by": [], "params": {}})
    moved, notes = real._whole(["total_pmpm"], {"'Member Months'[Month]": "2026-09-30"})
    assert moved == {"Calendar[Date]:month": "2026-09"} and "only part" in notes[0]


def test_a_measure_that_asks_for_that_column_keeps_it(real, monkeypatch):
    monkeypatch.setattr(dh.bi, "metric_needs", lambda layer, k: {"by": ["'Member Months'[Month]:month"], "params": {}})
    assert real._whole(["total_pmpm"], {"'Member Months'[Month]": "2026-09-30"})[0] == {"'Member Months'[Month]": "2026-09-30"}


def test_a_layers_number_says_which_part_it_is_of(kit):
    h = kit.compute.__self__
    kit._keep({"filters": {"Member[LOB]": "Commercial"}, "explain": {"label": "Rx", "total_before": 100.0, "total_after": 200.0, "now": "2026-09",
                                                                     "before": "2025-09", "steps": [{"value": "TX"}]}})
    assert h.layer_line(0).startswith("Rx in Commercial **+100.0%**") and "led by TX" in h.layer_line(0)


LOB, RX = "Member[Line of Business]", ["rx"]


def table(by, rows, metrics=RX):
    return {"kind": "compute", "columns": [*by, *metrics], "shown": rows, "metrics": metrics, "by": by, "sql": "sql"}


def known(by=(LOB,), where=None, rows=(("Commercial", 236.0), ("Medicaid", 180.0))):
    k = dh.Known()
    k.keep(RX, list(by), where or {MONTH: "2026-09"}, table(list(by), list(rows)), True)
    return k


def test_the_map_answers_the_same_question_again():
    assert known().find(RX, [LOB], {MONTH: "2026-09"}, lambda k: True)[1] == [["Commercial", 236.0], ["Medicaid", 180.0]]


def test_a_value_of_a_column_the_map_is_grouped_by_is_that_row():
    assert known().find(RX, [], {MONTH: "2026-09", LOB: "Commercial"}, lambda k: True)[:2] == (["rx"], [[236.0]])


def test_a_share_of_the_total_is_not_a_row_of_the_table_it_came_from():
    assert known().find(RX, [], {MONTH: "2026-09", LOB: "Commercial"}, lambda k: False) is None


@pytest.mark.parametrize("metrics,by,where", [
    (RX, [LOB], {MONTH: "2026-08"}),                        # another period
    (RX, [LOB], {}),                                        # wider than what was found
    (["medical"], [LOB], {MONTH: "2026-09"}),               # another measure
    (RX, ["Member[Plan]"], {MONTH: "2026-09"}),             # another cut
    (RX, [], {MONTH: "2026-09", LOB: "Individual"}),        # a value it does not have
    (RX, [], {MONTH: "2026-09"}),                           # a total is not the sum of a rate's rows
])
def test_what_the_map_does_not_cover_goes_to_the_warehouse(metrics, by, where):
    assert known().find(metrics, by, where, lambda k: True) is None


def test_a_table_cut_off_at_its_row_limit_answers_nothing():
    k = dh.Known()
    k.keep(RX, [LOB], {}, table([LOB], [("Commercial", 1.0)]), False)
    assert k.find(RX, [LOB], {}, lambda x: True) is None


def test_the_map_goes_stale(monkeypatch):
    k = known()
    k.tables[0]["at"] -= dh.MAP_SECONDS + 1
    assert k.find(RX, [LOB], {MONTH: "2026-09"}, lambda x: True) is None


def test_a_conversation_keeps_its_own_map(kit):
    h = kit.compute.__self__
    ask("q", "one")
    h._map().keep(RX, [], {}, table([], [(1.0,)]), True)
    ask("q", "two")
    assert h._map().tables == []


def test_a_second_ask_does_not_reach_the_warehouse_and_the_page_still_gets_its_result(kit):
    h = kit.compute.__self__
    ask("What is PMPM in 2026?")
    step = {"metrics": ["total_pmpm"], "filters": {"Calendar[Date]:year": 2026}, "given": {}, "notes": []}
    kit._keep(table([], [(880.6,)], ["total_pmpm"]) | {"filters": {"Calendar[Date]:year": 2026}})
    h._note(step, None, kit)
    monkeypatch_ran = len(kit.calls)
    out = h._run(dict(step), kit.compute, None, None, None, False, None)
    assert "already found" in out and "880" in out and len(kit.calls) == monkeypatch_ran
    assert kit.results[-1]["shown"] == [(880.6,)] and kit.results[-1]["seq"] == 2


def test_what_the_model_reads_is_capped_and_the_newest_comes_first():
    k = dh.Known()
    for i in range(30):
        k.keep(RX, [], {MONTH: f"2024-{i:02d}"}, table([], [(float(i),)]), True)
    said = k.text(str, lambda c, v: str(v), 300)
    assert len(said) <= 300 and "2024-29" in said and "2024-00" not in said


def carried(kit, filters, question):
    h = kit.compute.__self__
    ask(question)
    h.ledger["s"] = {"form": {"filters": {"Member[LOB]": "Commercial"}}}
    return h._carried({"filters": filters}, question)["filters"]


def test_a_new_question_does_not_inherit_the_last_ones_filter(kit):
    assert carried(kit, {"Member[LOB]": "Commercial"}, "what are the primary drivers of medical cost?") == {}


@pytest.mark.parametrize("question", ["what about Commercial by plan", "and the drivers there?", "drivers of medical cost for commercial"])
def test_a_question_that_leans_on_the_last_or_names_it_keeps_it(kit, question):
    assert carried(kit, {"Member[LOB]": "Commercial"}, question) == {"Member[LOB]": "Commercial"}


def test_a_filter_the_new_question_brought_is_its_own(kit):
    assert carried(kit, {"Member[LOB]": "Medicaid"}, "drivers of medical cost") == {"Member[LOB]": "Medicaid"}


def test_a_sorted_table_serves_from_the_map_in_the_order_asked(kit):
    h = kit.compute.__self__
    ask("q")
    h._map().keep(RX, [LOB], {}, table([LOB], [("Commercial", 2.0), ("Medicaid", 1.0), ("MA", 3.0)]), True)
    step = {"metrics": RX, "filters": {}, "given": {}, "notes": []}
    assert h._recall(step, [LOB], 1, "rx", False, None, kit) is not None and kit.results[-1]["shown"] == [("MA", 3.0)]
    assert h._recall(step, [LOB], None, "rx", True, None, kit) is not None and kit.results[-1]["shown"][0] == ("Medicaid", 1.0)


class Stalls(Model):
    async def ainvoke(self, sent):
        self.asked += 1
        raise TimeoutError

    async def astream(self, sent):
        raise AssertionError("a model that could not read is not asked to write")
        yield


def test_a_model_that_stalls_costs_the_words_never_the_answer(kit):
    lines, orch = told(kit, Stalls(), "how much is medical pmpm please")
    assert orch.seen == [] and kit.calls[-1][0] == ["medical_pmpm"]
    assert lines[-1]["type"] == "result" and "**" in lines[-1]["data"]["answer"]


def test_what_is_driving_is_read_in_code_with_no_model_to_wait_for(kit):
    h = kit.compute.__self__
    form = h.read_why("what are the primary drivers of medical pmpm and total pmpm?")
    assert form["route"] == "explain" and form["metrics"] == ["medical_pmpm", "total_pmpm"]
    assert h.read_why("what is driving that?") is None        # a follow-up needs the conversation
    assert h.read_why("what is driving pmpm for bananas") is None  # a word it cannot place goes to the model
    assert h.read_why("what was medical pmpm") is None


def test_two_questions_at_once_keep_to_their_own_results(kit):
    h = kit.compute.__self__
    kit._keep({"asker": "a", "kind": "compute", "metrics": ["x"]})
    kit.results[-1]["asker"] = "a"
    kit._keep({"kind": "compute", "metrics": ["y"]})
    kit.results[-1]["asker"] = "b"
    token = dh.bi.ASKER.set("a")
    assert [r["metrics"] for r in h._mine(0)] == [["x"]]
    dh.bi.ASKER.reset(token)


@pytest.mark.parametrize("said", ["what can you help me with", "What can you help me with?", "hi", "Hello!", "help", "what can I ask?", "what do you do",
                                  "what metrics do you have", "how can you help me?"])
def test_talk_about_the_desk_is_answered_at_once(kit, said):
    model = Model()
    lines, orch = told(kit, model, said)
    assert model.asked == 0 and orch.seen == [] and kit.calls == []
    assert lines[-1]["data"]["type"] == "chat" and "Total PMPM" not in said and "governed" in lines[-1]["data"]["answer"]


@pytest.mark.parametrize("said", ["help me understand Total PMPM", "what can you tell me about Medicaid", "hi what was Total PMPM in 2025", "what drove cost"])
def test_a_real_question_is_not_small_talk(kit, said):
    assert kit.compute.__self__.hello(said) is None


def test_the_agent_is_sent_to_compute_after_a_few_lookups(tmp_path, monkeypatch):
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    calls = []

    async def column_values(ctx):
        calls.append(1)
        return "values"

    class Looks(Kit):
        def tools(self):
            return [*super().tools(), column_values]

    k = Looks()
    k.folder = tmp_path
    tool = next(t for t in dh.install(k, tmp_path)[0] if t.__name__ == "column_values")
    dh.TRACE.set({"calls": 0, "start": dh.time.monotonic(), "n": 0, "secs": 0.0, "slowest": 0.0})
    said = [asyncio.run(tool(None)) for _ in range(dh.LOOKUPS + 1)]
    assert said[:-1] == ["values"] * dh.LOOKUPS and said[-1] == dh.ENOUGH and len(calls) == dh.LOOKUPS
    dh.TRACE.set(None)


def test_a_measure_a_layer_cannot_split_still_shows_its_number(kit, monkeypatch):
    h = kit.compute.__self__
    monkeypatch.setattr(h, "_why", lambda steps, q: "")
    ask("what is driving total pmpm")
    dh.TRACE.set({"calls": 0, "start": dh.time.monotonic(), "n": 0, "secs": 0.0, "slowest": 0.0})
    out, tool = h.run({"route": "explain", "metrics": ["total_pmpm"]}, "what is driving total pmpm")
    dh.TRACE.set(None)
    assert tool == "compute" and "Data: x" in out and kit.calls


def test_every_reason_a_turn_went_to_the_agent_is_kept():
    t = {}
    dh._why(t, "the reading found no form for it")
    dh._why(t, "agent error: GraphRecursionError")
    assert t["why"] == "the reading found no form for it; agent error: GraphRecursionError"


HELPERS = {"current_enddayid_pharmacy": m("Current EndDayID Pharmacy", "MAX('Dim_Period'[EndDayID])"),
           "pharmacy_paid": {**m("Pharmacy Paid", "SUM(Claims[Paid])"), "format": "$#,0"},
           "pharmacy_scripts": m("Pharmacy Scripts", "COUNTROWS(Claims)"),
           "rx_a": m("Rx A", "[Current EndDayID Pharmacy] + [Pharmacy Paid]"), "rx_b": m("Rx B", "[Current EndDayID Pharmacy]"),
           "rx_c": m("Rx C", "[Current EndDayID Pharmacy]"), "selected_plan": m("Selected Pharmacy Plan", "SELECTEDVALUE(Plan[Name])")}


@pytest.fixture
def helpers(tmp_path, monkeypatch):
    for k, v in HELPERS.items():
        monkeypatch.setitem(METRICS, k, v)
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    k = Kit()
    k.folder = tmp_path
    dh.install(k, tmp_path)
    dh.QUESTION.set("")
    return k.compute.__self__


def test_a_measure_other_measures_read_for_a_date_is_never_what_a_question_means(helpers):
    assert "current_enddayid_pharmacy" not in helpers.rank("pharmacy", 20)
    assert "selected_plan" not in helpers.rank("pharmacy", 20)


def test_of_the_measures_a_word_fits_the_one_shown_as_money_comes_first(helpers):
    assert helpers.guess("what is driving pharmacy cost")["metrics"] == ["pharmacy_paid"]


def test_the_log_says_why_a_layer_could_not_start(kit):
    h = kit.compute.__self__
    ask("what is driving total pmpm")
    t = {"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()}
    dh.TRACE.set(t)
    kit.layer.columns_for = lambda metrics: []
    assert h._why_one("total_pmpm", {}, "q") == ""
    del kit.layer.columns_for
    dh.TRACE.set(None)
    assert t["why"] == "total_pmpm: no date column it reaches"


class Periods(Months):
    def query(self, sql, limit=None):
        return ([], [(20250930,), (20260831,), (20260930,)]) if sql == "asof" else super().query(sql, limit)


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    """An owner's scope: a rolling range, a toggle on a table nothing reaches, and the latest as-of."""
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    monkeypatch.setattr(dh.bi, "metric_needs", lambda layer, k: {"by": [], "params": {}})
    (tmp_path / "analysis_default.yaml").write_text("filters:\n  Period[Range]: Rolling 12 months\n  Period[AsOf]: latest\n  Toggle[Basis]: ADP\n")
    k = Kit()
    k.folder, k.wh = tmp_path, Periods()
    k._values_sql = lambda col, where=None: "asof" if col == "Period[AsOf]" else (_ for _ in ()).throw(ValueError("not read"))
    k.resolve = lambda filters: (filters or {}, [])

    class Reach(Layer):
        def leaves(self, key):
            return [key]

        def compile(self, metrics, by, filters=None, *rest, **kw):
            if "Toggle[Basis]" in (filters or {}):
                raise ValueError("Toggle is not related to Claims")
            return "sql"

    k.layer = Reach()
    k.tools_ = dh.install(k, tmp_path)[0]
    k.compute.__self__.kit = k
    return k.compute.__self__


SCOPE = {"Period[Range]": "Rolling 12 months", "Period[AsOf]": 20260930}


def test_the_owners_scope_is_on_every_number_and_the_month_default_gives_way(scoped):
    ask("What is Total PMPM?")
    assert scoped._settle(["total_pmpm"], None, None)["filters"] == SCOPE


def test_this_month_stays_in_the_owners_scope(scoped):
    ask("What is this month's Total PMPM?")
    assert scoped._settle(["total_pmpm"], None, None)["filters"] == SCOPE


def test_a_fallback_cannot_drop_the_scope(scoped):
    ask("What is Total PMPM?")
    assert scoped._settle(["total_pmpm"], None, None)["given"] == SCOPE


def test_a_column_the_question_filters_itself_is_the_questions(scoped):
    ask("Total PMPM for rolling 3 months")
    got = scoped._settle(["total_pmpm"], None, {"Period[Range]": "Rolling 3 months"})["filters"]
    assert got == {"Period[Range]": "Rolling 3 months", "Period[AsOf]": 20260930}


def test_latest_is_pinned_for_the_conversation(scoped):
    ask("q", "one")
    assert scoped._pinned()["Period[AsOf]"] == 20260930
    scoped.kit.wh.query = lambda sql, limit=None: ([], [(20261031,)])
    assert scoped._pinned()["Period[AsOf]"] == 20260930
    ask("q", "two")
    assert scoped._pinned()["Period[AsOf]"] == 20261031


def test_a_change_compares_the_scope_with_the_same_scope_a_year_before(scoped):
    ask("what is driving Total PMPM")
    assert scoped._asof() == ("Period[AsOf]", "20260930", "20250930")
    ask("what moved from last month")
    assert scoped._asof() == ("Period[AsOf]", "20260930", "20260831")


def test_every_answer_says_its_scope(scoped):
    ask("q")
    assert scoped.scope_line() == "*Rolling 12 months · ADP*\n\n"


def test_with_no_file_nothing_changes(kit):
    ask("What is Total PMPM?")
    h = kit.compute.__self__
    assert h.defaults == {} and h._scope_for(["total_pmpm"], None, {}) == {} and h._asof() is None


@pytest.mark.parametrize("name", ["analysis_default.yaml", "precedents.yaml"])
def test_what_owners_set_is_found_in_the_capture_folder_too(tmp_path, name):
    (tmp_path / "capture").mkdir()
    (tmp_path / "capture" / name).write_text("filters: {}")
    assert dh._beside(tmp_path, name) == tmp_path / "capture" / name
    (tmp_path / name).write_text("filters: {}")
    assert dh._beside(tmp_path, name) == tmp_path / name


@pytest.fixture
def contract(scoped):
    """A model that refuses a metric until its whole reporting context is given, naming the table it wants."""
    plain = scoped.layer.compile

    def compile(metrics, by, filters=None, *rest, **kw):
        if not {"Period[Range]", "Period[AsOf]"} <= {c.split(":")[0] for c in filters or {}}:
            raise ValueError("Specify the missing reporting context: Period[Range], Period[AsOf]")
        return plain(metrics, by, filters, *rest, **kw)

    scoped.layer.compile = compile
    return scoped


def test_a_reporting_context_is_given_whole_and_a_table_it_names_is_not_out_of_reach(contract):
    ask("What is Total PMPM?")
    assert contract._settle(["total_pmpm"], None, None)["filters"] == SCOPE


def test_the_latest_month_is_read_inside_the_reporting_context(contract):
    ask("What is Total PMPM?")
    assert contract._latest_month("total_pmpm", "Calendar[Date]:month")


def test_a_table_truly_out_of_reach_still_leaves_the_rest_of_the_scope(contract):
    ask("q")
    assert contract._scope_for(["total_pmpm"], None, {}) == SCOPE  # Toggle is said to be not related, and goes alone


def test_metric_needs_says_what_the_model_wants_and_what_the_scope_gives(contract):
    ask("q")
    r = contract.requires("total_pmpm")
    assert "reporting context" in r["alone"] and r["scope"] == SCOPE and r["missing"] == "" and r["unreached"] == ["Toggle[Basis]"]
    text = contract.needs_text("total_pmpm")
    assert "Period[Range] = Rolling 12 months" in text and "Nothing else is needed" in text


def test_metric_needs_says_what_is_still_missing(contract):
    ask("q")
    contract.ledger.setdefault(dh.SESSION.get(), {})["pinned"] = {"Period[Range]": "Rolling 12 months"}
    assert "Still needed: Specify the missing reporting context" in contract.needs_text("total_pmpm")


def test_metric_needs_is_a_tool_and_about_metric_says_it_too(contract):
    ask("q")
    assert "The owner's scope gives" in contract.details("total_pmpm")
    tool = next(t for t in contract.kit.tools_ if t.__name__ == "metric_needs")
    assert "Nothing else is needed" in asyncio.run(tool(None, "total_pmpm"))


def test_a_metric_outside_the_owners_scope_never_comes_first(scoped):
    first, second = scoped.rank("pmpm", 2)
    scoped._scoped.clear()
    scoped.layer.columns_for = lambda ks: [] if ks == [first] else ["Period[Range]"]
    assert scoped.rank("pmpm", 2)[:2] == [second, first]


@pytest.mark.parametrize("value,month", [(20260930, (2026, 9)), ("202609", (2026, 9)), ("2026-09-30", (2026, 9)), ("Sep 2026", (2026, 9)),
                                         ("September 2026", (2026, 9)), ("9/30/2026", (2026, 9)), ("Rolling 12", None), (20261399, None)])
def test_a_period_reads_however_the_column_writes_it(value, month):
    assert dh._month_of(value) == month


def test_latest_goes_by_the_calendar_not_the_alphabet(scoped):
    ask("q", "text months")
    scoped.kit.wh.query = lambda sql, limit=None: ([], [("Sep 2025",), ("Oct 2025",), ("Jan 2026",)])
    assert scoped._pinned()["Period[AsOf]"] == "Jan 2026"


MONTH_ENDS = [37164, 37195, 37225, 37256, 37287, 37315, 37346, 37376, 37407, 37437, 37468, 37499, 37529]


@pytest.mark.parametrize("now,values,months,before", [
    (37529, MONTH_ENDS, 12, 37164), (37529, MONTH_ENDS, 1, 37499),
    ("37529", [str(v) for v in MONTH_ENDS], 12, "37164"),
    (120, list(range(100, 121)), 12, 108), (120, list(range(100, 121)), 1, 119),
    (20260930, [20250930, 20260831, 20260930], 12, 20250930), ("2026-01", ["2025-12", "2026-01"], 1, "2025-12"),
    (37529, [37529, 37499], 12, None), (500, [100, 300, 500], 12, None), ("Rolling", ["Rolling"], 12, None)])
def test_a_period_before_is_read_from_how_the_column_steps(now, values, months, before):
    assert dh._back(now, values, months) == before


def test_the_latest_key_goes_by_number_not_the_alphabet():
    assert dh._latest_of([9999, 37529]) == 37529 and dh._latest_of(["9999", "37529"]) == "37529"


@pytest.fixture
def keyed(scoped):
    """The as-of is a day count, as a warehouse key often is."""
    rows = [(v,) for v in MONTH_ENDS]
    scoped.kit.wh.query = lambda sql, limit=None: ([], rows)
    return scoped


def test_an_as_of_key_compares_with_a_year_before_and_is_never_shown(keyed):
    ask("what is driving Total PMPM")
    assert keyed._asof() == ("Period[AsOf]", "37529", "37164")
    assert keyed._against("37529", "37164") == "against the same period a year before"


def test_with_no_period_to_compare_a_scope_does_not_fall_back_to_months(scoped):
    ask("what is driving Total PMPM", "none")
    scoped.kit.wh.query = lambda sql, limit=None: ([], [("Current",)])
    t = {"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()}
    dh.TRACE.set(t)
    scoped._latest_month = lambda *a: pytest.fail("months are not what the scope compares")
    assert scoped._why_one("total_pmpm", {}, "q") == ""
    dh.TRACE.set(None)
    assert "has no value 12 months before Current" in t["why"]


def test_the_log_says_a_reason_once():
    t = {}
    dh._why(t, "a"); dh._why(t, "b"); dh._why(t, "a")
    assert t["why"] == "a; b"


def test_history_names_its_periods_so_a_reporting_context_is_whole():
    seen = {}

    class Strict:
        tables = "kept"

        def compile(self, metrics, by=None, filters=None, *rest, **kw):
            seen.update(filters or {})
            return "sql"

    layer = dh.Named(Strict(), "Period[AsOf]", [1, 2])
    assert layer.tables == "kept"
    layer.compile(["m"], ["Period[AsOf]"], {"Period[Range]": "R"})
    assert seen == {"Period[Range]": "R", "Period[AsOf]": [1, 2]}
    seen.clear()
    layer.compile(["m"], ["Period[AsOf]"], {"Period[AsOf]": [9]})
    assert seen == {"Period[AsOf]": [9]}
    seen.clear()
    layer.compile(["m"], ["Member[LOB]"], {"Period[Range]": "R"})
    assert seen == {"Period[Range]": "R"}


@pytest.mark.parametrize("label,shown", [("Visits%Change_Dental", "Visits % Change Dental"), ("Billed$Change_Dental", "Billed $ Change Dental"),
                                         ("Current_VisitCostBilled_Dental", "Current Visit Cost Billed Dental"), ("Total PMPM", "Total PMPM"),
                                         ("Admits per 1,000", "Admits per 1,000"), ("Members", "Members")])
def test_a_measure_kept_as_code_is_said_as_a_reader_says_it(label, shown):
    assert dh._nice(label) == shown


CHANGES = {"units_change": m("Visits%Change_Dental", "DIVIDE([Prior_Units]-[Current_Units],[Prior_Units])"),
           "current_units": m("Current_Units", "SUM(Claims[Units])"), "prior_units": m("Prior_Units", "SUM(Claims[Units])"),
           "lonely_change": m("Lonely Change", "1")}


@pytest.fixture
def changes(tmp_path, monkeypatch):
    for k, v in CHANGES.items():
        monkeypatch.setitem(METRICS, k, v)
    monkeypatch.setattr(dh.bi, "describe", lambda layer, k: "formula")
    monkeypatch.setattr(dh.bi, "needs", lambda layer, k: "")
    k = Kit()
    k.folder = tmp_path
    dh.install(k, tmp_path)
    dh.QUESTION.set("")
    return k.compute.__self__


def test_why_looks_at_what_a_change_measure_is_the_change_of(changes):
    assert changes._changing("units_change") == "current_units"
    assert changes._changing("current_units") == "current_units"
    assert changes._changing("lonely_change") is None


def test_a_change_measure_with_nothing_to_look_into_is_read_not_explained(changes):
    assert changes._why({"metrics": ["lonely_change"]}, "why") == ""


def test_the_change_in_a_measure_means_the_measure(changes):
    assert changes.rank("change in units")[0] == "current_units"
    assert changes.rank("units % change")[0] == "units_change"


def test_the_line_says_a_number_a_reader_can_trust(scoped):
    ask("what is driving it")
    rec = lambda a, b, metric="total_pmpm": {"seq": 1, "filters": {"Period[Range]": "Rolling 12 months", "Member[LOB]": "Commercial"},
                                             "explain": {"metric": metric, "label": "Visits_Dental", "period": "Period[AsOf]", "now": "37529", "before": "37164",
                                                         "total_after": a, "total_before": b, "steps": []}}
    scoped.kit.results = [rec(110.0, 100.0)]
    assert scoped.layer_line(0).strip() == "Visits Dental in Commercial **+10.0%** · against the same period a year before"
    scoped.kit.results = [rec(-5.0, -2.0)]
    assert "**-3.00**" in scoped.layer_line(0)


def test_a_trace_says_each_step_in_and_out():
    t = {"start": dh.time.monotonic()}
    dh.TRACE.set(t)
    dh._step("settle", ["total_pmpm"], "filters {}")
    dh._step("query", "select " + "x" * 400)
    dh.TRACE.set(None)
    dh._step("nowhere")
    out = dh._trace("what is it", t).splitlines()
    assert out[0] == "[trace] what is it" and "settle" in out[1] and "total_pmpm" in out[1] and out[2].strip() == "out: filters {}"
    assert len(out) == 4 and out[3].endswith("...") and len(out[3]) < dh.TRACE_CHARS + 40


def test_a_question_reads_the_whole_catalog_and_the_scope_it_must_not_add(scoped):
    ask("how much of the change in pmpm is utilization versus unit cost")
    system, human = scoped.form_for(dh.QUESTION.get(), dh.SESSION.get())
    assert all(k in human[1] for k in scoped.catalog.metrics if scoped.catalog.metrics[k]["role"] != "helper")
    assert "never add these: Period[Range]" in human[1] and "not by matching words" in system[1]
    assert human[1].startswith("Measures:")


def test_a_word_of_a_measure_is_not_a_slice(kit):
    h = kit.compute.__self__
    h.values = {"Member[Kind]": ["Cost"]}
    form = h.guess("what are the drivers of total pmpm cost trends")
    assert "Member[Kind]" not in (form or {}).get("filters", {})


ZERO = "Total PMPM: 0 in 2025-09, 0 in 2026-09, a change of +0.\nNothing changed, so there is nothing to explain."


def test_nothing_for_two_periods_is_asked_alone_and_says_what_stands(kit):
    h = kit.compute.__self__
    ask("what is driving total pmpm")
    h.explain = lambda *a, **k: ZERO
    kit.resolve = lambda f: (f or {}, [])
    kit.wh.query = lambda sql, limit=None: ([], [(812.5,)])
    got = h._why_one("total_pmpm", {}, "q")
    assert "812" in got and "could not be worked out" in got


def test_no_data_at_all_is_said_as_that_and_never_as_no_movement(kit):
    h = kit.compute.__self__
    ask("what is driving total pmpm")
    h.explain = lambda *a, **k: ZERO
    kit.resolve = lambda f: (f or {}, [])
    kit.wh.query = lambda sql, limit=None: ([], [(None,)])
    got = h._why_one("total_pmpm", {}, "q")
    assert got.endswith("no data in this scope.") and "movement" not in got
    assert h._why({"metrics": ["total_pmpm"]}, "q")  # the question ends here: no fallback runs


def test_a_small_rate_that_moved_is_not_taken_for_nothing(kit):
    h = kit.compute.__self__
    ask("what is driving total pmpm")
    moved = "Rate: 0 in 2025-09, 0 in 2026-09, a change of +0 (+25.0%).\nLevel 1"
    h.explain = lambda *a, **k: moved
    assert h._why_one("total_pmpm", {}, "q") == moved


@pytest.mark.parametrize("column,fits", [("Member[Plan]", True), ("Claims[Paid]", True), ("Claims[Line Of Business]", True), ("Claims[ClaimId]", False),
                                         ("Member[MemberKey]", False), ("Claims[Id]", False), ("Claims[SortOrder]", False), ("Claims[RowIndex]", False),
                                         ("Claims[AsofDay]", False), ("Claims[PeriodRange]", False), ("Claims[IsActive]", False), ("Claims[PageNumber]", False),
                                         ("Claims[Issuer]", True), ("Claims[EndMonth]", False)])
def test_a_driver_is_something_a_reader_could_be_told(kit, column, fits):
    assert kit.compute.__self__._driver(column) is fits


def test_the_owners_tables_and_a_yes_or_no_are_not_drivers(scoped):
    scoped.values = {"Member[Active]": ["Y", "N"], "Member[Plan]": ["Gold", "Silver"]}
    assert not scoped._driver("Toggle[Basis]") and not scoped._driver("Member[Active]") and scoped._driver("Member[Plan]")


def test_every_measure_a_question_names_is_looked_into(kit):
    h = kit.compute.__self__
    ask("what is driving these")
    seen = []
    h._why_one = lambda k, *a: seen.append(k) or f"{k} looked at"
    keys = [f"measure_{i}" for i in range(4)]
    h.idx.update({k: h.idx["total_pmpm"] for k in keys})
    h._why({"metrics": keys}, "q")
    assert sorted(seen) == keys


def test_an_empty_latest_period_is_read_at_the_latest_one_with_a_number_and_says_so(keyed, monkeypatch):
    ask("what is driving Total PMPM")
    t = {"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()}
    dh.TRACE.set(t)
    have = {37468: 410.0, 37437: 400.0, 37103: 380.0}  # nothing after 37468: its claims are not in yet
    keyed._under = lambda col: [37103, 37134, *MONTH_ENDS]
    keyed._values = lambda pairs, period, filters: {p: have.get(int(p[1])) for p in pairs}
    seen = []

    def trend(metric, period, now, before, filters):
        seen.append((str(now), str(before)))
        return (f"Total PMPM at {now}.", True) if int(now) in have else ("", False)

    keyed._trend = trend
    keyed.explain = lambda metric, period, now, before, filters=None, used=(), pinned=False: f"explained {now} against {before}, {pinned}"
    out = keyed._why_one("total_pmpm", {"metrics": ["total_pmpm"]}, "what is driving Total PMPM")
    assert seen == [("37529", "37164"), ("37468", "37103")]
    assert "no number yet for the latest reporting period" in out and "about 2 months earlier" in out
    assert out.endswith("explained 37468 against 37103, True")


def test_a_measure_with_no_number_in_any_period_says_so(keyed):
    ask("what is driving Total PMPM")
    dh.TRACE.set({"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()})
    keyed._values = lambda pairs, period, filters: dict.fromkeys(pairs)
    keyed._trend = lambda *a: ("", False)
    keyed.explain = lambda *a, **k: pytest.fail("nothing to explain")
    assert keyed._why_one("total_pmpm", {}, "q").endswith("no number in any reporting period of this scope.")


def test_a_latest_number_with_no_year_before_it_is_said_alone(keyed):
    ask("what is driving Total PMPM")
    dh.TRACE.set({"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()})
    keyed._values = lambda pairs, period, filters: {p: 410.0 if int(p[1]) == 37468 else None for p in pairs}
    keyed._trend = lambda metric, period, now, before, filters: ("", int(now) == 37468)
    keyed.explain = lambda *a, **k: pytest.fail("there is no year before to compare with")
    keyed._alone = lambda metric, filters, period: f"alone at {period}"
    out = keyed._why_one("total_pmpm", {}, "q")
    assert out.endswith("alone at {'Period[AsOf]': '37468'}")


def test_every_query_is_written_whole_with_what_came_back_when_an_audit_file_is_named(tmp_path, monkeypatch):
    target = tmp_path / "audit.jsonl"
    monkeypatch.setenv("DESK_AUDIT", str(target))
    dh.QUESTION.set("what is Dental PMPM")
    dh._audit("SELECT 1", 0.5, rows=[(None,)])
    dh._audit("SELECT 2", 0.1, error="no such table")
    first, second = (json.loads(x) for x in target.read_text().splitlines())
    assert first["sql"] == "SELECT 1" and first["rows"] == 1 and first["first"] == [[None]] and first["question"] == "what is Dental PMPM"
    assert second["error"] == "no such table" and second["rows"] is None


def test_no_audit_file_is_written_unless_one_is_named(tmp_path, monkeypatch):
    monkeypatch.delenv("DESK_AUDIT", raising=False)
    monkeypatch.chdir(tmp_path)
    dh._audit("SELECT 1", 0.5, rows=[])
    assert list(tmp_path.iterdir()) == []


def test_an_empty_number_is_asked_again_without_each_scope_filter_in_turn(keyed):
    t = {"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()}
    dh.TRACE.set(t)
    keyed._control("total_pmpm", {"Period[Range]": "Rolling 12 months", "Period[AsOf]": 37529})
    said = {given: got for _, node, given, got in t["steps"] if node == "control"}
    assert set(said) == {"total_pmpm without Period[Range]", "total_pmpm without Period[AsOf]"}


GRID = {"version": "x", "slices": {"S1": ["Member[Plan]", "Member[Member ID]"]},
        "metrics": {"total_pmpm": {"label": "Total PMPM", "formula": "total_paid / member_months", "slices": "S1",
                                   "refused": {"Claims is not related to Member Months": ["Claims[Setting]"]}}}}


def test_the_catalog_shows_what_each_measure_can_be_cut_by_from_the_grid(kit):
    h = kit.compute.__self__
    assert h.cut_by() == ""  # no grid, no section
    h.grid = GRID
    said = h.cut_by()
    assert "S1: Member[Plan]" in said and "Member ID" not in said and "measures: total_pmpm" in said


def test_get_measure_says_why_a_column_will_not_cut_a_measure_and_runs_nothing(kit):
    h = kit.compute.__self__
    h.grid = GRID
    kit.wh = None  # nothing may reach a warehouse
    out = h.measure("total_pmpm", "Claims[Setting]")
    assert out.startswith("total_pmpm cannot be cut by Claims[Setting]: Claims is not related to Member Months")
    assert "is: total_paid / member_months" in h.measure("total_pmpm")


def test_a_why_that_finds_no_number_anywhere_is_handed_to_the_agent_with_what_was_tried(keyed):
    ask("what is driving Total PMPM")
    t = {"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic(),
         "steps": [(0.1, "control", "total_pmpm without Period[Range]", 412.0)]}
    dh.TRACE.set(t)
    keyed._why_one = lambda k, steps, q: "Total PMPM: no data in this scope."
    assert keyed._why({"metrics": ["total_pmpm"]}, "q") == ""
    assert "no data in this scope" in t["tried"] and "without Period[Range]: 412.0" in t["tried"]


def test_a_month_column_written_as_text_is_matched_by_the_month_it_means(kit):
    h = kit.compute.__self__
    held = ["Apr 2025", "Aug 2025", "Aug 2026", "Sep 2026", "Jan 2026"]
    h._days = lambda col, fresh=False: sorted(held)
    h._known = lambda col, fresh=False: (held, True)
    notes = []
    assert h._on("Dim[AsOf]", "Aug 2026", dh._when("Aug 2026"), notes) == "Aug 2026" and not notes
    assert h._on("Dim[AsOf]", "2026-08", dh._when("2026-08"), notes) == "Aug 2026"
    with pytest.raises(dh.bi.LayerError, match="runs from Apr 2025 to Sep 2026"):
        h._on("Dim[AsOf]", "2027-01", dh._when("2027-01"), notes)
