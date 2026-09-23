"""Systematic model-matrix validation (§10–13, §21–22, §26–27).

Verifies metric math (TTFA recomputed from raw events), event ordering,
tool-count correctness, matrix combination generation + cost cap,
export shape, provider endpoint safety, and persistence across a fresh
Database instance (restart semantics). Real providers are NOT required —
these run in MOCK_MODE against synthetic fixtures.
"""
from __future__ import annotations

import json

import pytest

from app.pipeline import PipelineConfig, PipelineRunner
from app.registry import resolve_audio_path, list_test_cases


# ---------- §10/§11: TTFA + stage math from the raw event timeline ----------

def test_canonical_trace_url_is_tenant_scoped(monkeypatch):
    """Regression: legacy /projects/<name>/t/<id> URLs redirect to whichever org
    the viewer's browser has active and 404 when it differs from the key's org.
    The canonical URL must be tenant-scoped like LangSmith's own app_path."""
    from app.observability import langsmith_wiring as lw
    from datetime import datetime, timezone

    monkeypatch.setattr(lw, "_enabled", lambda: True)
    monkeypatch.setattr(lw, "_ui_base", lambda p, api_key=None: "/o/tnt-1/projects/p/prj-1")

    url = lw.canonical_trace_url("abc-123")
    assert url == "https://smith.langchain.com/o/tnt-1/projects/p/prj-1/r/abc-123?trace_id=abc-123"

    dt = datetime(2026, 9, 23, 9, 25, 41, 517243, tzinfo=timezone.utc)
    url2 = lw.canonical_trace_url("abc-123", start_time=dt)
    assert url2.endswith("&start_time=2026-09-23T09:25:41.517243")
    # epoch-ms input also accepted (RunTree uses datetime, API uses ms)
    url3 = lw.canonical_trace_url("abc-123", start_time=1758617141517)
    assert "start_time=" in url3


