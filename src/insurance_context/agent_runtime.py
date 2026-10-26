"""The agent runtime, on LangGraph: a tool-calling loop (model -> tools -> model, until the model
answers) per question, with the conversation kept per session, streamed as NDJSON events.

    orch = Orchestrator(api_key=..., base_url=..., model_name=..., system_prompt=..., tools=kit.tools())
    async for line in orch.stream("Which store sold most?", session_id="s1"):
        ev = json.loads(line)

Any OpenAI-compatible endpoint works (API_BASE_URL). Tools are async functions whose first argument
is a context the runtime does not use, then typed arguments and a docstring: the model sees the
arguments as the tool's JSON schema and the docstring as its description. A runtime tool factory,
`factory(emit, agent_id) -> tool`, is built per question so the tool can push its own events (the
Studio's canvas cards) into the stream.

Events, as the CLI, the Studio API and the web app read them:
    agent_status    {agent_id, status, detail}
    chunk           {text}                     the model's text, as it streams
    agent_tool_call {agent_id, tool, args, status: started | completed, summary}
    result          {type, answer}             the final answer
    error           {message}
plus whatever a runtime tool emits (studio_canvas).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import typing
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Callable

MAX_STEPS = 60  # graph steps per question (each model call and each tool round is one)


class RunContext:
    """The first argument of every tool. The runtime passes None: tools reach their data through the
    toolkit they close over, never through the context."""


@dataclass
class OrchestratorEvent:
    type: str
    data: dict = field(default_factory=dict)

    @staticmethod
    def agent_status(agent_id: str, status: str, detail: str = "") -> "OrchestratorEvent":
        return OrchestratorEvent("agent_status", {"agent_id": agent_id, "status": status, "detail": detail})

    @staticmethod
    def tool_call(agent_id: str, tool: str, args: dict | None = None, status: str = "started",
                  summary: str = "") -> "OrchestratorEvent":
        return OrchestratorEvent("agent_tool_call", {"agent_id": agent_id, "tool": tool, "args": args or {},
                                                     "status": status, "summary": summary})

    def line(self) -> str:
        return json.dumps({"type": self.type, "data": self.data}, default=str)


def as_tool(fn: Callable):
    """A toolkit function as a LangChain tool: its arguments after the context become the schema."""
    from langchain_core.tools import StructuredTool
    from pydantic import create_model

    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 - an unresolvable annotation is read as Any
        hints = {}
    params = list(inspect.signature(fn).parameters.values())
    if params and (params[0].name == "ctx" or hints.get(params[0].name) is RunContext):
        params = params[1:]
    fields = {p.name: (hints.get(p.name, Any), ... if p.default is inspect.Parameter.empty else p.default)
              for p in params}
    schema = create_model(f"{fn.__name__}_args", **fields)

    async def run(**kwargs):
        out = fn(None, **kwargs)
        return await out if inspect.isawaitable(out) else out

    return StructuredTool(name=fn.__name__, description=inspect.cleandoc(fn.__doc__ or fn.__name__),
                          args_schema=schema, coroutine=run, handle_tool_error=True)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content or [])


def skills_prompt(skills: list[dict]) -> str:
    """Skills as guidance in the system prompt: which way of working fits which kind of request."""
    lines = ["", "", "Ways of working, by kind of request (use the one that fits):"]
    for s in skills:
        lines.append(f"- {s['name']}: {s.get('description', '')} {s.get('system_prompt', '')} "
                     f"Tools: {', '.join(s.get('tools', []))}.")
    return "\n".join(lines)


class Orchestrator:
    def __init__(self, api_key: str | None, base_url: str | None, model_name: str, system_prompt: str,
                 tools: list[Callable] | None = None, tool_registry: dict | None = None,
                 builtin_skills: list[dict] | None = None, data_dir: str | Path | None = None,
                 storage: str | None = None, agent_name: str = "Agent",
                 runtime_tool_factories: list[Callable] | None = None, max_steps: int = MAX_STEPS,
                 chat_model: Callable | None = None):
        from langgraph.checkpoint.memory import InMemorySaver

        # tool_registry, data_dir and storage are accepted for the callers' sake: tools come from
        # `tools`, and sessions live in memory for the life of the process.
        self._api_key, self._base_url, self._model_name = api_key, base_url, model_name
        self._system_prompt = system_prompt
        self._skills = skills_prompt(builtin_skills) if builtin_skills else ""
        self._tools = [as_tool(f) for f in tools or []]
        self._factories = runtime_tool_factories or []
        self._memory = InMemorySaver()
        self.agent_name, self.max_steps = agent_name, max_steps
        self._chat_model = chat_model  # http_client -> a LangChain chat model; default ChatOpenAI

    def _graph(self, model, tools: list, system_prompt: str):
        from langchain_core.messages import SystemMessage
        from langgraph.graph import START, MessagesState, StateGraph
        from langgraph.prebuilt import ToolNode, tools_condition

        bound = model.bind_tools(tools) if tools else model

        async def agent(state: MessagesState):
            return {"messages": [await bound.ainvoke([SystemMessage(system_prompt), *state["messages"]])]}

        g = StateGraph(MessagesState)
        g.add_node("agent", agent)
        g.add_edge(START, "agent")
        if tools:
            g.add_node("tools", ToolNode(tools))
            g.add_conditional_edges("agent", tools_condition)
            g.add_edge("tools", "agent")
        return g.compile(checkpointer=self._memory)

    @staticmethod
    async def _close_open_calls(graph, config):
        """A turn stopped mid tool call (the person cancelled, or an error) leaves calls with no result,
        which the model's API refuses on the next turn: answer them as not run."""
        from langchain_core.messages import ToolMessage
        state = await graph.aget_state(config)
        msgs = (state.values or {}).get("messages", [])
        if msgs and getattr(msgs[-1], "tool_calls", None):
            await graph.aupdate_state(config, {"messages": [
                ToolMessage("Not run: the previous turn stopped.", tool_call_id=c["id"]) for c in msgs[-1].tool_calls
            ]}, as_node="tools")

    async def stream(self, message: str, session_id: str = "default",
                     system_prompt: str | None = None) -> AsyncIterator[str]:
        """Answer one message in a session; yields NDJSON lines. `system_prompt` overrides the one given
        at construction for this turn (who is asking, what they see)."""
        import httpx
        from langchain_core.messages import HumanMessage
        from langchain_openai import ChatOpenAI

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        agent_id = uuid.uuid4().hex[:12]

        async def emit(ev: OrchestratorEvent):
            await queue.put(ev.line())

        async def run():
            # one HTTP client per question: the CLI runs each question in its own event loop
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=30)) as http:
                try:
                    await emit(OrchestratorEvent.agent_status(agent_id, "active", "Analyzing request..."))
                    model = self._chat_model(http) if self._chat_model else ChatOpenAI(
                        model=self._model_name, api_key=self._api_key, base_url=self._base_url,
                        http_async_client=http, max_retries=2)
                    tools = self._tools + [as_tool(f(emit, agent_id)) for f in self._factories]
                    graph = self._graph(model, tools, (system_prompt or self._system_prompt) + self._skills)
                    config = {"configurable": {"thread_id": session_id}, "recursion_limit": self.max_steps}
                    await self._close_open_calls(graph, config)
                    async for ev in graph.astream_events({"messages": [HumanMessage(message)]}, config,
                                                         version="v2"):
                        kind = ev["event"]
                        if kind == "on_chat_model_stream":
                            if text := _text(ev["data"]["chunk"].content):
                                await emit(OrchestratorEvent("chunk", {"text": text}))
                        elif kind == "on_tool_start":
                            await emit(OrchestratorEvent.tool_call(agent_id, ev["name"], ev["data"].get("input")))
                        elif kind == "on_tool_end":
                            out = ev["data"].get("output")
                            summary = _text(getattr(out, "content", out))[:4000]
                            await emit(OrchestratorEvent.tool_call(agent_id, ev["name"], status="completed",
                                                                   summary=summary))
                    state = await graph.aget_state(config)
                    answer = _text(state.values["messages"][-1].content)
                    await emit(OrchestratorEvent("result", {"type": "analysis", "answer": answer}))
                except Exception as exc:  # noqa: BLE001 - the consumer shows the reason
                    await emit(OrchestratorEvent("error", {"message": f"{type(exc).__name__}: {exc}"}))
                finally:
                    await queue.put(None)

        task = asyncio.create_task(run())
        try:
            while (line := await queue.get()) is not None:
                yield line + "\n"
        finally:
            if not task.done():
                task.cancel()
