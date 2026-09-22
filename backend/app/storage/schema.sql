-- SQLite schema for benchmark results, sessions and concurrency tests.
-- Raw event data is preserved as JSON so aggregates can always be recomputed.

CREATE TABLE IF NOT EXISTS benchmark_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    pipeline_id TEXT NOT NULL,
    test_case_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,

    experiment_id TEXT,
    tts_first_audio_mode TEXT,

    stt_model TEXT,
    llm_model TEXT,
    tts_model TEXT,

    -- Exact model selection (§18 reproducibility): full provider:model[:voice]
    -- specs as requested by the UI/runner, plus the TTS voice identity.
    stt_spec TEXT,
    llm_spec TEXT,
    tts_spec TEXT,
    tts_voice_id TEXT,

    input_duration_ms REAL,
    stt_latency_ms REAL,
    stt_first_result_ms REAL,
    llm_ttft_ms REAL,
    llm_completion_ms REAL,
    tool_latency_ms REAL,
    tts_first_audio_ms REAL,
    tts_completion_ms REAL,
    time_to_first_audio_ms REAL,
    total_response_latency_ms REAL,
    stt_provider_ms REAL,
    tts_provider_ms REAL,

    -- LLM token usage as reported by the provider (NULL when not reported;
    -- never estimated — cost-governance requirement).
    llm_prompt_tokens INTEGER,
    llm_completion_tokens INTEGER,
    llm_total_tokens INTEGER,

    success INTEGER NOT NULL,
    failure_stage TEXT,
    error_type TEXT,
    error_detail TEXT,

    transcript TEXT,
    response_text TEXT,
    tool_calls_json TEXT,
    events_json TEXT,
    trace_id TEXT,

    -- Audio metadata (§6D): provenance of the exact audio processed
    original_filename TEXT,
    file_size_bytes INTEGER,
    sample_rate INTEGER,
    channels INTEGER,
    bit_depth INTEGER,
    audio_format TEXT,
    stt_confidence REAL
);

CREATE INDEX IF NOT EXISTS idx_runs_pipeline ON benchmark_runs(pipeline_id);
CREATE INDEX IF NOT EXISTS idx_runs_testcase ON benchmark_runs(test_case_id);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    pipeline_id TEXT NOT NULL,
    test_case_id TEXT,
    kind TEXT NOT NULL DEFAULT 'run',          -- run | concurrency
    status TEXT NOT NULL DEFAULT 'running',    -- running | completed | failed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS concurrency_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    run_index INTEGER NOT NULL,
    run_id TEXT,
    success INTEGER,
    ttfa_ms REAL,
    total_ms REAL,
    error_type TEXT,
    wall_ms REAL,
    created_at TEXT NOT NULL
);
