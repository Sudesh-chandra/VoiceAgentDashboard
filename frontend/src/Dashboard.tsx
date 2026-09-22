import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  api, TestCase, ConfigInfo, StageEvent, RunRow, RunDetail, PipelineCompare,
  ConcurrencyLevel, ProviderTestResult, Choice,
} from "./lib/api";
import {
  Metric, ms, RunStatus, EmptyState, LoadingState, TraceLink, PipelineStrip,
  LatencyWaterfall, StageTracker, useAutoscroll,
} from "./components";

type Tab = "benchmark" | "live" | "results" | "compare" | "reliability";

type PipelineSel = { stt: string; llm: string; tts: string; tool: string };

// Model specs (provider[:model[:voice]]) travel verbatim to the backend, which
// validates them against its catalog — selection genuinely changes execution.
// Default TTS = Deepgram Aura-2: verified working; the ElevenLabs account quota
// is currently exhausted (8 credits), so it would fail honestly at the tts stage.
const DEFAULT_PIPELINE: PipelineSel = { stt: "deepgram:nova-2", llm: "", tts: "deepgram:aura-2-thalia-en", tool: "auto" };

const TABS: { id: Tab; label: string }[] = [
  { id: "benchmark", label: "Benchmark" },
  { id: "live", label: "Live Run" },
  { id: "results", label: "Results" },
  { id: "compare", label: "Compare" },
  { id: "reliability", label: "Reliability & Concurrency" },
];

export default function Dashboard() {
  const [tab, setTab] = useState<Tab>("benchmark");
  const [cfg, setCfg] = useState<ConfigInfo | null>(null);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [pipeline, setPipeline] = useState<PipelineSel>(DEFAULT_PIPELINE);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    api<ConfigInfo>("/api/config").then(setCfg).catch(() => {});
    api<{ test_cases: TestCase[] }>("/api/test-cases").then((r) => setCases(r.test_cases)).catch(() => {});
    api<{ runs: RunRow[] }>("/api/runs").then((r) => setRuns(r.runs)).catch(() => {});
  }, []);

  const toggleCase = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setSelected(next);
  };

  const runBenchmark = async () => {
    if (selected.size === 0) { setErr("Select at least one test case."); return; }
    setBusy(true);
    setMsg("");
    setErr("");
    try {
      const r = await api<{ results: unknown[] }>("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ test_case_ids: [...selected], pipeline }),
      });
      setMsg(`Benchmark complete — ${r.results.length} run(s) stored.`);
      const rr = await api<{ runs: RunRow[] }>("/api/runs");
      setRuns(rr.runs);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  const upload = async (file: File, tools: number, noise: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", file.name.replace(/\.wav$/i, ""));
    fd.append("expected_tool_calls", String(tools));
    fd.append("noise_type", noise);
    try {
      await api<{ ok: boolean }>("/api/test-cases/upload", { method: "POST", body: fd });
      setMsg(`Uploaded ${file.name}.`);
      const tc = await api<{ test_cases: TestCase[] }>("/api/test-cases");
      setCases(tc.test_cases);
    } catch (e) {
      setErr(`Upload failed: ${String(e)}`);
    }
  };

  const choice = (list: Choice[] | undefined, id: string) =>
    list?.find((c) => c.id === id);

  const sttCh = choice(cfg?.stt?.choices, pipeline.stt);
  const llmCh = choice(cfg?.llm?.choices, pipeline.llm);
  const ttsCh = choice(cfg?.tts?.choices, pipeline.tts);

  return (
    <div className="app">
      <header className="topbar">
        <h1>Voice Agent Latency &amp; Reliability Observatory</h1>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {cfg?.mock_mode && <span className="chip warn">▲ MOCK MODE — offline labels, not real providers</span>}
          {!cfg?.mock_mode && (
            <span className="chip ok">● live providers · {cfg?.stt?.configured_id ?? "no STT"} / {cfg?.llm?.configured_id ?? "no LLM"} / {cfg?.tts?.configured_id ?? "no TTS"}</span>
          )}
          {cfg?.langsmith?.enabled
            ? <span className="chip info">◈ LangSmith · {cfg.langsmith.project}</span>
            : <span className="chip">◈ LangSmith off</span>}
        </div>
      </header>

      <nav className="tabs" role="tablist" aria-label="Views">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id}
                  className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </nav>

      {err && <div className="notice err" role="alert">{err}</div>}
      {msg && <div className="notice ok">{msg}</div>}

      {tab === "benchmark" && (
        <BenchmarkView
          cfg={cfg} cases={cases} selected={selected} toggleCase={toggleCase}
          pipeline={pipeline} setPipeline={setPipeline}
          sttCh={sttCh} llmCh={llmCh} ttsCh={ttsCh}
          runBenchmark={runBenchmark} busy={busy} upload={upload}
        />
      )}
      {tab === "live" && <LiveView pipeline={pipeline} cases={cases} />}
      {tab === "results" && <ResultsView runs={runs} langsmithProject={cfg?.langsmith?.project} />}
      {tab === "compare" && <CompareView />}
      {tab === "reliability" && <ReliabilityView cases={cases} pipeline={pipeline} cfg={cfg} />}
    </div>
  );
}

