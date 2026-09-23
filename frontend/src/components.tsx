import { useEffect, useRef } from "react";
import { StageEvent, traceUrl } from "./lib/api";

/* Shared primitives. Contract: DESIGN.md. Nothing in here is decorative —
   every component exists to make measured data readable. */

export function Metric(props: {
  label: string;
  value: string | null | undefined;
  unit?: string;
  hero?: boolean;
  done?: boolean;
  title?: string;
}) {
  const { label, value, unit, hero, done, title } = props;
  return (
    <div className={`metric${hero ? " hero" : ""}`} title={title}>
      <span className="lbl">{label}</span>
      <span className={`val${done ? " done" : ""}`}>
        {value ?? "—"}
        {value != null && unit ? <span className="unit"> {unit}</span> : null}
      </span>
    </div>
  );
}

export function ms(v: number | null | undefined): string | null {
  return v != null ? v.toFixed(0) : null;
}

export function StatusBadge(props: {
  state: "ok" | "warn" | "err" | "info" | "neutral";
  children: React.ReactNode;
}) {
  return <span className={`chip ${props.state === "neutral" ? "" : props.state}`}>{props.children}</span>;
}

export function RunStatus(props: { success: boolean | number; failureStage?: string | null }) {
  const ok = typeof props.success === "number" ? props.success === 1 : props.success;
  return ok ? (
    <span className="chip ok">● ok</span>
  ) : (
    <span className="chip err">✕ fail{props.failureStage ? ` · ${props.failureStage}` : ""}</span>
  );
}

export function EmptyState(props: { title: string; why?: string; action?: string }) {
  return (
    <div className="empty">
      <b>{props.title}</b>
      {props.why && <div>{props.why}</div>}
      {props.action && <div className="hint" style={{ marginTop: 4 }}>{props.action}</div>}
    </div>
  );
}

export function LoadingState(props: { label: string }) {
  return <div className="loading">{props.label}</div>;
}

export function TraceLink(props: {
  traceId: string | null | undefined;
  project?: string;
  canonicalUrl?: string | null;
  compact?: boolean;
}) {
  const { traceId, project, canonicalUrl, compact } = props;
  if (!traceId) {
    return <span className="dim" title="No LangSmith trace was recorded for this run">trace unavailable</span>;
  }
  const url = traceUrl(project, traceId, canonicalUrl);
  return (
    <span className="mono" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
      <a href={url ?? "#"} target="_blank" rel="noreferrer" title={url ?? traceId}>
        {traceId.slice(0, 8)}
      </a>
      {!compact && (
        <button
          className="ghost"
          style={{ padding: "0 4px", fontSize: "0.6875rem" }}
          onClick={() => navigator.clipboard?.writeText(traceId)}
          title="Copy full trace id"
        >
          copy
        </button>
      )}
    </span>
  );
}

/* Pipeline composition strip: STT → AGENT → TTS (+ tool slot). Availability is
   shown with a dot + dashed border; never hides the missing-credential fact. */
export function PipelineStrip(props: {
  stt: { label: string; available: boolean };
  llm: { label: string; available: boolean };
  tts: { label: string; available: boolean };
  tool: string;
}) {
  const node = (role: string, p: { label: string; available: boolean }) => (
    <div className={`node${p.available ? "" : " unavail"}`}>
      <div className="role">{role}</div>
      <div className="prov">
        <span className={`dot ${p.available ? "ok" : "warn"}`} aria-hidden="true" />
        {p.label}
      </div>
    </div>
  );
  return (
    <div className="pipestrip" aria-label="Pipeline composition">
      {node("STT", props.stt)}
      <span className="arrow">→</span>
      {node("Agent", props.llm)}
      <span className="arrow">→</span>
      {node("TTS", props.tts)}
      <span className="arrow">|</span>
      <div className="node">
        <div className="role">Tool</div>
        <div className="prov">{props.tool}</div>
      </div>
    </div>
  );
}

/* Stage timeline built ONLY from real StageEvents. Bars are positioned by
   actual event timestamps on the shared end-of-input timeline; overlapping
   stages therefore render faithfully. FIRST AUDIO gets an explicit marker. */
