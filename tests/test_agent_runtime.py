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


def paired(messages):
    """Every tool call sent has its result, and every result its call, as the model's API requires."""
    calls = {c["id"] for m in messages for c in getattr(m, "tool_calls", None) or []}
    results = {m.tool_call_id for m in messages if m.type == "tool"}
    return calls == results


def test_a_long_conversation_across_topics_stays_within_its_budget(monkeypatch):
    import insurance_context.agent_runtime as rt

    async def wide(ctx: RunContext, topic: str) -> str:
        """A result as wide as a breakdown by provider."""
        return f"{topic}: " + "row " * 5000

    monkeypatch.setattr(rt, "HISTORY_CHARS", 5_000)  # room for about two earlier turns, once cut
    topics = ["pmpm", "mlr", "glp1", "er visits", "modifier 25", "admits", "pharmacy", "members"]
    script = []
    for i, t in enumerate(topics):
        script += [call("wide", {"topic": t}, i=f"c{i}"), AIMessage(f"About {t}.")]
    model = Scripted(script=script, seen=[])
    orch = Orchestrator(None, None, "scripted", "base", tools=[wide], chat_model=lambda http: model)
    for t in topics:
        events = run(orch, f"Now tell me about {t}")
        assert events[-1] == {"type": "result", "data": {"type": "analysis", "answer": f"About {t}."}}
    for sent in model.seen:
        history = sent[1:]
        assert paired(history) and history[0].type == "human"
    last = model.seen[-1][1:]
    assert sum(len(str(m.content)) for m in last) < 5_000 + 25_000  # the budget, plus the current turn whole
    assert last[-1].content.startswith("members: ") and "cut from" not in last[-1].content  # this turn's result whole
    assert any("cut from" in str(m.content) for m in last if m.type == "tool")  # earlier ones cut
    asked = [m.content for m in last if m.type == "human"]
    assert "Now tell me about pmpm" not in asked and "Now tell me about admits" in asked  # oldest out, recent in


def test_a_stopped_turn_and_a_failed_turn_do_not_break_the_next_one():
    from langchain_core.messages import HumanMessage

    model = Scripted(script=[AIMessage("Fine again.")], seen=[])
    orch = Orchestrator(None, None, "scripted", "base", tools=[compute], chat_model=lambda http: model)
    graph = orch._graph(model, [as_tool(compute)], "base")
    config = {"configurable": {"thread_id": "s1"}}
    # a turn stopped mid tool call: the call has no result
    asyncio.run(graph.aupdate_state(config, {"messages": [HumanMessage("Why is MLR up?"),
                                                          call("compute", {"metrics": ["mlr"]})]}, as_node="agent"))
    assert run(orch, "Forget that. GLP-1 members by quarter?")[-1]["data"]["answer"] == "Fine again."
    assert paired(model.seen[-1][1:])
    # a turn the model failed on (nothing scripted: it raises), then a new topic
    assert run(orch, "And ER visits?")[-1]["type"] == "error"
    model.script.append(AIMessage("ER answer."))
    assert run(orch, "ER visits per 1,000 by state?")[-1]["data"]["answer"] == "ER answer."


def test_fitting_leaves_the_kept_history_whole():
    from langchain_core.messages import HumanMessage, ToolMessage

    from insurance_context.agent_runtime import fit

    msgs = [HumanMessage("a"), call("compute", {}, i="x"), ToolMessage("r" * 5000, tool_call_id="x"), AIMessage("A"),
            HumanMessage("b")]
    sent = fit(msgs, budget=10_000, old_result=100)
    assert len(sent[2].content) < 300 and len(msgs[2].content) == 5000
    assert fit(msgs, budget=50, old_result=100) == [msgs[-1]]


def test_a_turn_that_only_calls_tools_is_sent_as_text_databricks_takes():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from insurance_context.agent_runtime import chat_openai
    model = chat_openai(model="m", api_key="k", base_url="http://127.0.0.1:9")
    sent = model._get_request_payload([HumanMessage("q"), AIMessage("", tool_calls=[{"id": "c1", "name": "t", "args": {}}]),
                                       ToolMessage("r", tool_call_id="c1")])["messages"]
    assert [m["content"] for m in sent] == ["q", "", "r"] and sent[1]["tool_calls"]
