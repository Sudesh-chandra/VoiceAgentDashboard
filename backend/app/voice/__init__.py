"""Voice package: provider factories (replaceable per §11/§16/§17).

Model specs: "provider" | "provider:model" | "provider:model:voice"
  STT: deepgram:nova-2 · deepgram:nova-3 · deepgram:whisper-large ·
       elevenlabs:scribe_v1 · mock:mock-1
  TTS: elevenlabs:eleven_turbo_v2_5 · elevenlabs:eleven_multilingual_v2 ·
       elevenlabs:eleven_flash_v2_5 · deepgram:aura-2-thalia-en ·
       deepgram:aura-2-andromeda-en · mock:mock-voice
  LLM: openrouter:<model> · mock:echo (unchanged)

Unknown models raise ProviderUnavailableError (409 at the API layer) — never a
silent default. Mocks are used ONLY in MOCK_MODE and are always labeled mock.
"""
from __future__ import annotations

from ..core.config import (
    get_settings, parse_model_spec, STT_MODELS, TTS_MODELS, resolve_llm_provider,
)
from .agent import (
    MockChatModel,
    astream_agent,
    build_graph,
    extract_tool_calls,
    llm_choices,
    resolve_llm,
    to_langchain_tools,
)
from .providers.stt import DeepgramSTT, ElevenLabsScribeSTT, MockSTT
from .providers.tts import ElevenLabsTTS, DeepgramAuraTTS, MockTTS
from .providers.weather import WeatherTool
from .providers.mock_weather import MockWeatherTool
from .providers.search import MockSearchTool, TavilySearchTool


class ProviderUnavailableError(Exception):
    """Raised when a pipeline requests a provider/model whose credentials are
    missing or which does not exist — never silently swapped or defaulted."""


def build_stt(choice: str, mock_transcript: str | None = None):
    settings = get_settings()
    try:
        provider, model, _voice = parse_model_spec(choice)
    except ValueError as e:
        raise ProviderUnavailableError(str(e)) from e

    if provider == "mock":
        if not settings.mock_mode:
            raise ProviderUnavailableError(
                "mock STT requested outside MOCK_MODE — real benchmarks must use configured providers"
            )
        return MockSTT(transcript=mock_transcript)
    if provider == "deepgram":
        if not (settings.deepgram_api_key or settings.deepgram2_api_key):
            raise ProviderUnavailableError(
                "Deepgram STT is not configured: missing DEEPGRAM_API_KEY"
            )
        known = {m["id"] for m in STT_MODELS["deepgram"]}
        model_id = model or "nova-2"
        if model_id not in known:
            raise ProviderUnavailableError(
                f"unknown Deepgram STT model: {model_id!r} (known: {sorted(known)})"
            )
        return DeepgramSTT(model_id)
    if provider == "elevenlabs":
        if not (settings.elevenlabs_api_key or settings.elevenlabs2_api_key):
            raise ProviderUnavailableError(
                "ElevenLabs STT is not configured: missing ELEVENLABS_API_KEY"
            )
        known = {m["id"] for m in STT_MODELS["elevenlabs"]}
        model_id = model or "scribe_v1"
        if model_id not in known:
            raise ProviderUnavailableError(
                f"unknown ElevenLabs STT model: {model_id!r} (known: {sorted(known)})"
            )
        return ElevenLabsScribeSTT(model_id)
    raise ProviderUnavailableError(f"unknown or unavailable STT provider: {choice!r}")


def build_tts(choice: str):
    settings = get_settings()
    try:
        provider, model, voice = parse_model_spec(choice)
    except ValueError as e:
        raise ProviderUnavailableError(str(e)) from e

    if provider == "mock":
        if not settings.mock_mode:
            raise ProviderUnavailableError(
                "mock TTS requested outside MOCK_MODE — real benchmarks must use configured providers"
            )
        return MockTTS()
    if provider == "elevenlabs":
        if not (settings.elevenlabs_api_key or settings.elevenlabs2_api_key):
            raise ProviderUnavailableError("ElevenLabs TTS is not configured: missing ELEVENLABS_API_KEY")
        known = {m["id"] for m in TTS_MODELS["elevenlabs"]}
        model_id = model or "eleven_turbo_v2_5"
        if model_id not in known:
            raise ProviderUnavailableError(
                f"unknown ElevenLabs TTS model: {model_id!r} (known: {sorted(known)})"
            )
        return ElevenLabsTTS(model=model_id, voice_id=voice)
    if provider == "deepgram":
        if not (settings.deepgram_api_key or settings.deepgram2_api_key):
            raise ProviderUnavailableError("Deepgram TTS is not configured: missing DEEPGRAM_API_KEY")
        known = {m["id"] for m in TTS_MODELS["deepgram"]}
        model_id = model or "aura-2-thalia-en"
        if model_id not in known:
            raise ProviderUnavailableError(
                f"unknown Deepgram TTS model: {model_id!r} (known: {sorted(known)})"
            )
        return DeepgramAuraTTS(model_id)
    raise ProviderUnavailableError(f"unknown or unavailable TTS provider: {choice!r}")


def build_tool_providers(choice: str) -> list:
    """Build ToolProviders (not yet langchain-wrapped)."""
    if choice == "none":
        return []
    if choice == "auto":
        # Weather (keyless) + search (Tavily when key present, else the labeled
        # deterministic mock — search is a tool, not a measurement target).
        s = get_settings()
        search = TavilySearchTool() if s.tavily_api_key else MockSearchTool()
        return [WeatherTool(), search]
    if choice in ("weather", "mock_weather"):
        return [WeatherTool()] if choice == "weather" else [MockWeatherTool()]
    if choice in ("search", "mock_search"):
        return [TavilySearchTool()] if choice == "search" and get_settings().tavily_api_key else [MockSearchTool()]
    raise ValueError(f"unknown tool config: {choice}")


def build_agent_graph(llm_spec: str, tool_choice: str, emit=None):
    """Build the instrumented LangGraph agent. Returns (graph, tool_timings_sink)."""
    providers = build_tool_providers(tool_choice)
    sink: list = []
    lc_tools = to_langchain_tools(providers, sink, emit) if providers else []
    system_prompt = (
        "You are a concise voice assistant. Answer in one short spoken sentence. "
        "Use the lookup_weather tool when the user asks about weather and web_search "
        "when the user asks you to search; otherwise answer directly."
    )
    graph = build_graph(lc_tools, system_prompt, resolve_llm(llm_spec))
    return graph, sink


def default_llm_choice() -> str:
    llm = resolve_llm_provider()
    return llm["id"] if llm["configured"] else "mock:echo"


__all__ = [
    "build_stt", "build_tts", "build_tool_providers", "build_agent_graph",
    "astream_agent", "extract_tool_calls", "resolve_llm", "llm_choices",
    "MockChatModel", "to_langchain_tools", "ProviderUnavailableError",
    "default_llm_choice",
]
