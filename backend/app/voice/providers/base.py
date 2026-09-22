"""Provider adapters (§11/§16/§17): STT / LLM / TTS / Tool + the shared KeyRing.

Contract for every provider:
- `probe()` reports whether the provider is usable with the current env/keys.
- Methods are async and speak plain data (no framework types leak out).
- Latency is measured by the pipeline around the calls — never inside the provider.
- Credential choice is an adapter-internal detail surfaced only as an observable
  account label (e.g. key_account="deepgram#2") — never the key itself.
"""
from __future__ import annotations

import base64
from typing import Any, AsyncIterator, Protocol, runtime_checkable

import httpx


class KeyRing:
    """Rotates between configured API keys of ONE provider (e.g. two Deepgram
    accounts) to spread free-tier quotas. Deterministic (round-robin by call
    count). Never falls back silently to a different provider; exposes only a
    non-secret account label for observability. Zero keys → marked unavailable,
    never fabricated.

    Rings are SHARED per provider name (see `shared`) so rotation persists
    across the per-run adapter instances — concurrent runs actually spread
    accounts instead of each starting at account #1.
    """

    _shared: dict[str, "KeyRing"] = {}

    def __init__(self, keys: list[tuple[str, str]]) -> None:
        # [(secret, label)] — labels like "deepgram#1". Empty secrets dropped.
        self._keys = [(k, l) for k, l in keys if k]
        self._n = 0
        if not self._keys:
            raise ValueError("KeyRing requires at least one non-empty key")

    @classmethod
    def shared(cls, name: str, keys: list[tuple[str, str]]) -> "KeyRing":
        """Return the process-wide ring for `name`, building it once.
        Rebuilds only if the configured key set changed (env hot-reload)."""
        present = tuple((k, l) for k, l in keys if k)
        ring = cls._shared.get(name)
        if ring is None or tuple(ring._keys) != present:
            ring = cls(keys)
            cls._shared[name] = ring
        return ring

    def next(self) -> tuple[str, str]:
        key, label = self._keys[self._n % len(self._keys)]
        self._n += 1
        return key, label

    def __len__(self) -> int:
        return len(self._keys)


@runtime_checkable
class STTProvider(Protocol):
    name: str
    model: str

    def probe(self) -> bool: ...

    async def transcribe(
        self, audio: bytes, emit_partial
    ) -> tuple[str, dict[str, Any]]:
        """Return (transcript, extras). extras may carry provider-reported timings.
        emit_partial is an async callback fired when a first/partial result exists."""
        ...


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    model: str

    def probe(self) -> bool: ...

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        """Yield audio chunks. The first yielded chunk defines tts_first_audio."""
        ...


class ToolResult(dict):
    """Structured, JSON-serializable tool output."""


@runtime_checkable
class ToolProvider(Protocol):
    name: str
    description: str

    async def run(self, **kwargs) -> ToolResult: ...


def b64_audio(audio: bytes) -> str:
    """Encode raw audio bytes for providers that take base64 payloads."""
    return base64.b64encode(audio).decode("ascii")


class HttpTimeout:
    """Shared safe HTTP defaults: never hang a provider call."""
    default = httpx.Timeout(20.0, connect=5.0)
