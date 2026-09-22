# AI Usage & Engineering Decision Log

How AI coding tools were used during development of the Voice Agent Latency &
Reliability Observatory — what they accelerated, what they got wrong, what was
rejected, and which decisions, validations, and trade-offs were engineering
calls verified by execution. Everything below is reconstructed from the
repository's actual artifacts: tests, stored benchmark runs, failure records,
audit reports, and code. Claims without repository evidence are **not made**.

---

## 1. Development Philosophy

AI coding tools were used as **development accelerators** — drafting, wiring,
probing, and iterating quickly — while the application's correctness was
established through engineering review and evidence-based iteration:

- every headline claim in the README maps to an executable test or a stored
  benchmark run (see `backend/tests/`, `docs/benchmark-results/`);
- failures were treated as data: failed experiments are kept in the database
  and reported, not deleted (`docs/FAILURE_ANALYSIS.md`);
- when AI-generated code and observed behavior disagreed, behavior won, and
  the discrepancy was recorded (§16).

The repository deliberately documents where AI was wrong — because catching
those errors *was* the engineering work.

## 2. Problem Understanding

The assignment required a voice-agent pipeline whose responsiveness could be
measured, compared across models, and observed live.

**Engineering problem identified:** total-response averages hide where latency
originates, and the metric a voice user perceives first — silence between
finishing a question and hearing the first syllable of the answer — is not any
of the commonly reported numbers (request latency, LLM TTFT, TTS completion).

**Key decision:** define the headline metric as

```
TTFA = timestamp(first output audio sample) − timestamp(end of user input audio)
```

The boundary matters: anchoring at *request start* or *upload start* measures
the harness, not the pipeline; substituting *LLM TTFT* or *TTS completion*
measures the wrong perceptual event. This boundary is enforced in code
(`pipeline.py` anchors; first-chunk arrival recorded as an absolute
`perf_counter` moment) and pinned by the unit test
`test_ttfa_recomputed_from_events_matches_backend`, which re-derives TTFA from
the raw event timeline.

## 3. Architecture Decision

**Initial architecture:** an instrumented benchmark orchestrator — FastAPI
backend, SQLite storage, React dashboard, LangGraph agent, provider adapters.

**Why this and not a live-mic demo:** a file-driven benchmark makes both TTFA
boundaries code-controlled and exactly measurable; an RTC session (LiveKit /
Pipecat) would put the metric inside framework VAD/network timing that isn't
ours to measure. Transports were **rejected** for that reason; the provider
adapters are the same libraries those frameworks would drive.

| Area | AI suggestion | Final engineering decision |
|---|---|---|
| STT/TTS/LLM | env-driven adapters | adapter protocols + factory + strict allowlists (`provider:model[:voice]`) |
| Agent | `create_react_agent` + streaming | accepted; tool calls emitted as timed events |
| Tools | Open-Meteo weather (keyless, deterministic) | accepted; exactly-0 / exactly-1 semantics enforced by tests |
| Benchmark | store results | unique run IDs, per-run JSON artifacts, experiment IDs, cost caps |
| Storage | an ORM + Redis event bus | **rejected** — stdlib sqlite3 (WAL), thread-local connections, committed `schema.sql`, in-process WS |
| Observability | LangSmith env wiring | accepted + transcript hashing (privacy default), trace IDs stored per run |
| Frontend | five-view dashboard | accepted; later redesigned to the DESIGN.md observability contract |

## 4. Model Plug-in Architecture

**Decision:** never hardcode a single STT/TTS provider. The assignment's model
comparison requirement is only meaningful if a spec change actually changes
execution.

- `STT_MODELS` / `TTS_MODELS` catalogs in `backend/app/core/config.py`
  (allowlisted models with per-model timeouts);
- spec grammar `provider:model[:voice]` parsed with a strict regex; unknown
  models are rejected, never silently mapped to a default;
- factories in `backend/app/voice/__init__.py` resolve specs to adapters
  implementing one normalized interface; the engine has no
  `if provider == ...` branches;
- every selected spec is persisted on each run (`stt_spec`, `stt_model`,
  `tts_spec`, `tts_model`, `tts_voice_id`, `llm_spec`, `llm_model`) so any
  stored result can be reproduced;
