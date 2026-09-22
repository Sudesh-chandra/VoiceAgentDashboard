# AI Engineering Audit — Voice Agent Latency & Reliability Observatory

Date: 2026-09-22. Focus: model architecture, LLM behavior, LangGraph reality,
cost/token governance.

## Model architecture

- **STT abstraction**: `STTProvider` protocol (`transcribe → (transcript, extras)`).
  Adapters: Deepgram (nova-2/nova-3/whisper-large), ElevenLabs Scribe, Mock.
  Providers never self-measure; pipeline wraps calls. Different specs hit
  different vendor endpoints/models (verified: distinct latencies/confidences
  per model on the same WAV).
- **TTS abstraction**: `TTSProvider.stream → AsyncIterator[bytes]`; first yielded
  chunk defines FIRST AUDIO. Adapters: ElevenLabs ×3 models, Deepgram Aura ×2.
- **Registry**: static catalogs (`STT_MODELS`/`TTS_MODELS`) with per-model
  timeout metadata + credential-gated availability. Factories enforce
  allowlists — unknown model ⇒ `ProviderUnavailableError` (HTTP 409), never a
  silent default or hidden hardcoded model.
- **KeyRing**: two accounts per provider rotated round-robin (process-shared);
  only the label (`deepgram#2`) is observable. No fake keys, no silent fallback.

## LLM audit

- OpenRouter via ChatOpenAI; temperature 0.2; **max_tokens=200** (bounded output
  — voice answers are one sentence; also avoids OpenRouter 402s where accounts
  cannot afford 64k ceilings).
- **One LLM call per agent decision step; ReAct loop terminates on final
  answer.** Verified: no-tool cases → exactly 1 agent completion; one-tool cases
  → 2 model steps (decision + synthesis) with exactly 1 tool execution between.
  No unbounded retries anywhere (single attempt per provider call; failures are
  recorded, not retried — deliberate to keep measurements honest and avoid
  duplicate charges).
- Prompt: one short system prompt + the transcript. No conversation history
  accumulates across runs (stateless per run — verified: no state leakage in
  isolation tests). Tool outputs truncated before re-entering context.
- Streaming: enabled; TTFT measured from first AI chunk (`llm_ttft_ms`).

## LangGraph audit (real, not decorative)

- `create_react_agent` compiles a real graph; execution streams `updates` +
  `messages` modes; state carries the full message list; tool calls extracted
  from actual ToolMessages.
- Verified in live UI run: test_03 → `lookup_weather({"city":"Bangalore"})`
  executed (1,313 ms tool latency in the waterfall), then agent synthesized the
  answer with real weather data (95.7°F).
- Tool counts: no-tool tests ⇒ 0 executed tools; one-tool ⇒ exactly 1 (asserted
  by automated tests on event pairs start/end — an *executed* tool, not an
  LLM intention).
- Termination: ReAct stops after tool result synthesis; no loops observed in
  ~240 stored runs (max agent steps ≤ 2 for our single-tool cases).

## Token usage / cost governance

- **Captured (new)**: `llm_prompt_tokens`, `llm_completion_tokens`,
  `llm_total_tokens` from provider-reported `usage_metadata` (LangChain v3
  field). Real evidence: no-tool 137/25/162; tool case 212/22/234.
  Persisted in `benchmark_runs`, included in `/api/export` columns.
- STT/TTS usage metadata: Deepgram/Scribe report audio duration (STT) and audio
  bytes (TTS) — captured; character/token counts for STT/TTS are NOT reported
  by these providers → recorded as unavailable, never estimated.
- Cost: no fabricated dollar figures. Guards: `MAX_EXPERIMENT_RUNS` (matrix
  refuses oversized runs), `MAX_CONCURRENCY` (server-clamped), max_upload_mb,
  bounded max_tokens, single-attempt calls. Provider pricing varies by model;
  the platform records what is measurable (tokens) and exposes usage honestly.

## Prompt/context governance

- System prompt is fixed and minimal; transcript is the only user payload.
- No secrets/PII are injected into prompts; LangSmith hashing keeps raw text
  out of traces by default.
- Context is bounded: 1 human turn + tool result + system prompt. No history
  growth across runs.

## Verdict

AI-engineering status: **PASS** — real multi-provider execution, real LangGraph
with enforced tool semantics, provider-reported token accounting, bounded cost
surface.
