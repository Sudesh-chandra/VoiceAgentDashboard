# Frontend Design Audit

Date: 2026-09-22. Scope: the existing React/Vite/TS frontend (`frontend/src`) as
built in the implementation phases, reviewed as a design artifact before any
redesign. Verdict: **functionally sound, visually generic — needs a coherent
design system, not a rewrite.**

## Current state

- **Stack:** single `Dashboard.tsx` (535 lines, 6 view components inline), one
  hand-rolled `styles.css` (GitHub-dark-ish token set), `api.ts` typed client,
  WebSocket live events, no router, no UI library, no chart lib.
- **Views:** Benchmark (config + cases + run), Live Session (WS log + KPIs),
  Results table, Compare table, Reliability & Concurrency.

## What is working (must not break)

- Real WebSocket event stream driving the Live view; incremental KPI updates.
- Benchmark run/upload flows, availability-aware provider selects, 409 messaging.
- Results/Compare/Reliability tables fed by real persisted data.
- TypeScript strict build; zero fabricated data anywhere.

## Strengths

- Information is real and dense; no decorative filler exists to remove.
- Monospace already used for log lines and timestamps.
- Dark, low-chroma base is appropriate for long debugging sessions.

## Generic / AI-slop patterns found

1. **Pill badges everywhere** (`border-radius: 12px`) — "AI dashboard" tell.
2. **Uniform rounded cards** for sections and KPIs — identical rounded rectangles
   with no hierarchy between primary (TTFA) and secondary metrics.
3. **KPI tiles without units context or delta** — numbers float with no
   comparison or stage attribution.
4. **Accent-colored section headings** (`h2 { color: var(--accent) }`) — color
   used decoratively rather than semantically.
5. **Hero-sized product title** consuming the first 60px of every tab.
6. **No latency waterfall** — the product's core question ("where did the time
   go?") is only answerable by reading JSON in the log.

## UX problems

- Pipeline config reads as a generic settings form; the STT → AGENT → TTS
  composition is not visualized.
- Tool semantics ("auto decides") buried in a dropdown label.
- No per-run detail view; Results rows dead-end at a truncated trace id.
- Failure rows show `FAIL(stt)` but not error type/detail or recovery hint.
- Empty states are one-line hints; loading states are text-only.
- Trace ids displayed but not actionable (no link, no copy).

## Information hierarchy problems

- TTFA — the assignment's hero metric — has the same visual weight as STT.
- Compare table has 8 pipeline columns with no ordering by run count or recency.
- Reliability mixes summary, breakdown, and concurrency controls in one column.

## Accessibility problems

- Case rows are clickable `div`s (not buttons/labels) — no keyboard path.
- No `:focus-visible` styles beyond browser defaults; contrast of `--muted`
  (#8b949e on #0d1117) is ~4.1:1 — borderline for small text.
- Status conveyed by color/text only in some places; table status column is
  text-only (fine) but color classes rely on nth-child selectors (fragile).

## Responsive problems

- Tables overflow without scroll containers at <900px.
- Grid2 collapses but the live log + KPIs remain cramped on tablet widths.

## Interaction problems

- No visual distinction between running/completed stages in the live timeline
  beyond text color.
- Buttons have inconsistent disabled affordances; no busy indicator besides
  label swap ("Running…").

## Recommended design direction

**"Technical observability workstation"** — instrument-panel density, zero
decorative radius/shadows/gradients, semantic color only, monospace for all
measured values and ids, TTFA promoted to hero metric, per-run latency
waterfall built from the real event timestamps. Full rules in `DESIGN.md`.
Implementation keeps all existing data flow; visual layer only.