// ---------- Benchmark tab ----------

function formatAudioMeta(m?: Record<string, unknown>): string {
  if (!m) return "unavailable";
  const parts: string[] = [];
  if (m.original_filename) parts.push(String(m.original_filename));
  if (m.sample_rate) parts.push(`${m.sample_rate} Hz`);
  if (m.channels) parts.push(`${m.channels}ch`);
  if (m.audio_format) parts.push(String(m.audio_format));
  return parts.length ? parts.join(" · ") : "unavailable";
}

function shortProvider(label: string): string {
  // First token only ("Deepgram Nova-2" → "Deepgram"); full labels remain in
  // the selects below the strip.
  return label.split(/\s+/)[0] || label;
}

function stripLabel(choice: { provider?: string; model?: string } | undefined, fallbackSpec: string): string {
  // Compact composition label: provider first-token + model, e.g.
  // "Deepgram nova-3" — full names live in the selects.
  if (choice?.provider) {
    const p = shortProvider(choice.provider);
    return choice.model ? `${p} ${choice.model}` : p;
  }
  const parts = fallbackSpec.split(":");
  return parts.length >= 2 ? `${shortProvider(parts[0])} ${parts[1]}` : shortProvider(fallbackSpec);
}

function BenchmarkView(props: {
  cfg: ConfigInfo | null;
  cases: TestCase[];
  selected: Set<string>;
  toggleCase: (id: string) => void;
  pipeline: PipelineSel;
  setPipeline: (p: PipelineSel) => void;
  sttCh?: Choice;
  llmCh?: Choice;
  ttsCh?: Choice;
  runBenchmark: () => void;
  busy: boolean;
  upload: (f: File, tools: number, noise: string) => void;
}) {
  const { cfg, cases, selected, toggleCase, pipeline, setPipeline, sttCh, llmCh, ttsCh, runBenchmark, busy, upload } = props;
  const [uploadTools, setUploadTools] = useState(0);
  const [uploadNoise, setUploadNoise] = useState("none");

  const opt = (c: { id: string; label: string; available?: boolean }) => (
    <option key={c.id} value={c.id}>{c.label}{c.available === false ? " — unavailable" : ""}</option>
  );

  return (
    <div className="grid2">
      <div>
        <section>
          <h2>Pipeline</h2>
          <PipelineStrip
            stt={{ label: stripLabel(
              sttCh && { provider: sttCh.provider, model: sttCh.model },
              pipeline.stt,
            ), available: sttCh?.available !== false }}
            llm={{
              label: pipeline.llm ? stripLabel(
                llmCh && { provider: llmCh.provider, model: llmCh.model },
                pipeline.llm,
              ) : "configured",
              available: llmCh?.available !== false,
            }}
            tts={{ label: stripLabel(
              ttsCh && { provider: ttsCh.provider, model: ttsCh.model },
              pipeline.tts,
            ), available: ttsCh?.available !== false }}
            tool={shortProvider(cfg?.tool_choices?.find((t) => t.id === pipeline.tool)?.label ?? pipeline.tool)}
          />
          <p className="hint" style={{ marginTop: 8 }}>
            Unavailable providers are rejected with HTTP 409 before anything runs — results are never silently mocked.
          </p>

          <label htmlFor="sel-stt">STT — speech-to-text</label>
          <select id="sel-stt" value={pipeline.stt} onChange={(e) => setPipeline({ ...pipeline, stt: e.target.value })}>
            {(cfg?.stt?.choices ?? [{ id: "deepgram", label: "Deepgram" }]).map(opt)}
          </select>

          <label htmlFor="sel-llm">Agent — LLM</label>
          <select id="sel-llm" value={pipeline.llm} onChange={(e) => setPipeline({ ...pipeline, llm: e.target.value })}>
            {(cfg?.llm?.choices ?? [{ id: "mock:echo", label: "Mock LLM" }]).map(opt)}
          </select>

          <label htmlFor="sel-tts">TTS — text-to-speech</label>
          <select id="sel-tts" value={pipeline.tts} onChange={(e) => setPipeline({ ...pipeline, tts: e.target.value })}>
            {(cfg?.tts?.choices ?? [{ id: "elevenlabs", label: "ElevenLabs" }]).map(opt)}
          </select>

          <label htmlFor="sel-tool">Tool policy</label>
          <select id="sel-tool" value={pipeline.tool} onChange={(e) => setPipeline({ ...pipeline, tool: e.target.value })}>
            {(cfg?.tool_choices ?? [{ id: "auto", label: "Auto" }]).map(opt)}
          </select>
        </section>

        <ProviderTestSection />

        <section>
          <h2>Upload test case</h2>
          <label htmlFor="up-tools">Expected tool calls</label>
          <select id="up-tools" value={uploadTools} onChange={(e) => setUploadTools(Number(e.target.value))}>
            <option value={0}>0 — answer directly</option>
            <option value={1}>1 — one tool call</option>
          </select>
          <label htmlFor="up-noise">Noise classification</label>
          <select id="up-noise" value={uploadNoise} onChange={(e) => setUploadNoise(e.target.value)}>
            {["none", "tv", "radio", "white_noise", "cafe", "street"].map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          <label htmlFor="up-file">Audio file (.wav)</label>
          <input id="up-file" type="file" accept=".wav,audio/wav"
                 onChange={(e) => {
                   const f = e.target.files?.[0];
                   if (f) upload(f, uploadTools, uploadNoise);
                   e.target.value = "";
                 }} />
          <p className="hint">Validated server-side (RIFF/WAVE magic bytes, size cap); stored sandboxed in data/test_cases/.</p>
        </section>
      </div>

      <section>
        <h2>Test cases <span className="dim" style={{ fontWeight: 400 }}>({selected.size} selected)</span></h2>
        {cases.length === 0 ? (
          <EmptyState
            title="No test cases registered."
            why="The six .wav recordings are imported from the voice/ directory on first load, or can be uploaded."
            action="Upload a .wav file on the left, or restart the backend with voice/ populated."
          />
        ) : (
          <div className="cases">
            {cases.map((c) => (
              <label key={c.id} className={`case ${selected.has(c.id) ? "sel" : ""}`}>
                <input type="checkbox" checked={selected.has(c.id)} onChange={() => toggleCase(c.id)} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div><span className="id">{c.id}</span> <span className="nm">{c.name}</span></div>
                  <div className="meta">
                    <span className="chip">{c.expected_tool_calls === 0 ? "no tool" : "one tool"}</span>
                    {c.noise_type !== "none" && <span className="chip warn">▲ noise: {c.noise_type}</span>}
                  </div>
                </div>
                <audio controls preload="none" src={`/api/test-cases/${c.id}/audio`}
                       onClick={(e) => e.stopPropagation()} title={`Play ${c.id}`} />
              </label>
            ))}
          </div>
        )}
        <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center" }}>
          <button className="primary" disabled={busy || selected.size === 0} onClick={runBenchmark}>
            {busy ? "Benchmark running…" : `Run benchmark (${selected.size})`}
          </button>
          {busy && <LoadingState label="Executing pipeline — STT → agent → TTS" />}
        </div>
      </section>
    </div>
  );
}

// ---------- Provider test (single-provider probes before a full benchmark) ----------

function ProviderTestSection() {
  const [cfg, setCfg] = useState<ConfigInfo | null>(null);
  const [cases, setCases] = useState<TestCase[]>([]);
  const [sttSpec, setSttSpec] = useState("deepgram:nova-2");
  const [ttsSpec, setTtsSpec] = useState("elevenlabs:eleven_turbo_v2_5");
  const [tcId, setTcId] = useState("");
  const [sttRes, setSttRes] = useState<ProviderTestResult | null>(null);
  const [ttsRes, setTtsRes] = useState<ProviderTestResult | null>(null);
  const [sttBusy, setSttBusy] = useState(false);
  const [ttsBusy, setTtsBusy] = useState(false);

  useEffect(() => {
    api<ConfigInfo>("/api/config").then((c) => {
      setCfg(c);
      const firstOkStt = c.stt.choices.find((x) => x.available);
      const firstOkTts = c.tts.choices.find((x) => x.available);
      if (firstOkStt) setSttSpec(firstOkStt.id);
      if (firstOkTts) setTtsSpec(firstOkTts.id);
    }).catch(() => {});
    api<{ test_cases: TestCase[] }>("/api/test-cases").then((r) => {
      setCases(r.test_cases);
      if (r.test_cases.length) setTcId((prev) => prev || r.test_cases[0].id);
    }).catch(() => {});
  }, []);

  const probe = async (kind: "stt" | "tts") => {
    const spec = kind === "stt" ? sttSpec : ttsSpec;
    const setter = kind === "stt" ? setSttRes : setTtsRes;
    const busySetter = kind === "stt" ? setSttBusy : setTtsBusy;
    busySetter(true);
    setter(null);
    try {
      setter(await api<ProviderTestResult>("/api/provider-test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind, spec, test_case_id: kind === "stt" ? tcId || null : null }),
      }));
    } catch (e) {
      setter({ kind, spec, ok: false, error_type: "RequestError", error: String(e) });
    } finally {
      busySetter(false);
    }
  };

  return (
    <section>
      <h2>Provider test</h2>
      <p className="hint">
        Probe one provider/model with real I/O before running a benchmark. STT transcribes an actual
        test-case WAV; TTS synthesizes a fixed sentence so every provider gets identical input.
      </p>
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
        <div>
          <label htmlFor="pt-stt">STT provider/model</label>
          <select id="pt-stt" value={sttSpec} onChange={(e) => setSttSpec(e.target.value)}>
            {(cfg?.stt.choices ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.label}{c.available ? "" : " — not configured"}</option>
            ))}
          </select>
          <label htmlFor="pt-tc">Test audio</label>
          <select id="pt-tc" value={tcId} onChange={(e) => setTcId(e.target.value)}>
            {cases.map((c) => <option key={c.id} value={c.id}>{c.id} — {c.name}</option>)}
          </select>
          <button className="ghost" disabled={sttBusy || !tcId} onClick={() => probe("stt")} style={{ marginTop: 8 }}>
            {sttBusy ? "Transcribing…" : "Run STT test"}
          </button>
          {sttRes && <ProviderTestOut r={sttRes} />}
        </div>
        <div>
          <label htmlFor="pt-tts">TTS provider/model</label>
          <select id="pt-tts" value={ttsSpec} onChange={(e) => setTtsSpec(e.target.value)}>
            {(cfg?.tts.choices ?? []).map((c) => (
              <option key={c.id} value={c.id}>{c.label}{c.available ? "" : " — not configured"}</option>
            ))}
          </select>
          <button className="ghost" disabled={ttsBusy} onClick={() => probe("tts")} style={{ marginTop: 8 }}>
            {ttsBusy ? "Synthesizing…" : "Run TTS test"}
          </button>
          {ttsRes && <ProviderTestOut r={ttsRes} />}
        </div>
      </div>
    </section>
  );
}

