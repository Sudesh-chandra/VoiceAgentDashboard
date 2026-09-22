"""Security-relevant tests: upload validation, path traversal, secrets, privacy (§17)."""
from __future__ import annotations

import io
import json
import wave

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings, TEST_CASES_DIR


@pytest.fixture()
def client(synth_cases):
    from app.server import app
    return TestClient(app)


def _wav_bytes(duration_s: float = 1.0) -> bytes:
    rate = 16000
    n = int(rate * duration_s)
    audio = (np.sin(2 * np.pi * 300.0 * np.linspace(0, duration_s, n, endpoint=False)) * 10000).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    return buf.getvalue()


def test_upload_rejects_non_wav(client):
    r = client.post("/api/test-cases/upload", files={"file": ("evil.exe", b"MZ\x90\x00", "application/octet-stream")})
    assert r.status_code == 400


def test_upload_rejects_wav_magic_spoof(client):
    # .wav extension but NOT a RIFF/WAVE container
    r = client.post("/api/test-cases/upload", files={"file": ("fake.wav", b"not really a wav" * 10, "audio/wav")})
    assert r.status_code == 400


def test_upload_rejects_oversized(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "max_upload_mb", 0)  # 0 MB cap ⇒ everything oversized; auto-restored
    r = client.post("/api/test-cases/upload", files={"file": ("big.wav", _wav_bytes(), "audio/wav")})
    assert r.status_code == 413  # 413 Request Entity Too Large


def test_upload_accepts_valid_wav(client):
    r = client.post(
        "/api/test-cases/upload",
        files={"file": ("my_case.wav", _wav_bytes(), "audio/wav")},
        data={"name": "My Case", "expected_tool_calls": "1", "noise_type": "tv"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    cases = client.get("/api/test-cases").json()["test_cases"]
    assert any(c["id"] == body["test_case_id"] for c in cases)


def test_audio_endpoint_sandboxed(client):
    r = client.get("/api/test-cases/..%2f..%2fetc%2fpasswd/audio")
    assert r.status_code in (404, 422)


def test_unknown_test_case_rejected_in_run(client, synth_cases):
    r = client.post("/api/runs", json={
        "test_case_ids": ["does_not_exist"],
        "pipeline": {"stt": "mock", "llm": "mock:echo", "tts": "mock", "tool": "mock_weather"},
    })
    assert r.status_code == 404


def test_invalid_pipeline_rejected(client):
    # Malformed model spec -> 422 from the request pattern.
    r = client.post("/api/runs", json={
        "test_case_ids": ["synth_01"],
        "pipeline": {"stt": "deepgram:nova-2:extra:bits", "llm": "mock:echo",
                     "tts": "mock:mock-voice", "tool": "mock_weather"},
    })
    assert r.status_code == 422
    # Unknown provider -> 409 from the factory allowlist (never silently mapped).
    r2 = client.post("/api/runs", json={
        "test_case_ids": ["synth_01"],
        "pipeline": {"stt": "hax", "llm": "mock:echo",
                     "tts": "mock:mock-voice", "tool": "mock_weather"},
    })
    assert r2.status_code == 409
    # Unknown model on a known provider -> 409 as well.
    r3 = client.post("/api/runs", json={
        "test_case_ids": ["synth_01"],
        "pipeline": {"stt": "deepgram:not-a-model", "llm": "mock:echo",
                     "tts": "mock:mock-voice", "tool": "mock_weather"},
    })
    assert r3.status_code == 409
    assert "not-a-model" in r3.json()["detail"]


def test_llm_choice_whitelisted(client):
    r = client.post("/api/runs", json={
        "test_case_ids": ["synth_01"],
        "pipeline": {"stt": "mock:mock-1", "llm": "gpt-9-ultra",
                     "tts": "mock:mock-voice", "tool": "mock_weather"},
    })
    assert r.status_code == 422


def test_no_secrets_in_config_endpoint(client):
    body = json.dumps(client.get("/api/config").json())
    for secret in (get_settings().openai_api_key, get_settings().deepgram_api_key, get_settings().langsmith_api_key):
        if secret:
            assert secret not in body


def test_redaction_filter(client):
    from app.core.logging_config import redact
    assert redact("key sk-abc123def456ghi789 end") == "key [REDACTED] end"
    assert "api_key=hunter2" not in redact("api_key=hunter2")


@pytest.mark.asyncio
async def test_langsmith_privacy_hashing(synth_cases, fresh_db, monkeypatch):
    from app.core.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "langsmith_tracing", True)
    monkeypatch.setattr(s, "langsmith_api_key", "test")
    from app.observability.langsmith_wiring import _maybe_text
    assert _maybe_text("hello world").startswith("sha256:")
    assert _maybe_text("hello world") != "hello world"
