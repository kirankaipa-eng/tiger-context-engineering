"""The LangGraph agent runtime, with a scripted model (no LLM, no network): tools are called with the
model's arguments, the events are the ones the CLI and the Studio read, runtime tools reach the stream,
and the conversation is kept per session."""

import asyncio
import json

import pytest

pytest.importorskip("langgraph")
from langchain_core.language_models.chat_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from insurance_context.agent_runtime import Orchestrator, OrchestratorEvent, RunContext, as_tool  # noqa: E402


class Scripted(BaseChatModel):
    """Answers with the next scripted message; records what it was sent."""
    script: list
    seen: list

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def call(name, args, i="c1"):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": i, "type": "tool_call"}])


async def compute(ctx: RunContext, metrics: list[str], by: list[str] | None = None, top: int | None = None) -> str:
    """Compute captured metrics, grouped by columns."""
    return f"computed {metrics} by {by} top {top}"


def run(orch, message, session="s1", **kw):
    async def go():
        return [json.loads(line) async for line in orch.stream(message, session_id=session, **kw)]
    return asyncio.run(go())


def test_a_tool_takes_its_schema_from_the_signature_without_the_context():
    t = as_tool(compute)
    assert t.name == "compute" and t.description == "Compute captured metrics, grouped by columns."
    assert set(t.args) == {"metrics", "by", "top"}
    assert asyncio.run(t.ainvoke({"metrics": ["net_sales"], "top": 3})) == "computed ['net_sales'] by None top 3"


def test_events_follow_the_tool_loop():
    model = Scripted(script=[call("compute", {"metrics": ["net_sales"], "by": ["Store[Store]"]}),
                             AIMessage("Contoso sold most.")], seen=[])
    orch = Orchestrator(None, None, "scripted", "You answer from tools.", tools=[compute],
                        chat_model=lambda http: model)
    events = run(orch, "Which store sold most?")
    kinds = [(e["type"], e["data"].get("status")) for e in events]
    assert kinds[0] == ("agent_status", "active")
    assert ("agent_tool_call", "started") in kinds and ("agent_tool_call", "completed") in kinds
    done = next(e for e in events if e["type"] == "agent_tool_call" and e["data"]["status"] == "completed")
    assert done["data"]["summary"] == "computed ['net_sales'] by ['Store[Store]'] top None"
    assert events[-1] == {"type": "result", "data": {"type": "analysis", "answer": "Contoso sold most."}}
    assert model.seen[0][0].content == "You answer from tools."  # the system prompt leads every call


def test_runtime_tools_emit_into_the_stream_and_the_session_is_remembered():
    def make_show(emit, agent_id):
        async def show(ctx: RunContext, title: str) -> str:
            """Show a card."""
            await emit(OrchestratorEvent("studio_canvas", {"kind": "card", "title": title}))
            return "shown"
        return show

    model = Scripted(script=[call("show", {"title": "Sales"}), AIMessage("Shown."), AIMessage("Still here."),
                             AIMessage("New.")],
                     seen=[])
    orch = Orchestrator(None, None, "scripted", "base", runtime_tool_factories=[make_show],
                        chat_model=lambda http: model)
    events = run(orch, "Show sales", system_prompt="for Ana")
    assert {"type": "studio_canvas", "data": {"kind": "card", "title": "Sales"}} in events
    assert model.seen[0][0].content == "for Ana"  # a turn's prompt replaces the default
    run(orch, "Still there?")
    assert [m.content for m in model.seen[-1][1:] if m.type == "human"] == ["Show sales", "Still there?"]
    run(orch, "Fresh start?", session="s2")
    assert [m.content for m in model.seen[-1][1:]] == ["Fresh start?"]  # another session starts empty


def test_an_error_reaches_the_consumer():
    model = Scripted(script=[], seen=[])  # nothing to say: the model raises
    orch = Orchestrator(None, None, "scripted", "base", chat_model=lambda http: model)
    events = run(orch, "Hello?")
    assert events[-1]["type"] == "error" and "IndexError" in events[-1]["data"]["message"]
