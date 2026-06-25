"use client";
/** Routines — a center BOARD of editable cards (created in CHAT via /routine). The left "Routines" item
 *  opens the board + shows a compact list. Delete is real (/api/routines); enable/disable + edit are
 *  optimistic mocks until the backend endpoints land. */
import { useEffect, useState, type CSSProperties } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { registerList, registerTab } from "../contributions";
import { Icon } from "../ui-kit";
import { usePreviewPinTab } from "./previewPinTab";

const SUBJECT = "u_live";
interface Routine { id: string; name: string; cron: string; plan_summary?: string; enabled?: boolean }
const BOARD: TabDescriptor = { id: "board:routines", title: "Routines", kind: "routines", params: {}, context: null };

async function fetchRoutines(): Promise<Routine[]> {
  try { return ((await (await fetch(`/api/routines?subject=${SUBJECT}`)).json()).routines ?? []).map((r: Routine) => ({ ...r, enabled: true })); } catch { return []; }
}

function RoutinesBoardNav() {
  const nav = usePreviewPinTab<HTMLButtonElement>(BOARD);
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 9px", borderRadius: 7, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
      <Icon name="zap" size={14} />Routines board
    </button>
  );
}

function RoutineNavRow({ routine }: { routine: Routine }) {
  const nav = usePreviewPinTab<HTMLDivElement>(BOARD);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} style={{ padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)" }}>{routine.name}</div>
  );
}

// ── center BOARD (kind "routines") ────────────────────────────────────────────────
function RoutinesBoard() {
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  useEffect(() => { void fetchRoutines().then(setRoutines); }, []);
  const del = async (id: string) => { await fetch(`/api/routines/${id}?subject=${SUBJECT}`, { method: "DELETE" }); setRoutines((rs) => rs.filter((r) => r.id !== id)); };
  const toggle = (id: string) => setRoutines((rs) => rs.map((r) => (r.id === id ? { ...r, enabled: !r.enabled } : r)));          // mock
  const patch = (id: string, k: keyof Routine, v: string) => setRoutines((rs) => rs.map((r) => (r.id === id ? { ...r, [k]: v } : r))); // mock

  const sw = (on: boolean): CSSProperties => ({ width: 32, height: 18, borderRadius: 10, background: on ? "var(--green)" : "var(--panel2)", position: "relative", cursor: "pointer", flex: "none", transition: "background .15s" });
  const knob = (on: boolean): CSSProperties => ({ position: "absolute", top: 2, left: on ? 16 : 2, width: 14, height: 14, borderRadius: "50%", background: "#fff", transition: "left .15s" });
  const inp: CSSProperties = { background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 6, padding: "4px 8px", color: "var(--t1)", fontSize: 13, outline: "none", fontFamily: "inherit" };

  return (
    <div style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "24px" }}>
        <div style={{ fontSize: 18, color: "var(--t1)", fontWeight: 500, marginBottom: 4 }}>Routines</div>
        <div style={{ fontSize: 13, color: "var(--t3)", marginBottom: 20 }}>Scheduled agents. Create one in Chat with <code style={{ fontFamily: "var(--mono)", color: "var(--accent)" }}>/routine</code>; manage them here.</div>
        {routines.map((r) => (
          <div key={r.id} style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--panel)", padding: "14px 16px", marginBottom: 12, opacity: r.enabled ? 1 : 0.55 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              {editing === r.id
                ? <input style={{ ...inp, flex: 1, fontSize: 14 }} value={r.name} onChange={(e) => patch(r.id, "name", e.target.value)} />
                : <span style={{ fontSize: 14.5, color: "var(--t1)", fontWeight: 500, flex: 1 }}>{r.name}</span>}
              <div style={sw(!!r.enabled)} onClick={() => toggle(r.id)} title={r.enabled ? "Enabled" : "Disabled"}><div style={knob(!!r.enabled)} /></div>
              <button onClick={() => setEditing(editing === r.id ? null : r.id)} title="Edit" style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}><Icon name="panel" size={14} /></button>
              <button onClick={() => void del(r.id)} title="Delete" style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}><Icon name="x" size={14} /></button>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 9 }}>
              <span style={{ fontSize: 11, color: "var(--t3)" }}>schedule</span>
              {editing === r.id
                ? <input style={{ ...inp, fontFamily: "var(--mono)", width: 160 }} value={r.cron} onChange={(e) => patch(r.id, "cron", e.target.value)} />
                : <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, borderRadius: 5, padding: "1px 7px", background: "var(--panel2)", color: "var(--accent)" }}>{r.cron}</span>}
            </div>
            {r.plan_summary && <div style={{ fontSize: 12.5, color: "var(--t2)", marginTop: 9, lineHeight: 1.5 }}>{r.plan_summary}</div>}
          </div>
        ))}
        {routines.length === 0 && <div style={{ color: "var(--t3)", fontSize: 13, padding: "20px 0" }}>No routines yet — open Chat and try <code style={{ fontFamily: "var(--mono)", color: "var(--accent)" }}>/routine</code>.</div>}
      </div>
    </div>
  );
}

// ── left launcher (opens the board, shows a compact list) ─────────────────────────
function RoutinesLeft() {
  const layout = useService(LayoutServiceId);
  const [routines, setRoutines] = useState<Routine[]>([]);
  useEffect(() => { layout.openTab(BOARD); void fetchRoutines().then(setRoutines); }, [layout]);
  return (
    <div style={{ padding: "8px" }}>
      <RoutinesBoardNav />
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>scheduled agents</div>
      {routines.map((r) => <RoutineNavRow key={r.id} routine={r} />)}
      {routines.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>None yet — create with <code style={{ fontFamily: "var(--mono)", color: "var(--accent)" }}>/routine</code> in Chat.</div>}
    </div>
  );
}

registerTab("routines", RoutinesBoard);
registerList({ id: "routines", label: "Routines", icon: "zap", order: 40, component: RoutinesLeft });
