export type TestCase = {
  id: string;
  name: string;
  description: string;
  expected_tool_calls: number;
  noise_type: string;
  audio_path: string;
};

export type Choice = { id: string; label: string; available: boolean; provider?: string; model?: string };

export type ProviderTestResult = {
  kind: "stt" | "tts";
  spec: string;
  ok: boolean;
  provider?: string;
  model?: string;
  // STT success
  transcript?: string;
  latency_ms?: number;
  first_result_ms?: number | null;
  confidence?: number | null;
  audio?: Record<string, unknown>;
  // TTS success
  voice?: string | null;
  first_audio_ms?: number | null;
  total_ms?: number;
  first_audio_mode?: string;
  audio_bytes?: number;
  text?: string;
  // failure (safe message — providers never echo keys)
  error_type?: string;
  error?: string;
};

export type ConfigInfo = {
  mock_mode: boolean;
  langsmith: { enabled: boolean; project: string };
  llm: { configured: boolean; configured_id: string | null; choices: Choice[] };
  stt: { configured: boolean; configured_id: string | null; choices: Choice[] };
  tts: { configured: boolean; configured_id: string | null; choices: Choice[] };
  tool_choices: Choice[];
  max_concurrency: number;
};

export type StageEvent = {
  ts_ms: number;
  stage: string;
  label: string;
  kind: string;
  wall_clock: string;
  detail: Record<string, unknown>;
};

export type RunRow = {
  run_id: string;
  pipeline_id: string;
  test_case_id: string;
  timestamp: string;
  success: number | boolean;
  failure_stage: string | null;
  error_type: string | null;
  stt_latency_ms: number | null;
  llm_ttft_ms: number | null;
  tool_latency_ms: number | null;
  tts_first_audio_ms: number | null;
  time_to_first_audio_ms: number | null;
  total_response_latency_ms: number | null;
  trace_id: string | null;
};

export type Agg = { n: number; median_ms: number | null; p95_ms: number | null; mean_ms?: number | null };

export type PipelineCompare = {
  pipeline_id: string;
  runs: number;
  success_rate_pct: number;
  ttfa: Agg;
  stt: Agg;
  llm_ttft: Agg;
  tool: Agg;
  tts_first: Agg;
  total: Agg;
};

export type ConcurrencyLevel = {
  level: number;
  runs: number;
  successful: number;
  failed: number;
  success_rate_pct: number;
  ttfa: Agg;
  total: Agg;
  wall_clock_ms: number;
  throughput_rps: number | null;
  errors: (string | null)[];
};

export type RunDetail = RunRow & {
  events: StageEvent[];
  transcript?: string | null;
  response_text?: string | null;
  error_detail?: string | null;
  input_duration_ms?: number | null;
  tool_calls?: { name: string; args: Record<string, unknown> }[];
  audio_metadata?: Record<string, unknown>;
  langsmith_project?: string;
};

export function traceUrl(project: string | undefined, traceId: string | null | undefined): string | null {
  if (!traceId) return null;
  return `https://smith.langchain.com/projects/${encodeURIComponent(project ?? "default")}/t/${traceId}`;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}
