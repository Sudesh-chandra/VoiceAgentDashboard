# DESIGN.md — Visual System Contract

**Direction: technical observability workstation.** The product is an
instrument for measuring voice-agent latency — the UI should feel like a
profiler, not a marketing site. Every visual decision below is a rule, not a
suggestion. The data is the hero; chrome is neutral and quiet.

## 1. Color roles

All hex values on the dark surface. Color is **semantic only** — never
decorative. If a color doesn't encode meaning (status, stage, focus, or
selection), it must not be used.

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0b0e12` | app background |
| `--surface` | `#11151b` | panels/sections |
| `--surface-2` | `#161b23` | raised rows, table headers, log |
| `--border` | `#232a33` | default 1px borders |
| `--border-strong` | `#333d49` | hovered borders |
| `--text` | `#e2e8f0` | primary text (contrast ≥ 12:1) |
| `--text-2` | `#9aa7b4` | secondary/metadata (contrast ≥ 5.5:1) |
| `--text-3` | `#6b7683` | disabled, faint captions (never < 3:1 for meaningful text) |
| `--accent` | `#4da3ff` | interactive: links, active tab, focus, selection |
| `--ok` | `#41d47f` | success, completed stages |
| `--warn` | `#e0a437` | degraded, warnings, mock/label notices |
| `--err` | `#f2555a` | failures only |
| `--stage-stt` | `#6cb6ff` | STT stage identity (chips, waterfall) |
| `--stage-agent` | `#c39bff` | agent/LLM stage identity |
| `--stage-tool` | `#ffab70` | tool stage identity |
| `--stage-tts` | `#7ee0a3` | TTS stage identity |

No gradients. No glows. No colored section headings — headings are text-colored.

## 2. Typography

- **UI face:** Inter, `ui-sans-serif`, system-ui fallback.
- **Measured face:** JetBrains Mono / ui-monospace for **every measured value,
  id, model identifier, timestamp, and code**. Numbers align in tables via
  `font-variant-numeric: tabular-nums`.
- Type scale (rem): `0.6875` caption · `0.75` metadata · `0.8125` body-small /
  table · `0.875` body · `1` section title · `1.15` page title · `1.6` hero
  metric. Nothing larger; no display sizes.

## 3. Geometry

- **Radius:** 0 for panels, tables, inputs, buttons; **2px** on chips/badges
  only. Radius communicates nothing here — squared corners read as instrument.
- **Borders:** 1px `--border` everywhere; the layout is drawn with hairlines,
  not shadows. Single exception: the hero TTFA value gets no border treatment
  at all — scale does the work.
- **Shadows:** none.
- **Spacing:** 4px base — 4 / 8 / 12 / 16 / 24 / 32. Panels pad 16, sections
  separate by 16, related rows by 4–8.

## 4. Layout

- Max content width 1280px, centered, 20px gutters.
- Top bar: product name (compact, 1.15rem), provider status, env indicators —
  one 48px row, sticky-off.
- Tabs are underline-style (2px `--accent` underline + text brightening). No
  pill nav, no filled active tab.
- Tables: full-bleed inside their panel, header row `--surface-2` with
  `text-transform: uppercase; letter-spacing: 0.04em; font-size: 0.6875rem`
  labels, numeric columns right-aligned in mono, 6px cell padding, horizontal
  scroll wrapper for narrow viewports.

## 5. Components

- **Metric** — label (uppercase caption) over value (mono, 1.6rem for hero,
  1rem secondary). Hero TTFA value in `--text`; stage metrics in `--text-2`
  until complete. Never colored unless status semantics apply.
- **StatusBadge** — 2px radius chip: icon glyph + label + color
  (`● ok` / `▲ warn` / `✕ fail`). Never color alone.
- **LatencyWaterfall** — horizontal bars from real event timestamps; one row
  per stage; bar length = stage duration on shared end-of-input timeline; right
  gutter mono ms values; stage colors from the stage palette; FIRST AUDIO row
  marked with `--ok` tick. Requires real events; renders nothing otherwise.
- **PipelineStrip** — `STT → AGENT → TTS` composition with provider names and
  availability dots; the tool slot renders `auto · weather+search` or `—`.
- **TraceLink** — real URL only (`https://smith.langchain.com/<project>/t/<id>`),
  mono id, external-link glyph; copy affordance; renders `unavailable` state
  when absent. Never fabricated.
- **EmptyState** — what's missing + why + what to do, `--text-2`, no
  illustrations.
- **LoadingState** — stage-specific text ("Running STT…"), thin indeterminate
  bar in `--accent` (1px) under the label; disabled controls keep labels.

## 6. Interaction states

- **Focus:** `outline: 2px solid var(--accent); outline-offset: 2px` via
  `:focus-visible`, on every interactive element.
- **Hover:** border `--border-strong`; rows `--surface-2`. No transforms, no
  elevation.
- **Active/selected:** 1px `--accent` border + 6% accent background wash.
- **Disabled:** `--text-3` content, dashed border, `cursor: not-allowed`.

## 7. Motion

Motion communicates state transitions only: live-event arrival (new log line
fades 120ms), stage completion (waterfall bar draws 200ms), panel expand.
Durations ≤ 200ms, easing `ease-out`. Under `prefers-reduced-motion: reduce`
all transitions/animations are disabled. No looping/particle/decorative
animation, ever.

## 8. Tables & data rules

- Numeric right-aligned mono; text left-aligned sans.
- `—` for absent values; `unavailable` (not blank, not zero) for unmeasurable.
- Success/failure: StatusBadge + text; failure rows keep the stage in the badge
  and error type in the detail line.
- Sticky header allowed for >12-row tables; sorting on click for run/ttfa
  columns; no chart without a table twin when exact values matter.

## 9. Voice & microcopy

Precise, instrument-grade: "TTFA 842 ms", "TTS request failed after 10 s —
retry or check ELEVENLABS_API_KEY", "Benchmark running". No marketing voice,
no emoji in labels (status glyphs ● ▲ ✕ are glyphs, not emoji), no
exclamations.

## 10. Responsive rules

- ≥1100px: two-column layouts.
- 700–1100px: single column; tables scroll horizontally inside their panel.
- <700px: nav tabs scroll horizontally; KPI metrics wrap 2-up; the waterfall
  keeps its timeline and shrinks labels; nothing hides data.
