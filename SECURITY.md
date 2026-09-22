# Security Review

Methodology: the Cloudflare security-audit workflow (github.com/cloudflare/security-audit-skill) — enumerate trust boundaries, treat all external input as untrusted, verify secrets hygiene, fix real findings. Each finding below was found during review passes of this codebase and **fixed in code**, with a test where practical.

## Trust boundaries

```
Browser ──(REST + WS, untrusted)──► FastAPI ──(HTTPS)──► STT/LLM/TTS providers
   │                                │
   └─ uploads (untrusted)           └─(HTTPS)──► LangSmith
```

Everything arriving from the browser is untrusted: upload bytes and filenames,
test-case ids, pipeline parameters, concurrency parameters. The server is the
only holder of provider keys; the browser receives none.

## Findings and fixes

1. **Upload DoS via unbounded buffering** — the first implementation read the
   whole upload into memory before checking size. Fixed: the endpoint streams
   chunks (512 KB) and aborts at the `VABD_MAX_UPLOAD_MB` cap with HTTP 413.
   Test: `test_upload_rejects_oversized`.

2. **Path traversal via `startswith` check** — `str.startswith(base)` accepts
   sibling directories like `data/test_cases_evil/`. Fixed: strict
   `Path.relative_to(base)` resolution in `registry.resolve_audio_path`, plus
   metadata validation that rejects any `audio_path` containing directory
   components. Tests: `test_audio_endpoint_sandboxed`, registry unit path.

3. **Magic-byte spoofing** — a file named `.wav` with non-RIFF content was
   rejected by header check; deeper spoofing (valid RIFF header, corrupt frames)
   is caught by parsing with the `wave` module before storing. Tests:
   `test_upload_rejects_non_wav`, `test_upload_rejects_wav_magic_spoof`.

4. **Unbounded WS queues** — a slow dashboard consumer could balloon memory.
   Fixed: bounded queues (`maxsize=500`) with drop-on-full for subscribers, and
   a bounded per-session buffer (2000 events).

5. **Unsafe filenames** — uploads keep only `[A-Za-z0-9._-]` of the basename
   (≤120 chars), prefixed with a random id; names are never used as paths
   directly.

6. **Input validation** — Pydantic models whitelist every enum field (stt/tts/
   tool patterns, LLM choice whitelist, `expected_tool_calls` clamped to 0–1,
   concurrency levels clamped and de-duplicated to the settings cap server-side,
   list lengths capped). Tests: `test_invalid_pipeline_rejected`,
   `test_llm_choice_whitelisted`, `test_concurrency_level_capped`.

7. **Log redaction** — a logging filter rewrites common key shapes
   (`sk-…`, `api_key=…`, `token=…`) to `[REDACTED]` as defense-in-depth.
   Test: `test_redaction_filter`.

8. **CORS + method scope** — CORS restricted to configured dashboard origins
   with credentials disabled; only GET/POST allowed. WebSocket endpoints
   validate session ids against the live registry (404 on unknown).

## Secrets hygiene

- No secrets in code; everything is read from environment via pydantic-settings
  (`.env`, gitignored). `.env.example` documents every variable with blanks.
- Provider keys are used only inside provider clients on the server; the
  `/api/config` endpoint exposes **availability booleans only**.
  Test: `test_no_secrets_in_config_endpoint`.
- Logs never include keys (redaction filter); errors returned to clients carry
  error types and truncated messages, never provider payloads or keys.
- Tool payloads: the weather tool passes only a city name as a query parameter
  to fixed Open-Meteo endpoints (no user-controlled base URL → no SSRF).

## LangSmith privacy

- What is sent: pipeline structure, stage timings, tool names/args (city/query
  text), success/failure, and **transcript hashes** (`sha256:<16-hex-prefix>`)
  instead of raw text by default.
- `LANGSMITH_SEND_TRANSCRIPTS=true` opts into raw transcript/response text for
  debugging; audio bytes are never sent in either mode; no API keys or secrets
  are ever placed in spans.
- Test: `test_langsmith_privacy_hashing`.

## Remaining recommendations (prototype scope)

- Add authentication before any multi-user deployment (none assumed for a local
  benchmark prototype).
- Add rate limiting on run endpoints if exposed beyond localhost.
- Consider antivirus scanning of uploads in any shared deployment.
- `LIVEKIT_*` variables are intentionally absent — no RTC infrastructure is used.
