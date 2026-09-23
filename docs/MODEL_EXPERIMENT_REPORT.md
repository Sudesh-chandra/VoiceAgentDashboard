# Model Experiment Report

Experiment **EXP-2026-00003** · executed 2026-09-22 via `benchmark.run_matrix`
(cost-capped matrix runner). Every number below is read from persisted runs —
none is estimated.

## 1. Environment

- Backend: FastAPI + LangGraph agent; SQLite persistence; LangSmith tracing on
  (`voice-agent-benchmark` project).
- Audio: the six user-supplied WAVs (`voice/problem 1..6.wav` → test_01..06),
  48 kHz 16-bit stereo, 4.9–7.8 s, sent byte-identical to STT in every run.
- Measurement: `perf_counter` anchored at input-audio end; TTFA = FIRST_AUDIO −
  INPUT_END (verified by automated tests recomputing it from raw events).

## 2. Providers / models tested

- STT: Deepgram nova-2 (both pipelines, identical input)
- LLM: `openai/gpt-4.1-mini` vs `google/gemini-3.8-flash` (via OpenRouter)
- TTS: ElevenLabs `eleven_turbo_v2_5` (both pipelines)
- Tools: weather (Open-Meteo, live) + web search (labeled mock, no Tavily key)

Two earlier attempts are retained honestly: `EXP-2026-00001` (model id
`google/gemini-2.0-flash-001` — 404 "no endpoints found") and `EXP-2026-00002`
(both models 402 "more credits / fewer max_tokens" — fixed by bounding
`max_tokens=200` for short voice replies). Failed experiments stay in the DB.

## 3–7. Runs

| | |
|---|---|
| Pipelines | 2 |
| Test cases | 6 (all six real WAVs) |
| Runs per case | 2 |
| Total executions | 24 |
| Successful | 24 |
| Failed | 0 |

## 8. TTFA statistics (ms)

| Pipeline | median | mean | min | max | n |
|---|---|---|---|---|---|
| deepgram-or-openai/gpt-4.1-mini-elevenlabs | 6093 | 6753 | 4925 | 11385 | 12 |
| deepgram-or-google/gemini-3.8-flash-elevenlabs | 6945 | 8589 | 6019 | 17362 | 12 |

p95 is not reported for these groups: n=12 per pipeline but the runner reports
per-metric groups where several metrics have n<5 → shown as "insufficient
samples" per the honesty rule. (The DB retains everything for larger reruns.)

## 9. Stage latency (medians, ms)

| Stage | gpt-4.1-mini | gemini-3.8-flash |
|---|---|---|
| STT | 3264 | 3145 |
| LLM TTFT | 1113 | 2253 |
| TTS first audio | 792 | 744 |
| Tool | 900 (n=4) | 1671 (n=4) |
| Total response | 6095 | 6946 |

Observation: with identical STT/TTS, the TTFA gap tracks the LLM (TTFT 1113 vs
2253 ms) plus gemini's extra tool round-trips (below). STT is the largest single
stage in both pipelines.

## 10. Tool behavior

Expected tool counts: test_01/02/05/06 = 0 · test_03 = 1 (weather) · test_04 = 1 (search).

| Pipeline | test_01 | test_02 | test_03 | test_04 | test_05 | test_06 |
|---|---|---|---|---|---|---|
| gpt-4.1-mini | 0, 0 | 0, 0 | **1, 1** | **1, 1** | 0, 0 | 0, 0 |
| gemini-3.8-flash | 0, 0 | 0, 0 | **2, 3 ✗** | 1, 1 | 0, 0 | 0, 0 |

- `openai/gpt-4.1-mini`: exactly-one-tool semantics correct on all 24 runs.
- `google/gemini-3.8-flash`: **over-called the weather tool on test_03
  (2 and 3 invocations)** — a real reliability finding this platform exists to
  surface. Counts are actual executions (tool function ran), not LLM intents.
- Failure-mode consequence visible in the data: gemini's test_03 TTFA
  (14126 / 17362 ms) is 2–3× its other cases — extra tool round-trips dominate.

## 11. Noisy audio (test_05 TV-noise, test_06 white-noise)

- Transcription succeeded in all noisy runs at Deepgram confidence 0.996–0.998;
  no preprocessing applied (pass-through by design).
- test_05 STT medians: gpt-4.1-mini pipeline 3197–9155 ms (one 9.2 s outlier —
  same audio, same provider → variance is provider-side, honestly retained),
  gemini pipeline 3009–3129 ms.
- No "better model" claim is made from two noisy samples; the evidence
  (transcripts, confidences, per-run timings) is persisted per run for review.

## 12. Concurrency (same day, real paid stack)

Levels 1 and 5 on `test_02` (10 executions):

| Level | Success | TTFA median | TTFA p95 | Throughput |
|---|---|---|---|---|
| 1 | 2/2 | 5913 ms | 6443 ms | 0.30 rps |
| 5 | 10/10 | 6896 ms | 8425 ms | 1.16 rps |

Degradation under concurrency is real and measurable (+~1 s median, +2 s p95);
no provider errors at level 5. Level 10 available in the UI but not exercised
here to respect paid quotas.

## 13. Provider failures (all retained, none hidden)

| Pipeline | Stage | Error | Count | Cause |
|---|---|---|---|---|
| …-gemini-2.0-flash-001-… | llm | LLMError | 12 | model id retired → 404 (EXP-2026-00001) |
| …-gpt-4.1-mini-… | llm | LLMError | 12 | 402 credit/max_tokens ceiling (EXP-2026-00002) |
| …-gemini-3.8-flash-… | llm | LLMError | 12 | same 402 (EXP-2026-00002) |
| real-dg-or-el | stt | STTError | 12 | pre-fix header bug batch (kept as history) |

## 14. LangSmith trace status

Every successful run stores a `trace_id`; spot-verified via the LangSmith API:
`voice_session` root with nested `stt` / `agent` (LangGraph nodes,
`ChatOpenAI`, tool spans) / `tts` children. Transcripts hashed by default.

## 15. Known limitations

1. TTS adapters now stream incrementally (transport fix verified live on
   Deepgram: first audio precedes completion by ~1.5 s); ElevenLabs' live
   streaming behavior awaits its quota reset (contract-tested, NOT VERIFIED
   live).
2. At the time of `EXP-2026-00003`, this matrix held STT/TTS constant and
   primarily compared LLM behavior. The later multi-model validation pass added
   and executed additional STT/TTS selections; see
   `docs/MULTI_MODEL_TEST_REPORT.md` for the current STT/TTS source of truth.
3. Two repetitions per cell is below what p95 needs — the platform reports
   "insufficient samples" rather than a fake p95; rerun with `--runs 5` for
   distribution-grade stats.
4. Web-search tool results are a labeled mock (no Tavily credential).
