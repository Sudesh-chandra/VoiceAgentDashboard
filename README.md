# Voice Agent Latency & Reliability Observatory

A benchmarking and observability platform for voice-agent pipelines. It runs the
same recorded audio through configurable **STT → LangGraph agent → LLM →
optional tool → TTS** pipelines, measures every stage, and treats one metric as
the headline: **TTFA — Time To First Audio**, from the end of the user's spoken
input to the first sample of synthesized output audio.

The system executes **real provider APIs** (Deepgram, ElevenLabs, OpenRouter)
through a plug-in provider registry, persists complete pipeline provenance with
every result, streams live stage events to a dashboard, and records failures as
first-class data. An offline mock mode with identical code paths supports tests
and CI without credentials.

---

## Contents

1. [Project Overview](#1-project-overview)
2. [Problem Statement](#2-problem-statement)
3. [Why Voice-Agent Latency Matters](#3-why-voice-agent-latency-matters)
4. [Core Pipeline](#4-core-pipeline)
5. [Headline Metric: TTFA](#5-headline-metric-ttfa)
6. [Key Capabilities](#6-key-capabilities)
7. [Architecture](#7-architecture)
8. [Model Plug-in Architecture](#8-model-plug-in-architecture)
9. [Benchmark Methodology](#9-benchmark-methodology)
10. [Test Cases](#10-test-cases)
11. [Observability](#11-observability)
12. [Security](#12-security)
13. [Frontend](#13-frontend)
14. [Project Structure](#14-project-structure)
15. [Setup](#15-setup)
16. [Environment Variables](#16-environment-variables)
17. [Running the Application](#17-running-the-application)
18. [Running Tests](#18-running-tests)
19. [Running Benchmarks](#19-running-benchmarks)
20. [Model Comparison](#20-model-comparison)
21. [Screenshots](#21-screenshots)
22. [Documentation](#22-documentation)
23. [Assignment Phase Mapping](#23-assignment-phase-mapping)
24. [Known Limitations](#24-known-limitations)
25. [Future Improvements](#25-future-improvements)
26. [AI Usage](#26-ai-usage)

---

## 1. Project Overview

The Observatory is a controlled measurement instrument for voice agents, not a
voice product. It answers four engineering questions with stored, reproducible
evidence:

- **How fast is the pipeline, end to end and per stage?** Every run records
  STT latency, agent/LLM latency and TTFT, tool latency, TTS first-audio and
  total latency, TTFA, and total response latency — measured from real
  `perf_counter` boundaries around real provider calls.
- **Does a different model actually change the numbers?** STT and TTS accept
  `provider:model[:voice]` specs. The selected spec travels from the UI through
  the API into the provider factory and the actual HTTP call; the same WAV can
  be executed across every selectable provider and compared like-for-like.
- **Does the agent behave correctly?** The LangGraph agent must produce
  **0 tool calls** on no-tool cases and **exactly 1** on one-tool cases; the
  actual tool body executes and is timed.
- **What happens when things fail?** Provider quota exhaustion, timeouts,
  malformed audio, and tool errors are captured with stage, safe error message,
  and full provenance — never silently converted into success.

Every benchmark run gets a unique run ID, an experiment ID when part of a
matrix, a persisted JSON artifact, and (when LangSmith is configured) a real
trace ID linked to the stored row.

## 2. Problem Statement

Voice-agent quality is dominated by responsiveness, but typical evaluations
report averages of total response time — which hides the stage where the
experience actually breaks down. The assignment problem: build a system where
an engineer can (a) compose a voice pipeline from stages, (b) plug in different
models per stage, (c) run identical inputs through different pipelines, and
(d) see exactly where latency and failures originate, with TTFA — the metric
users perceive first — measured precisely rather than approximated.

## 3. Why Voice-Agent Latency Matters

In a spoken conversation, silence after the user stops talking is dead air.
Users perceive the gap between finishing their question and hearing the first
 syllable of the reply — not the completion of the whole response. A pipeline
that streams audio early but finishes slowly feels better than one that
completes quickly but starts late. That is why the headline metric is
**first audio**, not total latency, and why "TTS completion" or "LLM first
token" cannot substitute for it. Aggregates are reported as median and p95 over
raw stored runs; failures are never hidden inside averages.

## 4. Core Pipeline

```
User Audio (WAV)
      │
      ▼
STT Provider (Deepgram nova-2/nova-3/whisper-large · ElevenLabs Scribe v1)
      │
      ▼
LangGraph Agent  ── tool? ──► lookup_weather (Open-Meteo) / web_search (Tavily)
      │                        (exactly 0 or exactly 1 call, enforced by tests)
      ▼
LLM (OpenRouter: gpt-4.1-mini / gpt-4o-mini / gemini-3.8-flash)
      │
      ▼
TTS Provider (Deepgram Aura-2 Thalia/Andromeda · ElevenLabs turbo/multilingual/flash)
      │
      ▼
FIRST AUDIO ──────────────► TTFA measured here
```

Stage events (`input_end`, `stt_start/end`, `agent_start/end`, `tool_start/end`,
`tts_start`, `tts_first_audio`, `tts_end`, `done`) are emitted to the benchmark
store, the live WebSocket, and LangSmith. The full boundary semantics are in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §5.

## 5. Headline Metric: TTFA

**TTFA = timestamp(first output audio sample) − timestamp(end of user input
audio).**

- The input boundary is the code-controlled moment the uploaded WAV finishes
  playing/being submitted — not request start, not upload start.
- The output boundary is the arrival of the **first actual audio bytes/chunk**
  from the TTS provider — not TTS completion, not LLM first token.
- Providers that return a single buffered payload (all currently configured
  real TTS endpoints) honestly report `tts_first_audio_mode =
  BUFFERED_RESPONSE`, meaning TTFA ≈ full TTS latency for those providers.
  This limitation is stored per run rather than papered over.
- Leading silence in the input is measured from real audio bytes and reported
  separately; it is never counted against TTFA.
- A dedicated unit test recomputes TTFA from the raw event timeline and asserts
  it equals the persisted value (`test_ttfa_recomputed_from_events_matches_backend`).

## 6. Key Capabilities

- **Configurable voice pipeline** — per-stage provider/model/voice selection
  (`provider:model[:voice]` specs), validated against allowlists.
- **Multiple real STT providers/models** — Deepgram `nova-2`, `nova-3`,
  `whisper-large` (hosted), ElevenLabs `scribe_v1` (separate vendor API).
- **Multiple real TTS providers/models** — Deepgram `aura-2-thalia-en`,
  `aura-2-andromeda-en`; ElevenLabs `eleven_turbo_v2_5`,
  `eleven_multilingual_v2`, `eleven_flash_v2_5`. `openai:tts-1` is listed
  NOT_CONFIGURED without a key — never faked.
- **Configurable LLM** — OpenRouter models (existing behavior preserved);
  per-run token usage captured from the provider and persisted.
- **Benchmark execution** — batch runs over selected test cases, experiment
  IDs, per-run JSON artifacts, cost/run caps.
- **Same test case across pipelines** — comparison is by construction
  same-input; the Compare tab aggregates per pipeline over raw runs.
- **Stage-level latency + TTFA** — measured boundaries, waterfall timeline in
  the UI, event timeline persisted per run.
- **Reliability testing** — failure-rate-by-stage summary; provider health
  probes; honest failure recording with safe messages.
- **Concurrency testing** — true wave semantics, levels 1/5/10 (hard-capped),
  success rates, latency distributions, throughput.
- **Live observability** — WebSocket stage events rendered as they happen.
- **LangGraph** — the agent executes as a real graph; tool decisions are
  observable; no bypass path.
- **LangSmith** — full traces per run with trace IDs stored on benchmark rows.
- **Failure analysis** — failure stage, error type, safe message, provenance
  retained on failed rows.
- **Security controls** — `.env`-only secrets, upload sandboxing with magic-byte
  validation, path traversal protection, redacted logs, secret-leak tests.
- **Cost/token tracking** — LLM input/output/total tokens persisted per run
  where the provider reports them; run-count caps guard benchmark spend.

## 7. Architecture

Full architecture, component responsibilities, data/control/observability flow,
and every documented assumption: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
Visual design system: [docs/DESIGN.md](docs/DESIGN.md).

```
Frontend (React + Vite)  ──REST/WS──►  FastAPI server
                                        ├─ benchmark engine (runs, caps, artifacts)
                                        ├─ concurrency engine (wave semantics)
                                        ├─ pipeline (stage orchestration + events)
                                        │    ├─ STT factory ──► Deepgram / ElevenLabs adapters
                                        │    ├─ LangGraph agent ──► OpenRouter LLM (+ tools)
                                        │    └─ TTS factory ──► Deepgram / ElevenLabs adapters
                                        ├─ observability (metrics, LangSmith wiring)
                                        └─ storage (SQLite: runs, events, artifacts)
```

## 8. Model Plug-in Architecture

Providers are registered in catalogs ([backend/app/core/config.py](backend/app/core/config.py))
and resolved through factories ([backend/app/voice/\_\_init\_\_.py](backend/app/voice/__init__.py))
implementing a normalized adapter interface — the engine contains no
`if provider == ...` branches. Specs are parsed with a strict regex, validated
against per-provider model allowlists, and rejected with a clear error when
unknown or unconfigured (never silently mapped to a default). Credentials are
read from the environment only; a missing key marks a provider NOT_CONFIGURED.

Selected specs are persisted on every run (`stt_spec`, `stt_model`, `tts_spec`,
`tts_model`, `tts_voice_id`, `llm_spec`, `llm_model`, plus credential-account
provenance) and included in exports — any stored result can be reproduced.
The catalog with verified status per model: **[docs/MODEL_CATALOG.md](docs/MODEL_CATALOG.md)**.

## 9. Benchmark Methodology

- **Same input, many pipelines.** A test case is a fixed WAV + metadata; the
  benchmark executes it unchanged across pipeline configurations. Comparing
  different inputs would be scientifically invalid and is not possible in the
  tooling.
- **Unique run IDs.** Every run is a new row; results are never overwritten.
  Matrix runs share an `EXP-YYYY-NNNNN` experiment ID.
- **Repetitions.** The matrix runner supports `--runs N` per combination with a
  hard cap (`MAX_EXPERIMENT_RUNS`) and a printed pre-execution estimate of
  total external API calls.
- **Stage timing.** `perf_counter` deltas around each provider call; tool
  latency is the measured duration of the actual tool body.
- **Honest nulls.** Sub-metrics a provider cannot report (e.g. streaming STT
  partials from batch APIs) are stored as `null` and documented.
- **Raw artifacts.** Each run also writes a JSON artifact to
  `docs/benchmark-results/{run_id}.json`; aggregation is computed at query time
  from raw rows (median; p95 only when n ≥ 5).

## 10. Test Cases

Six user-recorded WAV files (committed at ~1.9 MB total; decision recorded in
[docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md)) with registered metadata:

| Case | Audio | Expected agent behavior |
|---|---|---|
| TC01 (`test_01.wav`) | clear | **0 tool calls** |
| TC02 (`test_02.wav`) | clear | **0 tool calls** |
| TC03 (`test_03.wav`) | clear | **exactly 1 tool call** (weather) |
| TC04 (`test_04.wav`) | clear | **exactly 1 tool call** (weather/search) |
| TC05 (`test_05.wav`) | background noise | processed as-is; behavior measured |
| TC06 (`test_06.wav`) | background noise | processed as-is; behavior measured |

Noisy cases are never denoised — the pipeline processes them as recorded and
their results (including graceful failures) are measured and visible. Verified
tool semantics: 0/0/1/1 tool counts across TC01–TC04 on correctly-behaving LLMs
(a cross-model finding: `gemini-3.8-flash` over-calls tools — documented in
[docs/MODEL_EXPERIMENT_REPORT.md](docs/MODEL_EXPERIMENT_REPORT.md)).

## 11. Observability

For any run an engineer can determine which test, which pipeline, which
STT/LLM/TTS model, whether a tool ran (which one, how long), every stage
timestamp, TTFA, where a failure occurred, and the corresponding LangSmith
trace. Stage events are triple-written: benchmark store (queryable), live
WebSocket (dashboard), LangSmith (trace tree with `stt`/`agent`/`tts` spans and
a `first_audio` marker). Transcript privacy: hashes unless
`LANGSMITH_SEND_TRANSCRIPTS=true`; audio bytes are never sent; no secrets are
serialized into spans.

## 12. Security

- **Secrets**: loaded from `.env` (gitignored) only; never returned by any API
  (verified by tests that assert key absence across all responses); never
  logged (redaction filter); never serialized into traces or artifacts.
- **Uploads**: extension + size limits, WAV magic-byte validation (spoofed
  content rejected), randomized sandboxed filenames, path traversal blocked —
  each covered by tests.
- **Input validation**: strict model-spec regex; unknown models/unconfigured
  providers rejected with 4xx, not silently substituted.
- **No auth**: the dashboard binds localhost for single-operator use; this is a
  documented limitation, not a hidden one. See
  [docs/audits/SECURITY_AUDIT.md](docs/audits/SECURITY_AUDIT.md).

## 13. Frontend

A technical observability workstation, deliberately not an "AI dashboard":
semantic-only color, squared hairline geometry, monospace for every measured
value, **TTFA as the hero metric**, per-run latency waterfall from real event
timestamps, factual comparison labels ("lowest observed TTFA", never "best"),
trace links that render "trace unavailable" rather than fabricated URLs,
keyboard-visible focus, `prefers-reduced-motion` support. Views: Benchmark
(configure + run + provider test), Live Run, Results (detail with full
provenance), Compare, Reliability & Concurrency. Design contract:
[docs/DESIGN.md](docs/DESIGN.md); audit trail:
[docs/FRONTEND_DESIGN_AUDIT.md](docs/FRONTEND_DESIGN_AUDIT.md),
[docs/audits/FRONTEND_AUDIT.md](docs/audits/FRONTEND_AUDIT.md).

## 14. Project Structure

```
VoiceAgentDashboard/
├── backend/
│   ├── app/                  FastAPI server, pipeline, engines, observability,
│   │                         storage, voice providers (STT/TTS/LLM/tools)
│   ├── benchmark/            matrix runner (run_matrix.py)
│   ├── scripts/              seed_test_cases, smoke_live, provider_matrix,
│   │                         capture_screenshots (CDP)
│   └── tests/                pytest suite (35 tests)
├── frontend/                 React + Vite dashboard
├── data/                     local runtime state (DB + uploads gitignored;
│   └── test_cases/           the six committed WAVs + metadata.json)
├── docs/                     architecture, methodology, reports (see §22)
│   ├── audits/               engineering audit reports
│   ├── benchmark-results/    raw per-run JSON artifacts
│   └── report/               LaTeX technical report + compiled PDF
├── screenshots/              real UI captures + README (see §21)
├── AI_USAGE.md               AI usage & engineering decision log
├── README.md                 this file
└── .env.example              required environment template (no secrets)
```

## 15. Setup

Full setup guide: **[docs/ENVIRONMENT_SETUP.md](docs/ENVIRONMENT_SETUP.md)**.

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\activate                 # Windows (.venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy ..\.env.example ..\.env           # then edit .env (see §16)

# Frontend (second terminal)
cd frontend
npm install
npm run dev                            # http://localhost:5173
```

The six WAV test cases are already registered in `data/test_cases/`. To add
your own: Benchmark tab → Upload Test Case, or
`python scripts/seed_test_cases.py <folder>`.

## 16. Environment Variables

Copy `.env.example` to `.env`. **Actual credentials must never be committed** —
`.env` is gitignored and secret-leak tests verify responses stay clean.

| Variable | Purpose |
|---|---|
| `DEEPGRAM_API_KEY` (+ optional `DEEPGRAM_API_KEY_2`) | Deepgram STT (nova-2/nova-3/whisper-large) **and** Aura-2 TTS; second key enables account rotation |
| `ELEVENLABS_API_KEY` (+ optional `ELEVENLABS_API_KEY_2`), `ELEVENLABS_VOICE_ID` | ElevenLabs TTS (turbo/multilingual/flash) **and** Scribe v1 STT |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | LLM (default `openai/gpt-4.1-mini`) |
| `OPENAI_API_KEY` | optional; OpenAI TTS-1 stays NOT_CONFIGURED without it |
| `TAVILY_API_KEY` | web_search tool (deterministic labeled mock executes without it) |
| `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` | LangSmith tracing |
| `LANGSMITH_SEND_TRANSCRIPTS` | privacy: `false` (default) = transcripts hashed |
| `MOCK_MODE` | deterministic offline providers; identical code paths |
| `VABD_MAX_UPLOAD_MB`, `VABD_MAX_CONCURRENCY` | upload cap (25 MB), concurrency hard cap (10) |

## 17. Running the Application

```bash
cd backend && .venv/Scripts/python -m uvicorn app.server:app --port 8000
cd frontend && npm run dev        # open http://localhost:5173
```

Dashboard → **Benchmark** tab → select test cases → pick STT/LLM/TTS/tool →
RUN BENCHMARK. **Live Run** tab executes one case with a WebSocket event
stream. **Results** shows stored runs (open a row for full provenance and the
latency waterfall); **Compare** aggregates per pipeline; **Reliability &
Concurrency** runs reliability summaries and concurrency levels. A repeatable
live smoke test: `python scripts/smoke_live.py`.

## 18. Running Tests

```bash
cd backend
.venv/Scripts/python -m pytest tests/ -v
```

35 tests cover TTFA recomputation from raw events, stage/tool timing and
accounting, provider specs/factories (selection must change execution), upload
security (magic-byte spoofing, oversize, traversal), secret-leak assertions,
log redaction, LangSmith privacy hashing, benchmark persistence + artifacts,
comparison aggregation, reliability summary, and concurrency (structure +
clamping). CI-equivalent offline mode: `MOCK_MODE=true pytest tests/ -q`.

## 19. Running Benchmarks

UI: Benchmark tab (per-run) / Reliability & Concurrency tab (levels).
CLI matrix (same WAVs × pipeline combinations × repetitions, cost-capped):

```bash
cd backend
.venv/Scripts/python -m benchmark.run_matrix \
  --tests test_01,test_02,test_03 \
  --stt "deepgram:nova-3,deepgram:nova-2" \
  --llm "openrouter:openai/gpt-4.1-mini" \
  --tts "deepgram:aura-2-thalia-en,elevenlabs:eleven_turbo_v2_5" \
  --runs 2 --max-experiments 100 --out ../docs/benchmark-results/EXP.json
```

The runner refuses to exceed `MAX_EXPERIMENT_RUNS`, assigns experiment IDs, and
prints honest per-pipeline aggregates. Single-provider validation (same WAV
through every STT; same text through every TTS):
`python scripts/provider_matrix.py --stt --tts --wav test_01`.

## 20. Model Comparison

The Compare tab aggregates stored runs per pipeline (median/p95 over raw data;
failures listed, not averaged away). Meaningful comparisons require identical
inputs and differing specs — the tooling enforces the first and makes the
second explicit in every row. Measured cross-model findings (real runs, stored
artifacts): nova-3 was consistently the fastest STT (≈2.6–3.5 s) with the
highest confidence on clean audio; hosted whisper-large matched transcripts but
exhibited 48–130 s cold starts; Aura-2 TTS first audio ≈2.6–2.9 s;
`gemini-3.8-flash` over-invoked tools on one-tool cases. Full data:
[docs/MULTI_MODEL_TEST_REPORT.md](docs/MULTI_MODEL_TEST_REPORT.md),
[docs/MODEL_EXPERIMENT_REPORT.md](docs/MODEL_EXPERIMENT_REPORT.md). No
universal "best model" is claimed; measurements are time-, account-, and
load-dependent.

## 21. Screenshots

Real captures of the running application (headless CDP against the live
backend, mid-benchmark in several cases): **[screenshots/](screenshots/README.md)** —
12 categories from dashboard overview to responsive layouts, each documented
with what it demonstrates and which requirement it supports. Reproducible via
`backend/scripts/capture_screenshots.py`.

## 22. Documentation

| Document | Content |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | component responsibilities, data/control/observability flow, boundary semantics, assumptions |
| [docs/MODEL_CATALOG.md](docs/MODEL_CATALOG.md) | every selectable model, verified status, credential source |
| [docs/ENVIRONMENT_SETUP.md](docs/ENVIRONMENT_SETUP.md) | setup, keys, troubleshooting |
| [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | assumption register with requirement/decision labeling |
| [docs/MODEL_VALIDATION_PLAN.md](docs/MODEL_VALIDATION_PLAN.md) | validation methodology |
| [docs/MODEL_EXPERIMENT_REPORT.md](docs/MODEL_EXPERIMENT_REPORT.md) | cross-LLM experiment results |
| [docs/MULTI_MODEL_TEST_REPORT.md](docs/MULTI_MODEL_TEST_REPORT.md) | multi-provider STT/TTS measured results |
| [docs/FAILURE_ANALYSIS.md](docs/FAILURE_ANALYSIS.md) | observed failures and handling |
| [docs/SECURITY.md](docs/SECURITY.md) | security model |
| [docs/DESIGN.md](docs/DESIGN.md) | frontend design system contract |
| [docs/audits/ENGINEERING_AUDIT.md](docs/audits/ENGINEERING_AUDIT.md) | full engineering audit (+ 6 sibling audit reports) |
| [docs/report/technical_report.pdf](docs/report/technical_report.pdf) | compiled LaTeX technical report (11 phases) |
| [AI_USAGE.md](AI_USAGE.md) | AI usage & engineering decision log |
| [docs/audits/FINAL_ENGINEERING_REPORT.md](docs/audits/FINAL_ENGINEERING_REPORT.md) | audit-pass final report + scorecard |

## 23. Assignment Phase Mapping

| Phase | Deliverable | Where |
|---|---|---|
| 1 — Problem definition | overview, problem, metric definition | README §1–5 |
| 2 — Architecture diagram | component + data flow diagrams | README §4/7, docs/ARCHITECTURE.md, report Phase 2 |
| 3 — Model plug-in architecture / API / data model | provider registry, specs, provenance schema | README §8, docs/MODEL_CATALOG.md, report Phase 3 |
| 4 — Benchmark execution / results | runner, artifacts, stored results | README §9/19, docs/benchmark-results/, report Phase 4 |
| 5 — Latency measurement | TTFA + stage methodology, measured values | README §5/9, report Phase 5 |
| 6 — Model comparison | same-input cross-pipeline comparison | README §20, report Phase 6 |
| 7 — Reliability testing | failure semantics, chaos probes, summary view | README §6/11, docs/FAILURE_ANALYSIS.md, report Phase 7 |
| 8 — Basic concurrency testing | wave engine, level results | README §19, report Phase 8 |
| 9 — Frontend / observability dashboard | five-view dashboard, live events, waterfall | README §13/17, screenshots/, report Phase 9 |
| 10 — Final summary | verified results, limitations, security | report Phase 10, docs/audits/FINAL_ENGINEERING_REPORT.md |
| 11 — Source code | this repository (see report Phase 11 for URL status) | — |

## 24. Known Limitations

- **Buffered TTS first audio**: currently configured real TTS endpoints return
  one payload, so `tts_first_audio` equals completion for them; the mode is
  stored per run (`tts_first_audio_mode = BUFFERED_RESPONSE`). No streaming
  chunk endpoint was available among configured credentials.
- **ElevenLabs account quota**: free-tier accounts drained mid-validation;
  earlier verified runs (turbo: 760 ms first audio; Scribe: 2,013 ms
  transcript) are documented in [docs/MULTI_MODEL_TEST_REPORT.md](docs/MULTI_MODEL_TEST_REPORT.md).
  After the quota reset the code paths execute unchanged; failures surface with
  the provider's own reason.
- **Hosted whisper-large cold starts** of 48–130 s under provider load
  (timeout 180 s, measured and recorded).
- **Batch STT APIs** expose no partial-result timestamp; `stt_first_result_ms`
  is stored `null` for real providers (mock STT emits a real two-phase signal).
- **No LiveKit/Pipecat transport** — deliberate scope; adapters are the same
  libraries those frameworks would drive.
- **No authentication** on the dashboard (single-operator, localhost binding).
- **Concurrency capped at 10** with staggered launches to respect provider
  rate limits.
- **Tool failures in the search tool without `TAVILY_API_KEY`** run a
  deterministic labeled mock (call count/latency real; snippets labeled mock).

## 25. Future Improvements

- Streaming STT over WebRTC for true partial timestamps on real providers.
- A streaming/chunked TTS endpoint (e.g. ElevenLabs websocket stream) for
  genuine sub-completion first audio on real credentials.
- Persistent pipeline presets and scheduled A/B runs.
- Optional LiveKit transport adapter for interactive sessions.
- LLM-as-judge response-quality scoring alongside latency.
- Optional auth layer if the dashboard is exposed beyond localhost.

## 26. AI Usage

AI coding tools were used as development accelerators under engineering
review; architecture, metric definitions, benchmark methodology, security
boundaries, and validation were engineering decisions, and every headline
claim in this README is backed by an executable test or a stored run. The
complete decision log — including documented cases where AI-generated code was
wrong and how it was caught — is **[AI_USAGE.md](AI_USAGE.md)**.
