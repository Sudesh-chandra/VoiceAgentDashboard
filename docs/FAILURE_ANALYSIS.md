# Failure Analysis

Generated from `benchmark_runs` (SQLite) — every failed run was persisted with
its stage, error type, and detail. Grouped for debugging; nothing hidden.

## By stage → provider → error type

### LLM (agent) — 36 failed runs, all resolved

| Pipeline (LLM) | Error type | Count | Root cause | Resolution |
|---|---|---|---|---|
| `google/gemini-2.0-flash-001` | LLMError (HTTP 404) | 12 | Model id retired on OpenRouter ("No endpoints found") | Use live ids from `/api/v1/models`; switched to `gemini-3.8-flash` |
| `openai/gpt-4.1-mini` + `gemini-3.8-flash` | LLMError (HTTP 402) | 24 | OpenRouter credit ceiling: models defaulting to `max_tokens=65536` exceeded affordable credits | Bounded `max_tokens=200` in `_openrouter_model` (voice replies are one sentence) — both models then ran 24/24 clean (EXP-2026-00003) |

**Diagnosis path that worked:** run row → `error_detail` contained the exact
provider message (404 / 402 with credits math) → fix applied at the adapter →
re-run matrix. Failure visibility did its job.

### STT — 13 failed runs (historical)

| Provider | Error type | Count | Root cause | Status |
|---|---|---|---|---|
| Deepgram | STTError (HTTP 400) | 12 | Early dev bug: headerless PCM sent instead of RIFF container | Fixed (`read_wav` returns full container); all later STT runs green |
| Deepgram | STTError | 1 | One transient failure during a dev pipeline (`deepgram\|…\|auto`) | Retained as data; not hidden from reliability totals |

### Tool — 0 failed executions

No tool-level failures recorded. Weather (Open-Meteo) succeeded whenever called
(13 executions across experiments, 82 ms–3.5 s). The labeled mock search tool
has no network failure mode.

### TTS — 0 failed executions in real mode

ElevenLabs succeeded in every real run (including concurrency level 5). The
earlier 402 "library voice" failure was fixed pre-experiments by pinning a
premade voice; it predates the current DB window and is documented in
SECURITY/audit docs.

### Input — 0 user-facing failures

Upload validation rejects malformed/oversized files before providers are
called; those rejections are API-level (415/413) and covered by tests.

## Failure→recovery mapping (what the UI shows)

| Stage | Typical error | Recovery hint surfaced |
|---|---|---|
| stt | STTError (400/401/timeout) | Check DEEPGRAM_API_KEY / audio container, re-run |
| llm | LLMError (402/404/timeout) | Check OPENROUTER credits & model id (`/api/v1/models`), re-run |
| tool | tool exception | Tool marked failed in span + events; run marked failed at tool stage |
| tts | TTSError (402/timeout) | Check ELEVENLABS_API_KEY/voice; first-audio stays null |

## Current state

After the fixes above, the latest experiment (EXP-2026-00003, 24 runs) and the
concurrency test (12 runs) completed with **zero failures** — while all 36
earlier LLM failures and 13 STT failures remain queryable in the database for
regression history.