- missing credentials mark a provider NOT_CONFIGURED and selection returns a
  clear 4xx — no silent fallback, no fake options.

**Why:** without this layer, "choose different models" degenerates into a
dropdown that always calls the same endpoint.

## 5. Multi-Model Support

The engineering work to go from one provider to many (2026-09-22 pass):

- STT: Deepgram `nova-2` / `nova-3` / hosted `whisper-large` **plus**
  ElevenLabs `scribe_v1` — a genuinely different vendor API
  (`api.elevenlabs.io` vs `api.deepgram.com`);
- TTS: Deepgram `aura-2-thalia-en` / `aura-2-andromeda-en` **plus** ElevenLabs
  `eleven_turbo_v2_5` / `eleven_multilingual_v2` / `eleven_flash_v2_5`;
- model metadata (timeouts tuned to measured cold starts), availability
  checks surfaced to the UI, Provider Test panel + `/api/provider-test` for
  pre-benchmark probing, per-model Deepgram STT parameters (whisper-large
  rejects smart_format/punctuate), credential-account provenance in events,
  optional second-account key rotation (`DEEPGRAM2_API_KEY`,
  `ELEVENLABS2_API_KEY`) via a process-shared key ring;
- verified by real executions: same WAV × 4 STT specs; same text × 5 TTS
  specs (see `docs/MULTI_MODEL_TEST_REPORT.md` for measured values and the
  quota failures that are also real evidence).

## 6. Benchmark Design

Key decisions, each enforced in code:

- **same WAV across pipelines** — a test case is fixed audio + metadata;
  the tooling cannot compare different inputs (scientific validity);
- **unique run IDs** — results are appended, never overwritten; matrix runs
  share an `EXP-YYYY-NNNNN` experiment ID;
- **pipeline configuration persistence** — full specs + voice on every row and
  in every artifact (§4);
- **repetitions** — `--runs N` with a hard cap (`MAX_EXPERIMENT_RUNS`) and a
  pre-execution estimate of total external API calls;
- **stage-level timing** — `perf_counter` deltas around each provider call;
  tool latency is the measured duration of the actual tool body;
- **failure recording** — failed runs keep stage, error type, and safe message;
  comparison never averages away failures (p95 only at n ≥ 5).

Comparing different inputs across pipelines would attribute audio-length and
phrasing differences to models; same-input execution is the only fair basis.

## 7. Latency Measurement

The strongest constraint in the system. Reasoning:

- **INPUT_END is the anchor**, because that is when the user stops talking;
  everything before it (upload, leading silence) is measured and reported but
  excluded from TTFA;
- **FIRST_AUDIO is the arrival of the first actual output audio bytes** — not
  TTS completion, not LLM TTFT. LLM TTFT measures reasoning start; TTS
  completion measures the *end* of the reply; only first audio matches what a
  caller perceives;
- timestamps use `perf_counter` (monotonic) deltas around stage boundaries;
  absolute wall-clock values are derived once for display, avoiding
  wall-clock skew in durations;
- overlapping operations are preserved as separate events rather than summed
  (the waterfall renders the real nested timeline, e.g. TOOL inside AGENT);
- providers that cannot report a sub-metric (batch STT partials) store `null`
  — never fabricated.

Verified by: the TTFA recomputation test; the live-view assertion that
`time_to_first_audio_ms` equals the persisted event delta; and cross-checks
during the audit pass.

## 8. Tool Calling

- No-tool cases (TC01/TC02) must produce **0 tool calls**; one-tool cases
  (TC03/TC04) **exactly 1**. Tests enforce both
  (`test_no_tool_queries_call_zero_tools`, tool accounting in the benchmark
  suite) — and caught real bugs (§16).
- The actual tool body executes and is timed (Open-Meteo geocode + forecast;
  0.7–1.6 s real network latency observed), not a stub.
- Tool failure is surfaced as stage `tool` with the safe error; the agent
  continues and the run records honestly.
- Changing STT/TTS providers does not alter tool semantics — verified across
  the multi-provider matrix (0/0/1/1 preserved on correctly-behaving LLMs).

