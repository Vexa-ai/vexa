"use client";
/** Routines surface (MVP2) — scheduled agents. A routine is (name, cron, plan): it compiles to a
 *  schedule.v1 cron job in the runtime whose body is a unit.v1 Invocation POSTed to /invocations when
 *  due. Create here (or via /routine in chat); the list comes straight from the scheduler. The unit
 *  runs unattended and commits to the workspace — the same agent-runtime-unit as chat, on a clock. */
import { useEffect, useState, type CSSProperties } from "react";
import { createStore, useStore } from "../platform";
import { registerSurface } from "../contributions";

const SUBJECT = "u_jane";
interface Routine { id: string; name: string; cron: string; plan_summary?: string; plan_kind?: string; next_run?: number; status?: string }
const store = createStore<{ routines: Routine[]; loading: boolean }>({ routines: [], loading: false });

async function loadRoutines() {
  store.set((s) => ({ ...s, loading: true }));
  try {
    const routines: Routine[] = (await (await fetch(`/api/routines?subject=${SUBJECT}`)).json()).routines ?? [];
    store.set(() => ({ routines, loading: false }));
  } catch { store.set((s) => ({ ...s, loading: false })); }
}
async function createRoutine(name: string, cron: string, prompt: string) {
  await fetch(`/api/routines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ subject: SUBJECT, name, cron, prompt }),
  });
  await loadRoutines();
}
async function deleteRoutine(id: string) {
  await fetch(`/api/routines/${id}?subject=${SUBJECT}`, { method: "DELETE" });
  await loadRoutines();
}

const CRON_PRESETS: [string, string][] = [
  ["Every minute (demo)", "* * * * *"],
  ["Every 15 min, work hours", "*/15 9-18 * * 1-5"],
  ["Weekdays 8am", "0 8 * * 1-5"],
  ["Daily 7am", "0 7 * * *"],
];

const inputStyle: CSSProperties = {
  width: "100%", background: "var(--panel2)", border: "1px solid var(--line2)", borderRadius: 8,
  padding: "8px 11px", color: "var(--t1)", fontSize: 13, outline: "none", fontFamily: "inherit",
};
const label: CSSProperties = { fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 5, display: "block" };

function CreateForm() {
  const [name, setName] = useState("");
  const [cron, setCron] = useState("* * * * *");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const canCreate = name.trim() && cron.trim() && prompt.trim() && !busy;
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--panel)", padding: 16, marginBottom: 22 }}>
      <div style={{ fontSize: 13.5, color: "var(--t1)", fontWeight: 500, marginBottom: 12 }}>New routine</div>
      <div style={{ marginBottom: 11 }}>
        <label style={label}>Name</label>
        <input style={inputStyle} value={name} onChange={(e) => setName(e.target.value)} placeholder="Morning brief" />
      </div>
      <div style={{ marginBottom: 11 }}>
        <label style={label}>Schedule (cron)</label>
        <input style={{ ...inputStyle, fontFamily: "var(--mono)" }} value={cron} onChange={(e) => setCron(e.target.value)} />
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 7 }}>
          {CRON_PRESETS.map(([lbl, expr]) => (
            <button key={expr} onClick={() => setCron(expr)} style={{ fontSize: 11, padding: "3px 8px", borderRadius: 6, cursor: "pointer", border: "1px solid var(--line2)", background: cron === expr ? "var(--accent)" : "var(--panel2)", color: cron === expr ? "#08110c" : "var(--t2)" }}>{lbl}</button>
          ))}
        </div>
      </div>
      <div style={{ marginBottom: 13 }}>
        <label style={label}>Plan (what the agent does each run)</label>
        <textarea style={{ ...inputStyle, minHeight: 64, resize: "vertical" }} value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="Review new emails since the last run and record any follow-ups as task entities linked to the people and companies involved." />
      </div>
      <button
        disabled={!canCreate}
        onClick={async () => { setBusy(true); try { await createRoutine(name.trim(), cron.trim(), prompt.trim()); setName(""); setPrompt(""); } finally { setBusy(false); } }}
        style={{ fontSize: 13, padding: "8px 16px", borderRadius: 8, border: "none", cursor: canCreate ? "pointer" : "not-allowed", background: canCreate ? "var(--accent)" : "var(--panel2)", color: canCreate ? "#08110c" : "var(--t3)", fontWeight: 500 }}
      >{busy ? "Creating…" : "Create routine"}</button>
    </div>
  );
}

const nextRun = (t?: number) => {
  if (!t) return null;
  const d = new Date(t * 1000);
  return d.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" });
};

function card(r: Routine) {
  return (
    <div key={r.id} style={{ display: "flex", alignItems: "flex-start", gap: 12, border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "12px 14px", marginBottom: 10 }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, color: "var(--t1)", fontWeight: 500 }}>{r.name}</div>
        <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 5, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <span style={{ fontFamily: "var(--mono)", fontSize: 11, borderRadius: 5, padding: "1px 6px", background: "var(--panel2)", color: "var(--accent)" }}>{r.cron}</span>
          {nextRun(r.next_run) && <span>next {nextRun(r.next_run)}</span>}
          <span style={{ color: "var(--t2)" }}>· scheduled</span>
        </div>
        {r.plan_summary && <div style={{ fontSize: 12.5, color: "var(--t2)", marginTop: 8, lineHeight: 1.5 }}>{r.plan_summary}</div>}
      </div>
      <button onClick={() => void deleteRoutine(r.id)} title="Delete routine" style={{ border: "none", background: "transparent", color: "var(--t3)", cursor: "pointer", fontSize: 16, lineHeight: 1, padding: 2 }}>×</button>
    </div>
  );
}

function RoutinesMain() {
  const s = useStore(store);
  useEffect(() => { void loadRoutines(); /* eslint-disable-next-line */ }, []);
  return (
    <div style={{ maxWidth: 720, margin: "0 auto", padding: "26px 24px" }}>
      <CreateForm />
      <div style={{ fontSize: 12, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 10 }}>Active routines</div>
      {s.loading && <div style={{ color: "var(--t3)", fontSize: 13 }}>loading…</div>}
      {!s.loading && s.routines.length === 0 && <div style={{ color: "var(--t3)", fontSize: 13 }}>No routines yet — create one above. It runs unattended on its schedule and commits to your workspace.</div>}
      {s.routines.map(card)}
    </div>
  );
}

registerSurface({
  id: "routines",
  activity: { id: "routines", label: "Routines", icon: "zap", order: 70 },
  views: [{ id: "routines.main", slot: "main", component: RoutinesMain }],
});
