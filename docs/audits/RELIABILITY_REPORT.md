# Reliability Report — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22.

## Failure handling matrix (executed)

| Scenario | How tested | Behavior | Verdict |
|---|---|---|---|
| Invalid STT credentials | chaos probe, key=`invalid-key-chaos-test` | 401 in 1.09 s; safe error; no secret; run persists as FAIL@stt | ✓ |
| Malformed audio | garbage RIFF bytes | HTTP 400 in 0.97 s; bounded message | ✓ |
| Provider cold-start timeout | whisper-large >120 s | fails at stt with empty msg → FIXED: 180 s + self-describing error; retry ✓ | ✓ (fixed) |
| Quota exhaustion | ElevenLabs TTS after drain | FAIL@tts persisted; provider's own reason shown in UI; other providers unaffected | ✓ |
| Provider not configured | openai:tts-1 | HTTP 409 before execution; never silently swapped | ✓ |
| Invalid audio upload | non-wav / spoof / oversize / traversal | 400/400/413/404-422 | ✓ |
| Unknown test case | API probe | 404 | ✓ |
| Tool failure | tool adapter wraps provider | tool_error result returned to LLM; latency+ok recorded; run continues | ✓ |

No failure mode hangs, crashes the server, fabricates success, or leaks secrets.
All failures are visible in Results + Reliability (236 runs, 54 failed — every
failure stored with stage/type).

## Retry policy

Deliberate: **no automatic retries** for provider calls. Rationale: benchmarks
measure real latency; silent retries would fabricate it and duplicate charges.
The matrix runner can be re-invoked to repeat runs; the UI offers per-provider
probes for debugging. Timeouts are bounded per provider (catalog `timeout_s`).

## Concurrency (executed, mock mode)

- Level 1 ×2: 100% success, TTFA median 469 ms, throughput 2.0 rps
- Level 5 ×10: 100% success, TTFA median 365 ms, throughput 16.8 rps
- Server-side clamp at `MAX_CONCURRENCY`; staggered launches; wave semaphore.
Real-provider concurrency was exercised during matrix runs (staggered,
single-account per request via rotation) without rate-limit failures beyond
the documented ElevenLabs quota.

## Data isolation (executed)

- Two overlapping live sessions (no-tool vs weather): distinct session ids,
  disjoint event streams (8 vs 10 events; tool events only in the weather
  session). Zero cross-session leakage.
- 192 stored runs → 192 distinct session rows (no session mixing).
- Concurrency writes: SQLite WAL + serialized transactions verified under
  parallel runs; unique run_ids (uuid) prevent overwrites.
- Trace ids unique per run (LangSmith RunTree uuid); compare queries group by
  pipeline_id, never mixing sessions.

## Verdict

Reliability: **PASS** — graceful degradation everywhere, failures as data.
