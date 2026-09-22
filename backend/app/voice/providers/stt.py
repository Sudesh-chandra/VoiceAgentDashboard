"""STT providers: Deepgram listen API with three genuinely distinct models
(nova-2, nova-3, and the Deepgram-hosted whisper-large third-party model) plus
ElevenLabs Scribe v1 — a genuinely different provider endpoint
(api.elevenlabs.io/v1/speech-to-text) — and a deterministic mock (MOCK_MODE
only, always labeled mock).

Model/provider differences are real, not cosmetic:
- nova-2 / nova-3 differ in model id and word segmentation (verified: the same
  audio returns different word counts/timings per model).
- whisper-large rejects smart_format/punctuate query params (HTTP 400) and has
  long cold starts, so it is sent with minimal params and a 120 s timeout.
- Scribe v1 is a separate vendor API (multipart upload, JSON result with
  language confidence) — verified live with a real transcript.

Two Deepgram accounts are supported via KeyRing rotation (free-tier concurrency
is per-account). Providers never measure themselves; the pipeline wraps every
call. `extras` carries only provider-reported values (duration, confidence).
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

import httpx

from ...core.config import get_settings, STT_MODELS
from .base import HttpTimeout, KeyRing

PartialCallback = Callable[[str], Awaitable[None]]

_TIMEOUT = HttpTimeout.default

# whisper-large (Deepgram-hosted third-party model) rejects these params.
_WHISPER_REJECTS = {"smart_format", "punctuate"}


def _describe_httpx_error(e: Exception, provider: str, model: str) -> str:
    """httpx timeout errors stringify to '' — give them a safe, self-describing
    message (§25: failures must be visible and meaningful, never key-bearing)."""
    if isinstance(e, (httpx.TimeoutException,)):
        kind = type(e).__name__  # ConnectTimeout / ReadTimeout / PoolTimeout
        return f"{provider} ({model}) request timed out ({kind}) — provider cold start or overload; retry"
    if isinstance(e, httpx.HTTPStatusError):
        return str(e).split("\n")[0][:300]
    return str(e)[:300] or type(e).__name__


class DeepgramSTT:
    name = "deepgram"

    def __init__(self, model: str = "nova-2") -> None:
        models = {m["id"] for m in STT_MODELS.get("deepgram", [])}
        if model not in models:
            raise ValueError(f"unknown Deepgram STT model: {model!r} (known: {sorted(models)})")
        self.model = model
        meta = next(m for m in STT_MODELS["deepgram"] if m["id"] == model)
        self._timeout = httpx.Timeout(float(meta["timeout_s"]), connect=5.0)
        self._is_whisper_hosted = model.startswith("whisper")
        # Rotation across configured Deepgram accounts (never fabricated keys);
        # ring is process-wide so concurrent runs actually alternate accounts.
        s = get_settings()
        self._ring = KeyRing.shared("deepgram", [
            (s.deepgram_api_key, "deepgram#1"),
            (s.deepgram2_api_key, "deepgram#2"),
        ])

    def probe(self) -> bool:
        s = get_settings()
        return bool(s.deepgram_api_key or s.deepgram2_api_key)

    async def transcribe(self, audio: bytes, emit_partial: PartialCallback) -> tuple[str, dict[str, Any]]:
        key, account = self._ring.next()
        params: dict[str, str] = {"model": self.model}
        if not self._is_whisper_hosted:
            params.update({"smart_format": "true", "punctuate": "true"})
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    "https://api.deepgram.com/v1/listen",
                    params=params,
                    headers={
                        "Authorization": f"Token {key}",
                        "Content-Type": "audio/wav",
                    },
                    content=audio,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                raise RuntimeError(_describe_httpx_error(e, "Deepgram STT", self.model)) from e
        alt = data["results"]["channels"][0]["alternatives"][0]
        transcript = alt.get("transcript", "")
        await emit_partial(transcript)  # batch API: first result is final
        meta = data["results"].get("metadata") or {}
        duration_s = float(meta.get("duration", 0) or 0)
        extras = {
            "stt_provider_ms": duration_s * 1000 if duration_s else None,
            "confidence": alt.get("confidence"),
            "stt_model": self.model,
            "key_account": account,  # observable label only — never the key
        }
        return transcript, extras


class ElevenLabsScribeSTT:
    """ElevenLabs Scribe v1: genuinely different STT provider (separate vendor,
    multipart upload, JSON with language confidence). Uses the SAME
    ELEVENLABS_API_KEY as TTS (it is a voice-platform credential)."""

    name = "elevenlabs"
    model = "scribe_v1"

    def __init__(self, model: str = "scribe_v1") -> None:
        models = {m["id"] for m in STT_MODELS.get("elevenlabs", [])}
        if model not in models:
            raise ValueError(f"unknown ElevenLabs STT model: {model!r} (known: {sorted(models)})")
        self.model = model
        meta = next(m for m in STT_MODELS["elevenlabs"] if m["id"] == model)
        self._timeout = httpx.Timeout(float(meta["timeout_s"]), connect=5.0)
        s = get_settings()
        self._ring = KeyRing.shared("elevenlabs", [
            (s.elevenlabs_api_key, "elevenlabs#1"),
            (s.elevenlabs2_api_key, "elevenlabs#2"),
        ])

    def probe(self) -> bool:
        s = get_settings()
        return bool(s.elevenlabs_api_key or s.elevenlabs2_api_key)

    async def transcribe(self, audio: bytes, emit_partial: PartialCallback) -> tuple[str, dict[str, Any]]:
        key, account = self._ring.next()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                "https://api.elevenlabs.io/v1/speech-to-text",
                headers={"xi-api-key": key},
                files={"file": ("audio.wav", audio, "audio/wav")},
                data={"model_id": self.model},
            )
            try:
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                raise RuntimeError(_describe_httpx_error(e, "ElevenLabs STT", self.model)) from e
        transcript = data.get("text", "")
        await emit_partial(transcript)
        extras = {
            "stt_provider_ms": None,  # Scribe reports no processing duration
            "confidence": data.get("language_probability"),
            "stt_model": self.model,
            "key_account": account,
        }
        return transcript, extras


class MockSTT:
    """Deterministic offline STT for MOCK_MODE (tests/CI). Clearly labeled mock.
    The transcript is supplied by the caller (registry); no audio is parsed."""

    name = "mock"
    model = "mock-1"

    def __init__(self, transcript: str | None = None) -> None:
        self._fixed_transcript = transcript

    def probe(self) -> bool:
        return True

    async def transcribe(self, audio: bytes, emit_partial: PartialCallback) -> tuple[str, dict[str, Any]]:
        text = self._fixed_transcript or "mock transcript: audio recognized"
        await asyncio.sleep(0.12)
        await emit_partial(text.split(":")[0])
        await asyncio.sleep(0.06)
        return text, {"stt_provider_ms": None, "confidence": None}
