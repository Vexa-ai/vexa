"use client";
/** Routines — the routines LIST (left). Scheduled agents are created in CHAT (the /routine skill / a
 *  tool); this list manages them (delete now; enable/disable/edit when the backend lands). Reads
 *  /api/routines. Each row shows name · cron · plan summary. */
import { useEffect, useState } from "react";
import { registerList } from "../contributions";
import { Icon } from "../ui-kit";

const SUBJECT = "u_jane";
interface Routine { id: string; name: string; cron: string; plan_summary?: string; next_run?: number }

function RoutinesList() {
  const [routines, setRoutines] = useState<Routine[]>([]);
  const load = async () => { try { setRoutines((await (await fetch(`/api/routines?subject=${SUBJECT}`)).json()).routines ?? []); } catch { /* offline */ } };
  useEffect(() => { void load(); }, []);
  const del = async (id: string) => { await fetch(`/api/routines/${id}?subject=${SUBJECT}`, { method: "DELETE" }); void load(); };

  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 6px" }}>scheduled agents</div>
      {routines.map((r) => (
        <div key={r.id} style={{ border: "1px solid var(--line)", borderRadius: 9, background: "var(--panel)", padding: "9px 11px", marginBottom: 7 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 13, color: "var(--t1)", fontWeight: 500, flex: 1 }}>{r.name}</span>
            <button onClick={() => void del(r.id)} title="Delete" style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex" }}><Icon name="x" size={13} /></button>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 4, alignItems: "center" }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, borderRadius: 5, padding: "1px 6px", background: "var(--panel2)", color: "var(--accent)" }}>{r.cron}</span>
          </div>
          {r.plan_summary && <div style={{ fontSize: 12, color: "var(--t2)", marginTop: 6, lineHeight: 1.45 }}>{r.plan_summary}</div>}
        </div>
      ))}
      {routines.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No routines — create one in Chat with <code style={{ fontFamily: "var(--mono)", color: "var(--accent)" }}>/routine</code>.</div>}
    </div>
  );
}

registerList({ id: "routines", label: "Routines", icon: "zap", order: 40, component: RoutinesList });
