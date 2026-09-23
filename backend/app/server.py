"""FastAPI application: REST + WebSocket live event streaming (§16/§17).

Security posture:
- Uploads: streamed with a hard size cap (DoS-safe), magic bytes + parseability
  validated, filenames sanitized, storage sandboxed (strict relative_to check).
- CORS restricted to configured dashboard origins; credentials disabled.
- No secrets in any API response; provider keys stay server-side.
- Concurrency params clamped server-side to settings caps.
- Live WS: bounded queues (slow consumers drop, never balloon memory).
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, WebSocket, WebSocketDisconnect, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, field_validator

from .core.config import get_settings, provider_availability
from .core.logging_config import setup_logging
from .core.metrics import now_wall
from .registry import (list_test_cases, get_test_case, save_upload, upsert_metadata,
                       resolve_audio_path, import_voice_files)
from .benchmark_engine import BenchmarkEngine, ReliabilityEngine, ConcurrencyEngine, ComparisonEngine
from .observability.langsmith_wiring import canonical_trace_url
from .pipeline import PipelineConfig
from .storage.db import get_db
from .voice import ProviderUnavailableError, llm_choices

setup_logging()
app = FastAPI(title="Voice Agent Benchmark Platform", version="0.1.0")

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

engine = BenchmarkEngine()
reliability = ReliabilityEngine()
concurrency = ConcurrencyEngine()
comparison = ComparisonEngine()


def new_session_id() -> str:
    from .core.metrics import new_id
    return new_id("sess")

# ---- live session fan-out ----------------------------------------------------
# session_id -> {"queues": [asyncio.Queue], "buffer": [events]}
# Buffer keeps the full event list so a WS client that connects moments after
# the run started still receives everything (no lost-first-events race).

_MAX_QUEUE = 500
_MAX_BUFFER = 2000
_live: dict[str, dict] = {}
_live_lock = asyncio.Lock()


async def publish(session_id: str, payload: dict) -> None:
    async with _live_lock:
        session = _live.get(session_id)
        if session is None:
            return
        session["buffer"].append(payload)
        if len(session["buffer"]) > _MAX_BUFFER:
            del session["buffer"][: len(session["buffer"]) - _MAX_BUFFER]
        queues = list(session["queues"])
    for q in queues:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # slow consumer: drop rather than block the pipeline


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "time": now_wall(), "mock_mode": settings.mock_mode}


@app.get("/api/availability")
async def availability() -> dict:
    """Non-secret provider availability for the dashboard (§17D)."""
    return provider_availability()


@app.get("/api/config")
async def config_info() -> dict:
    """Alias of /api/availability (kept for the existing dashboard)."""
    return provider_availability()


@app.get("/api/test-cases")
async def test_cases() -> dict:
    # Auto-import the evaluator's voice/ recordings on first call if not yet registered.
    if not list_test_cases():
        import_voice_files()
    return {"test_cases": list_test_cases()}


@app.post("/api/test-cases/upload")
async def upload_test_case(file: UploadFile = File(...), name: str = Form(""),
                           description: str = Form(""),
                           expected_tool_calls: int = Form(0),
                           noise_type: str = Form("none")) -> dict:
    """Upload a .wav test case. Size-capped while streaming (DoS-safe), magic-byte
    + parseability validated, filename sanitized, stored sandboxed."""
    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    chunks: list[bytes] = []
    received = 0
    while True:
        chunk = await file.read(1024 * 512)
        if not chunk:
            break
        received += len(chunk)
        if received > max_bytes:
            raise HTTPException(status_code=413, detail="file too large")
        chunks.append(chunk)
    content = b"".join(chunks)
    try:
        path = save_upload(file.filename or "unknown.wav", content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    tc_id = f"tc_{path.stem[-8:]}"[:64]
    try:
        upsert_metadata({
            "id": tc_id, "name": (name or path.stem)[:120], "description": description[:500],
            "expected_tool_calls": max(0, min(1, int(expected_tool_calls))),
            "noise_type": (noise_type or "none")[:40],
            "audio_path": path.name,
        })
    except ValueError as e:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "test_case_id": tc_id, "file": path.name}


@app.get("/api/test-cases/{tc_id}/audio")
async def serve_audio(tc_id: str):
    """Stream a test-case wav to the dashboard (sandboxed path)."""
    try:
        p = resolve_audio_path(tc_id)
    except (FileNotFoundError, PermissionError):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(p, media_type="audio/wav")


class PipelineRequest(BaseModel):
    """STT/TTS use model specs: 'provider' | 'provider:model' | 'provider:model:voice'.
    Model ids are validated against the static catalog (STT_MODELS/TTS_MODELS)
    at request time so an unknown model is rejected with a clear 422 — never
    silently mapped to another provider/model."""
    stt: str = Field(default="deepgram:nova-2", pattern="^[a-z0-9_-]+(:[A-Za-z0-9._-]+){0,2}$")
    llm: str = Field(default="")
    tts: str = Field(
        default=get_settings().default_tts_spec,
        pattern="^[a-z0-9_-]+(:[A-Za-z0-9._-]+){0,2}$",
    )
    tool: str = Field(default="auto", pattern="^(auto|weather|mock_weather|search|mock_search|none)$")
    pipeline_id: str | None = None

    @field_validator("llm")
    @classmethod
    def _llm_known(cls, v: str) -> str:
        if v == "":  # empty = default configured provider
            return v
        known = {c["id"] for c in llm_choices()}
        if v not in known:
            raise ValueError(f"unknown llm choice: {v}")
        return v


class RunRequest(BaseModel):
    test_case_ids: list[str] = Field(min_length=1, max_length=12)
    pipeline: PipelineRequest


class ConcurrencyRequest(BaseModel):
    test_case_ids: list[str] = Field(min_length=1, max_length=6)
    pipeline: PipelineRequest
    levels: list[int] = Field(default=[1, 5])
    runs_per_level: int = Field(default=2, ge=1, le=5)

    @field_validator("levels")
    @classmethod
    def _clamp_levels(cls, v: list[int]) -> list[int]:
        cap = get_settings().max_concurrency
        return sorted({max(1, min(l, cap)) for l in v})[:4]


def _cfg_from(req: PipelineRequest) -> PipelineConfig:
    from .voice import default_llm_choice

    llm = req.llm or default_llm_choice()
    return PipelineConfig(
        pipeline_id=req.pipeline_id or f"{req.stt}|{llm}|{req.tts}|{req.tool}",
        stt=req.stt, llm=llm, tts=req.tts, tool=req.tool,
    )


def _guard_pipeline_available(cfg: PipelineConfig) -> None:
    """Reject pipelines using unconfigured/unknown providers BEFORE running (§17D):
    clear 409, no silent fallback, no fabricated results. The factories are the
    single source of truth for credential + allowlist checks."""
    from .voice import build_stt, build_tts, ProviderUnavailableError

    problems: list[str] = []
    for label, builder in (("STT", lambda: build_stt(cfg.stt)), ("TTS", lambda: build_tts(cfg.tts))):
        try:
            builder()
        except ProviderUnavailableError as e:
            problems.append(f"{label}: {e}")
        except ValueError as e:  # malformed spec
            problems.append(f"{label}: {e}")
    if cfg.llm.startswith("openrouter:") and not settings.openrouter_api_key:
        problems.append(f"LLM: OPENROUTER_API_KEY missing ({cfg.llm})")
    if problems:
        raise HTTPException(status_code=409, detail="; ".join(problems))


@app.post("/api/runs")
async def start_run(req: RunRequest) -> dict:
    """Run one or more test cases against a pipeline; persist every result."""
    cfg = _cfg_from(req.pipeline)
    _guard_pipeline_available(cfg)
    for tc in req.test_case_ids:
        if not get_test_case(tc):
            raise HTTPException(status_code=404, detail=f"unknown test case {tc}")
    results = [await engine.run_single(tc, cfg, live=False) for tc in req.test_case_ids]
    return {"results": results}


@app.post("/api/runs/live")
async def start_live_run(req: RunRequest) -> dict:
    """Start a live session run in the background; connect to
    /ws/sessions/{session_id} for real-time stage events."""
    cfg = _cfg_from(req.pipeline)
    _guard_pipeline_available(cfg)
    tc = req.test_case_ids[0]
    if not get_test_case(tc):
        raise HTTPException(status_code=404, detail=f"unknown test case {tc}")
    session_id = new_session_id()
    # Register the live channel BEFORE the background run starts publishing.
    async with _live_lock:
        _live[session_id] = {"queues": [], "buffer": []}
    engine.start_live_session_with_id(session_id, tc, cfg, publish)
    return {"session_id": session_id}


@app.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: str):
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
    async with _live_lock:
        session = _live.get(session_id)
        if session is None:
            await websocket.close(code=4404)
            return
        # replay buffered events so late subscribers miss nothing
        for ev in list(session["buffer"]):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                break
        session["queues"].append(q)
    try:
        while True:
            payload = await q.get()
            await websocket.send_text(json.dumps(payload))
    except WebSocketDisconnect:
        pass
    finally:
        async with _live_lock:
            session = _live.get(session_id)
            if session and q in session["queues"]:
                session["queues"].remove(q)


@app.get("/api/runs")
async def list_runs(limit: int = 100, experiment_id: str | None = None) -> dict:
    limit = max(1, min(limit, 500))
    where, params = "", [limit]
    if experiment_id:
        where = "WHERE experiment_id=?"
        params = [experiment_id, limit]
    rows = get_db().query(
        f"SELECT run_id, session_id, pipeline_id, test_case_id, timestamp, experiment_id,"
        " tts_first_audio_mode, stt_spec, llm_spec, tts_spec, tts_voice_id,"
        " success, failure_stage, error_type,"
        " input_duration_ms, stt_latency_ms, stt_first_result_ms, llm_ttft_ms, llm_completion_ms,"
        " tool_latency_ms, tts_first_audio_ms, tts_completion_ms, time_to_first_audio_ms,"
        " total_response_latency_ms, trace_id, trace_url, transcript, tool_calls_json, original_filename,"
        " file_size_bytes, sample_rate, channels, bit_depth, audio_format, stt_confidence,"
        " llm_prompt_tokens, llm_completion_tokens, llm_total_tokens"
        f" FROM benchmark_runs {where} ORDER BY timestamp DESC LIMIT ?", tuple(params))
    for r in rows:
        try:
            r["tool_calls"] = json.loads(r.pop("tool_calls_json") or "[]")
        except Exception:
            r["tool_calls"] = []
        # Historical runs predate the persisted canonical URL: resolve lazily on
        # read (project coordinates are cached; no per-run API call).
        if r.get("trace_id") and not r.get("trace_url"):
            r["trace_url"] = canonical_trace_url(r["trace_id"])
    return {"runs": rows}


# ---- Provider health (lightweight, credential-free responses) -----------------

_providers_cache: dict[str, tuple[float, dict]] = {}
_PROVIDERS_TTL_S = 60.0


@app.get("/api/providers")
async def providers_health() -> dict:
    """Capability matrix without secrets. Static configuration is combined with
    cheap live probes (free endpoints only), cached 60s to avoid cost/hammers."""
    import time as _t
    import httpx as _hx

    now_s = _t.monotonic()
    if "all" in _providers_cache and now_s - _providers_cache["all"][0] < _PROVIDERS_TTL_S:
        return _providers_cache["all"][1]

    avail = provider_availability()
    out: dict = {"environment": avail.get("environment"), "mock_mode": settings.mock_mode,
                 "stt": [], "llm": [], "tts": [], "tools": []}

    async def live_status(url: str, headers: dict | None = None) -> str | None:
        """Free-endpoint probe; returns None when no cheap check exists."""
        try:
            async with _hx.AsyncClient(timeout=8) as client:
                r = await client.get(url, headers=headers or {})
                return "available" if r.status_code == 200 else f"unavailable (HTTP {r.status_code})"
        except Exception as e:
            return f"unavailable ({type(e).__name__})"

    s = settings
    dg_live: str | None = None
    if any(c["available"] for c in avail["stt"]["choices"] if c["provider"] == "deepgram"):
        # probe the first configured account (both share the same provider status)
        dg_key = s.deepgram_api_key or s.deepgram2_api_key
        live = await live_status("https://api.deepgram.com/v1/projects",
                                 {"Authorization": f"Token {dg_key}"})
        # one probe covers every Deepgram-hosted model (STT + Aura TTS)
        dg_live = live if live and live.startswith("unavailable") else None
    el_live: str | None = None
    if any(c["available"] for c in avail["tts"]["choices"] if c["provider"] == "elevenlabs"):
        el_key = s.elevenlabs_api_key or s.elevenlabs2_api_key
        live = await live_status("https://api.elevenlabs.io/v1/user",
                                 {"xi-api-key": el_key})
        el_live = live if live and live.startswith("unavailable") else None

    for c in avail["stt"]["choices"]:
        status = "available" if c["available"] else "NOT_CONFIGURED"
        if c["provider"] == "deepgram" and c["available"] and dg_live:
            status = dg_live
        out["stt"].append({"provider": c["provider"], "model": c["model"], "label": c["label"],
                           "spec": c["id"], "configured": bool(c["available"]), "status": status})

    for c in avail["llm"]["choices"]:
        entry = {"provider": c["id"], "model": c["id"].split(":", 1)[1] if ":" in c["id"] else "—",
                 "configured": bool(c["available"]), "status": "available" if c["available"] else "NOT_CONFIGURED"}
        if c["id"].startswith("openrouter") and c["available"]:
            live = await live_status("https://openrouter.ai/api/v1/models")
            entry["status"] = live if live and live.startswith("unavailable") else "available"
        out["llm"].append(entry)

    for c in avail["tts"]["choices"]:
        status = "available" if c["available"] else "NOT_CONFIGURED"
        if c["provider"] == "elevenlabs" and c["available"] and el_live:
            status = el_live
        elif c["provider"] == "deepgram" and c["available"] and dg_live:
            status = dg_live
        out["tts"].append({"provider": c["provider"], "model": c["model"], "label": c["label"],
                           "spec": c["id"], "configured": bool(c["available"]), "status": status})

    out["tools"].append({"provider": "weather", "model": "open-meteo", "configured": True, "status": "available"})
    search_cfg = bool(s.tavily_api_key)
    out["tools"].append({"provider": "web_search", "model": "tavily" if search_cfg else "mock (labeled)",
                         "configured": search_cfg, "status": "available" if search_cfg else "NOT_CONFIGURED"})

    _providers_cache["all"] = (now_s, out)
    return out


class ProviderTestRequest(BaseModel):
    """Single-provider probe (§20): test one STT or TTS selection in isolation
    before running a full benchmark. STT uses an existing test case's real WAV;
    TTS uses a fixed short sentence so every provider synthesizes the SAME text."""
    kind: str = Field(pattern="^(stt|tts)$")
    spec: str = Field(pattern="^[a-z0-9_-]+(:[A-Za-z0-9._-]+){0,2}$")
    test_case_id: str | None = None


@app.post("/api/provider-test")
async def provider_test(req: ProviderTestRequest) -> dict:
    """Execute ONE provider/model with real I/O and return its measured timings.
    Failures are reported per-provider — one failing provider never crashes the
    app — and error messages are the adapters' safe (key-free) strings."""
    if req.kind == "stt":
        if not req.test_case_id:
            raise HTTPException(status_code=422, detail="stt test requires test_case_id")
        try:
            tc = get_test_case(req.test_case_id)
            if not tc:
                raise HTTPException(status_code=404, detail=f"unknown test case {req.test_case_id}")
            audio_path = resolve_audio_path(req.test_case_id)
            from .pipeline import read_wav
            raw, _dur, _rate, meta = read_wav(audio_path)
            from .voice import build_stt
            stt = build_stt(req.spec)
            first: list[float] = []

            async def _on_partial(_t: str) -> None:
                if not first:
                    first.append(time.perf_counter())

            started = time.perf_counter()
            transcript, extras = await stt.transcribe(raw, _on_partial)
            return {
                "kind": "stt", "spec": req.spec, "provider": stt.name, "model": stt.model,
                "ok": True, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "first_result_ms": round((first[0] - started) * 1000, 1) if first else None,
                "transcript": transcript,
                "confidence": extras.get("confidence"),
                "audio": {k: meta[k] for k in ("sample_rate", "channels", "duration_ms")},
            }
        except HTTPException:
            raise
        except Exception as e:  # provider error: safe message, per-provider scope
            return {"kind": "stt", "spec": req.spec, "ok": False,
                    "error_type": type(e).__name__, "error": str(e)[:300]}

    # TTS probe: identical sentence across providers (§12: never compare
    # providers on different input text).
    text = "The benchmark reports time to first audio for this voice pipeline."
    try:
        from .voice import build_tts
        tts = build_tts(req.spec)
        started = time.perf_counter()
        first: list[float] = []
        size = 0
        chunks = 0
        async for chunk in tts.stream(text):
            if chunk:
                chunks += 1
                if not first:
                    first.append(time.perf_counter())
                size += len(chunk)
        total_ms = round((time.perf_counter() - started) * 1000, 1)
        return {
            "kind": "tts", "spec": req.spec, "provider": tts.name, "model": tts.model,
            "voice": getattr(tts, "voice_id", None),
            "ok": True, "text": text,
            "first_audio_ms": round((first[0] - started) * 1000, 1) if first else None,
            "total_ms": total_ms,
            "first_audio_mode": "STREAMING" if chunks > 1 else "BUFFERED_RESPONSE",
            "audio_bytes": size,
        }
    except Exception as e:
        return {"kind": "tts", "spec": req.spec, "ok": False,
                "error_type": type(e).__name__, "error": str(e)[:300]}


