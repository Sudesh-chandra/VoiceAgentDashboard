"""Concurrency engine tests (§15) — deterministic mock pipeline."""
from __future__ import annotations

import pytest

from app.pipeline import PipelineConfig
from app.benchmark_engine import ConcurrencyEngine


@pytest.mark.asyncio
async def test_concurrency_levels_report_structure(synth_cases, fresh_db):
    cfg = PipelineConfig(pipeline_id="p-conc", stt="mock", llm="mock:echo", tts="mock", tool="mock_weather")
    out = await ConcurrencyEngine().run_concurrency_test(["synth_03"], cfg, levels=[1, 5], runs_per_level=2)
    assert out["test_id"].startswith("conc_")
    assert [l["level"] for l in out["levels"]] == [1, 5]
    for lvl in out["levels"]:
        assert lvl["runs"] == lvl["level"] * 2
        assert lvl["successful"] + lvl["failed"] == lvl["runs"]
        assert 0 <= lvl["success_rate_pct"] <= 100
        assert "ttfa" in lvl and "total" in lvl
        assert lvl["wall_clock_ms"] > 0


@pytest.mark.asyncio
async def test_concurrency_level_capped(synth_cases, fresh_db, monkeypatch):
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "max_concurrency", 10)
    cfg = PipelineConfig(pipeline_id="p-conc", stt="mock", llm="mock:echo", tts="mock", tool="none")
    out = await ConcurrencyEngine().run_concurrency_test(["synth_01"], cfg, levels=[1, 5, 10, 999], runs_per_level=1)
    levels = [l["level"] for l in out["levels"]]
    assert levels == [1, 5, 10]  # 999 clamped to 10; deduped + sorted
