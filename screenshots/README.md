# Screenshot Evidence Package

All images in this directory are **real captures of the running application**
(headless Edge/Chrome driven over the Chrome DevTools Protocol against the
actual backend at `localhost:8000` — no mockups, no fabricated data). The
capture script is committed at
`backend/scripts/capture_screenshots.py` so the evidence is reproducible:

```bash
# from backend/ with the API (:8000) and frontend (:5173) running
.venv/Scripts/python scripts/capture_screenshots.py "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" ../screenshots
```

Several captures were taken while a **real benchmark was executing against
live providers** (Deepgram STT, OpenRouter LLM, Deepgram Aura TTS); latency
values, stage events, and traces visible in the images are the measured
values of those runs, also persisted in the benchmark store.

---

## 01-dashboard/dashboard-overview.png

Demonstrates:
- Main observability dashboard landing view (Benchmark tab)
- Live provider status chips (configured STT/LLM/TTS, LangSmith project)
- Test-case roster and pipeline configuration in a single control room view

Supports: *GUI frontend deliverable (Phase 9); live observability.*

## 02-test-cases/test-cases-list.png

Demonstrates:
- The six user-recorded WAV test cases (TC01–TC06) with metadata
  (duration, tool expectation: no-tool / one-tool / noisy)

Supports: *test-case management; same-input methodology (Phase 4).*

## 03-pipeline-configuration/pipeline-configuration.png

Demonstrates:
- STT provider/model selector, LLM selector, TTS provider/model selector
- Provider availability indicators (configured vs. not-configured)
- Provider Test panel for validating a provider before a run

Supports: *model plug-in architecture (Phase 3); configurable pipeline.*

## 03-pipeline-configuration/provider-test-stt-result.png

Demonstrates:
- A real single-shot STT provider test (Deepgram `nova-3`) executed from the
  UI: transcript, measured latency, provider/model identification

Supports: *provider health verification before benchmarking (Phase 3/5).*

## 04-live-benchmark/live-benchmark-running.png

Demonstrates:
- Live benchmark execution in progress with real-time stage events
  (input_end → STT → agent → tool → TTS → first_audio)

Supports: *live observability; event-stream evidence (Phase 5/9).*

## 04-live-benchmark/live-benchmark-complete.png

Demonstrates:
- Completed run with persisted summary: TTFA, stage latencies, tool count,
  trace reference

Supports: *TTFA headline metric (Phase 5); end-to-end execution chain.*

## 05-latency-waterfall/latency-waterfall.png

Demonstrates:
- Latency waterfall/timeline for a run: STT, agent, tool, TTS first-audio
  and TTS total segments against the TTFA boundary (input_end → first_audio)

Supports: *stage-level latency measurement (Phase 5).*

## 06-results/results-table.png

Demonstrates:
- Benchmark result table over real stored runs (unique run IDs, specs,
  TTFA, status)

Supports: *benchmark persistence and reproducibility (Phase 4).*

## 06-results/benchmark-result-detail.png

Demonstrates:
- Individual result detail: pipeline configuration provenance (STT/LLM/TTS
  provider+model+voice), timings, tool outcome

Supports: *result reproducibility; model identification in results
(Phase 3/4).*

## 06-results/benchmark-result-metadata.png

Demonstrates:
- Full metadata block of a stored run (specs, experiment id, trace link,
  token usage where the provider exposed it)

Supports: *observability and cost/token tracking (Phase 10).*

## 07-model-comparison/model-comparison.png

Demonstrates:
- Same-test-case comparison across pipelines: per-stage latency side by side

Supports: *model comparison (Phase 6); scientific same-input methodology.*

## 08-reliability/reliability-summary.png

Demonstrates:
- Reliability view: failure rates by stage, availability of providers

Supports: *reliability testing (Phase 7).*

## 09-concurrency/concurrency-panel.png

Demonstrates:
- Concurrency test panel and results (level selection, measured outcomes)

Supports: *basic concurrency testing (Phase 8).*

## 10-langsmith-trace/langsmith-trace-trace-visible.png

Demonstrates:
- Stored run detail with the LangSmith trace reference for the actual
  executed graph (trace id / link visible in result metadata)

Supports: *LangSmith integration; trace-to-benchmark correlation
(Phase 5/10).*

## 11-failures/failure-detail-opened-failed.png

Demonstrates:
- A real failed run opened from Results: failure stage, safe error message,
  full provenance retained (no silent success)

Supports: *failure surfacing (Phase 7); error semantics.*

## 12-responsive/responsive-768-benchmark.png

Demonstrates:
- Benchmark view at tablet width (768 px): controls remain usable, data
  density preserved

Supports: *frontend engineering quality (Phase 9).*

## 12-responsive/responsive-390-dashboard.png

Demonstrates:
- Dashboard at mobile width (390 px): layout reflow without loss of
  critical observability data

Supports: *responsive behavior (Phase 9).*

---

## Capture inventory

| Folder | Files | Status |
|---|---|---|
| 01-dashboard | 1 | captured from live app |
| 02-test-cases | 1 | captured from live app |
| 03-pipeline-configuration | 2 | captured from live app |
| 04-live-benchmark | 2 | captured during/after a real run |
| 05-latency-waterfall | 1 | captured from completed run |
| 06-results | 3 | captured from stored real runs |
| 07-model-comparison | 1 | captured from stored real runs |
| 08-reliability | 1 | captured from stored real runs |
| 09-concurrency | 1 | captured from stored real runs |
| 10-langsmith-trace | 1 | captured from stored real run |
| 11-failures | 1 | real failed run (provider quota) |
| 12-responsive | 2 | 768 px + 390 px viewports |

No screenshot directory is empty and no placeholder/mock imagery exists.
