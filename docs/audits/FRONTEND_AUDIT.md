# Frontend Audit — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22. Stack: React 19 + Vite + TypeScript; plain CSS design system
(`styles.css`); no component/UI library — deliberate, data-first.

## Architecture

- `Dashboard.tsx`: 5 tabs (Benchmark / Live Run / Results / Compare /
  Reliability & Concurrency). All data from real API calls (`lib/api.ts`
  typed client); zero hardcoded metrics, zero mock values, zero fake loaders.
- `components.tsx`: Metric, RunStatus, TraceLink, PipelineStrip,
  LatencyWaterfall (built ONLY from real StageEvents), StageTracker, EmptyState.
- Live Run uses WebSocket `/ws/sessions/{id}` with replay buffer so late
  subscribers miss nothing.

## Functional audit (executed in browser via preview driver)

Full chain verified: open dashboard → test cases listed (6 real WAVs, playable)
→ pipeline selectors (STT/LLM/TTS/tool) → provider test (STT ✓ nova-3
2,562.9 ms + transcript; TTS ✓ aura 2,786.9 ms) → run benchmark (test_03) →
"Benchmark complete — 1 run(s) stored" → Results row → detail panel with TTFA
hero metric, waterfall, tool call `lookup_weather({"city":"Bangalore"})`,
transcript, agent response, trace link → Compare tab medians → Reliability
failures visible. Screenshots captured (light-on-dark observability UI, dense
data tables, no marketing content).

## Anti-AI-slop audit: PASS

- No gradients/glassmorphism/glow; flat dark data-tool aesthetic.
- No hero sections, sparkles, robot art, terminal cosplay, or 3D.
- Pills/chips are semantic status badges (ok/fail/warn), not decoration.
- Typography dense and mono for values; data is the hero; whitespace serves
  scanning, not style.
- Every visual element encodes measured data (waterfall bars = real timestamps).

## Accessibility / UX

- Landmarks + roles present (`role=tablist/tab`, `aria-live` log, `role=img`
  waterfall with text alternative, labeled selects/inputs, `htmlFor` ids).
- Keyboard: standard buttons/selects/checkboxes are native and focusable;
  focus states from the browser; no keyboard traps.
- Status colors paired with text/symbols (●/✕/▲), not color-only.
- Empty states explain why + what to do; loading states describe the actual
  stage ("Executing pipeline — STT → agent → TTS").
- Reduced-motion: no CSS animations to reduce (static by design).

## Issues found

1. **Stale backend exposure (caught live)**: the running backend predated the
   multi-model catalog change, so the UI hid Scribe/Flash. Fixed by restarting
   the backend; lesson recorded — config surfaced via API, so process age
   matters. No frontend change required.
2. Long pipeline ids in the Results table truncate by design (title tooltips
   would help; cosmetic only — not fixed to avoid churn).

## Responsive

Layout is grid-based and collapses to single column at narrow widths; tables
scroll horizontally in `tablewrap`. Verified at ~1000px viewport in browser.

## Verdict

Frontend status: **PASS** (professional observability product; no AI slop).
