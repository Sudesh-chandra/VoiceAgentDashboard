"""Benchmark engine (§6/§10/§14/§15): runs, reliability, concurrency, comparison.

Real measurements only. Failures are data, never hidden inside averages.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import time
from typing import Any

from .core.metrics import new_id, now_wall
from .pipeline import PipelineConfig, PipelineRunner, EventSink, STTError, LLMError, TTSError
from .registry import resolve_audio_path, resolve_transcript
from .storage.db import get_db, save_benchmark_run, save_session, complete_session, now


def aggregate(values: list[float | None]) -> dict[str, Any]:
    """Honest aggregation. p95 requires n >= 5; below that it is None (the UI
    renders 'insufficient samples') rather than a statistically meaningless number."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return {"n": 0, "mean_ms": None, "median_ms": None, "p95_ms": None, "min_ms": None, "max_ms": None}
    return {
        "n": len(vals),
        "mean_ms": round(statistics.fmean(vals), 1),
        "median_ms": round(statistics.median(vals), 1),
        "p95_ms": (round(vals[min(len(vals) - 1, int(len(vals) * 0.95))], 1)
                   if len(vals) >= 5 else None),
        "min_ms": round(vals[0], 1),
        "max_ms": round(vals[-1], 1),
    }


class BenchmarkEngine:
    """Executes benchmark runs and persists every result (success or failure)."""

    def __init__(self) -> None:
        self._active_sessions: dict[str, list[asyncio.Task]] = {}

    async def run_single(
        self,
        test_case_id: str,
        config: PipelineConfig,
        live: bool = True,
    ) -> dict[str, Any]:
        """Run one test case; persist + return the result dict."""
        session_id = new_id("sess")
        save_session(session_id, config.pipeline_id, "run", test_case_id)
        queue: list = []

        async def sink(ev) -> None:
            queue.append(ev)

        runner = PipelineRunner(config, sink if live else None,
                                mock_transcript=resolve_transcript(test_case_id))
        audio_path = str(resolve_audio_path(test_case_id))

        res = await runner.execute(audio_path, test_case_id)

        save_benchmark_run(res)
        complete_session(session_id, "completed" if res.success else "failed")
        self._write_artifact(res)
        return res.to_dict()

    def start_live_session_with_id(self, session_id: str, test_case_id: str, config: PipelineConfig, publish) -> None:
        """Schedule a live run as a background task; session_id is chosen (and the
        live channel registered) by the caller so the WS client can never miss it."""
        save_session(session_id, config.pipeline_id, "run", test_case_id)
        asyncio.get_running_loop().create_task(self._live_run(session_id, test_case_id, config, publish))

    async def _live_run(self, session_id: str, test_case_id: str, config: PipelineConfig, publish) -> None:
        try:
            async def sink(ev) -> None:
                await publish(session_id, ev.to_dict())

            runner = PipelineRunner(config, sink, mock_transcript=resolve_transcript(test_case_id))
            audio_path = str(resolve_audio_path(test_case_id))
            res = await runner.execute(audio_path, test_case_id)
            save_benchmark_run(res)
            complete_session(session_id, "completed" if res.success else "failed")
            self._write_artifact(res)
            await publish(session_id, {"type": "session_done", "session_id": session_id,
                                       "success": res.success,
                                       "time_to_first_audio_ms": res.timings.time_to_first_audio_ms})
        except Exception as e:  # never let a background run die silently
            await publish(session_id, {"type": "session_done", "session_id": session_id,
                                       "success": False, "error": type(e).__name__})
            complete_session(session_id, "failed")

    async def run_suite(
        self,
        test_case_ids: list[str],
        config: PipelineConfig,
    ) -> list[dict[str, Any]]:
        results = []
        for tc in test_case_ids:
            results.append(await self.run_single(tc, config))
        return results

    def _write_artifact(self, res) -> None:
        from .core.config import RESULTS_DIR
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        out = RESULTS_DIR / f"{res.run_id}.json"
        out.write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")


class ReliabilityEngine:
    """Failure capture and breakdown (§14)."""

    def failure_summary(self, pipeline_id: str | None = None) -> dict[str, Any]:
        where = "WHERE pipeline_id=?" if pipeline_id else ""
        params = (pipeline_id,) if pipeline_id else ()
        rows = get_db().query(f"SELECT success, failure_stage, error_type FROM benchmark_runs {where}", params)
        total = len(rows)
        failed = [r for r in rows if not r["success"]]
        by_stage: dict[str, int] = {}
        by_error: dict[str, int] = {}
        for r in failed:
            stage = r["failure_stage"] or "unknown"
            by_stage[stage] = by_stage.get(stage, 0) + 1
            et = r["error_type"] or "unknown"
            by_error[et] = by_error.get(et, 0) + 1
        return {
            "total_runs": total,
            "successful": total - len(failed),
            "failed": len(failed),
            "failure_rate_pct": round(len(failed) / total * 100, 1) if total else 0.0,
            "failure_by_stage": by_stage,
            "failure_by_error_type": by_error,
        }


