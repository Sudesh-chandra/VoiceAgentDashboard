# Architecture — Voice Agent Benchmarking & Observability Platform

## 1. Purpose

A prototype platform that runs a controlled **audio-in / audio-out voice pipeline**
against six user-supplied `.wav` test cases, measures **per-stage latency** and
**Time To First Audio (TTFA)**, captures failures, runs reliability and concurrency
checks, and exports a full **LangSmith trace** for every interaction — with a
dashboard for live observability and cross-pipeline comparison.

This is deliberately **not** a live-microphone chat demo. The benchmark workload is
audio files, so the platform is a *batch/benchmark orchestrator over an instrumented
voice cascade* plus a live UI.

## 2. Foundational decision: why not LiveKit / Pipecat

The primary reference (langchain-ai/voice-demo) targets **interactive conversations**:
it opens a microphone/speaker session or an RTC room, with VAD, turn detection and
barge-in. The tracing backends (voice-agents-tracing) translate framework OTel spans
(`stt_node`, `llm_node`, `tts_node`) into LangSmith.

Our benchmark needs are different in a way that matters:

1. **Deterministic boundaries.** We must start the timer at the *exact* end of the
   user's audio and stop it at the *first output audio sample*. In a file-driven
   pipeline we control both boundaries precisely. In an RTC session the boundaries
   belong to the framework and would be approximations.
2. **Repeatable, comparable runs.** Same file → same pipeline → comparable numbers.
   A live transport adds jitter (VAD hangover, noise suppression, network) that is
   not part of what we benchmark.
3. **No extra infrastructure.** No LiveKit server, no Daily room, no websocket
   transport to maintain — matching the evaluator's "no unnecessary infrastructure".

**What we reuse instead (patterns, not code):**
- From `voice-demo`: the **cascade shape** (STT → LLM → TTS), the **Open-Meteo
  weather tool** (keyless, deterministic, structured errors), the **LangSmith env
  wiring convention** (`LANGSMITH_TRACING`, `LANGSMITH_PROJECT`), and the
  **LangGraph `create_agent` brain** (ReAct over one tool) with
  `LANGSMITH_TRACING_MODE=otel` so graph runs nest inside the pipeline trace.
- From `voice-agents-tracing`: the **span hierarchy** (`voice_session` →
  `stt` / `agent` (→ `llm`, `tool`) → `tts` → `first_audio`) and the discipline of
  recording what each span contains.
- From `pipecat-langgraph-example`: the separation of the **graph** from the
  transport, and OTel→LangSmith routing (`OTEL_EXPORTER_OTLP_ENDPOINT`).

The STT/TTS/LangGraph providers are the **same underlying libraries** the references
use (Deepgram, OpenAI, Cartesia, langchain/langgraph), so a future LiveKit/Pipecat
transport could be added without changing the benchmark engine.

## 3. Components

```
frontend/ (React + Vite + TS)
  Pipeline config · test-case grid · upload · live session ·
  benchmark table · pipeline comparison · reliability & concurrency views
        │  HTTP REST + WebSocket (/ws/sessions/{id})
backend/ (FastAPI, Python 3.10+)
  api/            routes: runs, pipelines, test-cases, upload, sessions, concurrency
  core/config.py  env-driven settings, no secrets in code
  voice/          providers (STT/LLM/TTS/tool adapters) + cascade + metrics
  benchmark/      runner, reliability, concurrency, comparison
  observability/  LangSmith wiring + privacy filtering
  storage/        SQLite (benchmark_runs, sessions, events) via stdlib sqlite3
  tools/          weather (Open-Meteo) + deterministic mock
data/
  test_cases/     the six user-provided .wav files
  test_cases/metadata.json
  uploads/        validated upload staging
docs/benchmark-results/   committed raw run artifacts (JSON)
```

## 4. Data flow (single benchmark run)

```
UI "Run test case" ──HTTP POST /api/runs──► backend creates session (SQLite)
                                             + BenchmarkRun row (status=running)
   ┌─────────────────────────────────────────┴──────────────────────────┐
   │ PipelineRunner.execute(audio_path, config)                         │
   │   t0 = end-of-input-audio boundary (after file decode + duration)  │
   │   1. STT:   audio bytes → transcript                               │
   │   2. Agent: LangGraph create_agent(transcript)                     │
   │              └─ optional single tool call (weather / mock)         │
   │   3. TTS:   reply text → audio (streaming)                         │
   │   every stage emits StageEvent(timestamps) → event bus             │
   └────────────────────────────┬───────────────────────────────────────┘
                                │
        WebSocket fan-out ──► UI Live Session panel (live latency logs)
                                │
                    events aggregated into per-stage metrics
                                │
                    BenchmarkRun row updated (status=success/failed,
                    stage latencies, ttfa, failure_stage, trace_id)
```

The runner is fully synchronous-with-events: the benchmark engine does not care
whether the UI is attached.

## 5. Latency measurement boundaries (explicit definitions)

The **headline metric TTFA** is measured from the moment the last sample of user
input is consumed by the pipeline (`input_audio_end`) to the first output audio
sample available for playback (`tts_first_audio`). For file-driven input this
equals `tts_first_audio_ts - input_audio_end_ts` exactly; no estimation.

| Metric | Definition | Boundary |
|---|---|---|
| `input_duration_ms` | decoded duration of the input wav | wave module |
| `stt_latency_ms` | STT call start → transcript ready | wall clock around provider call |
| `llm_ttft_ms` | agent graph start → first LLM token (streaming) | first `on_chat_model_stream` token |
| `tool_latency_ms` | tool invocation start → structured result | wall clock around tool body |
| `tts_first_audio_ms` | TTS start → first output audio sample | first audio chunk received |
| `time_to_first_audio_ms` | **end of user input → first output audio sample** | headline |
| `total_response_latency_ms` | end of user input → TTS synthesis complete | end-to-end |
| network_ms | optional provider-reported network overhead | provider payload |

