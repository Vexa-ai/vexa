"use client";
/** Hidden admin surface — read-only infra observability (workloads + meeting pipeline).
 *
 *  HIDDEN: nothing registers at import time. The module probes `/api/admin/me` (server-verified
 *  email allowlist — see app/api/admin/gate.ts); only a 200 registers the "Infra" list + the
 *  "adminInfra" tab, and the contributions registry notifies the shell so the switcher entry
 *  appears. Non-admins never see an entry and every /api/admin/* route answers 404 for them.
 *
 *  Read-only by design (v1): the panel observes containers and pipeline carriers; it cannot
 *  stop/kill anything. Data: GET /api/admin/overview → agent-api (internal tier) → runtime.v1
 *  /workloads + the redis meeting carriers (proc/tc streams, on-flag, cursor, active_meetings).
 */
import { useEffect, useState } from "react";
import { registerList, registerTab } from "../contributions";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { Icon } from "../ui-kit";
import { meetingsOnly } from "../app/mode";

interface StreamStat { len?: number; last_id?: string | null }
interface PipelineRow {
  meeting_id: string;
  row_keyed?: boolean;
  in_active_meetings?: boolean;
  processing_on?: boolean;
  copilot_cursor?: string | null;
  proc_stream?: StreamStat;
  transcript_stream?: StreamStat;
  live?: { native_id?: string; platform?: string; title?: string; status?: string };
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
  node?: string | null;
}
interface Overview {
  workloads?: Workload[];
  workloads_error?: string;
  meetings?: PipelineRow[];
  meetings_error?: string;
}

const PANEL: TabDescriptor = { id: "admin-infra", title: "Infra", kind: "adminInfra", params: {} };
const POLL_MS = 5000;

function ago(iso?: string): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

const th: React.CSSProperties = { textAlign: "left", fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "4px 8px", borderBottom: "1px solid var(--line)", whiteSpace: "nowrap" };
const td: React.CSSProperties = { fontSize: 12.5, color: "var(--t1)", padding: "5px 8px", borderBottom: "1px solid var(--line)", fontFamily: "var(--mono)", whiteSpace: "nowrap" };

function StateDot({ state }: { state?: string }) {
  const color = state === "running" ? "var(--green)" : state === "stopped" ? "var(--t3)" : "var(--accent)";
  return <span style={{ display: "inline-block", width: 7, height: 7, borderRadius: "50%", background: color, marginRight: 6 }} />;
}

function SectionError({ error }: { error?: string }) {
  if (!error) return null;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, color: "var(--accent)", fontSize: 12, padding: "6px 0" }}>
      <Icon name="alert" size={13} />{error}
    </div>
  );
}

