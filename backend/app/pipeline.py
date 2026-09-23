"""The instrumented voice cascade (§5): STT → LangGraph agent → TTS.

Every boundary is a real timestamp anchored at the END of the user's audio:
TTFA = first output audio sample − end of input audio. The runner emits StageEvents
for the live UI and never fabricates a metric it cannot measure.

Audio handling (§6A–6D): the actual .wav bytes are sent to STT; the real
transcript drives the agent. Audio metadata (filename, rate, channels, duration,
size) is captured into the result. No transcripts are injected from filenames or
metadata — the mock path exists only in MOCK_MODE and is labeled as such.
"""
from __future__ import annotations

import io
import os
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import numpy as np

from .core.metrics import StageEvent, StageTimings, PipelineResult, new_id, now_wall
from .core.config import get_settings
from .observability.langsmith_wiring import trace_pipeline_run
from .voice import (
    ProviderUnavailableError,
    build_stt,
    build_tts,
    build_agent_graph,
    astream_agent,
    extract_tool_calls,
)


@dataclass
class PipelineConfig:
    """STT/TTS/LLM selection uses model specs: provider[:model[:voice]].
    e.g. stt="deepgram:nova-3", tts="elevenlabs:eleven_multilingual_v2:<voice>".
    Bare provider names fall back to that provider's default model in the
    factory (never a different provider)."""
    pipeline_id: str
    stt: str = "deepgram:nova-2"   # deepgram:nova-2|nova-3|whisper-large | mock:mock-1
    llm: str = "openrouter:openai/gpt-4o-mini"
    tts: str = get_settings().default_tts_spec  # elevenlabs:* | deepgram:aura-2-* | mock:mock-voice
    tool: str = "auto"             # auto | weather | mock_weather | search | mock_search | none


EventSink = Callable[[StageEvent], Awaitable[None]]


class STTError(Exception): ...
class LLMError(Exception): ...
class TTSError(Exception): ...
class AudioValidationError(Exception): ...