If a provider cannot expose a sub-metric (e.g. no streaming TTFT in a batch API),
the field is `null` and the limitation is documented in the stage event — never
fabricated.

### Stage clock rules
- All timestamps are `time.perf_counter()` deltas anchored at `input_audio_end`
  (monotonic, high resolution) plus wall-clock `datetime` for correlation.
- Event ordering is monotonic; events emitted after a failure carry the stage that
  failed so the failure breakdown can attribute blame.

## 6. Benchmark flow

`benchmark/runner.py` iterates selected test cases × selected pipeline config:
1. resolves audio path (sandboxed to `data/test_cases/`)
2. runs the pipeline
3. collects `StageMetric`s into `StageTimings`
4. writes a `BenchmarkRun` row with all metrics
5. writes a raw JSON artifact under `docs/benchmark-results/{run_id}.json`

Comparison joins runs by `pipeline_id`; aggregation (median, p95) is computed
over raw runs — **never stored fabricated**.

## 7. Observability flow

Root span `voice_session` (LangSmith run type `chain`) is opened per run.
Children: `stt`, `agent` (LangSmith `run_type=chain` wrapping LangGraph), `tts`,
`weather` tool (run type `tool`). All carry stage timings as metadata.
Set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` (optionally
`LANGSMITH_TRACING_MODE=otel` when graph runs must nest under framework spans —
mirrors voice-demo's `pipecat_with_langgraph` wiring).
`trace_id` (LangSmith run id) is stored on the `BenchmarkRun` row so the UI can
deep-link to the exact trace.

**Privacy:** transcripts (not audio) are sent; audio is never uploaded to LangSmith.
`LANGSMITH_SEND_TRANSCRIPTS=false` (default) redacts transcript text from span
inputs/outputs, replacing it with a SHA-256 prefix hash so traces remain joinable
across runs without exposing user content. Set to `true` to debug with real text.
No API keys or audio bytes are ever serialized into spans.

## 8. LangGraph role

The agent stage is a LangGraph `create_agent` (ReAct) over the weather tool.
This adds real value here: it **decides** whether a tool call is needed (no-tool
queries short-circuit to a direct answer; weather queries trigger exactly one
tool call) and it is **natively traced** in LangSmith, satisfying the requirement
that graph execution is visible. It is *not* forced into STT or TTS where it would
only add latency. Stateless per run (no checkpointer) — each benchmark run is an
independent conversation, mirroring voice-demo's stateless-brain rationale.

## 9. Storage

SQLite via stdlib `sqlite3` (no ORM dependency): `benchmark_runs`, `sessions`,
`session_events`, `concurrency_runs`. One file (`data/vabd.db`), committed schema
in `storage/schema.sql`, thread-local connections, WAL mode.

## 10. Frontend/backend communication

- REST: pipelines CRUD-ish (list/create presets), test cases (list/upload),
  runs (start/list/detail), comparison, reliability, concurrency.
- WebSocket `/ws/sessions/{session_id}`: live `StageEvent` stream (JSON) during a
  run; the UI renders the live latency log as events arrive.
- No secrets in any API response; provider keys are read server-side from env only.

## 11. Security boundaries

- `.env` never committed; `.env.example` documents every variable.
- Uploads: MIME + magic-byte sniff (RIFF/WAVE), size cap, filename sanitization,
  path-traversal-proof resolution inside `data/test_cases/`.
- Test-case metadata JSON schema-validated; concurrency params clamped to safe
  ranges (1–10) server-side.
- CORS restricted to the dashboard origin; no provider key ever crosses to the
  browser; all external API calls are server-side.
- Logs redact secrets; LangSmith payloads filtered per §7.
- Provider calls have hard timeouts; a hung provider fails the run and is recorded.

## 12. Concurrency testing

A concurrency test replays one or more selected test cases against one pipeline at
levels 1 / 5 / 10 (configurable, clamped) using `asyncio.gather` over the same
instrumented runner. Measured: success rate, TTFA distribution, per-stage latency,
errors, wall-clock throughput. Rate-limit safety: default stagger delay between
launches, hard cap of 10 concurrent runs, and per-provider timeout.

## 13. Assumptions

- Python 3.10–3.13 available; providers use the standard `langchain-*` stack.
- The six `.wav` files are supplied by the evaluator; the app ships none but
  validates any uploaded file (RIFF/WAVE magic, size ≤ 25 MB).
- `noise_type` metadata is descriptive; the pipeline does not attempt denoising —
  noisy cases are run through the same pipeline and their results compared
  (graceful failure is acceptable and measured, not masked).
- One tool call max per query (weather or deterministic mock tool); test cases 1–2
  must produce zero tool calls, 3–4 exactly one; the benchmark checks this.
- TTFA for file-driven runs is exact (no playback buffering estimation).

## 14. Known limitations (documented, not hidden)

- TTS streaming boundaries: for providers that return the full clip at once
  (mock TTS, some providers), `tts_first_audio` equals synthesis-complete time;
  the limitation is recorded in the stage event rather than faked.
- STT first-result timestamps depend on provider streaming support; the mock STT
  emits a genuine two-phase (partial → final) signal; batch providers leave it null.
- LangSmith is optional; without a key the platform still runs with local-only
  observability (trace_id stored but not resolvable to LangSmith).