export function LatencyWaterfall(props: { events: StageEvent[]; totalMs?: number | null }) {
  const { events, totalMs } = props;
  const firstAudio = events.find((e) => e.kind === "first_audio");
  const end = totalMs ?? firstAudio?.ts_ms ?? Math.max(1, ...events.map((e) => e.ts_ms));

  type Span = { name: string; start: number; end: number; cls: string; ms: number };
  const spans: Span[] = [];

  const pair = (stage: string, cls: string, startEv: StageEvent | undefined, endEv: StageEvent | undefined) => {
    if (startEv && endEv && endEv.ts_ms >= startEv.ts_ms) {
      spans.push({ name: stage, start: startEv.ts_ms, end: endEv.ts_ms, cls, ms: endEv.ts_ms - startEv.ts_ms });
    }
  };
  pair("STT", "var(--stage-stt)",
    events.find((e) => e.stage === "stt" && e.kind === "start"),
    events.find((e) => e.stage === "stt" && e.kind === "end"));
  pair("Agent", "var(--stage-agent)",
    events.find((e) => e.stage === "agent" && e.kind === "start"),
    events.find((e) => e.stage === "agent" && e.kind === "end"));
  const toolStart = events.find((e) => e.stage === "tool" && e.kind === "start");
  const toolEnd = events.find((e) => e.stage === "tool" && e.kind === "end");
  if (toolStart && toolEnd) {
    pair("Tool", "var(--stage-tool)", toolStart, toolEnd);
  } else {
    // tool timings can arrive inside the agent end event when no discrete
    // tool start/end events were emitted for this run
    const agentEnd = events.find((e) => e.stage === "agent" && e.kind === "end");
    const toolMs = agentEnd?.detail?.tool_latencies as { latency_ms?: number }[] | undefined;
    if (agentEnd && Array.isArray(toolMs) && toolMs.length > 0) {
      spans.push({ name: "Tool", start: agentEnd.ts_ms, end: agentEnd.ts_ms, cls: "var(--stage-tool)", ms: 0 });
    }
  }
  pair("TTS", "var(--stage-tts)",
    events.find((e) => e.stage === "tts" && e.kind === "start"),
    events.find((e) => e.stage === "tts" && e.kind === "end"));

  if (spans.length === 0) return null;

  const pct = (v: number) => `${Math.min(100, (v / end) * 100)}%`;
  return (
    <div className="wf" role="img" aria-label={`Latency waterfall: ${spans.map((s) => `${s.name} ${Math.round(s.ms)} ms`).join(", ")}`}>
      {spans.map((s) => (
        <div className="wfrow" key={s.name}>
          <span className="nm">{s.name}</span>
          <span className="track">
            <span
              className="bar"
              style={{ left: pct(s.start), width: `calc(${pct(s.end)} - ${pct(s.start)})`, background: s.cls }}
            />
          </span>
          <span className="ms">{Math.round(s.ms)} ms</span>
        </div>
      ))}
      {firstAudio && (
        <div className="wfrow firstaudio">
          <span className="nm">First audio</span>
          <span className="track">
            <span className="bar" style={{ left: pct(firstAudio.ts_ms), width: "2px", background: "var(--ok)" }} />
          </span>
          <span className="ms">t+{Math.round(firstAudio.ts_ms)} ms</span>
        </div>
      )}
      <div className="legend">Timeline from end of user audio (t=0). Bar positions are real event timestamps.</div>
    </div>
  );
}

/* Live run stage meter: which stage is active right now, from real events. */
export function StageTracker(props: { events: StageEvent[] }) {
  const order = ["input", "stt", "agent", "tool", "tts", "done"];
  const seen = new Set(props.events.map((e) => e.stage));
  const running = props.events.length > 0 && !seen.has("done");
  return (
    <div className="pipestrip" aria-label="Execution stages">
      {order.map((s, i) => {
        const state = seen.has(s) ? "done" : running && order.slice(0, i).every((o) => seen.has(o)) ? "active" : "idle";
        return (
          <span
            key={s}
            className="chip"
            style={state === "done" ? { color: "var(--text-2)" } : state === "active" ? { color: "var(--accent)", borderColor: "var(--accent)" } : undefined}
          >
            {state === "done" ? "✓ " : state === "active" ? "▸ " : ""}{s === "agent" ? "agent" : s}
          </span>
        );
      })}
    </div>
  );
}

export function useAutoscroll<T>(dep: T, ref: React.RefObject<HTMLDivElement | null>) {
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [dep, ref]);
}