def read_wav(path) -> tuple[bytes, float, int, dict]:
    """Return (full_file_bytes, duration_ms, sample_rate, metadata).

    The COMPLETE RIFF/WAVE container is returned (header included) because STT
    providers need it — Deepgram rejects headerless PCM with HTTP 400. Raises
    AudioValidationError on non-RIFF/WAVE or unparseable input."""
    p = Path(path)
    if not p.exists():
        raise AudioValidationError(f"audio file not found: {p.name}")
    raw = p.read_bytes()
    size = len(raw)
    if size < 44 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise AudioValidationError("not_a_wav_file")
    try:
        with wave.open(str(p), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate()
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            # Streaming WAV writers (e.g. Deepgram TTS) emit a 0xFFFFFFFF
            # sentinel data-size; the wave module trusts it and reports an
            # absurd frame count. Clamp to what the file can physically
            # contain (payload ≈ file minus the 44-byte canonical header).
            max_frames = max(0, size - 44) // max(1, sampwidth * channels)
            if frames > max_frames:
                frames = max_frames
            duration_ms = (frames / rate) * 1000 if rate else 0.0
    except wave.Error as e:
        raise AudioValidationError(f"unparseable wav: {e}") from e
    if frames <= 0 or rate <= 0:
        raise AudioValidationError("wav has no audio frames")
    meta = {
        "original_filename": p.name,
        "file_size_bytes": size,
        "sample_rate": rate,
        "channels": channels,
        "bit_depth": sampwidth * 8,
        "audio_format": f"PCM{sampwidth * 8}",
        "duration_ms": round(duration_ms, 1),
    }
    return raw, duration_ms, rate, meta


def leading_silence_ms(wav_bytes: bytes, rate: int, channels: int = 1, threshold: float = 300.0) -> float:
    """Measure leading silence from actual audio bytes (RMS windows). Used to
    document noise-case behavior — never subtracted from TTFA."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            width = w.getsampwidth()
            n = w.getnframes()
            ch = w.getnchannels() or channels
            if width != 2 or n == 0:
                return 0.0
            audio = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
            if ch > 1:
                audio = audio.reshape(-1, ch).mean(axis=1)
    except Exception:
        return 0.0
    if audio.size == 0:
        return 0.0
    win = max(1, int(rate * 0.02))
    usable = audio[: (audio.size // win) * win].reshape(-1, win)
    rms = np.sqrt(np.mean(usable**2, axis=1))
    idx = np.where(rms > threshold)[0]
    if idx.size == 0:
        return round(float(usable.shape[0] * win / rate * 1000), 1)
    return round(float(idx[0] * win / rate * 1000), 1)


class PipelineRunner:
    """Executes one benchmark run of the voice cascade with full instrumentation."""

    def __init__(self, config: PipelineConfig, sink: EventSink | None = None,
                 mock_transcript: str | None = None) -> None:
        self.config = config
        self._sink = sink
        self._mock_transcript = mock_transcript
        self.events: list[StageEvent] = []
        self.timings = StageTimings()
        self._t0: float | None = None  # perf_counter anchor = END of user audio

    async def _emit(self, stage: str, label: str, kind: str, **detail: Any) -> None:
        ev = StageEvent(
            ts_ms=round((time.perf_counter() - self._t0) * 1000, 3) if self._t0 is not None else 0.0,
            stage=stage,
            label=label,
            kind=kind,  # type: ignore[arg-type]
            wall_clock=now_wall(),
            detail=detail,
        )
        self.events.append(ev)
        if self._sink:
            await self._sink(ev)

    async def execute(self, audio_path: str, test_case_id: str) -> PipelineResult:
        run_id = new_id("run")
        session_id = new_id("sess")
        res = PipelineResult(run_id=run_id, session_id=session_id,
                             pipeline_id=self.config.pipeline_id, test_case_id=test_case_id,
                             success=False)  # flipped to True only on full success
        # Share the LIVE timings instance so the LangSmith root span (which exits
        # before the finally-block sync) sees the measured values, not an empty default.
        res.timings = self.timings
        # Exact model provenance for this run (§18): recorded even on failure —
        # set BEFORE provider construction so unavailable-provider failures
        # still carry the requested configuration.
        res.stt_spec = self.config.stt
        res.llm_spec = self.config.llm
        res.tts_spec = self.config.tts

        try:
            stt = build_stt(self.config.stt, self._mock_transcript)
            tts = build_tts(self.config.tts)
        except ProviderUnavailableError as e:
            res.failure_stage = "unknown"
            res.error_type = "ProviderUnavailableError"
            res.error_detail = str(e)
            res.completed_at = now_wall()
            await self._emit("done", f"PIPELINE UNAVAILABLE: {e}", "error", detail=str(e))
            res.events = list(self.events)
            res.timings = self.timings
            return res

        # NOTE: _emit always records into self.events; the optional sink fans out to WS.
        graph, tool_timings = build_agent_graph(self.config.llm, self.config.tool, emit=self._emit)

        try:
            raw, duration_ms, rate, audio_meta = read_wav(audio_path)
            res.audio_metadata = audio_meta
            self.timings.input_duration_ms = round(duration_ms, 1)
            silence = leading_silence_ms(raw, rate, audio_meta["channels"])

            # ANCHOR: the TTFA timer starts exactly here — end of user's audio.
            self._t0 = time.perf_counter()
            await self._emit("input", "Input audio ended — timer started", "start",
                             input_duration_ms=self.timings.input_duration_ms,
                             leading_silence_ms=silence,
                             sample_rate=audio_meta["sample_rate"],
                             channels=audio_meta["channels"])

            with trace_pipeline_run(res, self.config, self.events) as ls_ctx:
                # ---- STT ------------------------------------------------------
                await self._emit("stt", "STT started", "start", provider=stt.name, model=stt.model)
                stt_span = ls_ctx.child("stt", "tool", {"provider": stt.name, "model": stt.model}) if ls_ctx else None
                stt_started = time.perf_counter()
                first_result: list[float] = []

                async def on_partial(_text: str) -> None:
                    if not first_result:
                        first_result.append((time.perf_counter() - stt_started) * 1000)

                try:
                    transcript, extras = await stt.transcribe(raw, on_partial)
                    self.timings.stt_latency_ms = round((time.perf_counter() - stt_started) * 1000, 3)
                    self.timings.stt_first_result_ms = round(first_result[0], 3) if first_result else None
                    self.timings.stt_provider_ms = extras.get("stt_provider_ms")
                    res.transcript = transcript
                    await self._emit("stt", "STT completed", "end",
                                     latency_ms=self.timings.stt_latency_ms,
                                     transcript_chars=len(transcript),
                                     transcript_preview=(transcript[:80] + "…") if len(transcript) > 80 else transcript,
                                     confidence=extras.get("confidence"),
                                     key_account=extras.get("key_account"))
                    if stt_span:
                        stt_span.end(outputs={"transcript": (res.transcript or "")[:200],
                                              "latency_ms": self.timings.stt_latency_ms,
                                              "key_account": extras.get("key_account")})
                        stt_span.patch()
                except Exception as e:
                    if stt_span:
                        stt_span.end(error=str(e)[:300])
                        stt_span.patch()
                    self.timings.stt_latency_ms = round((time.perf_counter() - stt_started) * 1000, 3)
                    raise STTError(str(e)[:300]) from e

                # ---- Agent (LangGraph) ----------------------------------------
                await self._emit("agent", "Agent started", "start", llm=self.config.llm)
                agent_span = ls_ctx.child("agent", "chain", {"llm": self.config.llm}) if ls_ctx else None
                agent_started = time.perf_counter()
                llm_ttft: list[float] = []

                async def on_first_token(ms: float) -> None:
                    if not llm_ttft:
                        llm_ttft.append(ms)

                final_text = ""
                final_state: dict | None = None
                try:
                    async for ev in astream_agent(graph, transcript, on_first_token):
                        if ev["type"] == "done":
                            final_text = ev["text"]
                            final_state = ev.get("state")
                            usage = ev.get("usage") or {}
                            # Provider-reported token usage (cost governance).
                            # Stays None when the provider doesn't report it.
                            self.timings.llm_prompt_tokens = usage.get("prompt_tokens")
                            self.timings.llm_completion_tokens = usage.get("completion_tokens")
                            self.timings.llm_total_tokens = usage.get("total_tokens")
                    self.timings.llm_ttft_ms = round(llm_ttft[0], 3) if llm_ttft else None
                    self.timings.llm_completion_ms = round((time.perf_counter() - agent_started) * 1000, 3)
                except Exception as e:
                    if agent_span:
                        agent_span.end(error=str(e)[:300])
                        agent_span.patch()
                    raise LLMError(str(e)[:300]) from e

                tool_calls = extract_tool_calls(final_state)
                if tool_timings:
                    self.timings.tool_latency_ms = round(sum(t["latency_ms"] for t in tool_timings), 3)
                await self._emit("agent", "Agent completed", "end",
                                 latency_ms=self.timings.llm_completion_ms,
                                 tool_calls=[tc["name"] for tc in tool_calls],
                                 tool_latencies=tool_timings,
                                 prompt_tokens=self.timings.llm_prompt_tokens,
                                 completion_tokens=self.timings.llm_completion_tokens,
                                 total_tokens=self.timings.llm_total_tokens)
                if agent_span:
                    agent_span.end(outputs={
                        "llm_ttft_ms": self.timings.llm_ttft_ms,
                        "tool_calls": [tc["name"] for tc in tool_calls],
                        "response_chars": len(final_text),
                    })
                    agent_span.patch()

                # ---- TTS --------------------------------------------------------
                await self._emit("tts", "TTS started", "start", provider=tts.name, model=tts.model)
                tts_span = ls_ctx.child("tts", "tool", {"provider": tts.name, "model": tts.model}) if ls_ctx else None
                tts_started = time.perf_counter()
                first_audio_rel: list[float] = []
                audio_bytes = bytearray()
                chunk_count = 0
                try:
                    async for chunk in tts.stream(final_text):
                        if chunk:
                            chunk_count += 1
                            if not first_audio_rel:
                                first_audio_rel.append(time.perf_counter())
                                await self._emit("tts", "FIRST AUDIO", "first_audio",
                                                 at_ms_from_input_end=round((time.perf_counter() - self._t0) * 1000, 3),
                                                 chunk_index=chunk_count)
                            audio_bytes.extend(chunk)
                    self.timings.tts_first_audio_ms = (
                        round((first_audio_rel[0] - tts_started) * 1000, 3) if first_audio_rel else None
                    )
                    self.timings.tts_completion_ms = round((time.perf_counter() - tts_started) * 1000, 3)
                    # Honest first-audio semantics: only claim STREAMING when the
                    # provider actually delivered multiple chunks; a single-chunk
                    # (buffered HTTP) response means first==total and must be labeled.
                    self.timings.tts_first_audio_mode = (
                        "STREAMING" if chunk_count > 1 else "BUFFERED_RESPONSE"
                    ) if first_audio_rel else None
                except Exception as e:
                    if tts_span:
                        tts_span.end(error=str(e)[:300])
                        tts_span.patch()
                    raise TTSError(str(e)[:300]) from e

                if not audio_bytes:
                    raise TTSError("tts produced no audio")
                res.tts_voice_id = getattr(tts, "voice_id", None) or self.timings.tts_voice_id
                if tts_span:
                    tts_span.end(outputs={
                        "tts_first_audio_ms": self.timings.tts_first_audio_ms,
                        "audio_bytes": len(audio_bytes),
                    })
                    tts_span.patch()

                # ---- Headline metric --------------------------------------------
                # TTFA = moment first output audio sample existed − end of user audio.
                if first_audio_rel:
                    self.timings.time_to_first_audio_ms = round((first_audio_rel[0] - self._t0) * 1000, 3)
                self.timings.total_response_latency_ms = round((time.perf_counter() - self._t0) * 1000, 3)

                res.success = True
                res.response_text = final_text
                res.tool_calls = tool_calls
                res.completed_at = now_wall()
                await self._emit("done", "Run complete", "end",
                                 time_to_first_audio_ms=self.timings.time_to_first_audio_ms,
                                 total_response_latency_ms=self.timings.total_response_latency_ms,
                                 stt_key_account=getattr(stt, "last_account", None),
                                 tts_key_account=getattr(tts, "last_account", None))
            return res

        except (STTError, LLMError, TTSError) as e:
            stage_map = {STTError: "stt", LLMError: "llm", TTSError: "tts"}
            res.success = False
            res.failure_stage = stage_map[type(e)]
            res.error_type = type(e).__name__
            res.error_detail = str(e)
            res.completed_at = now_wall()
            await self._emit("done", f"FAILED at {res.failure_stage}", "error",
                             error_type=res.error_type, detail=res.error_detail)
            return res
        except AudioValidationError as e:
            res.success = False
            res.failure_stage = "input"
            res.error_type = type(e).__name__
            res.error_detail = str(e)
            res.completed_at = now_wall()
            await self._emit("done", f"INVALID AUDIO: {e}", "error", detail=str(e))
            return res
        except Exception as e:  # unexpected — captured, never hidden
            res.success = False
            res.failure_stage = getattr(res, "failure_stage", None) or "unknown"
            res.error_type = type(e).__name__
            res.error_detail = str(e)[:500]
            res.completed_at = now_wall()
            await self._emit("done", f"FAILED: {res.error_type}", "error", detail=res.error_detail)
            return res
        finally:
            res.events = list(self.events)
            res.timings = self.timings