## 9. LangGraph

LangGraph executes the agent for **every** run; there is no bypass path.
Concretely: a `create_react_agent` graph streams
`astream(stream_mode=["updates", "messages"])`, which yields both state
updates (tool calls, final answer) and token messages (real TTFT). Tool nodes
wrap the tool providers, so the graph itself emits the tool events the
benchmark records. Graph behavior is observable per run: node transitions,
tool decision, tool count, termination — no hidden second implementation.

## 10. LangSmith / Observability

Tracing answers "why was this request slow?" after the fact. Each run creates
a `voice_session` trace with `stt` / `agent` (LangGraph runs nest via the
SDK) / `tts` children and a `first_audio` marker; the LangSmith run ID is
stored on the benchmark row (`trace_id`) and rendered as a real link —
"trace unavailable" is shown rather than a fabricated URL. Correlation:
run ID ↔ trace ID ↔ experiment ID ↔ raw JSON artifact. **Privacy:**
transcripts leave the process only as sha256 prefixes unless
`LANGSMITH_SEND_TRANSCRIPTS=true`; audio bytes and secrets never enter spans
(covered by tests).

## 11. Reliability Engineering

Actual reliability work and trade-offs:

- **per-model timeouts** (45 s STT / 180 s for whisper-large cold starts /
  30–90 s TTS), tuned to measured provider behavior, not guesses;
- **no automatic retries** — chosen deliberately: a retry would duplicate
  charges and blur what was actually measured. Failures are recorded with
  stage + safe message instead;
- **failure visibility** — failure stage, error type, safe message, and full
  provenance persist on failed rows; the UI shows failed runs as first-class
  data (the failure-detail screenshot is a real quota failure);
- **chaos probes executed** during the audit: bad credentials → clean 401
  surfacing; malformed audio → early validation failure; provider quota →
  honest failure at the correct stage; empty error strings from httpx
  timeouts → replaced with self-describing safe messages;
- **graceful degradation** — a provider failing never crashes the server;
  other runs and views keep working (verified during the multi-provider pass
  when ElevenLabs quota drained mid-session).

## 12. Concurrency

**Why:** a benchmark runner that is itself a bottleneck, or that mixes
sessions, produces invalid measurements.

- Engine uses **true wave semantics** (a new run starts only when one
  finishes), staggered launches, hard timeouts; levels clamped to
  `VABD_MAX_CONCURRENCY` (10).
- **Tested levels:** 1, 3, 5, and 10 (mock mode); 3 and 5 verified on the real
  paid stack (level 5: 10/10 ok, p95 TTFA 8.4 s vs 6.4 s solo — degradation
  is real and visible). Level-10 external-provider behavior is available in
  the UI but was **not** executed against paid providers at that level, to
  respect quotas.
- **Found and fixed:** a live-run race where the background task could finish
  before WS subscription (buffer replay added; registration before
  scheduling).
- Measured: success rate, TTFA/total distributions, throughput, errors.

## 13. Security Engineering

Actual decisions (full detail: `docs/audits/SECURITY_AUDIT.md`):

- **Secrets**: `.env` only (gitignored); never returned by any API (tests
  assert key absence across responses), never logged (redaction filter),
  never serialized into traces/artifacts; repo-wide and bundle secret scans
  are part of release validation.
- **Uploads**: extension + size caps (HTTP 413), WAV magic-byte validation
  (spoofed content rejected), `relative_to()` traversal checks (replacing a
  bypassable `startswith`), randomized sandboxed filenames, wave-parse
  validation — each covered by a test.
- **Input validation**: strict spec regex; Pydantic whitelisting of enum-ish
  inputs.
- **Prompt/tool safety**: transcripts leave as hashes by default; tool
  outputs are treated as data; no raw secrets can enter prompts via env
  wiring; LangSmith hashing default (`LANGSMITH_SEND_TRANSCRIPTS=false`).
- **Data isolation**: per-session WS channels with replay buffers; isolation
  probe verified Run A never receives Run B events.
- **Consciously out of scope** (documented, not hidden): no authentication /
  rate limiting — single-operator localhost tool.

## 14. Cost & Token Governance

