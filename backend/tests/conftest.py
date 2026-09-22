"""Shared pytest fixtures.

IMPORTANT (assignment §2): the six real test WAVs are supplied manually by the
evaluator — this suite never creates or ships files named test_01..test_06.
Tests use clearly-labeled synthetic fixtures (synth_*) generated at runtime so
CI can run without any real audio or API keys.
"""
from __future__ import annotations

import io
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Tests run in MOCK_MODE (deterministic offline providers, clearly labeled).
# The developer's real .env is authoritative for actual benchmarks — tests must
# never call paid APIs, so we force the flag before app modules read settings.
os.environ.setdefault("MOCK_MODE", "true")

from app.core.config import TEST_CASES_DIR, DATA_DIR, get_settings  # noqa: E402
get_settings.cache_clear()  # re-read with forced MOCK_MODE
get_settings().mock_mode = True


def _write_wav(path: Path, duration_s: float = 2.0, rate: int = 16000, lead_silence_s: float = 1.0) -> None:
    """Write a tiny synthetic wav: silence + tone + silence (~assignment convention)."""
    n_sil = int(rate * lead_silence_s)
    n_tone = int(rate * (duration_s - lead_silence_s))
    t = np.linspace(0, max(0.0, duration_s - lead_silence_s), n_tone, endpoint=False)
    tone = (np.sin(2 * np.pi * 220.0 * t) * 12000).astype(np.int16)
    audio = np.concatenate([np.zeros(n_sil, dtype=np.int16), tone]).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    path.write_bytes(buf.getvalue())


@pytest.fixture()
def synth_cases(monkeypatch) -> list[dict]:
    """Create 6 synthetic test cases in a temp test-case dir and point the app at it."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="vabd_cases_"))
    monkeypatch.setattr("app.core.config.TEST_CASES_DIR", tmp)
    # registry reads module-level TEST_CASES_DIR; patch there too
    import app.registry as reg
    monkeypatch.setattr(reg, "TEST_CASES_DIR", tmp)
    monkeypatch.setattr(reg, "META_PATH", tmp / "metadata.json")

    cases = [
        ("synth_01", "Capital question (no tool)", 0, "none"),
        ("synth_02", "Fun fact (no tool)", 0, "none"),
        ("synth_03", "Weather query (one tool)", 1, "none"),
        ("synth_04", "Search query (one tool)", 1, "none"),
        ("synth_05", "Weather with TV noise", 1, "tv"),
        ("synth_06", "Fact with white noise", 0, "white_noise"),
    ]
    meta = []
    for tc_id, name, tools, noise in cases:
        _write_wav(tmp / f"{tc_id}.wav")
        meta.append({"id": tc_id, "name": name, "description": name,
                     "expected_tool_calls": tools, "noise_type": noise,
                     "audio_path": f"{tc_id}.wav"})
    (tmp / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    """Isolated SQLite DB per test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.storage.db.DB_PATH", db_path)
    import app.storage.db as dbmod
    dbmod._db = None  # force re-create on next get_db()
    yield db_path
    dbmod._db = None
