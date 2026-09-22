"""LangGraph agent brain (§8): decides direct answer vs exactly-one tool call.

Why LangGraph: it *decides* tool usage (no-tool queries answer directly; weather/
search queries trigger exactly one tool call) and it is natively traced in
LangSmith, so graph execution is visible. It is NOT used for STT/TTS where it
would only add latency. Stateless per run — each benchmark run is an independent
conversation.

LLM: OpenRouter (OpenAI-compatible) via ChatOpenAI with a custom base_url, so any
OpenRouter model id works. `mock:echo` selects the deterministic offline model.

TTFT: the first chat-model message chunk timestamp is the LLM time-to-first-token.
The pipeline records it; nothing is fabricated.
"""
from __future__ import annotations

import time
from typing import Any, AsyncIterator, Awaitable, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool as lc_tool
from langgraph.prebuilt import create_react_agent

FirstTokenCallback = Callable[[float], Awaitable[None]]


def _openrouter_model(model_spec: str):
    """Build a ChatOpenAI pointed at OpenRouter."""
    from langchain_openai import ChatOpenAI

    from ..core.config import get_settings

    s = get_settings()
    model = model_spec.split(":", 1)[1] if ":" in model_spec else model_spec
    return ChatOpenAI(
        model=model,
        api_key=s.openrouter_api_key,
        base_url=s.openrouter_base_url,
        temperature=0.2,
        streaming=True,
        # Voice answers are one short sentence — a bounded max_tokens avoids the
        # OpenRouter 402 "more credits / fewer max_tokens" failure where models
        # default to a 64k ceiling the account cannot afford.
        max_tokens=200,
    )


def build_graph(lc_tools: list, system_prompt: str, model_spec: str):
    """Compile the ReAct agent. model_spec: "openrouter:<model>" | "mock:echo"."""
    if model_spec == "mock:echo":
        llm = MockChatModel()
    elif model_spec.startswith("openrouter:"):
        llm = _openrouter_model(model_spec)
    else:
        from langchain.chat_models import init_chat_model

        llm = init_chat_model(model_spec, temperature=0.2)
    return create_react_agent(llm, tools=lc_tools, prompt=system_prompt)


