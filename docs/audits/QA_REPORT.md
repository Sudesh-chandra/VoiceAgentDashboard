# QA Report — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22. All results below come from executed commands in this pass.

## Automated test suite

`cd backend && MOCK_MODE=true .venv/Scripts/python -m pytest tests/ -q`

Result: **35 passed** (4 consecutive runs during the audit, including after
every fix). Coverage highlights:

- TTFA recomputation from raw events (`test_ttfa_recomputed_from_events_matches_backend`)
- Event ordering incl. tool-inside-agent (`test_event_ordering_matches_spec`,
  `test_tool_case_exactly_one_execution_and_ordering`)
- Stage latency sanity / no double counting (`test_stage_latencies_no_double_counting`)
- Matrix combination generation + cost guard (`test_cost_guard_blocks_oversized_matrix`)
- Persistence across DB reconnect (`test_results_survive_database_reconnect`)
- Export JSON/CSV shape incl. token columns (`test_export_json_and_csv_shape`)
- Provider endpoint secret-safety (`test_providers_endpoint_never_leaks_secrets`)
- Upload/traversal/oversize security tests (7 cases)
- 409 guard for unknown provider/model; 422 for malformed specs

## Executed integration validation (real providers)

| Check | Command (abridged) | Result |
|---|---|---|
| Bad STT credentials fail safely | chaos probe with invalid key | ✗ 401 in 1.09 s, no secret in error |
| Malformed audio fails safely | garbage RIFF to Deepgram | ✗ 400 in 0.97 s, bounded message |
| Whisper-large timeout | matrix run | fixed (180 s) → ✓ 9,318/12,115 ms |
| Cross-provider STT matrix (same WAV) | `scripts/provider_matrix.py --stt` | nova-2/nova-3/whisper ✓ distinct latencies+conf |
| Cross-provider TTS matrix (same text) | `--tts` | Aura ×2 ✓; ElevenLabs quota error surfaced |
| Token capture | EXP-AUDIT-TOKENS2 | 137/25/162 tokens persisted |
| Tool-case tokens | EXP-AUDIT-TOKENTOOL | 212/22/234, tools=1 |
| Data isolation | two live sessions | zero event mixing |
| Concurrency | levels 1,5 × 2 runs (mock) | 100% both levels |
| Six real WAVs, pipeline A | EXP-FINAL-NOVA3 | 6/6 ✓, tools correct (0,0,1,1,0,0) |
| Second pipeline comparison | EXP-FINAL-NOVA2 | 3/3 ✓, genuinely different latencies |

## Browser E2E (in-app preview driver; Playwright not installed)

Executed against the live app (Vite dev server → FastAPI backend):

1. Dashboard loads; header shows live providers + LangSmith chip.
2. Pipeline selectors render all backend choices (incl. Scribe + Flash after
   the stale-backend restart — a real catch: the previously running backend
   predates the catalog change).
3. Provider Test → STT nova-3 on test_01: `✓ responded latency 2562.9 ms ·
   confidence 0.997` with the real transcript.
4. Provider Test → TTS aura-2-andromeda: `✓ first audio 2786.9 ms · total
   2786.9 ms · BUFFERED_RESPONSE · 24768 bytes` (pre-fix run; the transport
   streaming fix was validated afterwards — live Aura run now reports first
   audio 2,513 ms < completion 3,975 ms, mode `STREAMING`).
5. Provider Test → Scribe: honest failure card with the provider's 401 reason
   and recovery hint (no crash, other probes unaffected).
6. Benchmark via UI: test_03 + nova-3 + Aura → "Benchmark complete — 1 run(s)
   stored"; Results detail shows TTFA 10,640 ms, tool `lookup_weather
   ({"city":"Bangalore"})`, transcript, agent response, trace link `572bd9b6`,
   waterfall from real timestamps.
7. Compare tab: per-pipeline medians with n; Reliability tab: 236 runs, 54
   failed (historical, incl. intentional chaos + quota failures) — failures
   visible, never hidden.

## Verdict

QA status: **PASS with documented limitations** (ElevenLabs quota dependency,
Playwright absence). No blocking defects open.