- **Token usage**: LLM `usage_metadata` (`input_tokens` / `output_tokens` /
  `total_tokens`) is captured from the provider and persisted per run
  (verified live: 137/25/162 on a no-tool run; 212/22/234 on a tool run);
  STT/TTS token counts are not exposed by those APIs and are recorded as
  unavailable rather than invented.
- **Safeguards**: `MAX_EXPERIMENT_RUNS` (the runner refuses beyond it),
  pre-run estimate of total external API calls, `MAX_CONCURRENCY` cap,
  `max_tokens=200` bound on LLM replies, upload size cap, **no auto-retries**
  (no duplicate charges).
- **Cost model**: provider prices vary; the system stores the *usage*
  primitives (tokens, call counts, bytes) and leaves $ estimation explicit —
  no fabricated dollar figures anywhere in reports.

## 15. Frontend Engineering

Design intent: an engineering console, not an AI landing page (contract:
`docs/DESIGN.md`; audit: `docs/FRONTEND_DESIGN_AUDIT.md`).

- **Information density over decoration**: semantic-only color, squared
  hairline geometry, monospace for every measured value and ID.
- **TTFA as the hero metric** with an explanatory tooltip; per-run latency
  waterfall built from real event timestamps (FIRST AUDIO tick in success
  green).
- **Evidence, not verdicts**: comparison shows medians with n-counts and a
  factual "◂ lowest TTFA" marker — never "best pipeline".
- **Honesty states**: real trace URLs or "trace unavailable"; honest
  "unavailable" when metadata is missing; failed runs shown as data.
- **Anti-slop rejections**: pill badges, gradient hero, fake sparkline,
  "AI magic" loading animation, fake waveforms — all rejected in the design
  pass (§16 records the slop defaults AI initially proposed).
- **Accessibility**: keyboard-visible focus, `prefers-reduced-motion` kills
  all animation, semantic tables; verified via stylesheet inspection and
  responsive captures (768/390 px).

## 16. Debugging / Where AI Was Wrong

Real, repository-verifiable examples (each: problem → why wrong → how caught →
correction):

1. **TTFA boundary draft.** First draft computed TTFA from TTS-internal
   durations (`tts_completion − tts_first_audio`) — the wrong boundary.
   Caught in design review; replaced with absolute first-chunk-arrival minus
   the input-end anchor.
2. **Mock intent detector matched the system prompt.** It read the whole
   message list, saw "weather" in the prompt, and called the tool on every
   no-tool query. Caught by `test_no_tool_queries_call_zero_tools`; fixed to
   read HumanMessages only.
3. **Path traversal check used `startswith`.** A sibling-prefix directory
   would have passed. Caught in the security pass; replaced with
   `relative_to()`, covered by `test_audio_endpoint_sandboxed`.
4. **FastAPI `Form()` bug dropped upload metadata.** `expected_tool_calls` /
   `noise_type` were ignored — exposed only by the real end-to-end benchmark;
   now covered by `test_upload_persists_metadata_fields`.
5. **Config resolved `.env` from the wrong directory**, so every provider
   silently resolved unavailable and the real benchmark timed out. Caught via
   the availability endpoint; fixed to an absolute project-root path.
6. **Deepgram received headerless PCM** (it needs the RIFF container) — caught
   by a direct probe returning a transcript only after the fix.
7. **Retired model id in the first matrix** (`gemini-2.0-flash-001`): 12/12
   404s; then a 24/24 credit-parameter failure. Both batches kept as evidence;
   fix was live id resolution + `max_tokens` bound → EXP-2026-00003 24/24 ok.
8. **Token-usage capture read the wrong LangChain field**
   (`response_metadata.token_usage` instead of `usage_metadata`) and silently
   stored NULLs — a live run exposed it; fixed and verified (137/25/162).
9. **httpx timeout errors stringify to `""`**, so whisper-large failures were
   persisted with empty error details. Reproduced; fixed with self-describing
   safe messages; 180 s timeout re-verified.
10. **KeyRing rotation was per-adapter-instance** — concurrent runs would
    never actually alternate accounts. Made process-shared; verified.