async def astream_agent(
    graph,
    transcript: str,
    on_first_token: FirstTokenCallback | None = None,
) -> AsyncIterator[dict]:
    """Stream the graph; yields {"type": "token"|"done", ...} events.
    Fires `on_first_token(ms_since_start)` on the first model message chunk.
    Token usage is captured from provider-reported response metadata only —
    when the provider doesn't report usage, it stays None (never estimated)."""
    started = time.perf_counter()
    first_token_fired = False
    all_messages: list[BaseMessage] = []
    usage: dict | None = None

    async for mode, chunk in graph.astream(
        {"messages": [HumanMessage(content=transcript)]},
        stream_mode=["updates", "messages"],
        config={"tags": ["voice-agent"]},
    ):
        if mode == "messages":
            msg, _meta = chunk
            if isinstance(msg, AIMessage) and not first_token_fired:
                first_token_fired = True
                if on_first_token:
                    await on_first_token((time.perf_counter() - started) * 1000)
            if isinstance(msg, AIMessage) and _text_of(msg):
                yield {"type": "token", "text": _text_of(msg)}
        elif mode == "updates":
            for node_update in (chunk or {}).values():
                msgs = (node_update or {}).get("messages") or []
                all_messages.extend(msgs)
                # Provider-reported token usage (LangChain usage_metadata on the
                # final AI message). Stays None when the provider omits it.
                for m in reversed(msgs):
                    if isinstance(m, AIMessage):
                        u = getattr(m, "usage_metadata", None) or {}
                        if u.get("total_tokens") or u.get("input_tokens") or u.get("output_tokens"):
                            usage = u
                            break

    final_ai = [m for m in all_messages if isinstance(m, AIMessage) and not m.tool_calls]
    text = _text_of(final_ai[-1]) if final_ai else ""
    yield {
        "type": "done", "text": text, "state": {"messages": all_messages},
        # normalized to the pipeline's schema; None when not provider-reported
        "usage": ({
            "prompt_tokens": usage.get("input_tokens"),
            "completion_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        } if usage else None),
    }


def _text_of(msg: BaseMessage) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(part.get("text", "") for part in c if isinstance(part, dict))
    return str(c)


def extract_tool_calls(state: dict | None) -> list[dict[str, Any]]:
    """Pull structured tool-call records out of the graph's message state."""
    calls: list[dict[str, Any]] = []
    if not state:
        return calls
    for m in state.get("messages") or []:
        for tc in getattr(m, "tool_calls", None) or []:
            calls.append({"name": tc.get("name"), "args": tc.get("args"), "id": tc.get("id")})
    return calls


# ---- LangChain tool adapters with real latency measurement -------------------

ToolEmit = Callable[..., Awaitable[None]]


def _make_weather_tool(provider, timings_sink: list, emit: ToolEmit | None):
    @lc_tool
    async def lookup_weather(city: str) -> dict:
        """Get the current weather for a single city. Call once per city."""
        if emit:
            await emit("tool", f"Tool started: {provider.name}", "start", tool=provider.name, args={"city": city})
        t0 = time.perf_counter()
        try:
            result = await provider.run(city=city)
            ok = True
        except Exception as e:  # tool failure is data, not a silent swallow
            result = {"city": city, "error": f"tool_error: {type(e).__name__}"}
            ok = False
        latency = round((time.perf_counter() - t0) * 1000, 3)
        timings_sink.append({"name": provider.name, "latency_ms": latency, "ok": ok})
        if emit:
            await emit("tool", f"Tool completed: {provider.name}", "end", tool=provider.name, latency_ms=latency, ok=ok)
        return result

    return lookup_weather


def _make_search_tool(provider, timings_sink: list, emit: ToolEmit | None):
    @lc_tool
    async def web_search(query: str) -> dict:
        """Search the web for current information about a topic. Call once."""
        if emit:
            await emit("tool", f"Tool started: {provider.name}", "start", tool=provider.name, args={"query": query})
        t0 = time.perf_counter()
        try:
            result = await provider.run(query=query)
            ok = True
        except Exception as e:
            result = {"query": query, "error": f"tool_error: {type(e).__name__}"}
            ok = False
        latency = round((time.perf_counter() - t0) * 1000, 3)
        timings_sink.append({"name": provider.name, "latency_ms": latency, "ok": ok})
        if emit:
            await emit("tool", f"Tool completed: {provider.name}", "end", tool=provider.name, latency_ms=latency, ok=ok)
        return result

    return web_search


def to_langchain_tools(providers: list, timings_sink: list, emit: ToolEmit | None = None) -> list:
    """Wrap ToolProviders into LangChain tools with instrumented latency."""
    out = []
    for p in providers:
        if p.name.startswith("lookup_weather"):
            out.append(_make_weather_tool(p, timings_sink, emit))
        elif p.name == "web_search":
            out.append(_make_search_tool(p, timings_sink, emit))
        else:
            raise ValueError(f"no langchain adapter for tool: {p.name}")
    return out


# ---- LLM choices exposed to the dashboard (availability-aware) ----------------

def llm_choices() -> list[dict[str, str]]:
    from ..core.config import get_settings

    s = get_settings()
    default_model = s.openrouter_model or "openai/gpt-4o-mini"
    return [
        {"id": f"openrouter:{default_model}", "spec": f"openrouter:{default_model}",
         "label": f"OpenRouter · {default_model}", "available": bool(s.openrouter_api_key)},
        {"id": "mock:echo", "spec": "mock:echo", "label": "Mock LLM (deterministic, offline)",
         "available": True},
    ]


def resolve_llm(spec_or_label: str) -> str:
    if spec_or_label == "openrouter":
        # Bare provider name -> default model (was a 500 via init_chat_model before).
        from ..core.config import get_settings

        s = get_settings()
        return f"openrouter:{s.openrouter_model or 'openai/gpt-4o-mini'}"
    for c in llm_choices():
        if c["id"] == spec_or_label:
            return c["spec"]
    return spec_or_label


# ---- Deterministic mock LLM (MOCK_MODE only) ----------------------------------

_WEATHER_WORDS = ("weather", "temperature", "forecast", "rain", "wind", "humidity", "sunny")
_SEARCH_WORDS = ("search", "news", "latest", "look up", "find information")


class MockChatModel(BaseChatModel):
    """Deterministic LLM for MOCK_MODE (`mock:echo`).

    Decides tool vs direct answer by keyword over the human turn only. Real bound
    tool names decide which tool is called, so mock/real weather are swappable.
    """

    bound_tools: Any = None

    @property
    def _llm_type(self) -> str:
        return "mock"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ANN003
        self.bound_tools = list(tools or [])
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:  # noqa: ANN001, ANN003
        names = [t.name for t in (self.bound_tools or [])]
        used = {tc.get("name") for m in messages for tc in (getattr(m, "tool_calls", None) or [])}
        has_tool_result = any(isinstance(m, ToolMessage) for m in messages)
        human_text = self._human_text(messages)

        if has_tool_result:
            reply = self._answer_from_tool(messages)
        elif any(w in human_text for w in _WEATHER_WORDS) and "lookup_weather" in names:
            reply = AIMessage(content="", tool_calls=[{
                "name": "lookup_weather", "args": {"city": _city_of(human_text)}, "id": "mock_call_weather",
            }])
        elif any(w in human_text for w in _SEARCH_WORDS) and "web_search" in names:
            reply = AIMessage(content="", tool_calls=[{
                "name": "web_search", "args": {"query": human_text[-80:]}, "id": "mock_call_search",
            }])
        else:
            reply = AIMessage(content="This is the deterministic mock answer to your query.")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    @staticmethod
    def _human_text(messages) -> str:
        """Intent text = human turns only. The system prompt mentions tools and
        would otherwise poison keyword intent detection."""
        return " ".join(
            str(getattr(m, "content", "")) for m in messages if isinstance(m, HumanMessage)
        ).lower()

    @staticmethod
    def _answer_from_tool(messages) -> AIMessage:
        tool_msg = next(m for m in reversed(messages) if isinstance(m, ToolMessage))
        content = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
        return AIMessage(content=f"Here's what I found: {content[:180]}")


def _city_of(text: str) -> str:
    for city in ("bangalore", "tokyo", "rome", "berlin", "new york", "london", "paris"):
        if city in text:
            return city
    return "tokyo"
