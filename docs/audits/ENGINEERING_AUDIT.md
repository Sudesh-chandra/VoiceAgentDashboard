# Engineering Audit — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22. Final engineering validation pass (AI + backend + frontend +
QA + security + SRE review). Every claim below was verified by execution, not
code reading.

## CURRENT ARCHITECTURE

- **Backend**: FastAPI (`backend/app/server.py`) — REST + WebSocket live events;
  pipeline engine (`pipeline.py`) instruments the voice cascade:
  `INPUT_END → STT → LangGraph agent (LLM + optional tool) → TTS → FIRST_AUDIO`.
- **Provider layer**: spec-based factories (`voice/__init__.py`) + registry
  catalogs (`core/config.py`): STT `deepgram:nova-2|nova-3|whisper-large`,
  `elevenlabs:scribe_v1`, `mock`; TTS `elevenlabs:{turbo_v2_5,multilingual_v2,flash_v2_5}`,
  `deepgram:aura-2-{thalia,andromeda}`, `openai:tts-1 (NOT_CONFIGURED)`, `mock`.
  Dual-account `KeyRing` rotation (`providers/base.py`).
- **Agent**: LangGraph `create_react_agent` (`voice/agent.py`), OpenRouter
  ChatOpenAI (streaming, max_tokens=200), tool adapters with real latency.
- **Observability**: LangSmith RunTree root (`observability/langsmith_wiring.py`)
  with stt/agent/tts child spans; per-run full event timeline persisted.
- **Storage**: SQLite (`storage/db.py`, WAL) — 40+ columns per run incl. specs,
  tokens, trace_id; auto-migrations.
- **Frontend**: React + Vite, 5 tabs (Benchmark / Live Run / Results / Compare /
  Reliability & Concurrency), waterfall from real events, Provider Test section.
- **Runner**: `benchmark/run_matrix.py` (cartesian pipelines × tests × runs with
  a hard cost cap), `scripts/provider_matrix.py` (cross-provider harness).

## WHAT WORKS (verified)

- 3+3 real STT/TTS selections; selection changes execution (specs travel UI →
  API → factory → provider HTTP call; unknown/unconfigured → HTTP 409).
- TTFA = `FIRST_AUDIO.ts − INPUT_END.ts` on a monotonic clock; automated test
  recomputes it from raw events (`test_ttfa_recomputed_from_events_matches_backend`).
- LangGraph actually executes: tool cases produce exactly 1 real tool call
  (Open-Meteo weather / Tavily search); no-tool cases 0. Verified live via UI.
- Stage latencies measured around real calls, no double counting; agent span
  includes tool; waterfall renders from real event timestamps.
- LangSmith: real trace ids stored per run (e.g. `f46b60c5…`); spans identify
  provider/model + token usage; transcripts hashed unless opted in.
- Token governance: `llm_prompt_tokens/llm_completion_tokens/llm_total_tokens`
  captured from provider-reported usage (e.g. 137/25/162; tool case 212/22/234),
  persisted + exported. NULL when provider doesn't report (never estimated).
- Reliability: failures persisted with stage/type/safe detail; ElevenLabs quota
  failures surfaced with the provider's own reason; bad-credential and
  malformed-audio chaos tests fail safely (<1.2 s, no secret in message).
- Data isolation: two overlapping live sessions show zero event mixing (verified).
- Concurrency engine: L1/L5 verified 100% success in mock mode; per-account key
  rotation spreads free-tier quota; MAX_CONCURRENCY clamp server-side.
- Security: uploads magic-byte validated + sandboxed; no secrets in any API
  response, log, bundle, DB, or trace; redaction filter active.
- Frontend: real data everywhere; Provider Test executes real I/O from the UI.

## WHAT PARTIALLY WORKS

- **ElevenLabs TTS/STT (account quota)**: integration is real and was verified
  (TTS 760 ms first-audio; Scribe transcript 200 OK) but the free tier drained
  during validation; account #2 vendor-flagged (`detected_unusual_activity`).
  Failures surface honestly; works again after 2026-10-22 reset.
- **Whisper-large (hosted)**: works but shows 48–130 s cold starts under
  provider load; timeout raised to 180 s + self-describing timeout error added.
- **TTS streaming**: all working TTS providers return single-chunk HTTP; labeled
  `BUFFERED_RESPONSE` honestly (first == total), not fabricated as streaming.
- **p95 aggregates**: need ≥5 samples by design (honest "insufficient samples").

## WHAT IS BROKEN

Nothing currently breaks the critical path. Issues found and fixed this pass:
1. httpx timeout errors stringify to `""` → self-describing messages added.
2. whisper-large 120 s timeout too small for measured cold starts → 180 s.
3. LLM token usage was discarded → now captured/persisted/exported.
4. LangChain `usage_metadata` (not `token_usage`) is the real field → fixed.
5. Per-instance key rotation didn't actually alternate under per-run adapters →
   process-shared `KeyRing`.

## WHAT IS MISSING

- OpenAI TTS: `NOT_CONFIGURED` (no key) — listed unavailable, never faked.
- OpenRouter audio (STT/TTS): probed, not viable with current balance (402).
- Playwright: not installed in this environment; browser E2E was executed with
  the in-app preview driver + screenshots instead (documented, not faked).
- WER scoring: no ground-truth transcripts; raw transcripts stored instead.

## WHAT IS RISKY (accepted, documented)

- Free-tier rate limits can fail bursts; engine records failures honestly and
  the UI shows them; KeyRing rotation mitigates but cannot eliminate.
- `_live` session buffers are in-memory (bounded at 2000 events); restart loses
  live-session state only (results are durable).
- OpenRouter cost depends on selected model; max_tokens=200 caps per-call cost;
  matrix runner enforces `MAX_EXPERIMENT_RUNS` before starting.

## WHAT IS INCORRECTLY IMPLEMENTED (fixed this pass)

See "WHAT IS BROKEN" — all five items were corrected and re-tested (35/35).

## WHAT REQUIRES FIXING (next steps)

- Move the in-memory live-session registry behind Redis if multi-instance
  deployment is ever required (single-user tool today: acceptable).
- Add ground-truth transcripts for WER if the evaluator supplies them.