11. **Stale backend served the UI** during the audit pass, hiding the new
    Scribe/Flash options — caught because the dropdowns are backend-driven;
    restarted and re-verified end-to-end in the browser.
12. **Trace root showed `ttfa=None`** — the runner's `timings` object was a
    different instance from the result's until a late sync; fixed by sharing
    the instance.

No other verified examples are retained in the repository; none are invented.

## 17. Engineering Decisions Made Independently

| Decision | Reason | Trade-off | Result |
|---|---|---|---|
| TTFA = INPUT_END → FIRST_AUDIO | matches the perceptual event; assignment definition | requires exact event instrumentation | headline metric, unit-pinned |
| File-driven benchmark, no RTC transport | both TTFA boundaries code-controlled | less "demo-like" | exact, reproducible measurement |
| Provider registry + strict specs | selection must change execution | more config surface | genuine multi-model comparison |
| No automatic provider retries | measurements stay honest; no duplicate charges | transient blips show as failures | failures are data |
| Batch STT / buffered TTS sub-metrics stored as null | never fabricate | some cells empty | honest schema |
| Privacy default: hashed transcripts to LangSmith | user audio is personal | raw text needs opt-in | privacy by default |
| p95 only at n ≥ 5 | small-sample p95 is fiction | fewer numbers shown | statistics stay meaningful |
| Mock mode with identical code paths | tests/CI without credentials | risk of confusion — mitigated by `mock` labeling everywhere | 35-test offline suite |
| No auth on dashboard | single-operator localhost tool | documented limitation | simplicity |
| 10-run concurrency cap + stagger | respect provider quotas/limits | slower stress ceiling | no provider bans |

## 18. What AI Accelerated

Honestly scoped — AI tools materially sped up:

- boilerplate/scaffolding (FastAPI app, React/Vite app, pytest layout);
- provider API research and first-draft adapters;
- the CDP screenshot driver and probe scripts;
- documentation drafts (this log's early form, audits) — every claim later
  checked against the repository;
- refactoring suggestions during the design-system pass.

Every one of these went through review and, where behavior mattered,
execution. The debugging list in §16 is the record of what review caught.

## 19. Final Engineering Ownership

AI was used as an engineering accelerator; it was not a proxy for judgment.

The final system was validated through: architecture review (documented
boundaries), a 35-test suite including TTFA recomputation and security
assertions, real benchmark executions across 2 STT vendors / 2 TTS vendors /
3 LLMs with persisted artifacts, chaos and concurrency probes, browser-driven
end-to-end verification, secret scans, and a documentation consistency pass.
The evidence trail — tests, stored runs, failure records, trace IDs,
screenshots — is in the repository, and the decision log above shows both the
acceleration and the corrections that made it trustworthy.



## 20. Final Packaging, Technical Report, and GitHub Submission

**Task heading:** final repository packaging, documentation consistency, technical report generation, validation, and GitHub push.

**Where AI helped:** AI read the supplied packaging prompt, inspected the existing implementation and documentation, created `docs/report/` with a LaTeX technical report source, generated report figures from repository evidence, produced `technical_report.pdf` in the local environment, and ran verification commands for tests, builds, PDF content, screenshots, git status, and secret exclusion.

**Where AI was confidently wrong and had to be fixed:** the first PDF workflow assumption was that the bundled `container_tools/mark_artifact_operation_started` script and a system LaTeX compiler would be available. They were not present in this workspace, so the workflow was corrected: the LaTeX source remains the canonical report source, while a small ReportLab builder creates the PDF artifact for this environment. A stale documentation statement in `docs/MODEL_EXPERIMENT_REPORT.md` also still reflected the earlier single-axis model experiment; it was corrected to point to `docs/MULTI_MODEL_TEST_REPORT.md` as the current STT/TTS source of truth.

**What was decided independently:** the final report should not invent missing measurements or claim a perfect validation matrix. It explicitly preserves limitations such as ElevenLabs quota exhaustion, OpenAI TTS being NOT_CONFIGURED, hosted Whisper cold starts, buffered TTS first-audio semantics, and the absence of a claimed real-stack level-10 concurrency run. The GitHub submission keeps `.env`, `.freebuff/`, caches, local databases, and dependency directories excluded.
