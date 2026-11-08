"""Why one value stands out: it against everyone in the same period, and the page given the rows the answer states."""

from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "build" / "pbi" / "plains-health"


@pytest.fixture
def desk():
    if not (DEMO / "pilot.duckdb").exists():
        pytest.skip("the demo model is not built here")
    from insurance_context import desk_harness as dh
    from insurance_context.bi_agent import BIToolkit
    from insurance_context.bilayer import BILayer
    from insurance_context.biwarehouse import Warehouse

    layer = BILayer.load(DEMO)
    kit = BIToolkit(layer, Warehouse.open(DEMO, layer))
    dh.install(kit, DEMO)
    dh.SESSION.set("versus")
    dh.TRACE.set(None)  # a question's trace left by another test is not this one's
    return dh, kit, kit.compute.__self__


def test_why_a_value_is_highest_compares_it_with_everyone(desk):
    dh, kit, h = desk
    q = "why is IL the highest?"
    dh.QUESTION.set(q)
    out, tool = h.run({"route": "explain", "metrics": ["total_pmpm"], "filters": {"Member[State]": "IL"}}, q)
    assert tool == "explain" and "against" in out and "for everyone" in out and "not with its own past" in out
    assert "By service category" in out and "The largest gap is" in out
    last = kit.results[-1]
    assert last["by"] == ["Member[State]"] and len(last["shown"]) > 1  # the ranking the page draws


def test_why_it_changed_still_reads_its_past(desk):
    dh, kit, h = desk
    q = "why did IL total pmpm go up?"
    dh.QUESTION.set(q)
    out, _ = h.run({"route": "explain", "metrics": ["total_pmpm"], "filters": {"Member[State]": "IL"}}, q)
    assert "for everyone" not in out


def test_the_highest_is_read_from_the_whole_ranking(desk):
    dh, kit, h = desk
    q = "Where is Total PMPM highest?"
    dh.QUESTION.set(q)
    h.run({"route": "compute", "metrics": ["total_pmpm"], "by": ["Member[State]"], "top": 1}, q)
    last = kit.results[-1]
    states = [r[0] for r in last["shown"]]
    assert len(states) > 1 and last["shown"][0][1] == max(r[1] for r in last["shown"])


def test_a_why_keeps_the_two_periods_it_compared_for_the_page(desk):
    dh, kit, h = desk
    q = "what drove cost up in IL?"
    dh.QUESTION.set(q)
    seen = kit._seq
    h.run({"route": "explain", "metrics": ["rx_pmpm", "total_pmpm"], "filters": {"Member[State]": "IL"}}, q)
    last = kit.results[-1]
    assert last["kind"] == "compute" and len(last["by"]) == 1 and len(last["shown"]) == 2
    assert {"rx_pmpm", "total_pmpm"} <= set(last["metrics"])
    lines = [x for x in h.layer_line(seen).split("\n\n") if x]
    assert lines and lines[0].startswith("Rx PMPM")  # the first measure asked leads, not the first to finish


def test_a_read_whose_shape_slipped_still_runs(desk):
    dh, kit, h = desk
    q = "Where is Total PMPM highest, and how has it moved?"
    dh.QUESTION.set(q)
    form = {"route": "compute", "metrics": ["base:total_pmpm", "change_percent:total_pmpm_yoy_pct"],
            "by": ["Member[State]", {"Calendar[Date]:year": 2025}], "filters": {}, "order_by": "base:total_pmpm"}
    out, _ = h.run(form, q)
    assert out and not out.startswith("Error")
    last = kit.results[-1]
    assert last["by"] == ["Member[State]"] and last["metrics"] == ["total_pmpm"] and last["filters"] == {"Calendar[Date]:year": 2025}


def test_a_prior_version_the_read_added_does_not_sink_the_answer(desk):
    dh, kit, h = desk
    q = "Where is Total PMPM highest, and how has it moved?"
    dh.QUESTION.set(q)
    out, _ = h.run({"route": "compute", "metrics": ["total_pmpm", "total_pmpm_py", "total_pmpm_yoy_pct"], "by": ["Member[State]"],
                    "filters": {"Calendar[Date]:year": 2025}, "order_by": "total_pmpm"}, q)
    assert out and kit.results[-1]["metrics"] == ["total_pmpm"]
