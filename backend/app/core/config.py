"""Central, env-driven configuration. No secrets in code, ever.

- .env is resolved from the PROJECT ROOT (absolute), not the process cwd.
- Optional providers are individually "resolved"; a missing one is reported as
  unavailable, never silently swapped for a mock in a real benchmark.
- MOCK_MODE exists only for offline tests/CI and is labeled as mock everywhere.

Multi-model support (assignment: "at least 3 models for STT/TTS"): selectable
models are declared statically per provider with a credential-gated availability
flag — no network probing at listing time, so choices never cost money or leak
keys. Every model id below was executed for real against its provider API during
the multi-model validation pass (see MULTI_MODEL_TEST_REPORT.md).
"""
from __future__ import annotations

import re
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py is at <repo>/backend/app/core/config.py → parents[3] = repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = PROJECT_ROOT / "data"
ENV_FILE = PROJECT_ROOT / ".env"

# -----------------------------------------------------------------------------
# Selectable model catalogs. Provider keys gate `available`; nothing aspirational.
# - STT: Deepgram listen API (nova-2, nova-3, hosted whisper-large) + ElevenLabs
#   Scribe v1 (a genuinely different provider endpoint using ELEVENLABS_API_KEY,
#   verified 200 with a real transcript). whisper-large rejects
#   smart_format/punctuate params and needs a long timeout (cold starts).
# - TTS: ElevenLabs (two models) + Deepgram Aura-2 (a genuinely different
#   provider endpoint). OpenAI TTS exists in the adapter layer but is
#   NOT_CONFIGURED (no real key) and is therefore listed as unavailable —
#   never faked.
# -----------------------------------------------------------------------------
STT_MODELS = {
    "deepgram": [
        {"id": "nova-2", "label": "Deepgram Nova-2", "timeout_s": 45},
        {"id": "nova-3", "label": "Deepgram Nova-3", "timeout_s": 45},
        {"id": "whisper-large",
         "label": "Deepgram-hosted Whisper-large (third-party model, Deepgram API)",
         "timeout_s": 180},  # cold starts measured 48–130 s under provider load
    ],
    "elevenlabs": [
        {"id": "scribe_v1", "label": "ElevenLabs Scribe v1 (separate provider API)", "timeout_s": 60},
    ],
    "mock": [
        {"id": "mock-1", "label": "Mock STT (offline, labeled)", "timeout_s": 10},
    ],
}

TTS_MODELS = {
    "elevenlabs": [
        {"id": "eleven_turbo_v2_5", "label": "ElevenLabs Turbo v2.5", "timeout_s": 60},
        {"id": "eleven_multilingual_v2", "label": "ElevenLabs Multilingual v2", "timeout_s": 90},
        {"id": "eleven_flash_v2_5", "label": "ElevenLabs Flash v2.5 (lowest latency)", "timeout_s": 60},
    ],
    "deepgram": [
        {"id": "aura-2-thalia-en", "label": "Deepgram Aura-2 Thalia (EN)", "timeout_s": 30},
        {"id": "aura-2-andromeda-en", "label": "Deepgram Aura-2 Andromeda (EN)", "timeout_s": 30},
    ],
    "openai": [
        {"id": "tts-1", "label": "OpenAI TTS-1", "timeout_s": 60},
    ],
    "mock": [
        {"id": "mock-voice", "label": "Mock TTS (offline, labeled)", "timeout_s": 10},
    ],
}

# Model-spec format: "provider" | "provider:model" | "provider:model:voice"
_SPEC_RE = re.compile(r"^[a-z0-9_-]+(:[A-Za-z0-9._-]+){0,2}$")


