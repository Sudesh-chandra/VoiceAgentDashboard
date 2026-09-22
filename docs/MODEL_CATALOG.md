# Model Catalog

Every entry reflects what the application can actually execute today.
`NOT_CONFIGURED` / `NOT_IMPLEMENTED` entries are listed honestly, not hidden.
Source of truth: `backend/app/core/config.py` (`STT_MODELS` / `TTS_MODELS`) and
the factories in `backend/app/voice/`. Statuses below are from real executions
recorded in `docs/MULTI_MODEL_TEST_REPORT.md` and `docs/MODEL_EXPERIMENT_REPORT.md`
(verification date 2026-09-22; quota-bound entries reset with their provider
accounts — code paths are unchanged and were verified working before drain).

## STT

| Provider | Model spec | Config | Credential | Local/API | Streaming (partials) | Status | Notes |
|---|---|---|---|---|---|---|---|
| Deepgram | `deepgram:nova-2` | allowlisted | `DEEPGRAM_API_KEY` | API (REST batch) | No — batch API; `stt_first_result_ms` null | **AVAILABLE** (verified live) | conf ≈ 0.996–0.998 all six recordings; 2.6–3.9 s |
| Deepgram | `deepgram:nova-3` | allowlisted | `DEEPGRAM_API_KEY` | API (REST batch) | No | **AVAILABLE** (verified live) | consistently fastest (2.7–3.5 s), highest clean-audio confidence |
| Deepgram | `deepgram:whisper-large` | allowlisted | `DEEPGRAM_API_KEY` | API (Deepgram-hosted OpenAI Whisper) | No | **AVAILABLE** (verified live; slow) | third-party model on Deepgram API; cold starts 48–130 s; 180 s timeout |
| ElevenLabs | `elevenlabs:scribe_v1` | allowlisted | `ELEVENLABS_API_KEY` | API (separate vendor endpoint) | No | **AVAILABLE** (verified live; quota-gated) | genuinely different provider API; verified 200 with correct transcript (2,013 ms) before account quota drained |
| Mock STT | `mock:mock-1` | `MOCK_MODE=true` | none | Local | two-phase real signal | **AVAILABLE (mock only)** | deterministic; labeled `mock` in every result |
| Google Cloud STT | — | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_APPLICATION_CREDENTIALS` | required | API | — | **NOT_IMPLEMENTED** | env slot only; no adapter executes |
| OpenAI Whisper API | — | `OPENAI_API_KEY` | required | API | — | **NOT_CONFIGURED** | adapter slot exists; no real key configured |

## LLM (agent)

| Provider | Model | Config | Credential | Streaming (TTFT) | Status | Notes |
|---|---|---|---|---|---|---|
| OpenRouter | `openrouter:openai/gpt-4.1-mini` | `OPENROUTER_API_KEY` | required | Yes — real TTFT measured | **AVAILABLE** (verified live) | tool-calling correct (0/0/1/1/0/0); token usage captured per run |
| OpenRouter | `openrouter:openai/gpt-4o-mini` | same | required | Yes | **AVAILABLE** (verified live) | tool-calling correct in EXP-2026-00003 |
| OpenRouter | `openrouter:google/gemini-3.8-flash` | same | required | Yes | **AVAILABLE** (verified live) | **over-calls tools** on one-tool cases (2–3 calls) — genuine cross-model finding |
| OpenRouter | other model ids | same | required | Yes | **AVAILABLE in principle** | untested ids execute as-is; failures surface honestly |
| OpenAI direct | — | `OPENAI_API_KEY` | required | — | **NOT_CONFIGURED** | adapter exists; reports unavailable without a real key |
| Mock LLM | `mock:echo` | `MOCK_MODE=true` | none | Local | Yes | deterministic ReAct mock |

## TTS

| Provider | Model spec | Config | Credential | First-audio semantics | Status | Notes |
|---|---|---|---|---|---|---|
| Deepgram | `deepgram:aura-2-thalia-en` | allowlisted | `DEEPGRAM_API_KEY` | **BUFFERED_RESPONSE** (single payload; `tts_first_audio ≈ tts_total`) | **AVAILABLE** (verified live) | first audio 2.6–2.9 s measured; distinct voices verified |
| Deepgram | `deepgram:aura-2-andromeda-en` | allowlisted | `DEEPGRAM_API_KEY` | **BUFFERED_RESPONSE** | **AVAILABLE** (verified live) | distinct voice/size vs. Thalia confirmed in stored runs |
| ElevenLabs | `elevenlabs:eleven_turbo_v2_5` | allowlisted | `ELEVENLABS_API_KEY` | **BUFFERED_RESPONSE** | **AVAILABLE** (verified live; quota-gated) | verified 760 ms first audio / 40 KB audio before quota drain; voice overridable |
| ElevenLabs | `elevenlabs:eleven_multilingual_v2` | allowlisted | `ELEVENLABS_API_KEY` | **BUFFERED_RESPONSE** | **AVAILABLE** (quota-gated) | same provider API, distinct model id |
| ElevenLabs | `elevenlabs:eleven_flash_v2_5` | allowlisted | `ELEVENLABS_API_KEY` | **BUFFERED_RESPONSE** | **AVAILABLE** (quota-gated) | lowest-latency ElevenLabs tier |
| OpenAI TTS | `openai:tts-1` | allowlisted | `OPENAI_API_KEY` | — | **NOT_CONFIGURED** | no real key; selecting it returns a clear 4xx — never faked |
| Mock TTS | `mock:mock-voice` | `MOCK_MODE=true` | none | **STREAMING** (true multi-chunk) | **AVAILABLE (mock only)** | exercises the streaming first-audio path offline |
| Cartesia | — | `CARTESIA_API_KEY` | required | — | **NOT_CONFIGURED** | key absent; no adapter executes |

## Credential rotation

Deepgram and ElevenLabs each support an optional second account key
(`DEEPGRAM2_API_KEY`, `ELEVENLABS2_API_KEY`). Adapters rotate keys
round-robin via a process-shared key ring; the **account label** (never the
key) is observable in run events for provenance. A missing key never triggers
silent fallback — the provider's own error is surfaced.

## Tools

| Tool | Backend | Credential | Status | Notes |
|---|---|---|---|---|
| `lookup_weather` | Open-Meteo geocode + forecast | none | **AVAILABLE** | real network latency 0.7–1.6 s observed |
| `web_search` | Tavily | `TAVILY_API_KEY` | **NOT_CONFIGURED** → deterministic labeled mock executes | call count/latency real; snippets labeled mock |

## Capability matrix (what comparisons are meaningful)

```
              STT                       LLM                          TTS                          TOOL
Pipeline A    deepgram:nova-3           openrouter:gpt-4.1-mini      deepgram:aura-2-thalia-en    auto
Pipeline B    deepgram:nova-2           openrouter:gpt-4.1-mini      deepgram:aura-2-andromeda-en auto
Pipeline C    elevenlabs:scribe_v1      openrouter:gemini-3.8-flash  elevenlabs:eleven_turbo_v2_5 auto
Pipeline D    deepgram:nova-3           mock:echo                    mock:mock-voice              mock   (MOCK_MODE only)
```

Cross-LLM, cross-STT and cross-TTS comparisons: **yes** — same WAVs across
pipelines, with genuinely different provider endpoints on both STT (Deepgram
Listen vs. ElevenLabs Scribe) and TTS (Deepgram Aura-2 vs. ElevenLabs).
Untested model ids remain selectable and are executed honestly; results —
including provider quota failures — are recorded, not hidden.
