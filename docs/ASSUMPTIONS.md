# Assumptions

Distinguishing **ASSIGNMENT REQUIREMENT** (mandated), **ENGINEERING ASSUMPTION**
(judgment call within the mandate), and **IMPLEMENTATION DECISION** (concrete
choice).

## Test data

- **REQUIREMENT:** six user-supplied `.wav` files; no synthetic stand-ins for the
  official benchmark. The `voice/` folder contains `problem 1..6.wav` (48 kHz,
  16-bit stereo PCM, 4.9–7.8 s) — treated as the official six inputs, imported
  byte-identical as `test_01..test_06.wav`.
- **ENGINEERING ASSUMPTION:** slot order maps `problem 1→test_01 … problem
  6→test_06`, with the assignment's category pattern (1–2 no tool, 3–4 one
  tool, 5–6 noise). STT transcripts confirm the mapping is consistent
  (test_03 = weather query, test_04 = search query, test_05/06 = noise cases).
- **IMPLEMENTATION DECISION:** `data/test_cases/metadata.json` records id,
  name, description, expected_tool_calls, noise_type, expected_behavior, and
  the source filename; upload form fields allow overriding for user-added cases.

## Latency definitions

- **REQUIREMENT:** TTFA = end of user spoken input → first sample of output
  audio. Implemented exactly: `perf_counter` anchored after audio decode,
  stopped at arrival of the first TTS chunk.
- **ENGINEERING ASSUMPTION:** with file-driven input the "end of user audio"
  boundary is the moment decoding completes (leading silence is measured from
  the bytes and reported separately, never subtracted). A live-mic deployment
  would anchor at VAD end-of-speech.
- **IMPLEMENTATION DECISION:** stage clocks are `perf_counter` deltas (monotonic,
  sub-ms); wall-clock ISO timestamps ride along for correlation. No overlap
  double-counting: every stage is measured around its own call; TTFA/total come
  from absolute timestamps, never sums.

## Providers

- **ENGINEERING ASSUMPTION:** OpenRouter is the LLM path (`openai/gpt-4o-mini`
  default; any OpenRouter model id works); Deepgram nova-2 for STT; ElevenLabs
  turbo v2.5 for TTS — per the environment spec supplied for this phase.
- **ENGINEERING ASSUMPTION:** the provided OpenAI key matches a known placeholder
  pattern and is treated as NOT configured; OpenAI adapters remain but are
  inactive without a real key.
- **IMPLEMENTATION DECISION:** ElevenLabs voice defaults to the premade
  `JBFqnCBsd6RMkjVDRZzb` (free plans cannot use library voices — verified);
  overridable via `ELEVENLABS_VOICE_ID`.
- **IMPLEMENTATION DECISION:** providers without credentials are reported
  unavailable (HTTP 409 if selected). Mocks run only under `MOCK_MODE=true`
  (tests/CI) and are labeled `mock` in results. Nothing silently falls back.

## Tools

- **REQUIREMENT:** one tool per tool-query, no MCP. Weather uses Open-Meteo
  (keyless); search uses Tavily when `TAVILY_API_KEY` is present.
- **ENGINEERING ASSUMPTION:** without a Tavily key, search runs a deterministic
  mock tool (labeled) so the one-tool flow remains benchmarkable; this is
  disclosed in results (tool name `web_search` via `MockSearchTool`) and in
  this file.
- **IMPLEMENTATION DECISION:** `auto` tool mode binds weather + search and lets
  LangGraph decide per query; expected vs actual tool counts are checked in
  tests.

## Concurrency

- **ENGINEERING ASSUMPTION:** safe levels are 1 / 5 / 10 (cap configurable,
  hard-clamped ≤ 10) with staggered launches; verified at levels 1 and 3 on the
  paid stack to respect provider quotas. Concurrency multiplies provider usage
  (ElevenLabs characters, Deepgram seconds) — deliberate smallness is a
  cost/limit-respect decision.

## Observability & privacy

- **REQUIREMENT:** LangSmith traces for every interaction; no secrets in traces.
- **ENGINEERING ASSUMPTION:** transcripts are sensitive-by-default — sent as
  sha256 prefixes unless `LANGSMITH_SEND_TRANSCRIPTS=true`. Audio never leaves
  the process except to the STT provider (which must receive it to function).
- **IMPLEMENTATION DECISION:** tool args (city/query) are sent in the clear —
  they are the benchmark's stimulus, not user secrets; documented in SECURITY.md.

## Security posture

- **ENGINEERING ASSUMPTION:** single-user local prototype → no auth on the API;
  CORS restricted to the dev origin; bind defaults documented. Any shared
  deployment must add auth (SECURITY.md "Remaining recommendations").
- **IMPLEMENTATION DECISION:** uploads are validated (RIFF/WAVE magic + wave
  parse + size cap enforced while streaming + sanitized generated filenames) and
  stored in `data/test_cases/`; path resolution is `relative_to`-sandboxed.

## Environment

- **REQUIREMENT:** env-driven configuration; `.env` gitignored; `.env.example`
  committed with blanks only; `SECRET_KEY` generated locally by the user.
- **IMPLEMENTATION DECISION:** `.env` is resolved from the project root
  (absolute), not the process cwd — a previous bug (config resolving
  `backend/` as root) motivated an explicit, tested path.

## Multi-provider accounts (final audit pass)

- **IMPLEMENTATION DECISION:** two accounts per provider (`DEEPGRAM2_API_KEY`,
  `ELEVENLABS2_API_KEY`) are rotated round-robin by a process-shared `KeyRing`
  to spread free-tier quota; only a non-secret label (`deepgram#2`) is recorded.
  Account #2 (ElevenLabs) was vendor-flagged `detected_unusual_activity` —
  assumed unusable until the vendor clears it; failures surface honestly.
- **ENGINEERING ASSUMPTION:** measuring LLM token usage from the
  provider-reported `usage_metadata` (LangChain v3) is authoritative; where a
  provider does not report usage (Deepgram/Scribe TTS/STT character counts),
  the metric stays NULL — never estimated.
- **ENGINEERING ASSUMPTION:** hosted whisper-large cold starts (48–130 s
  measured) justify a 180 s timeout; all other providers keep shorter bounds.
