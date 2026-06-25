"use client";
/** Tasks — the tasks LIST (left). Reads kg/entities/task/* from the workspace; clicking a task opens its
 *  entity as a Doc tab (the "doc" tab-kind). Tasks are created/updated by the agent in Chat. */
import { useEffect, useState, type CSSProperties } from "react";
import type { TabDescriptor } from "../workbench/layout";
import { registerList } from "../contributions";
import { usePreviewPinTab } from "./previewPinTab";

const SUBJECT = "u_live";  // the terminal's single subject (until §0 auth) — must match every other surface
interface Task { path: string; title: string; state: string; priority?: string; due?: string }
const PRIO: Record<string, string> = { high: "var(--live)", med: "var(--accent)", medium: "var(--accent)", low: "var(--t2)" };

const taskDocTab = (path: string): TabDescriptor => ({ id: `doc:${path}`, title: path.split("/").pop() ?? path, kind: "doc", params: { path }, context: { kind: "doc-context", params: { path } } });
const cb = (done: boolean): CSSProperties => ({ width: 16, height: 16, borderRadius: 4, border: "1.5px solid var(--line2)", flex: "none", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, background: done ? "var(--green)" : "transparent", color: done ? "#08110c" : "transparent" });

function frontmatter(text: string): Record<string, string> {
  const m = text.match(/^---\n([\s\S]*?)\n---/);
  const o: Record<string, string> = {};
  if (m) for (const l of m[1].split("\n")) { const i = l.indexOf(":"); if (i > 0) o[l.slice(0, i).trim()] = l.slice(i + 1).trim(); }
  return o;
}

function TaskRow({ task }: { task: Task }) {
  const nav = usePreviewPinTab<HTMLDivElement>(taskDocTab(task.path));
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} style={{ display: "flex", alignItems: "flex-start", gap: 9, padding: "7px 9px", borderRadius: 6, cursor: "pointer" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <div style={cb(task.state === "done")}>✓</div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13, color: task.state === "done" ? "var(--t3)" : "var(--t1)", textDecoration: task.state === "done" ? "line-through" : "none" }}>{task.title}</div>
        <div style={{ display: "flex", gap: 8, marginTop: 3, fontSize: 11.5, color: "var(--t3)" }}>
          {task.priority && <span style={{ color: PRIO[task.priority] ?? "var(--t2)" }}>{task.priority}</span>}
          {task.due && <span>due {task.due}</span>}
        </div>
      </div>
    </div>
  );
}

function TasksList() {
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

  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 6px" }}>tasks</div>
      {tasks.map((t) => <TaskRow key={t.path} task={t} />)}
      {tasks.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>No tasks yet — ask the agent in Chat.</div>}
    </div>
  );
}

registerList({ id: "tasks", label: "Tasks", icon: "tasks", order: 50, component: TasksList });