_EXPORT_COLS = [
    "experiment_id", "run_id", "test_case_id", "pipeline_id", "timestamp",
    "stt_spec", "llm_spec", "tts_spec", "tts_voice_id",
    "stt_model", "llm_model", "tts_model", "tts_first_audio_mode",
    "success", "failure_stage", "error_type",
    "input_duration_ms", "stt_latency_ms", "llm_ttft_ms", "llm_completion_ms",
    "tool_latency_ms", "tts_first_audio_ms", "tts_completion_ms",
    "time_to_first_audio_ms", "total_response_latency_ms", "trace_id", "trace_url",
    "llm_prompt_tokens", "llm_completion_tokens", "llm_total_tokens",
]


def _tool_count(row: dict) -> int:
    try:
        return len(json.loads(row.get("tool_calls_json") or "[]"))
    except Exception:
        return 0


@app.get("/api/export")
async def export_results(format: str = "json", experiment_id: str | None = None,
                         limit: int = 1000) -> Response:
    """Full-fidelity export for analysis. JSON preserves types; CSV is flat.
    Includes tool-expected/actual via metadata + tool_calls_json."""
    limit = max(1, min(limit, 5000))
    where, params = "", [limit]
    if experiment_id:
        where = "WHERE experiment_id=?"
        params = [experiment_id, limit]
    rows = get_db().query(
        f"SELECT {', '.join(_EXPORT_COLS)}, tool_calls_json FROM benchmark_runs {where}"
        " ORDER BY timestamp DESC LIMIT ?", tuple(params))
    meta = {c["id"]: c for c in list_test_cases()}
    for r in rows:
        r["tool_expected"] = meta.get(r["test_case_id"], {}).get("expected_tool_calls")
        r["tool_actual"] = _tool_count(r)
        r.pop("tool_calls_json", None)
    cols = _EXPORT_COLS + ["tool_expected", "tool_actual"]

    if format == "csv":
        import csv as _csv
        import io as _io
        buf = _io.StringIO()
        w = _csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in cols})
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="benchmark_runs.csv"'},
        )
    return JSONResponse(content={"columns": cols, "rows": [{c: r.get(c) for c in cols} for r in rows]})


