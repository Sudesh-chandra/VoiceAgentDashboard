"""TTS streaming contract tests (production-audit §7/§21).

Contract: a TTS provider must yield chunks AS the HTTP body arrives — FIRST
AUDIO is the first non-empty network chunk, NOT response completion. These
tests prove incremental delivery through httpx's REAL transport behavior
(MockTransport with a generator body drives the same aiter_bytes path as a
live socket), plus an integration test through the real pipeline consumer.
No hand-rolled async generators pretending to be HTTP.
"""
from __future__ import annotations

import io
import wave

import httpx
import pytest

import app.voice.providers.tts as tts_mod
from app.voice.providers.tts import DeepgramAuraTTS, ElevenLabsTTS
from app.pipeline import PipelineConfig, PipelineRunner
from app.registry import resolve_audio_path


class _StaticRing:
    """KeyRing stand-in: deterministic single credential, no env dependency."""

    def next(self):
        return ("unit-test-key", "unit#1")


def _install_mock_transport(monkeypatch, handler) -> None:
    """Force every httpx.AsyncClient created by tts_mod onto MockTransport."""

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            k["timeout"] = 5.0
            super().__init__(*a, **k)

    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _Client)


def _wav(num_samples: int = 240) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * num_samples)
    return buf.getvalue()


async def _collect(agen, sink: list[bytes]) -> None:
    async for c in agen:
        if c:
            sink.append(c)


# ------------------------------------------------- §7: ElevenLabs adapter

@pytest.mark.asyncio
async def test_elevenlabs_stream_yields_first_chunk_before_completion(monkeypatch):
    """EL streams its MP3 body incrementally: first chunk yields as soon as the
    first network chunk arrives; bytes pass through unchanged, in order; the
    concatenation is one contiguous MP3 stream (no re-wrapping)."""
    chunk_a, chunk_b = b"\xff\xfb" + b"\x01" * 598, b"\xff\xfb" + b"\x02" * 598

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            yield chunk_a
            yield chunk_b
        return httpx.Response(200, content=body(), headers={"Content-Type": "audio/mpeg"})

    _install_mock_transport(monkeypatch, handler)
    tts = ElevenLabsTTS()
    tts._ring = _StaticRing()

    seen: list[bytes] = []
    await _collect(tts.stream("hello"), seen)

    assert len(seen) >= 2, f"expected incremental streaming (>=2 chunks), got {len(seen)}"
    assert seen[0] == chunk_a and seen[1] == chunk_b, "bytes must pass through, in order, exactly once"
    assert b"".join(seen) == chunk_a + chunk_b


# ------------------------------------------------- §7: Deepgram Aura adapter

@pytest.mark.asyncio
async def test_deepgram_stream_yields_each_frame(monkeypatch):
    """Aura passes MP3 frames through unchanged and in order — each frame is a
    separate chunk (chunk_count > 1 ⇒ pipeline labels the run STREAMING)."""
    frames = [b"ID3" + bytes([i]) * 200 for i in range(3)]

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            for f in frames:
                yield f
        return httpx.Response(200, content=body(), headers={"Content-Type": "audio/mpeg"})

    _install_mock_transport(monkeypatch, handler)
    tts = DeepgramAuraTTS()
    tts._ring = _StaticRing()

    seen: list[bytes] = []
    await _collect(tts.stream("hello"), seen)

    assert b"".join(seen) == b"".join(frames), "MP3 bytes must pass through unchanged, in order"
    assert len(seen) > 1, "frames must arrive as separate chunks (incremental, not buffered)"


# ------------------------------------------------- §6: empty-chunk semantics

@pytest.mark.asyncio
async def test_empty_chunks_never_trigger_first_audio(monkeypatch):
    """Empty transport chunks (keep-alive noise) are skipped — FIRST AUDIO is
    the first NON-EMPTY chunk."""
    real = b"\x00\x10" * 50

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            yield b""
            yield b""
            yield real
        return httpx.Response(200, content=body(), headers={"Content-Type": "audio/mpeg"})

    _install_mock_transport(monkeypatch, handler)
    tts = DeepgramAuraTTS()
    tts._ring = _StaticRing()

    seen: list[bytes] = []
    await _collect(tts.stream("x"), seen)
    assert seen == [real]


# ------------------------------------------------- §7: failure handling

