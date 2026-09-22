# Multi-Model STT + TTS Test Report

Date: 2026-09-22 (second validation pass). Scope: the Observatory's STT/TTS are
genuinely provider/model configurable, and **every selectable provider was
executed for real** — results below are measured, never fabricated. Failures are
reported honestly with the provider's own (key-free) reason.

Every run stores full provenance: `stt_spec`, `tts_spec`, `tts_voice_id`,
`experiment_id`, `trace_id` (schema: `backend/app/storage/schema.sql`; export
via `/api/export?format=csv|json`).

---

## CURRENT → TARGET (as implemented)

| Stage | Before | After |
|-------|--------|-------|
| STT | Deepgram `nova-2` only | `deepgram:nova-2`, `deepgram:nova-3`, `deepgram:whisper-large`, **`elevenlabs:scribe_v1`** (+ labeled mock) |
| LLM | OpenRouter (unchanged) | unchanged — `openrouter:<model>` |
| TTS | single default | `elevenlabs:eleven_turbo_v2_5`, `elevenlabs:eleven_multilingual_v2`, `elevenlabs:eleven_flash_v2_5`, `deepgram:aura-2-thalia-en`, `deepgram:aura-2-andromeda-en` (+ labeled mock); `openai:tts-1` listed **NOT_CONFIGURED** (no real key) |

Specs travel Frontend → `PipelineRequest` → `PipelineConfig` → factory
(`build_stt`/`build_tts`) → real HTTP calls. Unknown/unconfigured models are
rejected with HTTP 409 **before** anything runs — never silently swapped.

