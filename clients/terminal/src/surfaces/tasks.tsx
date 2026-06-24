"use client";
/** Tasks surface (MVP2 slice) — reads `kg/entities/task/*` from the workspace and renders them as a
 *  task list. Tasks are created/updated by the agent in Chat; this is the read view (reuses the
 *  /api/workspace endpoints — no new backend). Done-toggle (write-back) is a later increment. */
import { useEffect, type CSSProperties } from "react";
import { createStore, useStore } from "../platform";
import { registerSurface } from "../contributions";

const SUBJECT = "u_jane";
interface Task { path: string; title: string; state: string; due?: string; priority?: string; source?: string }
const store = createStore<{ tasks: Task[]; loading: boolean }>({ tasks: [], loading: false });

function frontmatter(text: string): Record<string, string> {
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  const o: Record<string, string> = {};
  if (m) for (const l of m[1].split("\n")) { const i = l.indexOf(":"); if (i > 0) o[l.slice(0, i).trim()] = l.slice(i + 1).trim(); }
  return o;
}
async function loadTasks() {
  store.set((s) => ({ ...s, loading: true }));
  try {
    const tree: string[] = (await (await fetch(`/api/workspace/tree?subject=${SUBJECT}`)).json()).files ?? [];
    const files = tree.filter((f) => f.startsWith("kg/entities/task/"));
    const tasks: Task[] = [];
    for (const path of files) {
      const c = (await (await fetch(`/api/workspace/file?subject=${SUBJECT}&path=${encodeURIComponent(path)}`)).json()).content ?? "";
      const f = frontmatter(c);
      tasks.push({ path, title: f.title ?? path, state: (f.status ?? f.state ?? "open").toLowerCase(), due: f.due, priority: (f.priority ?? "").toLowerCase(), source: f.source });
    }
    store.set(() => ({ tasks, loading: false }));
  } catch { store.set((s) => ({ ...s, loading: false })); }
}

const prioColor: Record<string, string> = { high: "var(--live)", med: "var(--accent)", medium: "var(--accent)", low: "var(--t2)" };
const row = (t: Task) => {
  const done = t.state === "done";
  const cb: CSSProperties = { width: 18, height: 18, borderRadius: 5, border: "1.5px solid var(--line2)", flex: "none", marginTop: 1, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 11, background: done ? "var(--green)" : "transparent", color: done ? "#08110c" : "transparent" };
  return (
    <div key={t.path} style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "12px 18px", borderBottom: "1px solid var(--line)" }}>
      <div style={cb}>✓</div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, color: done ? "var(--t3)" : "var(--t1)", textDecoration: done ? "line-through" : "none" }}>{t.title}</div>
        <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 3, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          {t.priority && <span style={{ fontSize: 10.5, borderRadius: 5, padding: "1px 6px", color: prioColor[t.priority] ?? "var(--t2)", background: "var(--panel2)" }}>{t.priority}</span>}
          {t.due && <span>due {t.due}</span>}
          {t.source && <span>from {t.source}</span>}
        </div>
      </div>
    </div>
  );
};

function TasksMain() {
  const s = useStore(store);
  useEffect(() => { void loadTasks(); /* eslint-disable-next-line */ }, []);
  const open = s.tasks.filter((t) => t.state !== "done");
  const done = s.tasks.filter((t) => t.state === "done");
  const ttl: CSSProperties = { fontSize: 12, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "18px 18px 2px" };
  return (
    <div>
      {s.loading && <div style={{ padding: 18, color: "var(--t3)", fontSize: 13 }}>loading…</div>}
      {!s.loading && s.tasks.length === 0 && <div style={{ padding: "40px 24px", color: "var(--t3)", fontSize: 13, maxWidth: 760, margin: "0 auto" }}>No tasks yet — ask the agent in Chat to "create a task".</div>}
      {open.length > 0 && <><div style={ttl}>Open</div>{open.map(row)}</>}
      {done.length > 0 && <><div style={ttl}>Done</div>{done.map(row)}</>}
    </div>
  );
}

registerSurface({
  id: "tasks",
  activity: { id: "tasks", label: "Tasks", icon: "tasks", order: 60 },
  views: [{ id: "tasks.main", slot: "main", component: TasksMain }],
});