@pytest.mark.asyncio
async def test_provider_error_surfaces_provider_reason(monkeypatch):
    """HTTP 4xx/5xx surfaces the provider's own safe reason — never key
    material, never a full header dump."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, content=b'{"detail":{"status":"quota_exceeded"}}',
                              headers={"Content-Type": "application/json"})

    _install_mock_transport(monkeypatch, handler)
    tts = DeepgramAuraTTS()
    tts._ring = _StaticRing()

    with pytest.raises(RuntimeError) as ei:
        await _collect(tts.stream("x"), [])
    msg = str(ei.value)
    assert "quota_exceeded" in msg and "unit-test-key" not in msg


@pytest.mark.asyncio
async def test_timeout_error_is_self_describing(monkeypatch):
    """httpx timeouts stringify to '' — the adapter must convert them into a
    safe, provider-identifying message (§25: failures must be visible)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    _install_mock_transport(monkeypatch, handler)
    tts = DeepgramAuraTTS()
    tts._ring = _StaticRing()

    with pytest.raises(RuntimeError) as ei:
        await _collect(tts.stream("x"), [])
    msg = str(ei.value)
    assert "Deepgram" in msg and "timed out" in msg and "unit-test-key" not in msg


# ------------------------------------------------- §6: single-chunk honesty

@pytest.mark.asyncio
async def test_single_chunk_response_is_labeled_buffered(monkeypatch, synth_cases):
    """A provider that delivers the whole body in one chunk must NOT be passed
    off as streaming: the pipeline labels it BUFFERED_RESPONSE (first==total)."""
    one = _wav(240)

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            yield one
        return httpx.Response(200, content=body(), headers={"Content-Type": "audio/mpeg"})

    _install_mock_transport(monkeypatch, handler)

    import app.pipeline as pipeline_mod

    def fake_build_tts(choice):
        tts = DeepgramAuraTTS()
        tts._ring = _StaticRing()
        return tts

    monkeypatch.setattr(pipeline_mod, "build_tts", fake_build_tts)

    cfg = PipelineConfig(pipeline_id="buffered-test", stt="mock", llm="mock:echo",
                         tts="deepgram:aura-2-thalia-en", tool="none")
    res = await PipelineRunner(cfg, mock_transcript="What is the capital of France?").execute(
        str(resolve_audio_path("synth_01")), "synth_01")

    assert res.success, res.error_detail
    assert res.timings.tts_first_audio_mode == "BUFFERED_RESPONSE"
    # first == total by construction for a single chunk
    assert res.timings.tts_first_audio_ms == pytest.approx(res.timings.tts_completion_ms, abs=0.5)


# ------------------------------------------------- §21: pipeline integration

@pytest.mark.asyncio
async def test_pipeline_first_audio_is_first_chunk_not_completion(monkeypatch, synth_cases):
    """Integration through the real pipeline consumer: with a genuinely
    streaming provider, FIRST AUDIO lands on the first chunk and the run is
    labeled STREAMING (chunk_count > 1), never as buffered completion."""
    chunk_a, chunk_b = _wav(240), b"\x03\x00" * 240

    def handler(request: httpx.Request) -> httpx.Response:
        async def body():
            yield chunk_a
            yield chunk_b
        return httpx.Response(200, content=body(), headers={"Content-Type": "audio/mpeg"})

    _install_mock_transport(monkeypatch, handler)

    import app.pipeline as pipeline_mod

    def fake_build_tts(choice):
        tts = DeepgramAuraTTS()
        tts._ring = _StaticRing()
        return tts

    monkeypatch.setattr(pipeline_mod, "build_tts", fake_build_tts)

    cfg = PipelineConfig(pipeline_id="stream-test", stt="mock", llm="mock:echo",
                         tts="deepgram:aura-2-thalia-en", tool="none")
    res = await PipelineRunner(cfg, mock_transcript="What is the capital of France?").execute(
        str(resolve_audio_path("synth_01")), "synth_01")

    assert res.success, res.error_detail
    assert res.timings.tts_first_audio_mode == "STREAMING", (
        "two delivered chunks must be labeled STREAMING, not BUFFERED_RESPONSE")
    # FIRST AUDIO precedes completion on the same monotonic clock (rounding can
    # make them equal at 0.001 ms granularity, never inverted).
    assert res.timings.tts_first_audio_ms <= res.timings.tts_completion_ms
    # Event ordering: first_audio precedes pipeline completion (TTS completion
    # itself is recorded in timings + the done event, not a tts/end event).
    kinds = [(e.stage, e.kind) for e in res.events]
    assert kinds.index(("tts", "first_audio")) < kinds.index(("done", "end"))
    # TTFA (headline) equals first-audio arrival − input end, unchanged semantics.
    first_audio = next(e for e in res.events if e.kind == "first_audio")
    input_end = next(e for e in res.events if e.stage == "input" and e.kind == "start")
    assert res.timings.time_to_first_audio_ms == pytest.approx(
        first_audio.ts_ms - input_end.ts_ms, abs=0.5)