function ProviderTestOut({ r }: { r: ProviderTestResult }) {
  if (!r.ok) {
    return (
      <div className="notice err" role="alert" style={{ marginTop: 8 }}>
        <b>{r.error_type}</b> — {r.error}
        <div className="hint" style={{ marginTop: 4 }}>
          Check credentials for this provider in .env, then retry. Failures are scoped to this probe.
        </div>
      </div>
    );
  }
  return (
    <div className="notice ok" role="status" style={{ marginTop: 8 }}>
      <div>✓ {r.provider} · <span className="mono">{r.model}</span> responded</div>
      {r.kind === "stt" ? (
        <>
          <div className="mono" style={{ fontSize: "0.78rem", marginTop: 4 }}>
            latency {r.latency_ms} ms{r.first_result_ms != null ? ` · first result ${r.first_result_ms} ms` : ""}
            {r.confidence != null ? ` · confidence ${Number(r.confidence).toFixed(3)}` : ""}
          </div>
          {r.transcript && <p style={{ margin: "4px 0 0" }}>“{r.transcript}”</p>}
        </>
      ) : (
        <div className="mono" style={{ fontSize: "0.78rem", marginTop: 4 }}>
          first audio {r.first_audio_ms} ms · total {r.total_ms} ms · {r.first_audio_mode} · {r.audio_bytes} bytes
          {r.voice ? ` · voice ${r.voice}` : ""}
        </div>
      )}
    </div>
  );
}

