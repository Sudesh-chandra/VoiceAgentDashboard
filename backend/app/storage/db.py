"""Thread-safe SQLite storage. Schema committed in schema.sql; no ORM."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..core.config import DATA_DIR

DB_PATH = DATA_DIR / "vabd.db"

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


class Database:
    def __init__(self, path: Path | None = None) -> None:
        # NOTE: default must be resolved at call time, not def time, so tests can
        # monkeypatch DB_PATH and get true isolation.
        self._path = path or DB_PATH
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        conn = self._conn()
        with self._lock:
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            # Lightweight migrations for columns added after first release.
            existing = {r[1] for r in conn.execute("PRAGMA table_info(benchmark_runs)")}
            for col, ddl in (
                ("experiment_id", "TEXT"),
                ("tts_first_audio_mode", "TEXT"),
                ("stt_spec", "TEXT"),
                ("llm_spec", "TEXT"),
                ("tts_spec", "TEXT"),
                ("tts_voice_id", "TEXT"),
                ("llm_prompt_tokens", "INTEGER"),
                ("llm_completion_tokens", "INTEGER"),
                ("llm_total_tokens", "INTEGER"),
            ):
                if col not in existing:
                    conn.execute(f"ALTER TABLE benchmark_runs ADD COLUMN {col} {ddl}")
            conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> None:
        conn = self._conn()
        with self._lock:
            conn.execute(sql, params)
            conn.commit()

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        conn = self._conn()
        with self._lock:
            conn.executemany(sql, rows)
            conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        conn = self._conn()
        with self._lock:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db


def save_benchmark_run(res) -> None:  # PipelineResult, avoid import cycle
    ev_json = json.dumps([e.to_dict() for e in res.events])
    tc_json = json.dumps(res.tool_calls)
    t = res.timings
    am = res.audio_metadata or {}
    stt_conf = None
    for e in res.events:
        if e.stage == "stt" and e.kind == "end" and e.detail.get("confidence") is not None:
            stt_conf = float(e.detail["confidence"])
            break
    row = (
        res.run_id, res.session_id, res.pipeline_id, res.test_case_id, res.started_at,
        res.experiment_id, getattr(t, "tts_first_audio_mode", None),
        getattr(res, "stt_spec", None), getattr(res, "llm_spec", None),
        getattr(res, "tts_spec", None), getattr(res, "tts_voice_id", None),
        t.input_duration_ms, t.stt_latency_ms, t.stt_first_result_ms,
        t.llm_ttft_ms, t.llm_completion_ms, t.tool_latency_ms,
        t.tts_first_audio_ms, t.tts_completion_ms,
        t.time_to_first_audio_ms, t.total_response_latency_ms,
        t.stt_provider_ms, t.tts_provider_ms,
        t.llm_prompt_tokens, t.llm_completion_tokens, t.llm_total_tokens,
        1 if res.success else 0, res.failure_stage, res.error_type, res.error_detail,
        res.transcript, res.response_text, tc_json, ev_json, res.trace_id,
        am.get("original_filename"), am.get("file_size_bytes"), am.get("sample_rate"),
        am.get("channels"), am.get("bit_depth"), am.get("audio_format"), stt_conf,
    )
    cols = [
        "run_id", "session_id", "pipeline_id", "test_case_id", "timestamp",
        "experiment_id", "tts_first_audio_mode",
        "stt_spec", "llm_spec", "tts_spec", "tts_voice_id",
        "input_duration_ms", "stt_latency_ms", "stt_first_result_ms",
        "llm_ttft_ms", "llm_completion_ms", "tool_latency_ms",
        "tts_first_audio_ms", "tts_completion_ms",
        "time_to_first_audio_ms", "total_response_latency_ms",
        "stt_provider_ms", "tts_provider_ms",
        "llm_prompt_tokens", "llm_completion_tokens", "llm_total_tokens",
        "success", "failure_stage", "error_type", "error_detail",
        "transcript", "response_text", "tool_calls_json", "events_json", "trace_id",
        "original_filename", "file_size_bytes", "sample_rate", "channels", "bit_depth",
        "audio_format", "stt_confidence",
    ]
    assert len(cols) == len(row), f"column/value mismatch: {len(cols)} cols vs {len(row)} values"
    placeholders = ",".join("?" for _ in cols)
    get_db().execute(
        f"INSERT OR REPLACE INTO benchmark_runs ({', '.join(cols)}) VALUES ({placeholders})",
        row,
    )


def save_session(session_id: str, pipeline_id: str, kind: str, test_case_id: str | None = None) -> None:
    get_db().execute(
        "INSERT INTO sessions (session_id, pipeline_id, test_case_id, kind, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (session_id, pipeline_id, test_case_id, kind, "running", now(), now()),
    )


def complete_session(session_id: str, status: str = "completed") -> None:
    get_db().execute("UPDATE sessions SET status=?, updated_at=? WHERE session_id=?", (status, now(), session_id))


def now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