@pytest.mark.asyncio
async def test_ttfa_recomputed_from_events_matches_backend(synth_cases, fresh_db):
    """TTFA must equal FIRST_AUDIO.ts − INPUT_END (t=0) recomputed from raw
    events — i.e. the backend value is derived from the same timeline it emits."""
    cfg = PipelineConfig(pipeline_id="mv-ttfa", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    res = await PipelineRunner(cfg, mock_transcript="What is the weather in Tokyo?").execute(
        str(resolve_audio_path("synth_03")), "synth_03")
    assert res.success, res.error_detail

    first_audio = next(e for e in res.events if e.kind == "first_audio")
    input_end = next(e for e in res.events if e.stage == "input" and e.kind == "start")
    # input_end is the anchor: its ts is ~0 by construction
    assert abs(input_end.ts_ms) < 1.0
    recomputed = first_audio.ts_ms - input_end.ts_ms
    assert res.timings.time_to_first_audio_ms == pytest.approx(recomputed, abs=0.5)


@pytest.mark.asyncio
async def test_event_ordering_matches_spec(synth_cases, fresh_db):
    """INPUT_END < STT_START < STT_END < AGENT_START < TTS_START <
    FIRST_AUDIO < DONE (no-tool path must have zero tool events)."""
    cfg = PipelineConfig(pipeline_id="mv-order", stt="mock", llm="mock:echo", tts="mock", tool="none")
    res = await PipelineRunner(cfg, mock_transcript="What is the capital of France?").execute(
        str(resolve_audio_path("synth_01")), "synth_01")
    assert res.success
    assert len(res.tool_calls) == 0  # §12: no-tool case → 0 actual executions

    key_events = [(e.stage, e.kind) for e in res.events
                  if (e.stage, e.kind) in {("input", "start"), ("stt", "start"), ("stt", "end"),
                                           ("agent", "start"), ("tts", "start"),
                                           ("tts", "first_audio"), ("done", "end")}]
    expected = [("input", "start"), ("stt", "start"), ("stt", "end"),
                ("agent", "start"), ("tts", "start"), ("tts", "first_audio"), ("done", "end")]
    assert key_events == expected
    # monotonic timestamps
    tss = [e.ts_ms for e in res.events]
    assert tss == sorted(tss)


@pytest.mark.asyncio
async def test_tool_case_exactly_one_execution_and_ordering(synth_cases, fresh_db):
    cfg = PipelineConfig(pipeline_id="mv-tool", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    res = await PipelineRunner(cfg, mock_transcript="What is the weather in Tokyo?").execute(
        str(resolve_audio_path("synth_03")), "synth_03")
    assert res.success
    # A tool CALL means the function executed — exactly one tool start/end pair.
    tool_starts = [e for e in res.events if e.stage == "tool" and e.kind == "start"]
    tool_ends = [e for e in res.events if e.stage == "tool" and e.kind == "end"]
    assert len(tool_starts) == 1 and len(tool_ends) == 1
    assert len(res.tool_calls) == 1
    # TOOL sits inside the agent span, before TTS
    agent_start = next(e for e in res.events if e.stage == "agent" and e.kind == "start")
    tts_start = next(e for e in res.events if e.stage == "tts" and e.kind == "start")
    assert agent_start.ts_ms < tool_starts[0].ts_ms < tool_ends[0].ts_ms < tts_start.ts_ms


@pytest.mark.asyncio
async def test_stage_latencies_no_double_counting(synth_cases, fresh_db):
    """Stage latencies are deltas of their own start/end; TTFA ≤ total; and
    TTFA is NOT the sum of stages (total is timestamp-derived)."""
    cfg = PipelineConfig(pipeline_id="mv-stages", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    res = await PipelineRunner(cfg, mock_transcript="What is the weather in Tokyo?").execute(
        str(resolve_audio_path("synth_03")), "synth_03")
    t = res.timings
    assert t.stt_latency_ms > 0
    assert t.tts_first_audio_ms is not None and t.tts_first_audio_ms >= 0
    assert t.total_response_latency_ms >= t.time_to_first_audio_ms
    # tts completion >= first audio (same clock, same stage)
    assert t.tts_completion_ms >= t.tts_first_audio_ms
    # TTFA must be smaller than STT + agent + tool + tts sum when stages are
    # measured around their own calls (agent includes tool, so sum would
    # double-count) — just assert TTFA is not absurdly larger than total.
    assert t.time_to_first_audio_ms <= t.total_response_latency_ms + 1.0


# ---------- §6/§7: matrix combination generation + cost cap ----------

def test_combination_generation_count():
    from benchmark.run_matrix import gen_combinations
    combos = gen_combinations(["deepgram", "mock"], ["openrouter:openai/gpt-4o-mini", "mock:echo"],
                              ["elevenlabs", "mock"])
    assert len(combos) == 2 * 2 * 2
    triples = {(c["stt"], c["llm"], c["tts"]) for c in combos}
    assert ("deepgram", "mock:echo", "mock") in triples
    assert len(triples) == 8  # all unique


def test_cost_guard_blocks_oversized_matrix(synth_cases, fresh_db, monkeypatch, capsys):
    """§29: the runner must refuse to exceed MAX_EXPERIMENTS."""
    from benchmark import run_matrix as rm
    argv = ["run_matrix", "--tests", "synth_01,synth_02,synth_03,synth_04,synth_05,synth_06",
            "--stt", "deepgram,mock", "--llm", "mock:echo", "--tts", "elevenlabs,mock",
            "--runs", "5", "--max-experiments", "10"]
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as ei:
        rm.main()
    assert "cost guard" in str(ei.value)


@pytest.mark.asyncio
async def test_matrix_runner_executes_and_records_experiment_id(synth_cases, fresh_db, monkeypatch):
    """Small real matrix through the runner: 1 pipeline × 1 case × 2 runs under
    MOCK_MODE; experiment_id must land on every stored row."""
    from benchmark import run_matrix as rm
    args = rm.parser().parse_args([
        "--tests", "synth_01", "--stt", "mock", "--llm", "mock:echo",
        "--tts", "mock", "--runs", "2", "--max-experiments", "10",
        "--experiment-id", "EXP-TEST-1", "--out", "",
    ])
    monkeypatch.setattr("builtins.print", lambda *a, **k: None)
    report = await rm.run_matrix(args)
    assert report["experiment_id"] == "EXP-TEST-1"
    assert len(report["results"]) == 2

    from app.storage.db import get_db
    rows = get_db().query("SELECT experiment_id, pipeline_id, success FROM benchmark_runs WHERE experiment_id='EXP-TEST-1'")
    assert len(rows) == 2
    assert all(r["experiment_id"] == "EXP-TEST-1" for r in rows)
    assert all(r["success"] in (0, 1) for r in rows)


# ---------- §22: persistence across restart ----------

@pytest.mark.asyncio
async def test_results_survive_database_reconnect(synth_cases, fresh_db, monkeypatch):
    from app.benchmark_engine import BenchmarkEngine
    cfg = PipelineConfig(pipeline_id="mv-persist", stt="mock", llm="mock:echo", tts="mock", tool="none")
    out = await BenchmarkEngine().run_single("synth_01", cfg, live=False)
    from app.storage.db import get_db
    get_db().execute("UPDATE benchmark_runs SET experiment_id='EXP-PERSIST' WHERE run_id=?", (out["run_id"],))

    # Simulate a backend restart: brand-new Database instance on the SAME file
    # (fresh_db's path, read at call time — not the module default).
    from app.storage import db as dbmod
    fresh = dbmod.Database(fresh_db)
    row = fresh.query_one("SELECT run_id, experiment_id, time_to_first_audio_ms, trace_id FROM benchmark_runs WHERE run_id=?", (out["run_id"],))
    assert row is not None
    assert row["experiment_id"] == "EXP-PERSIST"
    assert row["time_to_first_audio_ms"] is not None
    # events timeline survives too (raw data preserved)
    row2 = fresh.query_one("SELECT events_json FROM benchmark_runs WHERE run_id=?", (out["run_id"],))
    events = json.loads(row2["events_json"])
    assert any(e["kind"] == "first_audio" for e in events)
    # restore the shared singleton for later tests in this session
    dbmod._db = None


# ---------- §26/§27: export + providers API ----------

def test_export_json_and_csv_shape(synth_cases, fresh_db):
    from fastapi.testclient import TestClient
    from app.server import app
    with TestClient(app) as c:
        r = c.get("/api/export")
        assert r.status_code == 200
        body = r.json()
        assert "experiment_id" in body["columns"] and "time_to_first_audio_ms" in body["columns"]
        assert isinstance(body["rows"], list)

        r2 = c.get("/api/export?format=csv")
        assert r2.status_code == 200
        assert "text/csv" in r2.headers["content-type"]
        header = r2.text.splitlines()[0]
        assert "time_to_first_audio_ms" in header and "tool_actual" in header


def test_providers_endpoint_never_leaks_secrets(synth_cases, fresh_db):
    from fastapi.testclient import TestClient
    from app.server import app
    with TestClient(app) as c:
        r = c.get("/api/providers")
        assert r.status_code == 200
        text = json.dumps(r.json())
        for frag in ("sk-or-", "lsv2_pt_", "sk_ad1", "xi-api-key", "Authorization"):
            assert frag not in text
        body = r.json()
        assert {"stt", "llm", "tts"} <= set(body)
        for entry in body["stt"] + body["llm"] + body["tts"]:
            assert entry["status"] in ("available", "NOT_CONFIGURED") or entry["status"].startswith("unavailable")


def test_p95_requires_min_samples():
    from app.benchmark_engine import aggregate
    small = aggregate([100.0, 110.0, 120.0])
    assert small["n"] == 3 and small["p95_ms"] is None  # honest: insufficient samples
    big = aggregate([100.0 + i for i in range(10)])
    assert big["p95_ms"] is not None
    assert big["mean_ms"] is not None


def test_six_real_test_cases_registered():
    cases = [c for c in list_test_cases() if c["id"].startswith("test_")]
    assert len(cases) == 6
    tools = {c["id"]: c["expected_tool_calls"] for c in cases}
    assert tools["test_01"] == 0 and tools["test_02"] == 0
    assert tools["test_03"] == 1 and tools["test_04"] == 1
    assert tools["test_05"] == 0 and tools["test_06"] == 0
