"""End-to-end pipeline tests against deterministic mock providers (Phase C/D/E).

These verify the REAL code paths: pipeline orchestration, TTFA anchor, stage
timings, tool-call accounting, persistence — with mock providers standing in for
network services. Real-provider behavior is gated behind keys (integration).
"""
from __future__ import annotations

import pytest

from app.core.metrics import new_id
from app.pipeline import PipelineConfig, PipelineRunner
from app.benchmark_engine import BenchmarkEngine, ReliabilityEngine, ComparisonEngine


@pytest.mark.asyncio
async def test_pipeline_happy_path_measures_all_boundaries(synth_cases, fresh_db):
    cfg = PipelineConfig(pipeline_id="p-mock", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    runner = PipelineRunner(cfg, mock_transcript="What is the weather in Tokyo?")
    res = await runner.execute("non_used.wav", "synth_03")

    # We execute against a real file path from the registry for duration measurement.
    from app.registry import resolve_audio_path
    res2 = await PipelineRunner(cfg, mock_transcript="What is the weather in Tokyo?").execute(
        str(resolve_audio_path("synth_03")), "synth_03")

    assert res2.success, res2.error_detail
    t = res2.timings
    # headline metric present and sane
    assert t.time_to_first_audio_ms is not None and t.time_to_first_audio_ms > 0
    assert t.stt_latency_ms and t.stt_latency_ms > 0
    assert t.llm_ttft_ms is not None and t.llm_ttft_ms > 0
    assert t.tts_first_audio_ms is not None and t.tts_first_audio_ms > 0
    assert t.total_response_latency_ms >= t.time_to_first_audio_ms
    # exactly one tool call for a weather query
    assert len(res2.tool_calls) == 1
    assert res2.tool_calls[0]["name"] == "lookup_weather"
    assert t.tool_latency_ms is not None and t.tool_latency_ms > 0
    # event stream covers the required stages with FIRST AUDIO marked
    kinds = [e.kind for e in res2.events]
    assert "first_audio" in kinds
    stages = {e.stage for e in res2.events}
    assert {"input", "stt", "agent", "tool", "tts", "done"} <= stages


@pytest.mark.asyncio
async def test_no_tool_queries_call_zero_tools(synth_cases, fresh_db):
    cfg = PipelineConfig(pipeline_id="p-mock", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    from app.registry import resolve_audio_path
    res = await PipelineRunner(cfg, mock_transcript="What is the capital of France?").execute(
        str(resolve_audio_path("synth_01")), "synth_01")
    assert res.success
    assert len(res.tool_calls) == 0


@pytest.mark.asyncio
async def test_search_query_calls_search_tool_once(synth_cases, fresh_db):
    cfg = PipelineConfig(pipeline_id="p-mock", stt="mock", llm="mock:echo", tts="mock", tool="mock_search")
    from app.registry import resolve_audio_path
    res = await PipelineRunner(cfg, mock_transcript="Search the web for the latest AI news.").execute(
        str(resolve_audio_path("synth_04")), "synth_04")
    assert res.success
    assert len(res.tool_calls) == 1
    assert res.tool_calls[0]["name"] == "web_search"


@pytest.mark.asyncio
async def test_failure_is_captured_not_hidden(synth_cases, fresh_db, monkeypatch):
    from app.pipeline import STTError
    import app.pipeline as pl

    class BoomSTT:
        name = "boom"
        model = "boom-1"

        def probe(self):
            return True

        async def transcribe(self, audio, emit_partial):
            raise STTError("simulated provider outage")

    monkeypatch.setattr(pl, "build_stt", lambda choice, mt=None: BoomSTT())
    cfg = PipelineConfig(pipeline_id="p-fail", stt="mock", llm="mock:echo", tts="mock", tool="none")
    from app.registry import resolve_audio_path
    res = await PipelineRunner(cfg).execute(str(resolve_audio_path("synth_01")), "synth_01")
    assert not res.success
    assert res.failure_stage == "stt"
    assert res.error_type == "STTError"


@pytest.mark.asyncio
async def test_bad_audio_path_reported_as_unknown_failure(synth_cases, fresh_db):
    """A missing/unreadable file is captured as a failure, never silently dropped."""
    cfg = PipelineConfig(pipeline_id="p-bad", stt="mock", llm="mock:echo", tts="mock", tool="none")
    res = await PipelineRunner(cfg).execute("Z:/definitely/not/there.wav", "synth_01")
    assert not res.success
    assert res.failure_stage == "input"
    assert res.error_type == "AudioValidationError"


@pytest.mark.asyncio
async def test_llm_failure_captured_and_persisted(synth_cases, fresh_db, monkeypatch):
    """An LLM/agent failure must surface with failure_stage=llm, be persisted as a
    failed run, and be counted by the reliability summary — never hidden."""
    import app.pipeline as pl
    from app.pipeline import LLMError

    async def boom_agent(*args, **kwargs):
        raise LLMError("simulated LLM timeout")

    monkeypatch.setattr(pl, "astream_agent", boom_agent)
    eng = BenchmarkEngine()
    cfg = PipelineConfig(pipeline_id="p-fail-llm", stt="mock", llm="mock:echo", tts="mock", tool="none")
    out = await eng.run_single("synth_01", cfg, live=False)
    assert out["success"] is False
    assert out["failure_stage"] == "llm"
    assert out["error_type"] == "LLMError"

    from app.storage.db import get_db
    row = get_db().query_one("SELECT success, failure_stage, error_type FROM benchmark_runs WHERE run_id=?", (out["run_id"],))
    assert row is not None and row["success"] in (0, False)
    assert row["failure_stage"] == "llm"
    summary = ReliabilityEngine().failure_summary("p-fail-llm")
    assert summary["failed"] >= 1 and summary["failure_by_stage"].get("llm", 0) >= 1


@pytest.mark.asyncio
async def test_tts_failure_captured_and_persisted(synth_cases, fresh_db, monkeypatch):
    """A TTS failure must surface with failure_stage=tts and be persisted."""
    import app.pipeline as pl
    from app.pipeline import TTSError

    class BoomTTS:
        name = "boom"
        model = "boom-1"

        def probe(self):
            return True

        async def stream(self, text):
            raise TTSError("simulated TTS provider outage")
            yield b""  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(pl, "build_tts", lambda choice: BoomTTS())
    eng = BenchmarkEngine()
    cfg = PipelineConfig(pipeline_id="p-fail-tts", stt="mock", llm="mock:echo", tts="mock", tool="none")
    out = await eng.run_single("synth_01", cfg, live=False)
    assert out["success"] is False
    assert out["failure_stage"] == "tts"
    assert out["error_type"] == "TTSError"


@pytest.mark.asyncio
async def test_benchmark_engine_persists_and_artifact_written(synth_cases, fresh_db):
    from app.core.config import RESULTS_DIR
    eng = BenchmarkEngine()
    cfg = PipelineConfig(pipeline_id="p-bench", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    out = await eng.run_single("synth_03", cfg, live=False)
    assert out["success"] is True
    assert out["timings"]["time_to_first_audio_ms"] > 0

    from app.storage.db import get_db
    row = get_db().query_one("SELECT * FROM benchmark_runs WHERE run_id=?", (out["run_id"],))
    assert row is not None
    assert row["time_to_first_audio_ms"] == pytest.approx(out["timings"]["time_to_first_audio_ms"], abs=0.01)

    artifact = RESULTS_DIR / f"{out['run_id']}.json"
    assert artifact.exists()


@pytest.mark.asyncio
async def test_comparison_aggregates_two_pipelines(synth_cases, fresh_db):
    eng = BenchmarkEngine()
    for pid in ("pA", "pB"):
        cfg = PipelineConfig(pipeline_id=pid, stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
        await eng.run_single("synth_03", cfg, live=False)
        await eng.run_single("synth_01", cfg, live=False)
    comp = ComparisonEngine().compare(["pA", "pB"])
    ids = {c["pipeline_id"] for c in comp}
    assert {"pA", "pB"} <= ids
    for c in comp:
        assert c["runs"] >= 2
        assert c["ttfa"]["n"] >= 2


def test_reliability_summary_shape(synth_cases, fresh_db):
    summary = ReliabilityEngine().failure_summary()
    assert {"total_runs", "successful", "failed", "failure_rate_pct"} <= set(summary.keys())
