"""End-to-end smoke test: live session over the real HTTP + WebSocket stack.

Usage:  python scripts/smoke_live.py

Registers a temporary synthetic test case (never one of the evaluator's six),
runs it through POST /api/runs/live, subscribes to the WebSocket, and prints the
live event log exactly as the dashboard receives it. Cleans up after itself.
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import wave
from pathlib import Path

import numpy as np

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from app.core.config import TEST_CASES_DIR
    from app.registry import upsert_metadata, _load_meta

    # 1. temporary synthetic case (clearly not one of the six evaluator files)
    rate = 16000
    t = np.linspace(0, 1, rate, endpoint=False)
    audio = (np.sin(2 * np.pi * 220 * t) * 12000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    (TEST_CASES_DIR / "smoke_01.wav").write_bytes(buf.getvalue())
    upsert_metadata({"id": "smoke_01", "name": "Smoke test case", "description": "temporary",
                     "expected_tool_calls": 1, "noise_type": "none", "audio_path": "smoke_01.wav"})

    try:
        from fastapi.testclient import TestClient
        from app.server import app

        with TestClient(app) as client:
            r = client.post("/api/runs/live", json={
                "test_case_ids": ["smoke_01"],
                "pipeline": {"stt": "mock", "llm": "mock:echo", "tts": "mock", "tool": "auto"},
            })
            sid = r.json()["session_id"]
            print(f"live session: {sid}")
            with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
                while True:
                    ev = json.loads(ws.receive_text())
                    if ev.get("type") == "session_done":
                        print(f"session done — success={ev.get('success')} "
                              f"ttfa={ev.get('time_to_first_audio_ms')} ms")
                        break
                    ts = ev.get("ts_ms")
                    ts_s = f"{ts:>8.1f}" if isinstance(ts, (int, float)) else "        "
                    print(f"[{ts_s}] {ev.get('stage', 'live'):>5} | {ev.get('label', '')}")
        return 0
    finally:
        # cleanup: remove smoke artifacts from the real data dir
        (TEST_CASES_DIR / "smoke_01.wav").unlink(missing_ok=True)
        meta = [e for e in _load_meta() if e.get("id") != "smoke_01"]
        (TEST_CASES_DIR / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        db = TEST_CASES_DIR.parent / "vabd.db"
        for suffix in ("", "-wal", "-shm"):
            db.with_name(f"vabd.db{suffix}").unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
