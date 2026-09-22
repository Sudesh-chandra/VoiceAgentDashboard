"""Test-case registry (§6/§13): the six evaluator-supplied recordings.

The real audio arrives via the `voice/` folder (or upload/CLI seeding) and is
copied byte-identical into data/test_cases/ as test_01.wav … test_06.wav with
schema-validated metadata. Filenames are sanitized; resolution is sandboxed to
data/test_cases/ with a strict relative_to() check.

TRANSCRIPT POLICY (§6B): transcripts ALWAYS come from the configured STT provider
processing the actual audio. `_MOCK_HINTS` exists ONLY for the labeled offline
mock STT (MOCK_MODE / unit tests) and is never used to bypass STT in real runs.
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
import wave
from pathlib import Path
from typing import Any

from .core.config import TEST_CASES_DIR, PROJECT_ROOT, get_settings

META_PATH = TEST_CASES_DIR / "metadata.json"
_ALLOWED_EXTS = {".wav"}
_MAX_META_ITEMS = 64
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
# The evaluator's recordings live in the workspace `voice/` folder (sibling of the
# repo checkout). Fall back to a repo-internal voice/ if present.
_VOICE_CANDIDATES = [PROJECT_ROOT.parent / "voice", PROJECT_ROOT / "voice"]
_VOICE_DIR = next((p for p in _VOICE_CANDIDATES if p.exists()), _VOICE_CANDIDATES[0])

# The six assignment slots with expected behavior (tools) and noise class.
# NOTE: test_05's actual recording is a noisy KNOWLEDGE question ("explain what is
# a REST API", Deepgram-verified) — it needs NO tool. Tool cases are 03/04 only.
CASE_SLOTS = [
    ("test_01", "Spoken query — no tool call", 0, "none"),
    ("test_02", "Spoken query — no tool call", 0, "none"),
    ("test_03", "Spoken query — exactly one tool call (weather)", 1, "none"),
    ("test_04", "Spoken query — exactly one tool call (web search)", 1, "none"),
    ("test_05", "Spoken query with background noise — no tool call", 0, "environmental"),
    ("test_06", "Spoken query with background noise", 0, "environmental"),
]

# Mock-mode hints ONLY (labeled offline STT in tests/CI). Real runs ignore these.
_MOCK_HINTS = {
    "test_01": "What is the capital of France?",
    "test_02": "Tell me a fun fact about octopuses.",
    "test_03": "What's the weather in Tokyo right now?",
    "test_04": "Search the web for the latest AI news.",
    "test_05": "Please explain what is a REST API in simple terms.",
    "test_06": "Tell me a fun fact about dolphins.",
}


def _sanitize(name: str) -> str:
    name = _SAFE_NAME.sub("_", Path(name).name)
    return name[-120:] if len(name) > 120 else name


def _load_meta() -> list[dict[str, Any]]:
    if not META_PATH.exists():
        return []
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return data[:_MAX_META_ITEMS]


def _validate_entry(e: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(e, dict):
        return None
    tc_id = e.get("id")
    audio = e.get("audio_path")
    if not isinstance(tc_id, str) or not re.fullmatch(r"[\w\-]{1,64}", tc_id):
        return None
    if not isinstance(audio, str) or Path(audio).suffix.lower() not in _ALLOWED_EXTS:
        return None
    if Path(audio).name != audio:  # no directory components
        return None
    expected_tools = e.get("expected_tool_calls", 0)
    if expected_tools not in (0, 1):
        expected_tools = 0
    return {
        "id": tc_id,
        "name": str(e.get("name", tc_id))[:120],
        "description": str(e.get("description", ""))[:500],
        "expected_tool_calls": expected_tools,
        "noise_type": str(e.get("noise_type", "none"))[:40],
        "audio_path": audio,
        "expected_behavior": str(e.get("expected_behavior", ""))[:200],
    }


def import_voice_files(overwrite: bool = False) -> list[dict[str, Any]]:
    """Copy the six evaluator recordings from the workspace `voice/` folder into
    data/test_cases/ as test_01..test_06 (byte-identical) and register metadata."""
    if not _VOICE_DIR.exists():
        return []
    wavs = sorted(_VOICE_DIR.glob("*.wav"),
                  key=lambda p: int("".join(c for c in p.stem if c.isdigit()) or 0))
    imported = []
    for i, src in enumerate(wavs[:6]):
        tc_id, name, tools, noise = CASE_SLOTS[i]
        dst = TEST_CASES_DIR / f"{tc_id}.wav"
        if overwrite or not dst.exists():
            shutil.copyfile(src, dst)
        imported.append({
            "id": tc_id, "name": name, "description": f"Imported from voice/{src.name}",
            "expected_tool_calls": tools, "noise_type": noise,
            "audio_path": dst.name,
            "expected_behavior": ("direct answer, zero tool calls" if tools == 0
                                  else "exactly one tool call"),
        })
    if imported:
        entries = _load_meta()
        have = {e.get("id") for e in entries}
        for e in imported:
            if e["id"] not in have:
                entries.append(e)
        META_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return imported


def list_test_cases() -> list[dict[str, Any]]:
    out = []
    for e in _load_meta():
        v = _validate_entry(e)
        if v and (TEST_CASES_DIR / v["audio_path"]).exists():
            out.append(v)
    return out


def get_test_case(tc_id: str) -> dict[str, Any] | None:
    for tc in list_test_cases():
        if tc["id"] == tc_id:
            return tc
    return None


def resolve_audio_path(tc_id: str) -> Path:
    """Sandboxed resolution: returned path is always inside TEST_CASES_DIR.
    Uses Path.relative_to (not startswith) so sibling-prefix dirs cannot pass."""
    tc = get_test_case(tc_id)
    if not tc:
        raise FileNotFoundError(f"unknown test case: {tc_id}")
    base = TEST_CASES_DIR.resolve()
    p = (base / tc["audio_path"]).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        raise PermissionError("path traversal blocked")
    if not p.exists():
        raise FileNotFoundError(f"audio missing: {tc['audio_path']}")
    return p


def mock_transcript_hint(tc_id: str) -> str:
    """MOCK_MODE-only transcript hint for the labeled offline STT.
    Real benchmarks never call this — STT output is authoritative."""
    for key, text in _MOCK_HINTS.items():
        if key in tc_id:
            return text
    tc = get_test_case(tc_id) or {}
    blob = f"{tc.get('name', '')} {tc.get('description', '')}".lower()
    if "weather" in blob:
        return "What's the weather in Tokyo right now?"
    if "search" in blob:
        return "Search the web for the latest AI news."
    if "capital" in blob:
        return "What is the capital of France?"
    if "fact" in blob:
        return "Tell me a fun fact about octopuses."
    return "Recognized speech from uploaded audio."


# Backwards-compatible name used by the engine (real providers ignore it).
def resolve_transcript(tc_id: str) -> str:
    return mock_transcript_hint(tc_id)


def save_upload(filename: str, content: bytes) -> Path:
    """Validate + store an uploaded wav. Raises ValueError on bad input.
    Caller enforces the size cap while streaming (see server.upload_test_case)."""
    safe = _sanitize(filename or "")
    if not safe.lower().endswith(".wav"):
        raise ValueError("only .wav files are accepted")
    if len(content) < 44 or content[:4] != b"RIFF" or content[8:12] != b"WAVE":
        raise ValueError("invalid wav magic bytes")
    import io

    try:
        with wave.open(io.BytesIO(content), "rb") as w:
            if w.getnframes() <= 0 or w.getframerate() <= 0:
                raise ValueError("wav has no audio frames")
    except wave.Error as e:
        raise ValueError(f"not a parseable wav: {e}")
    target = TEST_CASES_DIR / f"upload_{uuid.uuid4().hex[:8]}_{safe}"
    target.write_bytes(content)
    return target


def upsert_metadata(entry: dict[str, Any]) -> None:
    """Append/update a metadata entry (validated; stored canonicalized)."""
    entries = _load_meta()
    clean = _validate_entry(entry)
    if not clean:
        raise ValueError("invalid metadata entry")
    entries = [e for e in entries if e.get("id") != clean["id"]]
    entries.append(clean)
    META_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
