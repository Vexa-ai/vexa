"use client";
/** Routines — a center BOARD of editable cards (created in CHAT via /routine). The left "Routines" item
 *  opens the board + shows a compact list. Delete and enable/disable call /api/routines; edit updates
 *  the current card draft locally. */
import { useEffect, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../platform/layout";
import { registerList, registerTab } from "../contributions";
import { reducedMode } from "../platform/mode";
import { Icon } from "../ui-kit";
import { usePreviewPinTab } from "./previewPinTab";
// Data-access lives in its own SoC module (scoped to the authed user — no client subject, P20),
// proven in isolation by routinesApi.test.ts.
import { listRoutines, deleteRoutine, setRoutineEnabled, routineNote, pausedNote, refusedNote, type Routine } from "./routinesApi";
import { presentError } from "../data/apiClient";
import "./routines.css";

const BOARD: TabDescriptor = { id: "board:routines", title: "Routines", kind: "routines", params: {}, context: null };

function RoutinesBoardNav() {
  const nav = usePreviewPinTab<HTMLButtonElement>(BOARD);
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} className="pt-2 pr-2 pb-2 pl-2 r-md bd-strong bg-2 c-1 t-sm mb-2" style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", cursor: "pointer" }}>
      <Icon name="zap" size={14} />Routines board
    </button>
  );
}

function RoutineNavRow({ routine }: { routine: Routine }) {
  const nav = usePreviewPinTab<HTMLDivElement>(BOARD);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} className="pt-1_5 pr-2 pb-1_5 pl-2 r-md t-xs c-2" style={{ cursor: "pointer" }}>{routine.name}</div>
  );
}

// ── center BOARD (kind "routines") ────────────────────────────────────────────────
function RoutinesBoard() {
  const [routines, setRoutines] = useState<Routine[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18): a load/mutation error is shown, never swallowed
  useEffect(() => { void listRoutines().then((rs) => { setRoutines(rs); setError(null); }).catch((e: unknown) => setError(presentError(e).headline)); }, []);
  const del = async (id: string) => {
    try { await deleteRoutine(id); setRoutines((rs) => rs.filter((r) => r.id !== id)); }
    catch (e: unknown) { setError(presentError(e).headline); }
  };
  const toggle = async (routine: Routine) => {
    const nextEnabled = !routine.enabled;
    setRoutines((rs) => rs.map((r) => (r.id === routine.id ? { ...r, enabled: nextEnabled } : r)));
    try {
      await setRoutineEnabled(routine.name, nextEnabled);  // throws on a backend error (fail-loud)
    } catch (e: unknown) {
      setError(presentError(e).headline);
      setRoutines((rs) => rs.map((r) => (r.id === routine.id && r.enabled === nextEnabled ? { ...r, enabled: routine.enabled } : r)));
    }
  };
  const patch = (id: string, k: "name" | "cron", v: string) => setRoutines((rs) => rs.map((r) => (r.id === id ? { ...r, [k]: v } : r))); // local card draft


  return (
    <div className="bg-0" style={{ height: "100%", overflowY: "auto" }}>
      <div className="mt-0 mr-auto mb-0 ml-auto p-6" style={{ maxWidth: 760 }}>
        <div className="t-lg c-1 fw-500 mb-1">Routines</div>
        <div className="t-sm c-3 mb-5">Scheduled agents. Create one in Chat with <code className="f-mono c-accent">/routine</code>; manage them here.</div>
        {error && <div role="alert" className="t-xs c-danger bg-2 bd-danger r-md pt-2 pr-3 pb-2 pl-3 mb-3">⚠ Couldn’t load routines — {error}</div>}
        {routines.map((r) => (
          <div key={r.id} className="bd r-lg bg-2 pt-3 pr-4 pb-3 pl-4 mb-3" style={{ opacity: r.enabled ? 1 : 0.55 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              {editing === r.id
                ? <input className="rt-input" style={{ flex: 1 }} value={r.name} onChange={(e) => patch(r.id, "name", e.target.value)} />
                : <span className="t-md c-1 fw-500" style={{ flex: 1 }}>{r.name}</span>}
              <div className="rt-switch" data-on={r.enabled ? "" : undefined} onClick={() => void toggle(r)} title={r.enabled ? "Enabled" : "Disabled"}>
                {/* a white knob reads on both themes' green track (the one raw colour this board keeps) */}
                <div className="rt-knob" style={{ background: "#fff" }} />
              </div>
              <button onClick={() => setEditing(editing === r.id ? null : r.id)} title="Edit" className="bg-none bd-none c-3" style={{ cursor: "pointer", display: "flex" }}><Icon name="panel" size={14} /></button>
              <button onClick={() => void del(r.id)} title="Delete" className="bg-none bd-none c-3" style={{ cursor: "pointer", display: "flex" }}><Icon name="x" size={14} /></button>
            </div>
            <div className="mt-2" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="t-xs c-3">schedule</span>
              {editing === r.id
                ? <input className="rt-input" style={{ width: 160 }} value={r.cron} onChange={(e) => patch(r.id, "cron", e.target.value)} />
                : <span className="f-mono t-xs r-md pt-0 pr-1_5 pb-0 pl-1_5 bg-3 c-accent">{r.cron}</span>}
            </div>
            {r.plan_summary && <div className="t-xs c-2 mt-2 lh-snug">{r.plan_summary}</div>}
            {routineNote(r) && (
              <div role="note" className="rt-note">⚠ {routineNote(r)}</div>
            )}
            {pausedNote(r) && (
              <div role="note" className="rt-note">Paused — {pausedNote(r)}</div>
            )}
            {refusedNote(r) && (
              <div role="note" className="rt-note" data-routine-refused={r.name}>Not running — this old routine file could not become a routine: {refusedNote(r)}. Ask in Chat to set it up again.</div>
            )}
          </div>
        ))}
        {routines.length === 0 && <div className="c-3 t-sm pt-5 pr-0 pb-5 pl-0">No routines yet — open Chat and try <code className="f-mono c-accent">/routine</code>.</div>}
      </div>
    </div>
  );
}

// ── left launcher (opens the board, shows a compact list) ─────────────────────────
function RoutinesLeft() {
  const layout = useService(LayoutServiceId);
  const [routines, setRoutines] = useState<Routine[]>([]);
  useEffect(() => { layout.openTab(BOARD); void listRoutines().then(setRoutines).catch(() => {/* the board view surfaces the error loudly */}); }, [layout]);
  return (
    <div className="p-2">
      <RoutinesBoardNav />
      <div className="t-xs c-3 pt-1_5 pr-1 pb-1 pl-1 rt-kicker">scheduled agents</div>
      {routines.map((r) => <RoutineNavRow key={r.id} routine={r} />)}
      {routines.length === 0 && <div className="pt-2 pr-1 pb-2 pl-1 c-3 t-xs">None yet — create with <code className="f-mono c-accent">/routine</code> in Chat.</div>}
    </div>
  );
}

// Agent surface — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings).
if (!reducedMode()) {
  registerTab("routines", RoutinesBoard);
  registerList({ id: "routines", label: "Routines", icon: "zap", order: 40, component: RoutinesLeft });
}
