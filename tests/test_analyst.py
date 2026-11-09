"""The analyst: one loop, a few plain tools, the owner's scope applied and said, the chart the answer is about."""

import asyncio
import json
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parents[1] / "build" / "pbi" / "plains-health"


@pytest.fixture
def made(tmp_path):
    if not (DEMO / "pilot.duckdb").exists():
        pytest.skip("the demo model is not built here")
    import shutil

    for name in ("capture.yaml", "pilot.duckdb", "pilot.duckdb.about"):
        if (DEMO / name).exists():
            shutil.copy(DEMO / name, tmp_path / name)

    def make(scope: str | None = None):
        from insurance_context import analyst
        from insurance_context.bi_agent import BIToolkit
        from insurance_context.bilayer import BILayer
        from insurance_context.biwarehouse import Warehouse

        if scope:
            (tmp_path / "analysis_default.yaml").write_text(scope)
        layer = BILayer.load(tmp_path)
        kit = BIToolkit(layer, Warehouse.open(tmp_path, layer))
        a = analyst.Analyst(kit, tmp_path)
        a._ready.wait()
        return analyst, kit, a
    return make


def test_a_step_runs_under_the_owners_scope_and_says_so(made):
    _, kit, a = made("filters:\n  Member[Line of Business]: Commercial\n")
    out = a.compute([{"metrics": ["total_pmpm"], "by": ["Member[State]"],
                      "filters": {"Member[Line of Business]": "Medicaid"}}])  # a scope column is the owner's
    assert out.startswith("R1 ok") and "owner's scope: Member[Line of Business] = Commercial" in out
    assert kit.results[-1]["filters"]["Member[Line of Business]"] == "Commercial"


def test_each_step_says_ok_empty_or_refused(made):
    _, _, a = made()
    out = a.compute([{"metrics": ["total_pmpm"]},
                     {"metrics": ["total_pmpm"], "filters": {"Calendar[Date]:year": 1990}},
                     {"metrics": ["no_such_measure"]}])
    assert "R1 ok" in out and "R2 empty" in out and "R3 refused: no_such_measure is not a measure key" in out


def test_the_step_the_answer_is_about_is_the_one_drawn(made):
    _, kit, a = made()
    a.compute([{"metrics": ["total_pmpm"], "by": ["Member[State]"], "show": True}, {"metrics": ["rx_pmpm"]}])
    assert kit.results[-1]["metrics"] == ["total_pmpm"] and kit.results[-1]["by"] == ["Member[State]"]


def test_the_prompt_holds_what_the_model_needs(made):
    _, _, a = made("filters:\n  Member[Line of Business]: Commercial\n")
    text = a.prompt() + a.facts()
    assert "total_pmpm breaks into medical_pmpm + rx_pmpm" in text and "Member[State]: IL" in text
    assert "Latest month with data: 20" in text and "never add these): Member[Line of Business] = Commercial" in text


def test_a_question_has_a_budget_of_tool_calls(made):
    analyst, kit, a = made()
    tools, _, _ = analyst.install(kit, a.folder)
    values = next(t for t in tools if t.__name__ == "values")
    analyst.TURN.set({"calls": analyst.MAX_CALLS, "start": __import__("time").monotonic(), "n": 0, "tools": []})
    assert asyncio.run(values(None, column="Member[State]")) == analyst.ENOUGH


class Scripted:
    """A chat model that calls compute once, then answers: the shape of most questions."""

    def __new__(cls, steps, answer):
        from langchain_core.language_models.chat_models import BaseChatModel
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        class Model(BaseChatModel):
            seen: list = []

            @property
            def _llm_type(self):
                return "scripted"

            def bind_tools(self, tools, **kw):
                return self

            def _generate(self, messages, stop=None, run_manager=None, **kw):
                self.seen.append(messages)
                if isinstance(messages[-1], ToolMessage):
                    msg = AIMessage(answer)
                else:
                    msg = AIMessage("", tool_calls=[{"name": "compute", "args": {"steps": steps}, "id": "c1"}])
                return ChatResult(generations=[ChatGeneration(message=msg)])
        return Model()


def test_a_question_is_one_loop_of_model_tools_model(made):
    analyst, kit, a = made()
    from insurance_context.agent_runtime import Orchestrator

    model = Scripted([{"metrics": ["total_pmpm"], "by": ["Member[State]"], "order_by": "total_pmpm", "show": True}],
                     "Illinois has the highest Total PMPM, $952 in September 2026.")
    tools, prompt, wrap = analyst.install(kit, a.folder)
    orch = wrap(Orchestrator(api_key="k", base_url="u", model_name="m", system_prompt=prompt, tools=tools,
                             chat_model=lambda http: model))

    async def go():
        return [json.loads(x) async for x in orch.stream("Where is Total PMPM highest?", session_id="s")]
    lines = asyncio.run(go())
    done = [x for x in lines if x["type"] == "agent_tool_call" and x["data"]["status"] == "completed"]
    assert done[0]["data"]["tool"] == "compute" and done[0]["data"]["summary"].startswith("R1 ok")
    assert lines[-1]["type"] == "result" and "Illinois" in lines[-1]["data"]["answer"]
    assert kit.results[-1]["by"] == ["Member[State]"]
    assert "Latest month with data" in str(model.seen[0][0].content)  # this turn's facts, in its system prompt
