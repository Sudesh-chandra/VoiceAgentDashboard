"""Cross-provider execution harness (assignment §11/§12).

Runs the SAME audio through every STT provider/model and the SAME text through
every TTS provider/model via the app's real adapters, printing a compact table
of measured results. Used to generate the values in MULTI_MODEL_TEST_REPORT.md.
Real providers only (no mocks) — requires the matching keys in .env.

Usage (from backend/):
  .venv/Scripts/python scripts/provider_matrix.py --stt --wav test_01
  .venv/Scripts/python scripts/provider_matrix.py --tts
  .venv/Scripts/python scripts/provider_matrix.py --stt --tts --wav test_01,test_02
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import TEST_CASES_DIR  # noqa: E402
from app.pipeline import read_wav  # noqa: E402
from app.voice import build_stt, build_tts  # noqa: E402
from app.core.config import STT_MODELS, TTS_MODELS  # noqa: E402

TTS_TEXT = "The benchmark reports time to first audio for this voice pipeline."


async def run_stt(spec: str, wav_path: Path) -> dict:
    raw, _dur, _rate, meta = read_wav(wav_path)
    stt = build_stt(spec)
    first: list[float] = []

    async def _on_partial(_t: str) -> None:
        if not first:
            first.append(time.perf_counter())

    t0 = time.perf_counter()
    try:
        transcript, extras = await stt.transcribe(raw, _on_partial)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return {
            "spec": spec, "provider": stt.name, "model": stt.model, "ok": True,
            "latency_ms": ms, "first_result_ms": round((first[0] - t0) * 1000, 1) if first else None,
            "confidence": extras.get("confidence"), "key_account": extras.get("key_account"),
            "chars": len(transcript), "transcript": transcript[:100],
            "audio_ms": meta["duration_ms"],
        }
    except Exception as e:
        return {"spec": spec, "provider": getattr(stt, "name", "?"), "model": getattr(stt, "model", "?"),
                "ok": False, "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error_type": type(e).__name__, "error": str(e)[:200]}


async def run_tts(spec: str) -> dict:
    size = 0
    chunks = 0
    first: list[float] = []
    t0 = time.perf_counter()
    try:
        tts = build_tts(spec)
        t0 = time.perf_counter()
        async for chunk in tts.stream(TTS_TEXT):
            if chunk:
                chunks += 1
                if not first:
                    first.append(time.perf_counter())
                size += len(chunk)
        total = round((time.perf_counter() - t0) * 1000, 1)
        return {"spec": spec, "provider": tts.name, "model": tts.model, "ok": True,
                "voice": getattr(tts, "voice_id", None), "key_account": getattr(tts, "last_account", None),
                "first_audio_ms": round((first[0] - t0) * 1000, 1) if first else None,
                "total_ms": total, "mode": "STREAMING" if chunks > 1 else "BUFFERED_RESPONSE",
                "bytes": size, "text": TTS_TEXT}
    except Exception as e:
        return {"spec": spec, "provider": spec.split(":")[0], "model": spec.split(":")[-1],
                "ok": False, "total_ms": round((time.perf_counter() - t0) * 1000, 1),
                "error_type": type(e).__name__, "error": str(e)[:200]}


def _stt_specs() -> list[str]:
    return [f"{prov}:{m['id']}" for prov, models in STT_MODELS.items() if prov != "mock" for m in models]


def _tts_specs() -> list[str]:
    return [f"{prov}:{m['id']}" for prov, models in TTS_MODELS.items() if prov != "mock" for m in models]


async def main() -> int:
    ap = argparse.ArgumentParser(description="Real-provider STT/TTS cross matrix")
    ap.add_argument("--stt", action="store_true", help="run all STT providers on the same WAV")
    ap.add_argument("--tts", action="store_true", help="run all TTS providers on the same text")
    ap.add_argument("--wav", default="test_01", help="test case id(s), comma-separated")
    args = ap.parse_args()
    if not (args.stt or args.tts):
        ap.error("choose --stt and/or --tts")

    rc = 0
    if args.stt:
        for tc in [t.strip() for t in args.wav.split(",") if t.strip()]:
            wav = TEST_CASES_DIR / f"{tc}.wav"
            print(f"\n=== STT matrix — same WAV: {wav.name} ===")
            for spec in _stt_specs():
                r = await run_stt(spec, wav)
                if r["ok"]:
                    conf = f"conf={r['confidence']:.3f} " if isinstance(r.get("confidence"), float) else ""
                    acct = f" key={r['key_account']}" if r.get("key_account") else ""
                    print(f"  {r['spec']:<28} OK  {r['latency_ms']:>9.1f} ms  {conf}"
                          f"chars={r['chars']}{acct}\n    “{r['transcript']}”")
                else:
                    rc = 1
                    print(f"  {r['spec']:<28} FAIL {r['error_type']}: {r['error']}")

    if args.tts:
        print(f"\n=== TTS matrix — same text: “{TTS_TEXT}” ===")
        for spec in _tts_specs():
            r = await run_tts(spec)
            if r["ok"]:
                acct = f" key={r['key_account']}" if r.get("key_account") else ""
                print(f"  {r['spec']:<36} OK  first={r['first_audio_ms']:>7.1f} ms  total={r['total_ms']:>7.1f} ms"
                      f"  {r['mode']}  {r['bytes']} B{acct}")
            else:
                rc = 1
                print(f"  {r['spec']:<36} FAIL {r['error_type']}: {r['error']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
