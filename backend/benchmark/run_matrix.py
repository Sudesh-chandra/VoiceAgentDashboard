"""Experiment matrix runner (§25–29): same WAVs × generated pipeline combos ×
repetitions, with hard cost caps and honest aggregation.

Usage (from backend/):
  .venv/Scripts/python -m benchmark.run_matrix \
      --tests test_01,test_02,test_03 \
      --llm "openrouter:openai/gpt-4o-mini,mock:echo" \
      --tts elevenlabs,mock \
      --runs 2 --max-experiments 24

Every experiment gets an experiment_id (EXP-YYYY-NNN) recorded on each run;
pipelines are named <stt>-<llm>-<tts> (semantic, not a DB surrogate). A single
cost guard: pipelines × tests × runs must not exceed --max-experiments.
"""
from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.benchmark_engine import BenchmarkEngine, aggregate  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.pipeline import PipelineConfig  # noqa: E402
from app.registry import list_test_cases  # noqa: E402


def gen_combinations(stt: list[str], llm: list[str], tts: list[str]) -> list[dict]:
    """Cartesian product of the requested stage choices (validity is enforced
    later by the engine: unavailable providers fail the run honestly, they are
    not silently swapped)."""
    return [
        {"stt": a, "llm": b, "tts": c}
        for a, b, c in itertools.product(stt, llm, tts)
    ]


def pipeline_name(c: dict) -> str:
    llm = c["llm"].replace("openrouter:", "or-").replace(":", "-")
    return f"{c['stt']}-{llm}-{c['tts']}"


async def run_matrix(args: argparse.Namespace) -> dict:
    settings = get_settings()
    cases = [c["id"] for c in list_test_cases() if c["id"].startswith("test_")]
    if args.tests:
        cases = [t.strip() for t in args.tests.split(",") if t.strip()]

    combos = gen_combinations(
        [s.strip() for s in args.stt.split(",")],
        [l.strip() for l in args.llm.split(",")],
        [t.strip() for t in args.tts.split(",")],
    )

    total = len(combos) * len(cases) * args.runs
    if total > args.max_experiments:
        raise SystemExit(
            f"cost guard: {len(combos)} pipelines × {len(cases)} tests × {args.runs} runs "
            f"= {total} executions exceeds MAX_EXPERIMENTS={args.max_experiments}. "
            "Reduce scope or raise --max-experiments explicitly."
        )
    print(f"[matrix] {len(combos)} pipeline(s) × {len(cases)} case(s) × {args.runs} run(s) "
          f"= {total} executions (cap {args.max_experiments})")

    experiment_id = args.experiment_id or f"EXP-{datetime.now().year}-{int(time.time()) % 100000:05d}"
    engine = BenchmarkEngine()
    results: list[dict] = []

    for i, combo in enumerate(combos, 1):
        pid = pipeline_name(combo)
        print(f"[matrix] pipeline {i}/{len(combos)}: {pid}")
        for run_no in range(1, args.runs + 1):
            for tc in cases:
                cfg = PipelineConfig(pipeline_id=pid, stt=combo["stt"],
                                     llm=combo["llm"], tts=combo["tts"],
                                     tool=args.tool)
                try:
                    r = await engine.run_single(tc, cfg, live=False)
                except Exception as e:
                    # engine persists failures itself; this guards runner-level bugs
                    r = {"test_case_id": tc, "success": False, "error_type": type(e).__name__,
                         "error_detail": str(e)[:200], "timings": {}, "run_id": None,
                         "pipeline_id": pid, "trace_id": None, "tool_calls": []}
                r["experiment_id"] = experiment_id
                r["run_no"] = run_no
                # persist experiment identity on the stored row
                if r.get("run_id"):
                    from app.storage.db import get_db
                    get_db().execute(
                        "UPDATE benchmark_runs SET experiment_id=? WHERE run_id=?",
                        (experiment_id, r["run_id"]))
                results.append(r)
                t = r.get("timings") or {}
                ttfa = t.get("time_to_first_audio_ms")
                status = "ok" if r.get("success") else f"FAIL({r.get('failure_stage')})"
                print(f"  {tc} run{run_no}: {status} ttfa={ttfa and round(ttfa)}ms "
                      f"tools={len(r.get('tool_calls') or [])}")

    return {"experiment_id": experiment_id,
            "pipelines": [pipeline_name(c) for c in combos],
            "results": results}


def summarize(report: dict) -> dict:
    """Per-pipeline aggregates from the runs of this experiment only."""
    by_pipeline: dict[str, list[dict]] = {}
    for r in report["results"]:
        by_pipeline.setdefault(r.get("pipeline_id", "?"), []).append(r)

    def agg_for(rows: list[dict], key_path: str) -> dict:
        vals = []
        for r in rows:
            t = r.get("timings") or {}
            v = t.get(key_path)
            if v is not None:
                vals.append(v)
        return aggregate(vals)

    summary = {}
    for pid, rows in by_pipeline.items():
        ok = [r for r in rows if r.get("success")]
        tool_counts: dict[str, int] = {}
        for r in rows:
            n = len(r.get("tool_calls") or [])
            tool_counts[r["test_case_id"]] = n
        summary[pid] = {
            "runs": len(rows),
            "successful": len(ok),
            "failed": len(rows) - len(ok),
            "ttfa": agg_for(rows, "time_to_first_audio_ms"),
            "stt": agg_for(rows, "stt_latency_ms"),
            "llm_ttft": agg_for(rows, "llm_ttft_ms"),
            "tool": agg_for(rows, "tool_latency_ms"),
            "tts_first": agg_for(rows, "tts_first_audio_ms"),
            "total": agg_for(rows, "total_response_latency_ms"),
            "tool_calls_by_case": tool_counts,
        }
    return summary


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Voice-agent benchmark matrix runner")
    p.add_argument("--tests", default="")
    p.add_argument("--stt", default="deepgram")
    p.add_argument("--llm", default="openrouter:openai/gpt-4o-mini")
    p.add_argument("--tts", default="elevenlabs")
    p.add_argument("--tool", default="auto")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--max-experiments", type=int,
                   default=int(__import__("os").environ.get("MAX_EXPERIMENT_RUNS", "100")))
    p.add_argument("--experiment-id", default="")
    p.add_argument("--out", default="", help="write JSON report here")
    return p


def main() -> None:
    args = parser().parse_args()
    report = asyncio.run(run_matrix(args))
    report["summary"] = summarize(report)
    payload = json.dumps(report, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"[matrix] report written to {args.out}")
    print(json.dumps(report["summary"], indent=2, default=str))


if __name__ == "__main__":
    main()
