"use client";
/** Tasks — the tasks LIST (left). Reads kg/entities/task/* from the workspace; clicking a task opens its
 *  entity as a Doc tab (the "doc" tab-kind). Tasks are created/updated by the agent in Chat. */
import { useEffect, useState, type CSSProperties } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList } from "../contributions";

const SUBJECT = "u_jane";
interface Task { path: string; title: string; state: string; priority?: string; due?: string }

function frontmatter(text: string): Record<string, string> {
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  const o: Record<string, string> = {};
  if (m) for (const l of m[1].split("\n")) { const i = l.indexOf(":"); if (i > 0) o[l.slice(0, i).trim()] = l.slice(i + 1).trim(); }
  return o;
}

function TasksList() {
  const layout = useService(LayoutServiceId);
  const [tasks, setTasks] = useState<Task[]>([]);
  useEffect(() => { void (async () => {
    try {
      const tree: string[] = (await (await fetch(`/api/workspace/tree?subject=${SUBJECT}`)).json()).files ?? [];
      const out: Task[] = [];
      for (const path of tree.filter((f) => f.startsWith("kg/entities/task/"))) {
        const c = (await (await fetch(`/api/workspace/file?subject=${SUBJECT}&path=${encodeURIComponent(path)}`)).json()).content ?? "";
        const f = frontmatter(c);
        out.push({ path, title: f.title ?? path, state: (f.status ?? f.state ?? "open").toLowerCase(), priority: (f.priority ?? "").toLowerCase(), due: f.due });
      }
      setTasks(out);
    } catch { /* offline */ }
  })(); }, []);

  const prio: Record<string, string> = { high: "var(--live)", med: "var(--accent)", medium: "var(--accent)", low: "var(--t2)" };
  const open = (path: string) => layout.openTab({ id: `doc:${path}`, title: path.split("/").pop() ?? path, kind: "doc", params: { path }, context: { kind: "doc-context", params: { path } } });
  const cb = (done: boolean): CSSProperties => ({ width: 16, height: 16, borderRadius: 4, border: "1.5px solid var(--line2)", flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, background: done ? "var(--green)" : "transparent", color: done ? "#08110c" : "transparent" });

  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 6px" }}>tasks</div>
      {tasks.map((t) => (
        <div key={t.path} onClick={() => open(t.path)} style={{ display: "flex", alignItems: "flex-start", gap: 9, padding: "7px 9px", borderRadius: 6, cursor: "pointer" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <div style={cb(t.state === "done")}>✓</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13, color: t.state === "done" ? "var(--t3)" : "var(--t1)", textDecoration: t.state === "done" ? "line-through" : "none" }}>{t.title}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 3, fontSize: 11.5, color: "var(--t3)" }}>
              {t.priority && <span style={{ color: prio[t.priority] ?? "var(--t2)" }}>{t.priority}</span>}
              {t.due && <span>due {t.due}</span>}
            </div>
          </div>
        </div>
      ))}
      {tasks.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No tasks yet — ask the agent in Chat.</div>}
    </div>
  );
}

registerList({ id: "tasks", label: "Tasks", icon: "tasks", order: 50, component: TasksList });