class ConcurrencyEngine:
    """Basic concurrency test (§15): N parallel runs at levels 1/5/10 (configurable,
    clamped). Rate-limit safety: staggered launches + hard cap from settings."""

    async def run_concurrency_test(
        self,
        test_case_ids: list[str],
        config: PipelineConfig,
        levels: list[int],
        runs_per_level: int = 3,
    ) -> dict[str, Any]:
        from .core.config import get_settings

        s = get_settings()
        cap = max(1, min(s.max_concurrency, 10))
        levels = sorted({max(1, min(l, cap)) for l in levels})[:6]
        stagger = s.concurrency_stagger_ms / 1000.0
        test_id = new_id("conc")

        out_levels = []
        for level in levels:
            level_results = []
            tasks: list[asyncio.Task] = []

            async def one(i: int) -> dict[str, Any]:
                tc = test_case_ids[i % len(test_case_ids)]
                t0 = time.perf_counter()
                runner = PipelineRunner(config, mock_transcript=resolve_transcript(tc))
                try:
                    audio_path = str(resolve_audio_path(tc))
                    res = await runner.execute(audio_path, tc)
                    save_benchmark_run(res)
                    d = res.to_dict()
                except Exception as e:
                    d = {"success": False, "error_type": type(e).__name__,
                         "timings": {}, "failure_stage": "unknown", "error_detail": str(e)[:200]}
                wall = (time.perf_counter() - t0) * 1000
                d["wall_ms"] = round(wall, 1)
                d["run_index"] = i
                return d

            total_runs = level * runs_per_level
            # True wave semantics: `level` tasks run concurrently; a new launch
            # only starts when one finishes. Stagger adds launch spacing so we
            # never stampede providers beyond the requested level.
            sem = asyncio.Semaphore(level)

            async def launch(idx: int) -> dict[str, Any]:
                async with sem:
                    if stagger:
                        await asyncio.sleep(min(idx, level) * stagger / 2)
                    return await one(idx)

            tasks = [asyncio.create_task(launch(i)) for i in range(total_runs)]
            level_results = await asyncio.gather(*tasks)

            ok = [r for r in level_results if r["success"]]
            ttfas = [r["timings"].get("time_to_first_audio_ms") for r in ok]
            totals = [r["timings"].get("total_response_latency_ms") for r in ok]
            wall_total_ms = max((r.get("wall_ms") or 0) for r in level_results)
            record = {
                "level": level,
                "runs": len(level_results),
                "successful": len(ok),
                "failed": len(level_results) - len(ok),
                "success_rate_pct": round(len(ok) / len(level_results) * 100, 1),
                "ttfa": aggregate(ttfas),
                "total": aggregate(totals),
                "wall_clock_ms": round(wall_total_ms, 1),
                "throughput_rps": round(len(ok) / (wall_total_ms / 1000), 3) if wall_total_ms else None,
                "errors": [r.get("error_type") for r in level_results if not r["success"]],
            }
            out_levels.append(record)
            for i, r in enumerate(level_results):
                get_db().execute(
                    "INSERT INTO concurrency_runs (test_id, level, run_index, success, ttfa_ms, total_ms, error_type, wall_ms, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (test_id, level, i, 1 if r["success"] else 0,
                     (r.get("timings") or {}).get("time_to_first_audio_ms"),
                     (r.get("timings") or {}).get("total_response_latency_ms"),
                     r.get("error_type"), r.get("wall_ms"), now()),
                )
        return {"test_id": test_id, "levels": out_levels}


class ComparisonEngine:
    """Aggregate comparison across pipelines (§16F) — evidence, not verdicts."""

    def compare(self, pipeline_ids: list[str] | None = None) -> list[dict[str, Any]]:
        db = get_db()
        if pipeline_ids:
            marks = ",".join("?" for _ in pipeline_ids)
            rows = db.query(f"SELECT * FROM benchmark_runs WHERE pipeline_id IN ({marks})", tuple(pipeline_ids))
        else:
            rows = db.query("SELECT * FROM benchmark_runs")
        by_pipeline: dict[str, list] = {}
        for r in rows:
            by_pipeline.setdefault(r["pipeline_id"], []).append(r)
        out = []
        for pid, runs in by_pipeline.items():
            out.append({
                "pipeline_id": pid,
                "runs": len(runs),
                "success_rate_pct": round(sum(1 for r in runs if r["success"]) / len(runs) * 100, 1),
                "ttfa": aggregate([r["time_to_first_audio_ms"] for r in runs]),
                "stt": aggregate([r["stt_latency_ms"] for r in runs]),
                "llm_ttft": aggregate([r["llm_ttft_ms"] for r in runs]),
                "tool": aggregate([r["tool_latency_ms"] for r in runs]),
                "tts_first": aggregate([r["tts_first_audio_ms"] for r in runs]),
                "total": aggregate([r["total_response_latency_ms"] for r in runs]),
            })
        return out
