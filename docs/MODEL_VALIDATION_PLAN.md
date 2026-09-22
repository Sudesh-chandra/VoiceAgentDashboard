# Model Validation Plan

Date: 2026-09-22. Scope: determine whether the system can *reliably compare*
models/pipelines end-to-end, with evidence — no invented providers, no
fabricated numbers.

## Current architecture (validated)

```
FastAPI backend (app/)
  pipeline.py        PipelineRunner — the instrumented cascade; ALL timestamps
                     are generated here with time.perf_counter() anchored at
                     INPUT_END (end of user audio decode)
  voice/             provider adapters behind factory functions (build_stt /
                     build_tts / build_tool_providers / build_agent_graph);
                     LangGraph create_react_agent wraps the LLM; tools are
                     LangChain-wrapped and timed
  observability/     LangSmith RunTree wiring (voice_session root, stt/agent/
                     tts children via create_child + tracing_context)
  benchmark_engine.py  run_single / run_suite / live sessions / concurrency /
                     comparison aggregation (median, p95, min, max)
  storage/db.py      SQLite (WAL) benchmark_runs — events_json preserves the
                     raw timeline for every run; artifacts in docs/benchmark-results/
frontend/           React observatory; consumes /api + /ws; no metric math —
                     backend is the single source of truth
```

## Providers actually implemented (adapters that execute)

| Stage | Provider | Adapter | Credential | Status today |
|---|---|---|---|---|
| STT | Deepgram nova-2 | `DeepgramSTT` (REST, batch) | `DEEPGRAM_API_KEY` | configured, verified |
| STT | Mock (deterministic) | `MockSTT` | — | MOCK_MODE only |
| LLM | OpenRouter (any model id) | ChatOpenAI via `init` in `agent.py` | `OPENROUTER_API_KEY` | configured, verified |
| LLM | Mock (deterministic ReAct) | `MockChatModel` | — | MOCK_MODE only |
| TTS | ElevenLabs turbo v2.5 | `ElevenLabsTTS` (REST pcm→WAV) | `ELEVENLABS_API_KEY` | configured, verified |
| TTS | Mock | `MockTTS` | — | MOCK_MODE only |
| Tool | Weather (Open-Meteo) | `WeatherTool` | none | live |
| Tool | Web search (Tavily) | `TavilySearchTool` | `TAVILY_API_KEY` | NOT_CONFIGURED → labeled mock |

Not implemented (honest gaps): Google STT, local Whisper, OpenAI TTS/LLM,
Cartesia — adapter *slots* exist in env docs only. Marked NOT_IMPLEMENTED in
MODEL_CATALOG.md, never offered as working choices.

## Supported pipeline combinations today

- STT: 1 real provider → cross-STT comparison is NOT possible yet.
- LLM: OpenRouter exposes many model ids (`openrouter:<model>`), so genuine
  LLM-vs-LLM comparison is possible.
- TTS: 1 real provider; mock TTS available for labeled A/B (ElevenLabs vs no-TTS
  cost), not a real second TTS vendor.

## How a benchmark executes

`BenchmarkEngine.run_single(tc, cfg)` → `PipelineRunner.execute(path, tc_id)`:
read_wav → anchor `_t0 = perf_counter()` (INPUT_END) → STT → LangGraph agent
(→ tool) → TTS stream (first chunk = FIRST_AUDIO) → TTFA computed in backend;
every boundary emitted as StageEvent; events + timings persisted atomically per
run. Live mode fans the same events out over WebSocket.

## Where latency timestamps are generated

Only in `pipeline.py` via `time.perf_counter()` (monotonic). Frontend never
computes TTFA; it displays backend values only.

## Where results are stored

SQLite `data/vabd.db` (benchmark_runs + sessions + concurrency_runs) and JSON
artifacts `docs/benchmark-results/<run_id>.json`. `events_json` keeps the raw
timeline so aggregates are always recomputable.

## How traces are generated

`trace_pipeline_run` posts a `voice_session` RunTree; children via
`create_child`; `tracing_context(parent=root)` makes LangGraph/ChatOpenAI runs
nest. `trace_id` stored per run. Works only when configured — never required.

## Known limitations going into this pass

1. TTS REST mode is BUFFERED (one HTTP response) → tts_first_audio ≈ tts_total.
   First-audio measurement mode must be labeled per run (STREAMING vs
   BUFFERED_RESPONSE) — added this pass.
2. Single real STT/TTS vendor → matrix varies LLM (and labeled mock TTS).
3. `p95` was computed for any n ≥ 1 — statistically dishonest for tiny n
   (fixed: null unless n ≥ 5, with "insufficient samples" in UI).
4. No experiment identity (experiment_id) tying matrix runs together — added.
5. No bulk export — added (JSON + CSV).

## Validation plan

1. Provider capability probe (config + lightweight live checks) → matrix.
2. Audio inventory of the six real WAVs (validity, duration, rate, channels).
3. Automated correctness tests: TTFA recomputation from events, event
   ordering, tool-count assertions, combination generation, cap enforcement,
   persistence-across-restart, export shape, no-secrets in API responses.
4. Real experiment matrix via the new runner (cost-capped), repetitions ≥ 2.
5. Concurrency (small levels), noisy-audio cross-pipeline observation.
6. Reports generated strictly from stored measurements.
