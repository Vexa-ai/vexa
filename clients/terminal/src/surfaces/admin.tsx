"use client";
/** Hidden admin surface — read-only infra observability (workloads + meeting pipeline + probe).
 *
 *  HIDDEN: nothing registers at import time. The module probes `/api/admin/me` (server-verified
 *  email allowlist — see app/api/admin/gate.ts); only a 200 registers the "Infra" list + the
 *  "adminInfra" tab, and the contributions registry notifies the shell so the switcher entry
 *  appears. Non-admins never see an entry and every /api/admin/* route answers 404 for them.
 *
 *  Layout: a persistent TRANSCRIPTION GOLDEN PROBE strip (gateway → meeting-api → runtime →
 *  redis carriers → transcript relay; run on demand) above two in-panel tabs — Workloads and
 *  Meeting pipeline — each with client-side filters. Read-only by design (v1): no stop/kill.
 *  Data: GET /api/admin/overview + POST /api/admin/probe → agent-api (internal tier).
 */
import { useEffect, useMemo, useState, type CSSProperties } from "react";
import { registerList, registerTab } from "../contributions";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { Icon } from "../ui-kit";
import { reducedMode } from "../app/mode";

interface StreamStat { len?: number; last_id?: string | null; last_type?: string }
interface PipelineRow {
  meeting_id: string;
  row_keyed?: boolean;
  in_active_meetings?: boolean;
  processing_on?: boolean;
  copilot_cursor?: string | null;
  proc_stream?: StreamStat;
  transcript_stream?: StreamStat;
  pending_drain?: { deadline: number; overdue: boolean };
  live?: { native_id?: string; platform?: string; title?: string; status?: string; last_seen?: number };
}
interface Workload {
  workloadId: string;
  kind?: string;
  meeting_id?: string;
  profile?: string;
  state?: string;
  startedAt?: string;
  stoppedAt?: string;
  exitCode?: number | null;
  stopReason?: string | null;
}
interface Overview {
  workloads?: Workload[];
  workloads_error?: string;
  meetings?: PipelineRow[];
  meetings_error?: string;
}
interface ProbeStage { id: string; label: string; status: "pass" | "warn" | "fail"; latency_ms?: number; detail?: string }
interface ProbeResult { status: "pass" | "warn" | "fail"; stages: ProbeStage[]; duration_ms: number; at: number }

const PANEL: TabDescriptor = { id: "admin-infra", title: "Infra", kind: "adminInfra", params: {} };
const POLL_MS = 5000;

