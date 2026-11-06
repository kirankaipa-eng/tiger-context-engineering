"""What a question means is settled in code: one metric for a word, one period when none is said, no prior-year
measure unasked, and nothing changes for what is not an agent's turn."""

import asyncio
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


def test_the_deep_search_runs_only_when_asked(investigating, monkeypatch):
    seen = looked(monkeypatch)
    ask("Dig deeper into inpatient")
    investigating.compute.__self__.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    assert seen["depth"] == 3 and len(seen["cols"]) == 4


def test_an_investigation_stops_at_its_limit_and_says_it_is_partial(investigating, monkeypatch):
    looked(monkeypatch)
    ask("Dig deeper")
    out = investigating.compute.__self__.explain("total_pmpm", MONTH, "2026-09", "2025-09")
    assert investigating.wh.ran == dh.EXPLAIN_QUERIES and "partial" in out


def test_a_turn_that_goes_round_and_round_is_told_to_answer(kit):
    calls = []

    async def fake(ctx=None):
        calls.append(1)
        return "ok"

    fake.__name__ = "column_values"
    kit.tools = lambda: [fake]
    tools, _, _ = dh.install(kit, kit.folder)
    tool = next(t for t in tools if t.__name__ == "column_values")
    dh.TRACE.set({"n": 0, "secs": 0.0, "slowest": 0.0, "calls": 0, "start": dh.time.monotonic()})
    got = [asyncio.run(tool()) for _ in range(dh.TURN_CALLS + 1)]
    assert got[-2] == "ok" and got[-1] == dh.OUT_OF_TURN and len(calls) == dh.TURN_CALLS


def test_a_turn_that_hangs_ends_with_an_error_the_screen_can_show(kit, monkeypatch):
    class Hangs:
        async def stream(self, message, session_id="default"):
            yield "first\n"
            await asyncio.sleep(30)

    _, _, wrap = dh.install(kit, kit.folder)
    kit.compute.__self__.turn_seconds = 0.05
    orch = wrap(Hangs())

    async def run():
        return [x async for x in orch.stream("why?")]

    got = asyncio.run(asyncio.wait_for(run(), 5))
    assert got[0] == "first\n" and json.loads(got[1])["type"] == "error"


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