@app.get("/api/runs/{run_id}")
async def run_detail(run_id: str) -> dict:
    row = get_db().query_one("SELECT * FROM benchmark_runs WHERE run_id=?", (run_id,))
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    row["events"] = json.loads(row.get("events_json") or "[]")
    row["tool_calls"] = json.loads(row.get("tool_calls_json") or "[]")
    row["transcript"] = row.get("transcript")
    row["response_text"] = row.get("response_text")
    row["audio_metadata"] = {
        "original_filename": row.get("original_filename"),
        "file_size_bytes": row.get("file_size_bytes"),
        "sample_rate": row.get("sample_rate"),
        "channels": row.get("channels"),
        "bit_depth": row.get("bit_depth"),
        "audio_format": row.get("audio_format"),
    }
    # Canonical (tenant-scoped) trace URL persisted at trace time; older rows
    # get it resolved lazily here. Legacy project-name URL remains the
    # client-side fallback when LangSmith is disabled/unreachable — nothing is
    # invented either way.
    if row.get("trace_id") and not row.get("trace_url"):
        row["trace_url"] = canonical_trace_url(row["trace_id"])
    row["langsmith_project"] = settings.langsmith_project
    return row


@app.get("/api/reliability")
async def reliability_summary(pipeline_id: str | None = None) -> dict:
    return reliability.failure_summary(pipeline_id)


@app.post("/api/concurrency")
async def run_concurrency(req: ConcurrencyRequest) -> dict:
    cfg = _cfg_from(req.pipeline)
    _guard_pipeline_available(cfg)
    for tc in req.test_case_ids:
        if not get_test_case(tc):
            raise HTTPException(status_code=404, detail=f"unknown test case {tc}")
    return await concurrency.run_concurrency_test(req.test_case_ids, cfg, req.levels, req.runs_per_level)


@app.get("/api/compare")
async def compare(pipeline_ids: str | None = None) -> dict:
    pids = [p.strip() for p in pipeline_ids.split(",") if p.strip()] if pipeline_ids else None
    return {"pipelines": comparison.compare(pids)}
