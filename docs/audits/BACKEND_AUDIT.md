# Backend Audit — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22.

## Structure

```
backend/app/
  server.py            FastAPI routes, WS fan-out, provider-test probes, export
  pipeline.py          instrumented cascade (single source of timing truth)
  benchmark_engine.py  run/reliability/concurrency/comparison engines
  registry.py          test-case registry (sandboxed resolution)
  core/config.py       env-driven settings + model catalogs
  core/metrics.py      StageEvent/StageTimings/PipelineResult dataclasses
  storage/db.py        SQLite (WAL) + migrations; save_benchmark_run
  voice/__init__.py    factories (build_stt/build_tts/build_agent_graph)
  voice/agent.py       LangGraph agent + tool adapters + MockChatModel
  voice/providers/     stt.py / tts.py / weather.py / search.py / base.py
  observability/       langsmith_wiring.py (RunTree hierarchy)
```

Separation is clean: routes ≠ timing ≠ storage ≠ providers. No repository layer
is needed at this scale (single table + 2 aux tables).

## Verified properties

- **Async hygiene**: all provider I/O is httpx.AsyncClient; no blocking calls in
  async paths; SQLite writes are short, lock-guarded, and off the hot path.
- **Connection lifecycle**: httpx clients are context-managed per call; SQLite
  per-thread connections with a process singleton; WS queues bounded (500) and
  buffers bounded (2000) — slow consumers drop, never balloon.
- **Error handling**: every failure path returns a structured result
  (`failure_stage`, `error_type`, safe `error_detail`); background live runs
  catch and publish failures (never die silently).
- **Database**: WAL, parameterized SQL, auto-migrations for new columns
  (incl. the new token columns), INSERT OR REPLACE keyed by run_id (a rerun of
  the same run_id updates; experiment ids append — no result loss).
- **Resource cleanup**: temp smoke artifacts removed in `finally`; uploads are
  written directly into the sandboxed dir (no orphan temp files).

## Issues found & fixed during audit

1. Token usage discarded → captured + persisted + exported.
2. `usage_metadata` vs `token_usage` (LangChain v3 field) → fixed.
3. Timeout errors with empty messages → self-describing safe messages.
4. whisper-large timeout 120→180 s (measured cold starts up to 130 s).
5. KeyRing rotation reset per adapter instance → process-shared.

## Remaining observations (accepted)

- `_live` in-memory registry: fine for single-instance; Redis would be needed
  for multi-worker deployments (uvicorn workers > 1 would split WS fan-out —
  document: run with 1 worker, as started by the provided scripts).
- `provider_availability()` computes availability synchronously from env —
  no network probes on listing (deliberate: no cost/latency on UI loads).
- BenchmarkEngine `_write_artifact` writes one JSON per run into
  `docs/benchmark-results/` — useful provenance; volume bounded by run count.

## Verdict

Backend status: **PASS** for the assignment's scope.
