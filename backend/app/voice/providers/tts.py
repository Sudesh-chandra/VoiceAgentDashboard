"""TTS providers: ElevenLabs (three verified models) + Deepgram Aura-2 (a
genuinely different provider endpoint using DEEPGRAM_API_KEY), plus a
deterministic mock (MOCK_MODE only, always labeled mock).

All real adapters consume the HTTP body INCREMENTALLY (`client.stream` +
`aiter_bytes`): the first non-empty network chunk is yielded immediately, so
the pipeline's FIRST AUDIO timestamp is genuine first-audio arrival — not
response completion. Verified live against Deepgram /v1/speak (270+ MP3
frames arriving over ~2.4 s for one sentence). Chunk counts and per-chunk
gaps are provider/transport behavior; the pipeline labels first-audio
honestly (STREAMING vs BUFFERED_RESPONSE) from what actually arrived.

Chunk contract (same as MockTTS): concatenating all yielded chunks yields
one playable audio stream. Both real providers emit a contiguous container
byte stream (self-synchronizing MP3; no header split needed).

OpenAI TTS (tts-1) exists in the catalog but the workspace has no real key,
so it is listed NOT_CONFIGURED and rejected by the factory — never faked.
ElevenLabs rotates two configured accounts via KeyRing (free-tier quota
spread).
"""
from __future__ import annotations

import asyncio
import struct
import wave
import io
from typing import AsyncIterator

import httpx

from ...core.config import get_settings, TTS_MODELS
from .base import KeyRing

_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


def _describe_httpx_error(e: Exception, provider: str, model: str) -> str:
    """httpx timeout errors stringify to '' — give them a safe, self-describing
    message (§25: failures must be visible and meaningful, never key-bearing)."""
    if isinstance(e, httpx.TimeoutException):
        kind = type(e).__name__
        return f"{provider} ({model}) request timed out ({kind}) — retry or check provider status"
    if isinstance(e, httpx.HTTPStatusError):
        return str(e).split("\n")[0][:300]
    return str(e)[:300] or type(e).__name__


def _raise_provider_error(resp: httpx.Response, provider: str) -> None:
    """Fail with the provider's own safe reason (no headers, no key material)."""
    if resp.status_code >= 400:
        body = resp.text[:200].replace("\n", " ")
        raise httpx.HTTPStatusError(
            f"{provider} returned {resp.status_code}: {body}",
            request=resp.request, response=resp,
        )

# Default premade voice (verified usable on a free plan during the audit —
# free plans cannot call library voices via the API).
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"  # "George"
_SAMPLE_RATE = 24000


class ElevenLabsTTS:
    name = "elevenlabs"
    model = "eleven_turbo_v2_5"

    def __init__(self, model: str | None = None, voice_id: str | None = None) -> None:
        models = {m["id"] for m in TTS_MODELS.get("elevenlabs", [])}
        model = model or self.model
        if model not in models:
            raise ValueError(f"unknown ElevenLabs TTS model: {model!r} (known: {sorted(models)})")
        self.model = model
        s = get_settings()
        self.voice_id = voice_id or s.elevenlabs_voice_id or DEFAULT_VOICE_ID
        self._ring = KeyRing.shared("elevenlabs", [
            (s.elevenlabs_api_key, "elevenlabs#1"),
            (s.elevenlabs2_api_key, "elevenlabs#2"),
        ])

    def probe(self) -> bool:
        s = get_settings()
        return bool(s.elevenlabs_api_key or s.elevenlabs2_api_key)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield MP3 chunks as the provider streams them (default mp3_44100_128
        output, as in the earlier live-verified buffered call — this fix changes
        the transport only). FIRST AUDIO therefore equals first provider bytes,
        not response completion."""
        key, account = self._ring.next()
        # EL free-tier rejects very long inputs with 400. Bound here with a
        # safe message instead of shipping a provider 400 (§25).
        if len(text) > 2000:
            raise ValueError("ElevenLabs TTS input exceeds 2000 characters")
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                async with client.stream(
                    "POST",
                    f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}",
                    headers={
                        "xi-api-key": key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json={
                        "text": text,
                        "max_buffer_size_in_seconds": 15,
                        "optimize_streaming_latency": 4,
                    },
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()  # consume error body before .text
                        _raise_provider_error(resp, "ElevenLabs")
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.HTTPError as e:
                raise RuntimeError(_describe_httpx_error(e, "ElevenLabs TTS", self.model)) from e
        self.last_account = account  # observable label for provenance


class DeepgramAuraTTS:
    """Deepgram Aura-2: a genuinely different TTS provider endpoint
    (api.deepgram.com/v1/speak) using the same DEEPGRAM_API_KEY.

    Returns MP3 (audio/mpeg); the container bytes are yielded as they arrive
    over the wire so players handle them natively and FIRST AUDIO is the
    provider's first MP3 frame, not the completed download (verified live:
    ~140–600 B MP3 frames across ~2.4 s for one sentence).
    """

    name = "deepgram"

    def __init__(self, model: str = "aura-2-thalia-en", voice_id: str | None = None) -> None:
        models = {m["id"] for m in TTS_MODELS.get("deepgram", [])}
        if model not in models:
            raise ValueError(f"unknown Deepgram TTS model: {model!r} (known: {sorted(models)})")
        self.model = model
        meta = next(m for m in TTS_MODELS["deepgram"] if m["id"] == model)
        self._timeout = httpx.Timeout(float(meta["timeout_s"]), connect=5.0)
        s = get_settings()
        self._ring = KeyRing.shared("deepgram", [
            (s.deepgram_api_key, "deepgram#1"),
            (s.deepgram2_api_key, "deepgram#2"),
        ])

    def probe(self) -> bool:
        s = get_settings()
        return bool(s.deepgram_api_key or s.deepgram2_api_key)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        key, account = self._ring.next()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                async with client.stream(
                    "POST",
                    f"https://api.deepgram.com/v1/speak?model={self.model}",
                    headers={
                        "Authorization": f"Token {key}",
                        "Content-Type": "application/json",
                    },
                    json={"text": text},
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()  # consume error body before .text
                        _raise_provider_error(resp, "Deepgram TTS")
                    async for chunk in resp.aiter_bytes():
                        if chunk:
                            yield chunk
            except httpx.HTTPError as e:
                raise RuntimeError(_describe_httpx_error(e, "Deepgram TTS", self.model)) from e
        self.last_account = account  # observable label for provenance


class MockTTS:
    """Deterministic offline TTS for MOCK_MODE: emits a real WAV, chunked so the
    first-audio boundary is genuinely exercised (first chunk before completion)."""

    name = "mock"
    model = "mock-voice"

    def probe(self) -> bool:
        return True

    def _wav(self, num_samples: int, sample_rate: int = _SAMPLE_RATE) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sample_rate)
            w.writeframes(b"\x00\x00" * num_samples)
        return buf.getvalue()

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        await asyncio.sleep(0.12)
        yield self._wav(240)  # 10 ms first-audio chunk
        await asyncio.sleep(0.10)
        n = max(240, min(len(text) * 60, 24000))
        body = self._wav(n)
        yield body[44:] + struct.pack("<h", 0) * max(0, n - (len(body) - 44) // 2)