function ago(t?: number): string {
  if (!t) return "—";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
const isoAgo = (iso?: string) => (iso ? `${ago(Date.parse(iso))} ago` : "—");
/** redis stream ids are `{ms}-{seq}` — the ms half is a wall-clock timestamp. */
const idMs = (id?: string | null): number | null => {
  const ms = id ? Number(id.split("-")[0]) : NaN;
  return Number.isFinite(ms) && ms > 0 ? ms : null;
};

type Tone = "ok" | "warn" | "danger";
const TONE: Record<Tone, { fg: string; bg: string }> = {
  ok: { fg: "var(--green)", bg: "var(--greenbg)" },
  warn: { fg: "var(--warn)", bg: "var(--warnbg)" },
  danger: { fg: "var(--danger)", bg: "var(--dangerbg)" },
};

/** One health verdict per meeting row, most severe first (ADR-0027 Train-2-aware):
 *  native keying (S2) → a processed_pending member past its drain deadline (the run-46 S1
 *  signature, LIVE and exact) → still-draining (in the zset, within deadline — expected for
 *  ~2 db-writer ticks after a stop) → the undrained-tail approximation (proc entries newer
 *  than the bot's stop with no view_end marker and out of the sweep) → ok. A proc tail that IS
 *  the worker's view_end marker completed cleanly, so it suppresses the approximation. */
function healthOf(m: PipelineRow, botStopMs: number | null): { label: string; tone: Tone } {
  if (m.row_keyed === false) return { label: "native key (S2)", tone: "danger" };
  if (m.pending_drain) {
    return m.pending_drain.overdue
      ? { label: "stuck drain (S1)", tone: "danger" }
      : { label: "draining…", tone: "warn" };
  }
  const procMs = idMs(m.proc_stream?.last_id);
  if (m.proc_stream?.last_type !== "view_end" &&
      botStopMs && procMs && procMs > botStopMs && !m.in_active_meetings) {
    return { label: "undrained tail?", tone: "warn" };
  }
  return { label: "ok", tone: "ok" };
}

const th: CSSProperties = { textAlign: "left", fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "4px 8px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap", fontWeight: 500 };
const td: CSSProperties = { fontSize: 12.5, color: "var(--t1)", padding: "5px 8px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", whiteSpace: "nowrap" };
const segBtn = (on: boolean): CSSProperties => ({ fontSize: 12, padding: "3px 10px", borderRadius: 6, border: "1px solid var(--line)", cursor: "pointer", background: on ? "var(--panel2)" : "transparent", color: on ? "var(--t1)" : "var(--t2)" });
const searchStyle: CSSProperties = { width: 210, height: 28, fontSize: 12.5, padding: "0 8px", borderRadius: 6, border: "1px solid var(--line)", background: "var(--panel)", color: "var(--t1)", outline: "none" };

function Pill({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span className="t-xs f-sans pt-0_5 pr-2 pb-0_5 pl-2 r-full" style={{ background: TONE[tone].bg, color: TONE[tone].fg, whiteSpace: "nowrap" }}>
      {label}
    </span>
  );
}

function StateDot({ on }: { on: boolean }) {
  return <span className="r-full mr-1_5" style={{ display: "inline-block", width: 7, height: 7, background: on ? "var(--green)" : "var(--t3)" }} />;
}

function SectionError({ error }: { error?: string | null }) {
  if (!error) return null;
  return (
    <div className="c-accent t-xs pt-1_5 pr-3 pb-1_5 pl-3" style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <Icon name="alert" size={13} />{error}
    </div>
  );
}

// ── the golden probe strip ────────────────────────────────────────────────────────
function ProbeStrip() {
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [running, setRunning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    setRunning(true);
    setErr(null);
    try {
      const r = await fetch("/api/admin/probe", { method: "POST", cache: "no-store" });
      if (!r.ok) { setErr(`probe failed (${r.status})`); return; }
      const body = (await r.json()) as ProbeResult;
      setProbe({ ...body, at: Date.now() / 1000 });
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const tone: Tone = probe ? (probe.status === "pass" ? "ok" : probe.status === "warn" ? "warn" : "danger") : "ok";
  return (
    <div className="pt-2 pr-3 pb-2 pl-3 bd-b bg-2">
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className="t-xs fw-600 c-2" style={{ whiteSpace: "nowrap" }}>Transcription golden probe</span>
        {probe && <Pill label={`${probe.status} · ${(probe.duration_ms / 1000).toFixed(1)}s`} tone={tone} />}
        {probe && <span className="t-xs c-3">ran {ago(probe.at * 1000)} ago</span>}
        {err && <span className="t-xs c-danger">{err}</span>}
        <button onClick={() => void run()} disabled={running}
          className="ml-auto" style={{ ...segBtn(false), display: "flex", alignItems: "center", gap: 5, opacity: running ? 0.6 : 1 }}>
          <Icon name="zap" size={12} />{running ? "Running…" : "Run probe"}
        </button>
      </div>
      {probe && (
        <div className="mt-2" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {probe.stages.map((s) => (
            <span key={s.id} title={s.detail}
              className="t-xs f-mono pt-0_5 pr-2 pb-0_5 pl-2 r-md" style={{ background: TONE[s.status === "pass" ? "ok" : s.status === "warn" ? "warn" : "danger"].bg, color: TONE[s.status === "pass" ? "ok" : s.status === "warn" ? "warn" : "danger"].fg, cursor: s.detail ? "help" : "default" }}>
              {s.label}{s.latency_ms != null ? ` ${s.latency_ms}ms` : ""}{s.status !== "pass" && s.detail ? ` — ${s.detail}` : ""}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── workloads tab ─────────────────────────────────────────────────────────────────
function WorkloadsTab({ workloads, error }: { workloads: Workload[]; error?: string }) {
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<"all" | "bot" | "agent-worker">("all");
  // default = all kinds, RUNNING only — the admin's first question is "what's up right now";
  // stopped/exited history is one click away (Any state)
  const [state, setState] = useState<"all" | "running" | "stopped">("running");
  const shown = useMemo(() => workloads.filter((w) =>
    (kind === "all" || w.kind === kind) &&
    (state === "all" || w.state === state) &&
    (!q || `${w.workloadId} ${w.meeting_id ?? ""}`.toLowerCase().includes(q.toLowerCase())),
  ), [workloads, q, kind, state]);
  return (
    <div>
      <div className="pt-2 pr-3 pb-2 pl-3" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input placeholder="Filter by id or meeting…" value={q} onChange={(e) => setQ(e.target.value)} style={searchStyle} />
        <div style={{ display: "flex", gap: 4 }}>
          {(["all", "bot", "agent-worker"] as const).map((k) => (
            <button key={k} style={segBtn(kind === k)} onClick={() => setKind(k)}>{k === "all" ? "All" : k === "bot" ? "Bots" : "Workers"}</button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {(["all", "running", "stopped"] as const).map((s) => (
            <button key={s} style={segBtn(state === s)} onClick={() => setState(s)}>{s === "all" ? "Any state" : s[0].toUpperCase() + s.slice(1)}</button>
          ))}
        </div>
        <span className="ml-auto t-xs c-3">{shown.length} shown</span>
      </div>
      <SectionError error={error} />
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead><tr>
            <th className="pl-3" style={{ ...th }}>workload</th><th style={th}>kind</th><th style={th}>state</th>
            <th style={th}>meeting</th><th style={th}>started</th><th style={th}>exit</th>
          </tr></thead>
          <tbody>
            {shown.map((w) => (
              <tr key={w.workloadId} style={{ opacity: w.state === "running" ? 1 : 0.75 }}>
                <td className="pl-3" style={{ ...td }}>{w.workloadId}</td>
                <td className="f-sans c-2" style={{ ...td }}>{w.kind ?? "—"}</td>
                <td style={td}><StateDot on={w.state === "running"} />{w.state ?? "—"}</td>
                <td style={td}>{w.meeting_id ?? "—"}</td>
                <td className="c-2" style={{ ...td }} title={w.startedAt}>{isoAgo(w.startedAt)}</td>
                <td className="c-3" style={{ ...td }}>{w.exitCode ?? w.stopReason ?? "—"}</td>
              </tr>
            ))}
            {shown.length === 0 && !error && (
              <tr><td className="c-3 pl-3" style={{ ...td }} colSpan={6}>
                {workloads.length === 0 ? "no managed containers"
                  : state === "running" && !q && kind === "all" ? "nothing running — Any state shows stopped containers"
                    : "nothing matches the filters"}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── meeting pipeline tab ──────────────────────────────────────────────────────────
function PipelineTab({ meetings, botStops, error }: { meetings: PipelineRow[]; botStops: Record<string, number>; error?: string }) {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState<"all" | "on" | "live" | "issues">("all");
  const rows = useMemo(() => meetings.map((m) => ({ m, health: healthOf(m, botStops[m.meeting_id] ?? null) })), [meetings, botStops]);
  const shown = useMemo(() => rows.filter(({ m, health }) =>
    (mode === "all" || (mode === "on" && m.processing_on) || (mode === "live" && m.live?.status === "live") || (mode === "issues" && health.tone !== "ok")) &&
    (!q || `${m.meeting_id} ${m.live?.native_id ?? ""} ${m.live?.title ?? ""}`.toLowerCase().includes(q.toLowerCase())),
  ), [rows, q, mode]);
  const stat = (s?: StreamStat) =>
    s ? `${s.len} @ …${(s.last_id ?? "").slice(-6) || "—"}${s.last_type === "view_end" ? " · view_end" : ""}` : "—";
  return (
    <div>
      <div className="pt-2 pr-3 pb-2 pl-3" style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <input placeholder="Filter by meeting id…" value={q} onChange={(e) => setQ(e.target.value)} style={searchStyle} />
        <div style={{ display: "flex", gap: 4 }}>
          {([["all", "All"], ["on", "Processing on"], ["live", "Live only"], ["issues", "Issues"]] as const).map(([k, label]) => (
            <button key={k} style={{ ...segBtn(mode === k), ...(k === "issues" ? { color: mode === k ? "var(--danger)" : "var(--t2)" } : {}) }} onClick={() => setMode(k)}>{label}</button>
          ))}
        </div>
        <span className="ml-auto t-xs c-3">{shown.length} shown</span>
      </div>
      <SectionError error={error} />
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead><tr>
            <th className="pl-3" style={{ ...th }}>meeting</th><th style={th}>live</th><th style={th}>processing</th>
            <th style={th}>proc stream</th><th style={th}>transcript</th><th style={th}>cursor</th>
            <th style={th}>active set</th><th style={th}>health</th>
          </tr></thead>
          <tbody>
            {shown.map(({ m, health }) => (
              <tr key={m.meeting_id}>
                <td className="pl-3" style={{ ...td }} title={m.live?.title}>{m.meeting_id}{m.live?.native_id ? <span className="c-3"> ({m.live.native_id})</span> : null}</td>
                <td style={td} title={m.live?.last_seen ? "registry last_seen — re-stamped every segment batch; silent entries self-demote after 60s" : undefined}>
                  {m.live ? <><StateDot on={m.live.status === "live"} />{m.live.status}{m.live.last_seen ? <span className="c-3"> · {ago(m.live.last_seen * 1000)}</span> : null}</> : "—"}
                </td>
                <td style={{ ...td, color: m.processing_on ? "var(--t1)" : "var(--t2)" }}>{m.processing_on ? "ON" : "off"}</td>
                <td style={td}>{stat(m.proc_stream)}</td>
                <td style={td}>{stat(m.transcript_stream)}</td>
                <td className="c-2" style={{ ...td }}>{m.copilot_cursor ? `…${m.copilot_cursor.slice(-6)}` : "—"}</td>
                <td className="c-2" style={{ ...td }}>{m.in_active_meetings ? "yes" : "no"}</td>
                <td style={td}><Pill label={health.label} tone={health.tone} /></td>
              </tr>
            ))}
            {shown.length === 0 && !error && (
              <tr><td className="c-3 pl-3" style={{ ...td }} colSpan={8}>{meetings.length ? "nothing matches the filters" : "no pipeline carriers in redis"}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── the panel: probe strip + tab bar + tables ─────────────────────────────────────
function AdminPanel({ active }: { id: string; params: Record<string, unknown>; active: boolean }) {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ts, setTs] = useState(0);
  const [tab, setTab] = useState<"w" | "p">("w");

  useEffect(() => {
    if (!active) return;
    let on = true;
    const poll = async () => {
      if (document.hidden) return;
      try {
        const r = await fetch("/api/admin/overview", { cache: "no-store" });
        if (!on) return;
        if (!r.ok) { setErr(`overview fetch failed (${r.status})`); return; }
        setData((await r.json()) as Overview);
        setErr(null);
        setTs(Date.now());
      } catch (e) {
        if (on) setErr((e as Error).message);
      }
    };
    void poll();
    const iv = setInterval(() => void poll(), POLL_MS);
    return () => { on = false; clearInterval(iv); };
  }, [active]);

  const workloads = data?.workloads ?? [];
  const meetings = data?.meetings ?? [];
  const botStops = useMemo(() => {
    const out: Record<string, number> = {};
    for (const w of workloads) {
      if (w.kind === "bot" && w.meeting_id && w.stoppedAt) {
        const t = Date.parse(w.stoppedAt);
        if (Number.isFinite(t)) out[w.meeting_id] = Math.max(out[w.meeting_id] ?? 0, t);
      }
    }
    return out;
  }, [workloads]);

  const tabBtn = (on: boolean): CSSProperties => ({ border: "none", borderBottom: on ? "2px solid var(--accent)" : "2px solid transparent", borderRadius: 0, background: "none", fontSize: 13, fontWeight: on ? 600 : 400, color: on ? "var(--t1)" : "var(--t2)", padding: "6px 12px", cursor: "pointer" });
  return (
    <div className="bg-0" style={{ height: "100%", overflowY: "auto" }}>
      <div className="pt-3 pr-3 pb-2 pl-3" style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <h2 className="t-md fw-600 c-1 m-0">Infra</h2>
        <span className="t-xs c-3">read-only · refreshes every {POLL_MS / 1000}s{ts ? ` · updated ${ago(ts)} ago` : ""}</span>
      </div>
      {err && <SectionError error={err} />}
      <ProbeStrip />
      <div className="pt-2 pr-3 pb-0 pl-3 bd-b" style={{ display: "flex", gap: 2 }}>
        <button style={tabBtn(tab === "w")} onClick={() => setTab("w")}>Workloads <span className="c-3 fw-400">{workloads.length}</span></button>
        <button style={tabBtn(tab === "p")} onClick={() => setTab("p")}>Meeting pipeline <span className="c-3 fw-400">{meetings.length}</span></button>
      </div>
      {tab === "w"
        ? <WorkloadsTab workloads={workloads} error={data?.workloads_error} />
        : <PipelineTab meetings={meetings} botStops={botStops} error={data?.meetings_error} />}
    </div>
  );
}

// ── left launcher — opens the panel, shows a one-line summary ─────────────────────
function AdminLeft() {
  const layout = useService(LayoutServiceId);
  useEffect(() => { layout.openTab(PANEL); }, [layout]);
  return (
    <div className="pt-2 pr-3 pb-2 pl-3 t-xs c-3">
      Read-only infrastructure panel: transcription golden probe, running bots, agent workers, and per-meeting pipeline state.
    </div>
  );
}

// Nothing registers unless the server confirms the caller is an allowlisted admin (404 for
// everyone else — see app/api/admin/*). Absent in meetings-only mode like the other agent surfaces.
if (!reducedMode() && typeof window !== "undefined") {
  void fetch("/api/admin/me", { cache: "no-store" })
    .then((r) => {
      if (!r.ok) return;
      registerTab("adminInfra", AdminPanel);
      registerList({ id: "admin", label: "Infra", icon: "radio", order: 90, component: AdminLeft });
    })
    .catch(() => { /* hidden — a failed probe just means no entry */ });
}