function AdminPanel({ active }: { id: string; params: Record<string, unknown>; active: boolean }) {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [ts, setTs] = useState<number>(0);

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
  const bots = workloads.filter((w) => w.kind === "bot");
  const agents = workloads.filter((w) => w.kind === "agent-worker");
  const other = workloads.filter((w) => w.kind !== "bot" && w.kind !== "agent-worker");

  return (
    <div style={{ height: "100%", overflowY: "auto", padding: "18px 22px", background: "var(--bg)" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4 }}>
        <h2 style={{ fontSize: 15, fontWeight: 600, color: "var(--t1)", margin: 0 }}>Infra — live observability</h2>
        <span style={{ fontSize: 11.5, color: "var(--t3)" }}>
          read-only · polls every {POLL_MS / 1000}s{ts ? ` · updated ${ago(new Date(ts).toISOString())} ago` : ""}
        </span>
      </div>
      {err && <SectionError error={err} />}

      <div style={{ fontSize: 12, color: "var(--t2)", margin: "10px 0 6px", fontWeight: 600 }}>
        Workloads <span style={{ color: "var(--t3)", fontWeight: 400 }}>· {bots.length} bot{bots.length === 1 ? "" : "s"} · {agents.length} agent worker{agents.length === 1 ? "" : "s"}{other.length ? ` · ${other.length} other` : ""}</span>
      </div>
      <SectionError error={data?.workloads_error} />
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead><tr>
            <th style={th}>workload</th><th style={th}>kind</th><th style={th}>state</th>
            <th style={th}>meeting</th><th style={th}>started</th><th style={th}>exit</th>
          </tr></thead>
          <tbody>
            {workloads.map((w) => (
              <tr key={w.workloadId}>
                <td style={td}>{w.workloadId}</td>
                <td style={td}>{w.kind ?? "—"}</td>
                <td style={td}><StateDot state={w.state} />{w.state ?? "—"}</td>
                <td style={td}>{w.meeting_id ?? "—"}</td>
                <td style={td} title={w.startedAt}>{w.startedAt ? `${ago(w.startedAt)} ago` : "—"}</td>
                <td style={td}>{w.exitCode ?? (w.stopReason ?? "—")}</td>
              </tr>
            ))}
            {workloads.length === 0 && !data?.workloads_error && (
              <tr><td style={{ ...td, color: "var(--t3)" }} colSpan={6}>no managed containers</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div style={{ fontSize: 12, color: "var(--t2)", margin: "18px 0 6px", fontWeight: 600 }}>
        Meeting pipeline <span style={{ color: "var(--t3)", fontWeight: 400 }}>· redis carriers per meeting — a non-row-keyed row or a proc stream growing after stop is a persistence bug</span>
      </div>
      <SectionError error={data?.meetings_error} />
      <div style={{ overflowX: "auto" }}>
        <table style={{ borderCollapse: "collapse", width: "100%" }}>
          <thead><tr>
            <th style={th}>meeting</th><th style={th}>live</th><th style={th}>processing</th>
            <th style={th}>proc stream</th><th style={th}>transcript</th><th style={th}>cursor</th>
            <th style={th}>active set</th><th style={th}>keyed</th>
          </tr></thead>
          <tbody>
            {meetings.map((m) => (
              <tr key={m.meeting_id}>
                <td style={td} title={m.live?.title}>{m.meeting_id}{m.live?.native_id ? ` (${m.live.native_id})` : ""}</td>
                <td style={td}>{m.live ? <><StateDot state={m.live.status === "live" ? "running" : "stopped"} />{m.live.status}</> : "—"}</td>
                <td style={td}>{m.processing_on ? "ON" : "off"}</td>
                <td style={td}>{m.proc_stream ? `${m.proc_stream.len} @ ${m.proc_stream.last_id ?? "—"}` : "—"}</td>
                <td style={td}>{m.transcript_stream ? `${m.transcript_stream.len} @ ${m.transcript_stream.last_id ?? "—"}` : "—"}</td>
                <td style={td}>{m.copilot_cursor ?? "—"}</td>
                <td style={td}>{m.in_active_meetings ? "yes" : "no"}</td>
                <td style={{ ...td, color: m.row_keyed === false ? "var(--accent)" : "var(--t1)" }}>
                  {m.row_keyed === false ? "NATIVE (S2!)" : "row"}
                </td>
              </tr>
            ))}
            {meetings.length === 0 && !data?.meetings_error && (
              <tr><td style={{ ...td, color: "var(--t3)" }} colSpan={8}>no pipeline carriers in redis</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── left launcher — opens the panel, shows a one-line summary ─────────────────────
function AdminLeft() {
  const layout = useService(LayoutServiceId);
  useEffect(() => { layout.openTab(PANEL); }, [layout]);
  return (
    <div style={{ padding: "10px 12px", fontSize: 12, color: "var(--t3)" }}>
      Read-only infrastructure panel: running bots, agent workers, and per-meeting pipeline state.
    </div>
  );
}

// Nothing registers unless the server confirms the caller is an allowlisted admin (404 for
// everyone else — see app/api/admin/*). Absent in meetings-only mode like the other agent surfaces.
if (!meetingsOnly() && typeof window !== "undefined") {
  void fetch("/api/admin/me", { cache: "no-store" })
    .then((r) => {
      if (!r.ok) return;
      registerTab("adminInfra", AdminPanel);
      registerList({ id: "admin", label: "Infra", icon: "radio", order: 90, component: AdminLeft });
    })
    .catch(() => { /* hidden — a failed probe just means no entry */ });
}
