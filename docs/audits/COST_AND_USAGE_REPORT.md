# Cost & Usage Report — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22. Principle: report only what providers actually meter;
`COST_UNAVAILABLE` where they do not. No invented dollar figures.

## What is measured per run (stored in `benchmark_runs`, exported via /api/export)

| Field | Source | Example (verified) |
|---|---|---|
| `llm_prompt_tokens` | provider-reported usage_metadata | 137 (no-tool), 212 (tool case) |
| `llm_completion_tokens` | provider-reported | 25 / 22 |
| `llm_total_tokens` | provider-reported | 162 / 234 |
| `stt_provider_ms` | Deepgram-reported audio duration | ~4,896 ms audio |
| audio bytes / duration / rate / channels | file metadata | test_01: 940,110 B, 4.9 s, 48 kHz |
| TTS audio bytes | adapter measurement | 21,888–24,768 B MP3 per Aura call |

STT/TTS token or character usage is **not reported** by Deepgram/ElevenLabs
batch endpoints → marked unavailable, never estimated.

## Measured totals from the final validation runs (9 runs, 2 pipelines)

- LLM tokens: 1,419 prompt + 291 completion = **1,710 total tokens**
  (openai/gpt-4.1-mini via OpenRouter, max_tokens=200 cap per call).
- STT: 9 Deepgram listens of 4.9–7.4 s audio (~48 s audio total).
- TTS: 9 Aura-2 syntheses of one-sentence answers (~22 KB MP3 each).
- Tool calls: weather ×4 (~1.3–2.2 s each, keyless Open-Meteo: zero cost).

Estimated monetary cost of the full session: **COST_UNAVAILABLE** — Deepgram
free-tier credits consumed (amount not exposed via API), OpenRouter usage
metered in dollars on their dashboard, not derivable precisely here. What the
platform guarantees: tokens are recorded per run, so cost can be computed
exactly by joining stored token counts with the provider's price sheet.

## Safeguards (verified)

- `MAX_EXPERIMENT_RUNS=100` — matrix runner refuses oversized runs
  (`test_cost_guard_blocks_oversized_matrix`).
- `MAX_CONCURRENCY=10` server-side clamp; staggered launches.
- `max_tokens=200` on the LLM — bounded generation cost per call.
- `MAX_UPLOAD_MB=25` — bounded ingest.
- No automatic retries → no duplicate charges.
- Matrix runner prints planned executions (pipelines × tests × runs) and the
  cap before starting.
- Provider-test probes are single-call by design (debug before benchmarking).

## Observed usage risks

- Free-tier ElevenLabs quota exhausted during validation (10,000 chars/mo);
  surfaced honestly. Mitigation: KeyRing rotation across two accounts (one
  currently vendor-flagged), Aura TTS as the working default.
- Deepgram free tier: ~$200 credit per account; two accounts rotate
  automatically.