def parse_model_spec(spec: str) -> tuple[str, str | None, str | None]:
    """Parse 'provider', 'provider:model' or 'provider:model:voice'.

    Returns (provider, model, voice). Raises ValueError on malformed specs —
    the caller (factory) converts that into ProviderUnavailableError with a
    clear message. Unknown models are rejected by the factories' allowlists,
    NOT silently mapped to a default.
    """
    if not spec or not _SPEC_RE.match(spec):
        raise ValueError(f"malformed model spec: {spec!r} (expected provider[:model[:voice]])")
    parts = spec.split(":")
    provider = parts[0]
    model = parts[1] if len(parts) >= 2 else None
    voice = parts[2] if len(parts) >= 3 else None
    return provider, model, voice


def _stt_model_meta(provider: str, model: str | None) -> dict | None:
    for m in STT_MODELS.get(provider, []):
        if m["id"] == (model or ""):
            return m
    return None


def _tts_model_meta(provider: str, model: str | None) -> dict | None:
    for m in TTS_MODELS.get(provider, []):
        if m["id"] == (model or ""):
            return m
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore"
    )

    # -- LLM via OpenRouter -----------------------------------------------------
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = ""

    # -- STT ---------------------------------------------------------------------
    deepgram_api_key: str = ""
    whisper_model: str = "base"          # local fallback (optional)
    # Second Deepgram account: free-tier concurrency is per-account, so the
    # KeyRing rotates keys across requests (observable via `key_account`).
    deepgram2_api_key: str = ""

    # -- TTS ---------------------------------------------------------------------
    elevenlabs_api_key: str = ""
    elevenlabs_model: str = "eleven_turbo_v2_5"
    elevenlabs_voice_id: str = ""
    # Second ElevenLabs account (rotation + separate Scribe STT quota).
    elevenlabs2_api_key: str = ""
    # Default TTS = Aura-2 (verified working); ElevenLabs remains selectable — its
    # account quota is currently exhausted, so runs with it fail honestly at tts.
    default_tts_spec: str = "deepgram:aura-2-thalia-en"
    openai_api_key: str = ""

    # -- Observability -----------------------------------------------------------
    langsmith_api_key: str = ""
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "voice-agent-benchmark"
    langsmith_send_transcripts: bool = False

    # -- Application -------------------------------------------------------------
    environment: str = "development"
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    frontend_url: str = ""
    secret_key: str = ""

    # -- Frontend build-time (documented; not secrets) ----------------------------
    vite_api_base_url: str = ""
    vite_ws_url: str = ""

    # -- Database -----------------------------------------------------------------
    database_url: str = ""  # empty = default sqlite file

    # -- Upload safety / concurrency caps ------------------------------------------
    max_upload_mb: int = 25
    max_concurrency: int = 10
    concurrency_stagger_ms: int = 250

    # -- Mock mode: deterministic local providers for tests/CI only -----------------
    mock_mode: bool = False

    # -- Legacy optional providers (kept as extra adapters, not required) -----------
    tavily_api_key: str = ""
    cartesia_api_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [o.strip() for o in self.frontend_url.split(",") if o.strip()]
        # dev defaults unless explicitly configured
        return origins or [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


def resolve_llm_provider() -> dict:
    """LLM adapter availability. OpenRouter is the configured LLM path;
    OpenAI direct is available if only that key exists."""
    s = get_settings()
    if s.openrouter_api_key:
        model = s.openrouter_model or "openai/gpt-4o-mini"
        return {
            "provider": "openrouter",
            "id": f"openrouter:{model}",
            "model": model,
            "configured": True,
        }
    if s.openai_api_key and s.openai_api_key.startswith("sk-") and "uvwx" not in s.openai_api_key:
        # NOTE: a known placeholder key pattern is treated as not-configured.
        return {"provider": "openai", "id": "openai:gpt-4o-mini", "model": "gpt-4o-mini", "configured": True}
    return {"provider": None, "id": None, "model": None, "configured": False}


def resolve_stt_provider() -> dict:
    s = get_settings()
    if s.deepgram_api_key:
        return {"provider": "deepgram", "id": "deepgram", "model": "nova-2", "configured": True}
    if s.elevenlabs_api_key:
        return {"provider": "elevenlabs", "id": "elevenlabs", "model": "scribe_v1", "configured": True}
    # Local whisper is NOT bundled: mark unavailable rather than pretend.
    return {"provider": None, "id": None, "model": None, "configured": False}


def resolve_tts_provider() -> dict:
    s = get_settings()
    if s.elevenlabs_api_key:
        return {
            "provider": "elevenlabs",
            "id": "elevenlabs",
            "model": s.elevenlabs_model or "eleven_turbo_v2_5",
            "configured": True,
        }
    if s.cartesia_api_key:
        return {"provider": "cartesia", "id": "cartesia", "model": "sonic-english", "configured": True}
    if s.openai_api_key and "uvwx" not in s.openai_api_key:
        return {"provider": "openai", "id": "openai", "model": "tts-1", "configured": True}
    return {"provider": None, "id": None, "model": None, "configured": False}


def provider_availability() -> dict:
    """Non-secret capability map for the UI: every selectable provider+model with
    a credential-gated availability flag. No network calls, no secrets."""
    s = get_settings()
    llm = resolve_llm_provider()
    stt = resolve_stt_provider()
    tts = resolve_tts_provider()
    dg = bool(s.deepgram_api_key or s.deepgram2_api_key)
    el = bool(s.elevenlabs_api_key or s.elevenlabs2_api_key)
    oa = bool(s.openai_api_key) and "uvwx" not in s.openai_api_key

    stt_choices = []
    for prov, models in STT_MODELS.items():
        for m in models:
            available = {"deepgram": dg, "elevenlabs": el, "mock": True}.get(prov, False)
            stt_choices.append({"id": f"{prov}:{m['id']}", "provider": prov, "model": m["id"],
                                "label": m["label"], "available": available})

    tts_choices = []
    for prov, models in TTS_MODELS.items():
        for m in models:
            available = {"elevenlabs": el, "deepgram": dg, "openai": oa, "mock": True}.get(prov, False)
            tts_choices.append({"id": f"{prov}:{m['id']}", "provider": prov, "model": m["id"],
                                "label": m["label"], "available": available})

    return {
        "environment": s.environment,
        "llm": {
            "choices": [
                {"id": f"openrouter:{s.openrouter_model or 'openai/gpt-4o-mini'}",
                 "label": f"OpenRouter · {s.openrouter_model or 'openai/gpt-4o-mini'}",
                 "available": bool(s.openrouter_api_key)},
                {"id": "mock:echo", "label": "Mock LLM (deterministic, offline)",
                 "available": True},
            ],
            "configured": llm["configured"],
            "configured_id": llm["id"],
        },
        "stt": {
            "choices": stt_choices,
            "configured": stt["configured"],
            "configured_id": stt["id"],
        },
        "tts": {
            "choices": tts_choices,
            "configured": tts["configured"],
            "configured_id": tts["id"],
        },
        "tool_choices": [
            {"id": "auto", "label": "Auto: weather + web search (agent decides)", "available": True},
            {"id": "weather", "label": "Weather (Open-Meteo, keyless)", "available": True},
            {"id": "mock_weather", "label": "Weather (mock, deterministic)", "available": True},
            {"id": "search", "label": "Web search (Tavily)", "available": bool(s.tavily_api_key)},
            {"id": "mock_search", "label": "Web search (mock, deterministic)", "available": True},
            {"id": "none", "label": "No tools", "available": True},
        ],
        "langsmith": {
            "enabled": bool(s.langsmith_tracing and s.langsmith_api_key),
            "project": s.langsmith_project,
        },
        "mock_mode": s.mock_mode,
        "max_concurrency": s.max_concurrency,
        "max_upload_mb": s.max_upload_mb,
    }


DATA_DIR.mkdir(parents=True, exist_ok=True)
TEST_CASES_DIR = DATA_DIR / "test_cases"
UPLOADS_DIR = DATA_DIR / "uploads"
RESULTS_DIR = PROJECT_ROOT / "docs" / "benchmark-results"
TEST_CASES_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
