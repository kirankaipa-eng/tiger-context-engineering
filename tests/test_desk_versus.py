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
