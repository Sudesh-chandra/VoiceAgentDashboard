"""Shared metric/schemas. Every field is measured or explicitly None — never fabricated."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

Stage = Literal["input", "stt", "agent", "llm", "tool", "tts", "done"]
FailureStage = Literal["stt", "llm", "tool", "tts", "unknown", None]


def now_wall() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int((time.time() % 1) * 1000):03d}Z"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


@dataclass
class StageEvent:
    """A single observable point in the pipeline. Timestamps are ms relative to input end."""

    ts_ms: float          # ms since input_audio_end (monotonic anchor)
    stage: str            # input | stt | agent | llm | tool | tts
    label: str            # human-readable event, e.g. "STT started", "FIRST AUDIO"
    kind: Literal["start", "end", "first_audio", "first_token", "partial", "error", "info"]
    wall_clock: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StageTimings:
    input_duration_ms: float | None = None
    stt_latency_ms: float | None = None
    stt_first_result_ms: float | None = None
    llm_ttft_ms: float | None = None
    llm_completion_ms: float | None = None
    tool_latency_ms: float | None = None
    tts_first_audio_ms: float | None = None
    tts_completion_ms: float | None = None
    time_to_first_audio_ms: float | None = None
    total_response_latency_ms: float | None = None
    # provider-reported extras (never invented)
    stt_provider_ms: float | None = None
    tts_provider_ms: float | None = None
    # How FIRST AUDIO was observed: STREAMING (true first chunk) or
    # BUFFERED_RESPONSE (provider returned complete audio; first==total).
    tts_first_audio_mode: str | None = None
    # Voice of the TTS voice used (provider-reported identity when available).
    tts_voice_id: str | None = None
    # LLM token usage as reported by the provider (OpenRouter/OpenAI usage
    # metadata in the stream). None when the provider does not report it —
    # never estimated, never fabricated (cost-governance requirement).
    llm_prompt_tokens: int | None = None
    llm_completion_tokens: int | None = None
    llm_total_tokens: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items()}


@dataclass
class PipelineResult:
    """Outcome of one pipeline execution (success or failure — both are data)."""

    run_id: str
    session_id: str
    pipeline_id: str
    test_case_id: str
    success: bool
    transcript: str = ""
    response_text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    timings: StageTimings = field(default_factory=StageTimings)
    events: list[StageEvent] = field(default_factory=list)
    failure_stage: FailureStage | str | None = None
    error_type: str | None = None
    error_detail: str | None = None
    trace_id: str | None = None
    # Canonical (tenant-scoped) LangSmith UI URL captured at trace time so the
    # link survives org-independent redirects; None → UI falls back to the
    # legacy project-name URL.
    trace_url: str | None = None
    audio_metadata: dict[str, Any] = field(default_factory=dict)
    # Exact models used (§18 reproducibility): provider:model[:voice] specs +
    # provider-reported TTS voice identity.
    stt_spec: str = ""
    llm_spec: str = ""
    tts_spec: str = ""
    tts_voice_id: str | None = None
    experiment_id: str | None = None
    started_at: str = field(default_factory=now_wall)
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "pipeline_id": self.pipeline_id,
            "test_case_id": self.test_case_id,
            "success": self.success,
            "transcript": self.transcript,
            "response_text": self.response_text,
            "tool_calls": self.tool_calls,
            "timings": self.timings.to_dict(),
            "failure_stage": self.failure_stage,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
            "trace_id": self.trace_id,
            "trace_url": self.trace_url,
            "audio_metadata": self.audio_metadata,
            "stt_spec": self.stt_spec,
            "llm_spec": self.llm_spec,
            "tts_spec": self.tts_spec,
            "tts_voice_id": self.tts_voice_id,
            "experiment_id": self.experiment_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "events": [e.to_dict() for e in self.events],
        }
