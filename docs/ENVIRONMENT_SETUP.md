# Environment Setup

Copy `.env.example` → `.env` and fill in the providers you have. **Never commit
`.env`.** The application validates configuration at startup and reports which
providers are configured/unavailable — it never prints secret values.

## Provider semantics

Providers are **adapters with availability**. A missing key does not crash the
app; the provider is reported unavailable and pipelines using it are rejected
with a clear error (HTTP 409) instead of silently producing fake results.
`MOCK_MODE=true` is the only way to run without keys, and every result is then
labeled `mock` — for tests/CI, never for real benchmarks.

## Variables

| Variable | Purpose | Required | Provider | Example format | Where to obtain | Security notes |
|---|---|---|---|---|---|---|
| `OPENROUTER_API_KEY` | LLM calls via OpenRouter | For real LLM | OpenRouter | `sk-or-v1-…` | openrouter.ai/keys | server-side only; never logs |
| `OPENROUTER_BASE_URL` | OpenRouter API base | No | OpenRouter | `https://openrouter.ai/api/v1` | — | fixed constant by default |
| `OPENROUTER_MODEL` | Model id | No (default `openai/gpt-4o-mini`) | OpenRouter | `openai/gpt-4o-mini` | openrouter.ai/models | — |
| `DEEPGRAM_API_KEY` | STT | For real STT | Deepgram | 32-hex token | console.deepgram.com | server-side only |
| `ELEVENLABS_API_KEY` | TTS | For real TTS | ElevenLabs | `sk_…` | elevenlabs.io → Profile → API keys | server-side only |
| `ELEVENLABS_MODEL` | TTS model | No (default `eleven_turbo_v2_5`) | ElevenLabs | `eleven_turbo_v2_5` | docs | — |
| `ELEVENLABS_VOICE_ID` | TTS voice | No (uses a default public voice) | ElevenLabs | 20-char id | dashboard voices | — |
| `OPENAI_API_KEY` | Alternative LLM/TTS | No | OpenAI | `sk-…` | platform.openai.com | server-side only |
| `LANGSMITH_API_KEY` | Tracing | For traces | LangSmith | `lsv2_pt_…` | smith.langchain.com → Settings | server-side only |
| `LANGSMITH_TRACING` | Enable tracing | No (`true`/`false`) | — | `true` | — | — |
| `LANGSMITH_ENDPOINT` | Trace endpoint | No | LangSmith | `https://api.smith.langchain.com` | — | — |
| `LANGSMITH_PROJECT` | Trace project name | No | — | `voice-agent-benchmark` | — | — |
| `LANGSMITH_SEND_TRANSCRIPTS` | Privacy switch | No (default `false`) | — | `false` | — | `true` sends raw transcripts |
| `ENVIRONMENT` | dev/prod marker | No | — | `development` | — | — |
| `BACKEND_HOST` | Bind host | No | — | `0.0.0.0` | — | — |
| `BACKEND_PORT` | Bind port | No | — | `8000` | — | — |
| `FRONTEND_URL` | CORS allowlist | No (dev defaults to localhost:5173) | — | `http://localhost:5173` | — | comma-separated for several |
| `VITE_API_BASE_URL` | Frontend API base | No | — | `` (same-origin default) | — | **never put secrets in VITE_* vars** |
| `VITE_WS_URL` | Frontend WS base | No | — | `` (same-origin default) | — | same |
| `DATABASE_URL` | Storage | No (default sqlite `data/vabd.db`) | — | `` or URL | — | — |
| `SECRET_KEY` | App secret | Recommended | — | random string | generate locally | see below |
| `MOCK_MODE` | Offline test mode | No (`false` for real runs) | — | `false` | — | mock results are labeled |
| `MAX_UPLOAD_MB` | Upload cap | No (default 25) | — | `25` | — | DoS guard |
| `MAX_CONCURRENCY` | Concurrency cap | No (default 10) | — | `10` | — | rate-limit guard |
| `WHISPER_MODEL` | Local whisper slot | No | local | `base` | — | adapter slot, not bundled |
| `GOOGLE_CLOUD_PROJECT` / `GOOGLE_APPLICATION_CREDENTIALS` | Google STT slot | No | Google | — | — | adapter slot, not required |
| `TAVILY_API_KEY` | Web-search tool | No | Tavily | `tvly-…` | tavily.com | server-side only |
| `CARTESIA_API_KEY` | Alternative TTS | No | Cartesia | — | cartesia.ai | server-side only |

## SECRET_KEY generation

`SECRET_KEY` is an application-generated secret — you generate it locally and
paste the **value** into `.env` (never the command itself):

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

WRONG: `SECRET_KEY=python -c "import secrets; …"` — that stores the command as
the value. RIGHT: `SECRET_KEY=HkQx3_…` (the generated output).

## Startup validation

On boot the backend resolves provider availability and logs a **non-secret**
summary, e.g.:

```
provider availability: llm=openrouter:openai/gpt-4o-mini stt=deepgram tts=elevenlabs langsmith=enabled mock_mode=False
```

Selecting a provider without credentials returns HTTP 409 with the missing
provider named (never the key). Optional providers never prevent startup.

## Privacy defaults

LangSmith receives structure + timings + hashed transcripts by default
(`LANGSMITH_SEND_TRANSCRIPTS=false`). Audio bytes are never uploaded anywhere.
No key material is ever included in traces, logs, error messages, or API
responses.