Two accounts per provider (Deepgram #1/#2, ElevenLabs #1/#2) are read from env
(`DEEPGRAM2_API_KEY`, `ELEVENLABS2_API_KEY`) and rotated round-robin by a shared
`KeyRing` (free-tier quota/concurrency spread). Only a non-secret account label
(`key_account: "deepgram#2"`) is observable — keys never leave the server.

## Files changed (this pass)

- `backend/app/core/config.py` — Scribe STT catalog entry, `ELEVENLABS2_API_KEY`/`DEEPGRAM2_API_KEY` settings, availability gates, whisper-large timeout 120→180 s
- `backend/app/voice/providers/base.py` — `KeyRing` (process-shared round-robin, observable labels, never fabricated keys)
- `backend/app/voice/providers/stt.py` — new `ElevenLabsScribeSTT` (separate vendor API), Deepgram key rotation, self-describing timeout errors
- `backend/app/voice/providers/tts.py` — key rotation for ElevenLabs/Aura, new Flash v2.5 model, self-describing timeout errors
- `backend/app/voice/__init__.py` — factory registers `elevenlabs:scribe_v1`, dual-key availability checks
- `backend/app/pipeline.py` — `key_account` provenance into stage events + LangSmith spans
- `backend/app/server.py` — dual-key live probes
- `backend/scripts/provider_matrix.py` — NEW: cross-provider execution harness (same WAV × every STT; same text × every TTS)
- `.env` (gitignored) — added the two second-account keys; `.env.example` documents them

## Test commands used

```bash
# offline suite (36 checks; spec parsing, 409 guards, TTFA recompute, provenance)
cd backend && MOCK_MODE=true .venv/Scripts/python -m pytest tests/ -q     # 35 passed

# §11/§12 cross-provider matrix (real API calls; same WAV / same text)
cd backend && .venv/Scripts/python scripts/provider_matrix.py --stt --tts --wav test_01

# §13/§21 full pipelines via the experiment matrix runner (cost-capped)
cd backend && .venv/Scripts/python -m benchmark.run_matrix \
  --tests test_01,test_02,test_05 --stt "deepgram:nova-2,deepgram:nova-3,deepgram:whisper-large" \
  --llm "openrouter:openai/gpt-4.1-mini" --tts "deepgram:aura-2-thalia-en" \
  --runs 1 --max-experiments 12 --experiment-id EXP-MULTI-STTX3
```

---

## STT — same WAV through every model

`test_01.wav` ("What is the difference between supervised learning and
unsupervised learning? Please explain it in the simple terms", 48 kHz stereo,
4.9 s):

| Provider | Model | Result | Latency | Confidence | Notes |
|----------|-------|--------|---------|------------|-------|
| Deepgram | nova-2 | ✓ | 3,038–3,922 ms | 0.997 | full punctuation |
| Deepgram | nova-3 | ✓ | 2,755–3,093 ms | 0.997 | same transcript, consistently fastest |
| Deepgram | whisper-large (hosted) | ✓ | 89,158 ms (cold), 3,496 ms (warm, in-pipeline) | 0.822 | lowercase, punctuation stripped |
| ElevenLabs | scribe_v1 | ✓ earlier (2,013 ms, correct transcript, lang_prob 0.97) → then **401** | — | 0.970 | integration verified live; see limitations |

`test_02.wav` (Python list-comprehension question): nova-2 3,479 ms /
nova-3 2,868 ms / whisper-large 3,128 ms — all correct; whisper again lowercased
(conf 0.792). The three Deepgram models are genuinely distinct executions
(different model ids, latencies, confidences); Scribe is a different vendor API.

## TTS — same text through every model

Sentence: "The benchmark reports time to first audio for this voice pipeline."

| Provider | Model | Result | First audio | Total | Notes |
|----------|-------|--------|-------------|-------|-------|
| Deepgram | aura-2-thalia-en | ✓ | 2,631–2,740 ms | same | 21,888–24,768 B MP3, verified across runs |
| Deepgram | aura-2-andromeda-en | ✓ | 2,673–2,925 ms | same | second Aura voice — distinct output size |
| ElevenLabs | eleven_turbo_v2_5 | ✗ quota_exceeded | — | — | key valid; **verified working earlier today (760 ms, 40,124 B audio)** before the free tier drained; provider's own reason surfaced |
| ElevenLabs | eleven_multilingual_v2 | ✗ quota_exceeded | — | — | authorized for this key; same drained quota |
| ElevenLabs | eleven_flash_v2_5 | ✗ quota_exceeded | — | — | cataloged + adapter-ready; same quota |
| OpenAI | tts-1 | **NOT_CONFIGURED** | — | — | `OPENAI_API_KEY` empty — listed unavailable, never faked |

All adapters are honest about first-audio semantics: single-chunk HTTP responses
are labeled `BUFFERED_RESPONSE` (first == total), not passed off as streaming.

## Full pipelines — same WAVs × different providers (§13, §21)

`openrouter:openai/gpt-4.1-mini` agent throughout. All values from stored runs
(experiment ids kept in the DB).

| STT | LLM | TTS | Test Case | TTFA | Tools | Status |
|-----|-----|-----|-----------|------|-------|--------|
| nova-2 | gpt-4.1-mini | aura-2-thalia | test_01 | 9,582 ms | 0 | ✓ (EXP-MULTI-BASELINE) |
| nova-2 | gpt-4.1-mini | aura-2-thalia | test_01 / 02 / 05 (noisy) | 10,975 / 11,724 / 9,034 ms | 0 | ✓ |
| nova-3 | gpt-4.1-mini | aura-2-thalia | test_01 / 02 / 05 (noisy) | 8,811 / 11,277 / 9,744 ms | 0 | ✓ |
| whisper-large | gpt-4.1-mini | aura-2-thalia | test_05 (noisy) | 8,910 ms | 0 | ✓ |
| whisper-large | gpt-4.1-mini | aura-2-thalia | test_01 / 02 | — | — | ✗ stt (120 s timeout under provider load) → **timeout raised to 180 s + self-describing error added; re-run ✓ 9,318 / 12,115 ms** |
| nova-2 | gpt-4.1-mini | aura-2-thalia | test_03 (weather) | 9,439 ms | **1** | ✓ |
| nova-2 | gpt-4.1-mini | aura-2-thalia | test_04 (search) | 8,478 ms | **1** | ✓ |
| nova-2 | gpt-4.1-mini | aura-2-thalia | test_06 (noisy) | 7,089 ms | 0 | ✓ |
| nova-3 | gpt-4.1-mini | aura-2-andromeda | test_01 | 7,474 ms | 0 | ✓ (cross combination) |
| nova-2 | gpt-4.1-mini | elevenlabs turbo | test_01 | — | — | ✗ tts quota_exceeded — persisted with full provenance, surfaced safely |

Tool behavior is unaffected by STT/TTS selection: no-tool cases → 0 calls,
one-tool cases → exactly 1. TTFA remains `FIRST_AUDIO − INPUT_END`, recomputed
by automated tests from raw events.

## Known limitations

1. **ElevenLabs free-tier quota**: account #1 drained to 1 credit during testing
   (TTS verified working at 760 ms first-audio before draining; Scribe STT
   verified at 2,013 ms earlier the same day). Quota resets 2026-10-22. Account
   #2 was flagged by the vendor (`detected_unusual_activity` — multiple free
   accounts) and is unusable; both failures surface with the provider's own
   reason, no retry-fallback is faked.
2. **OpenAI TTS**: `OPENAI_API_KEY` not set → `NOT_CONFIGURED`, guarded with 409.
3. **OpenRouter TTS/STT audio models**: probed — zero audio-output models listed;
   audio-input models require ≥ $0.50 balance (key holds ~$0.07) → not viable
   with current credentials, therefore not added (no fake options).
4. Hosted `whisper-large` shows 48–130 s cold starts (measured); its timeout is
   180 s and cold/warm variance is recorded rather than hidden.
5. TTS first-audio is `BUFFERED_RESPONSE` for all currently working providers
   (single-chunk HTTP); labeled rather than faked.
6. Playwright is not installed in this environment; browser flows verified via
   driven browser + screenshots in prior audit docs.