// ---------- Live run tab ----------

function LiveView({ pipeline, cases }: { pipeline: PipelineSel; cases: TestCase[] }) {
  const [caseId, setCaseId] = useState(cases[0]?.id ?? "");
  const [events, setEvents] = useState<StageEvent[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [final, setFinal] = useState<{ success: boolean; ttfa: number | null; error?: string } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  useAutoscroll(events.length, logRef);

  useEffect(() => {
    if (!caseId && cases.length) setCaseId(cases[0].id);
  }, [cases]);

  const start = async () => {
    setEvents([]);
    setFinal(null);
    setRunning(true);
    try {
      const r = await api<{ session_id: string }>("/api/runs/live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ test_case_ids: [caseId], pipeline }),
      });
      setSessionId(r.session_id);
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/ws/sessions/${r.session_id}`);
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data);
        if (ev.type === "session_done") {
          setFinal({ success: ev.success, ttfa: ev.time_to_first_audio_ms, error: ev.error });
          setRunning(false);
          ws.close();
          return;
        }
        setEvents((prev) => [...prev, ev as StageEvent]);
      };
      ws.onclose = () => setRunning(false);
    } catch (e) {
      setFinal({ success: false, ttfa: null, error: String(e) });
      setRunning(false);
    }
  };

  const ttfaEvent = events.find((e) => e.kind === "first_audio");
  const sttEnd = events.find((e) => e.stage === "stt" && e.kind === "end");
  const agentEnd = events.find((e) => e.stage === "agent" && e.kind === "end");
  const ttsStart = events.find((e) => e.stage === "tts" && e.kind === "start");
  const runningStage = running
    ? ttsStart ? "Waiting for first audio sample…"
      : agentEnd ? "Generating TTS…"
        : sttEnd ? "Running agent…"
          : "Running STT…"
    : null;

  return (
    <section>
      <h2>Live run</h2>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <select value={caseId} onChange={(e) => setCaseId(e.target.value)} style={{ maxWidth: 380 }}
                aria-label="Test case">
          {cases.map((c) => <option key={c.id} value={c.id}>{c.id} — {c.name}</option>)}
        </select>
        <button className="primary" disabled={running || !caseId} onClick={start}>
          {running ? "Running…" : "Run live"}
        </button>
        {sessionId && <span className="mono dim">session {sessionId}</span>}
      </div>

      <div className="metrics">
        <Metric hero label="Time to first audio" value={ttfaEvent ? ttfaEvent.ts_ms.toFixed(0) : null}
                unit={ttfaEvent ? "ms" : undefined}
                title="End of user spoken input → first sample of output audio" />
        <Metric label="STT" value={ms(sttEnd?.detail?.latency_ms as number)} unit="ms" done={!!sttEnd} />
        <Metric label="Agent" value={ms(agentEnd?.detail?.latency_ms as number)} unit="ms" done={!!agentEnd} />
      </div>

      <StageTracker events={events} />
      {runningStage && <LoadingState label={runningStage} />}

      <div className="log" ref={logRef} aria-live="polite" aria-label="Live execution events">
        {events.length === 0 && !running && (
          <EmptyState title="No events yet."
                      why="The timeline streams real backend events while a live run executes."
                      action="Select a test case and press Run live." />
        )}
        {events.map((e, i) => (
          <div key={i} className={`ev ${e.kind}`}>
            <span className="ts">[{fmt(e.ts_ms)}]</span>
            <span className={`st ${e.stage}`}>{e.stage}</span>
            <span className="lb">{e.label}</span>
            {e.detail && Object.keys(e.detail).length > 0 && (
              <span className="dt">{JSON.stringify(e.detail)}</span>
            )}
          </div>
        ))}
      </div>

      <LatencyWaterfall events={events} totalMs={final?.ttfa ?? null} />

      {final && (
        <div className={`notice ${final.success ? "ok" : "err"}`} role="status">
          {final.success
            ? `Run complete — Time to First Audio ${final.ttfa != null ? final.ttfa.toFixed(0) : "n/a"} ms.`
            : `Run failed${final.error ? ` — ${final.error}` : "."} The run is persisted as failed; see Reliability.`}
        </div>
      )}
    </section>
  );
}

function fmt(msv: number): string {
  const s = Math.floor(msv / 1000);
  const rem = (msv % 1000).toFixed(0).padStart(3, "0");
  return `00:${String(s).padStart(2, "0")}.${rem}`;
}

// ---------- Results tab ----------

function ResultsView({ runs, langsmithProject }: { runs: RunRow[]; langsmithProject?: string }) {
  const [rows, setRows] = useState<RunRow[]>(runs);
  const [open, setOpen] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => setRows(runs), [runs]);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const rr = await api<{ runs: RunRow[] }>("/api/runs");
      setRows(rr.runs);
    } finally {
      setLoading(false);
    }
  }, []);

  const openRow = async (runId: string) => {
    if (open?.run_id === runId) { setOpen(null); return; }
    try {
      setOpen(await api<RunDetail>(`/api/runs/${runId}`));
    } catch {
      setOpen(null);
    }
  };

  return (
    <section>
      <h2>Benchmark results</h2>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <button className="ghost" onClick={refresh}>{loading ? "Refreshing…" : "Refresh"}</button>
        <span className="hint">{rows.length} stored run(s) — every execution is persisted, successes and failures.</span>
      </div>

      {rows.length === 0 ? (
        <EmptyState title="No benchmark runs yet."
                    why="Results appear after executing test cases against a pipeline."
                    action="Go to Benchmark, select cases, and run." />
      ) : (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Run</th><th>Case</th><th>Pipeline</th>
                <th className="num">TTFA</th><th className="num">STT</th><th className="num">LLM TTFT</th>
                <th className="num">Tool</th><th className="num">TTS 1st</th><th className="num">Total</th>
                <th>Status</th><th>Trace</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <Fragment key={r.run_id}>
                  <tr className="rowlink" onClick={() => openRow(r.run_id)}
                      title="Open run detail">
                    <td className="mono dim">{r.run_id.slice(0, 10)}</td>
                    <td className="mono">{r.test_case_id}</td>
                    <td className="mono dim">{r.pipeline_id}</td>
                    <td className="num">{ms(r.time_to_first_audio_ms) ?? "—"}</td>
                    <td className="num dim">{ms(r.stt_latency_ms) ?? "—"}</td>
                    <td className="num dim">{ms(r.llm_ttft_ms) ?? "—"}</td>
                    <td className="num dim">{ms(r.tool_latency_ms) ?? "—"}</td>
                    <td className="num dim">{ms(r.tts_first_audio_ms) ?? "—"}</td>
                    <td className="num dim">{ms(r.total_response_latency_ms) ?? "—"}</td>
                    <td><RunStatus success={r.success} failureStage={r.failure_stage} /></td>
                    <td><TraceLink traceId={r.trace_id} project={langsmithProject} compact /></td>
                  </tr>
                  {open?.run_id === r.run_id && (
                    <tr>
                      <td colSpan={11} style={{ background: "var(--bg)" }}>
                        <RunDetailPanel d={open} langsmithProject={langsmithProject} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RunDetailPanel({ d, langsmithProject }: { d: RunDetail; langsmithProject?: string }) {
  const evs = (d.events ?? []) as StageEvent[];
  return (
    <div style={{ padding: "8px 4px" }}>
      <div className="metrics">
        <Metric hero label="Time to first audio" value={ms(d.time_to_first_audio_ms)} unit="ms"
                title="End of user spoken input → first sample of output audio" />
        <Metric label="Input duration" value={ms(d.input_duration_ms)} unit="ms" />
        <Metric label="STT" value={ms(d.stt_latency_ms)} unit="ms" />
        <Metric label="LLM TTFT" value={ms(d.llm_ttft_ms)} unit="ms" />
        <Metric label="Tool" value={ms(d.tool_latency_ms)} unit="ms" />
        <Metric label="TTS 1st" value={ms(d.tts_first_audio_ms)} unit="ms" />
        <Metric label="Total" value={ms(d.total_response_latency_ms)} unit="ms" />
      </div>

      <LatencyWaterfall events={evs} totalMs={d.time_to_first_audio_ms} />

      {d.error_type && (
        <div className="notice err">
          <b>{d.error_type}</b> at stage <span className="mono">{d.failure_stage}</span> — {d.error_detail}
          <div className="hint" style={{ marginTop: 4 }}>
            Recovery: check provider status/credentials for this stage, then re-run the case.
          </div>
        </div>
      )}

      {(d.tool_calls ?? []).length > 0 && (
        <>
          <h3>Tool calls</h3>
          {(d.tool_calls ?? []).map((tc, i) => (
            <div key={i} className="mono" style={{ fontSize: "0.78rem" }}>
              → {tc.name}({JSON.stringify(tc.args)})
            </div>
          ))}
        </>
      )}

      {d.transcript && (
        <>
          <h3>Transcript (actual STT output)</h3>
          <p style={{ margin: "4px 0" }}>{d.transcript}</p>
        </>
      )}
      {d.response_text && (
        <>
          <h3>Agent response</h3>
          <p style={{ margin: "4px 0" }}>{d.response_text}</p>
        </>
      )}

      <h3>Metadata</h3>
      <dl className="kv">
        <dt>Audio</dt>
        <dd>{formatAudioMeta(d.audio_metadata)}</dd>
        <dt>Timestamp</dt>
        <dd>{d.timestamp}</dd>
        <dt>Trace</dt>
        <dd><TraceLink traceId={d.trace_id} project={langsmithProject} /></dd>
      </dl>
    </div>
  );
}

// ---------- Compare tab ----------

function CompareView() {
  const [rows, setRows] = useState<PipelineCompare[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    api<{ pipelines: PipelineCompare[] }>("/api/compare")
      .then((r) => setRows(r.pipelines))
      .catch(() => {})
      .finally(() => setLoaded(true));
  }, []);

  const metrics: { key: "ttfa" | "stt" | "llm_ttft" | "tool" | "tts_first" | "total"; label: string }[] = [
    { key: "ttfa", label: "TTFA (median)" },
    { key: "stt", label: "STT (median)" },
    { key: "llm_ttft", label: "LLM TTFT (median)" },
    { key: "tool", label: "Tool (median)" },
    { key: "tts_first", label: "TTS first audio (median)" },
    { key: "total", label: "Total (median)" },
  ];

  return (
    <section>
      <h2>Pipeline comparison</h2>
      <p className="hint">
        Same test inputs across pipeline configurations; medians with sample counts. Evidence only — the
        platform does not declare a "best" pipeline. Lowest observed TTFA is factual, not a verdict.
      </p>
      {loaded && rows.length === 0 && (
        <EmptyState title="Nothing to compare yet."
                    why="Comparison needs runs from at least two pipeline configurations."
                    action="Run the same cases under a second pipeline (e.g. mock TTS vs ElevenLabs)." />
      )}
      {rows.length > 0 && (
        <div className="tablewrap">
          <table>
            <thead>
              <tr>
                <th>Metric</th>
                {rows.map((r) => <th key={r.pipeline_id} className="mono">{r.pipeline_id}</th>)}
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => {
                const vals = rows.map((r) => r[m.key].median_ms).filter((v): v is number => v != null);
                const best = vals.length > 0 ? Math.min(...vals) : null;
                return (
                  <tr key={m.key}>
                    <td>{m.label}</td>
                    {rows.map((r) => {
                      const agg = r[m.key];
                      const isBest = best != null && agg.median_ms === best && m.key === "ttfa";
                      return (
                        <td key={r.pipeline_id} className={`num${isBest ? "" : " dim"}`}
                            title={isBest ? "Lowest observed TTFA among compared pipelines" : undefined}>
                          {agg.median_ms != null ? `${agg.median_ms.toFixed(0)} ms (n=${agg.n})` : "—"}
                          {isBest ? " ◂ lowest TTFA" : ""}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              <tr>
                <td>Success rate</td>
                {rows.map((r) => (
                  <td key={r.pipeline_id} className="num">{r.success_rate_pct}% (n={r.runs})</td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---------- Reliability & concurrency tab ----------

function ReliabilityView({ cases, pipeline, cfg }: { cases: TestCase[]; pipeline: PipelineSel; cfg: ConfigInfo | null }) {
  const [summary, setSummary] = useState<Record<string, unknown> | null>(null);
  const [levels, setLevels] = useState("1,5");
  const [perLevel, setPerLevel] = useState(2);
  const [concCase, setConcCase] = useState(cases[0]?.id ?? "");
  const [busy, setBusy] = useState(false);
  const [concOut, setConcOut] = useState<ConcurrencyLevel[] | null>(null);

  const loadSummary = useCallback(() => {
    api<Record<string, unknown>>("/api/reliability").then(setSummary).catch(() => {});
  }, []);
  useEffect(loadSummary, [loadSummary]);

  const runConc = async () => {
    setBusy(true);
    try {
      const lvl = levels.split(",").map((s) => parseInt(s.trim(), 10)).filter((n) => !isNaN(n));
      const r = await api<{ levels: ConcurrencyLevel[] }>("/api/concurrency", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          test_case_ids: [concCase || cases[0]?.id].filter(Boolean),
          pipeline,
          levels: lvl,
          runs_per_level: perLevel,
        }),
      });
      setConcOut(r.levels);
      loadSummary();
    } finally {
      setBusy(false);
    }
  };

  const stages = (summary?.failure_by_stage ?? {}) as Record<string, number>;
  const errTypes = (summary?.failure_by_error_type ?? {}) as Record<string, number>;

  return (
    <div className="grid2even">
      <section>
        <h2>Reliability</h2>
        <button className="ghost" onClick={loadSummary}>Refresh</button>
        {summary ? (
          <>
            <div className="metrics">
              <Metric label="Total runs" value={String(summary.total_runs)} />
              <Metric label="Successful" value={String(summary.successful)} done />
              <Metric label="Failed" value={String(summary.failed)} />
              <Metric label="Failure rate" value={String(summary.failure_rate_pct)} unit="%" />
            </div>
            <h3>Failures by stage</h3>
            {Object.keys(stages).length === 0 ? (
              <p className="hint">No failures recorded.</p>
            ) : (
              <table>
                <thead><tr><th>Stage</th><th className="num">Count</th></tr></thead>
                <tbody>
                  {Object.entries(stages).map(([s, n]) => (
                    <tr key={s}><td className="mono">{s}</td><td className="num">{n}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
            {Object.keys(errTypes).length > 0 && (
              <>
                <h3>Failures by error type</h3>
                <table>
                  <thead><tr><th>Error type</th><th className="num">Count</th></tr></thead>
                  <tbody>
                    {Object.entries(errTypes).map(([s, n]) => (
                      <tr key={s}><td className="mono">{s}</td><td className="num">{n}</td></tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
            <p className="hint" style={{ marginTop: 10 }}>
              Failed runs are persisted with failure stage and error type — they are excluded from medians but always counted here.
            </p>
          </>
        ) : (
          <LoadingState label="Loading reliability summary…" />
        )}
      </section>

      <section>
        <h2>Concurrency test</h2>
        <p className="hint">
          Same case executed at N parallel requests (staggered, hard-capped at {cfg?.max_concurrency ?? 10}).
          Reveals provider contention and queueing effects on TTFA.
        </p>
        <label htmlFor="cc-case">Test case</label>
        <select id="cc-case" value={concCase} onChange={(e) => setConcCase(e.target.value)}>
          {cases.map((c) => <option key={c.id} value={c.id}>{c.id} — {c.name}</option>)}
        </select>
        <label htmlFor="cc-levels">Levels (comma-separated; clamped to {cfg?.max_concurrency ?? 10})</label>
        <input id="cc-levels" value={levels} onChange={(e) => setLevels(e.target.value)} />
        <label htmlFor="cc-per">Runs per level (1–5)</label>
        <input id="cc-per" type="number" min={1} max={5} value={perLevel}
               onChange={(e) => setPerLevel(Math.max(1, Math.min(5, Number(e.target.value) || 1)))} />
        <div style={{ marginTop: 12 }}>
          <button className="primary" disabled={busy || !concCase} onClick={runConc}>
            {busy ? "Running…" : "Run concurrency test"}
          </button>
          {busy && <LoadingState label="Executing parallel runs…" />}
        </div>
        {concOut && (
          <div className="tablewrap">
            <table>
              <thead>
                <tr><th className="num">Level</th><th className="num">Runs</th><th className="num">OK</th>
                    <th className="num">Failed</th><th className="num">Success</th>
                    <th className="num">TTFA med</th><th className="num">TTFA p95</th>
                    <th className="num">Total med</th><th className="num">Throughput</th></tr>
              </thead>
              <tbody>
                {concOut.map((l) => (
                  <tr key={l.level}>
                    <td className="num">{l.level}</td>
                    <td className="num">{l.runs}</td>
                    <td className="num">{l.successful}</td>
                    <td className="num">{l.failed}</td>
                    <td className="num">{l.success_rate_pct}%</td>
                    <td className="num">{l.ttfa.median_ms != null ? `${l.ttfa.median_ms.toFixed(0)} ms` : "—"}</td>
                    <td className="num dim" title={l.ttfa.p95_ms == null ? "p95 needs at least 5 samples — insufficient samples" : undefined}>{l.ttfa.p95_ms != null ? `${l.ttfa.p95_ms.toFixed(0)} ms` : "N/A — insufficient samples"}</td>
                    <td className="num dim">{l.total.median_ms != null ? `${l.total.median_ms.toFixed(0)} ms` : "—"}</td>
                    <td className="num dim">{l.throughput_rps != null ? `${l.throughput_rps.toFixed(2)} rps` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!concOut && !busy && (
          <EmptyState title="No concurrency data in this view yet."
                      why="Results show after running a test."
                      action="Keep levels small (1, 3) on paid providers — concurrency multiplies API usage." />
        )}
      </section>
    </div>
  );
}
