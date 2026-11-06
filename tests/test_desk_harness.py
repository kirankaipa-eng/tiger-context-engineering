"""What a question means is settled in code: one metric for a word, one period when none is said, no prior-year
measure unasked, and nothing changes for what is not an agent's turn."""

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


class Kit:
    layer, folder = Layer(), None

    def __init__(self):
        self.calls, self.results = [], []

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
        if by == [MONTH]:
            self.results = [*self.results, {"shown": [("2026-08", 1.0), ("2026-09", 2.0), ("2026-10", None)]}]
        return "Data: x"


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
    assert kit.results == []  # the look at the calendar is not an exhibit


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


def test_a_dashboard_on_the_same_toolkit_is_untouched(kit):
    assert kit.compute(["total_pmpm_final"]) == "Data: x"
    assert kit.calls[-1] == (["total_pmpm_final"], None, None)


def test_the_list_is_replaced_and_the_prompt_says_so(kit):
    names = [t.__name__ for t in kit.tools_]
    assert "find_metrics" in names and "list_metrics" not in names and "find_metrics" in kit.prompt
