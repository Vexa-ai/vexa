"use client";
/** Workspace surface (MVP1) — reads the per-person git knowledge graph (/api/workspace/*) and renders
 *  it: a file tree in the sidebar, the selected entity (frontmatter + wikilinked body) in the main view. */
import { useEffect, type ReactNode } from "react";
import { createStore, useStore } from "../platform";
import { registerSurface } from "../contributions";

const SUBJECT = "u_jane"; // matches MVP0; real auth/subject is MVP1 hardening

interface WS { tree: string[]; selected: string | null; content: string | null; loading: boolean }
const ws = createStore<WS>({ tree: [], selected: null, content: null, loading: false });

async function loadTree() {
  ws.set((s) => ({ ...s, loading: true }));
  try {
    const r = await fetch(`/api/workspace/tree?subject=${SUBJECT}`);
    const files: string[] = (await r.json()).files ?? [];
    ws.set((s) => ({ ...s, tree: files, loading: false }));
    if (!ws.getState().selected && files.length) void selectFile(files.find((f) => f.startsWith("kg/")) ?? files[0]);
  } catch {
    ws.set((s) => ({ ...s, loading: false }));
  }
}
async function selectFile(path: string) {
  ws.set((s) => ({ ...s, selected: path, content: null }));
  try {
    const r = await fetch(`/api/workspace/file?subject=${SUBJECT}&path=${encodeURIComponent(path)}`);
    ws.set((s) => ({ ...s, content: r.ok ? (await r.json()).content : "(not found)" }));
  } catch {
    ws.set((s) => ({ ...s, content: "(error)" }));
  }
}

function parseEntity(text: string): { fm: [string, string][]; body: string } {
  const m = text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!m) return { fm: [], body: text };
  const fm: [string, string][] = [];
  for (const line of m[1].split("\n")) { const i = line.indexOf(":"); if (i > 0) fm.push([line.slice(0, i).trim(), line.slice(i + 1).trim()]); }
  return { fm, body: m[2] };
}
function wikilinks(text: string): ReactNode[] {
  return text.split(/(\[\[[^\]]+\]\])/).map((part, i) =>
    part.startsWith("[[") ? <span key={i} style={{ color: "var(--blue)" }}>{part}</span> : <span key={i}>{part}</span>);
}

function Sidebar() {
  const s = useStore(ws);
  useEffect(() => { if (!s.tree.length && !s.loading) void loadTree(); /* eslint-disable-next-line */ }, []);
  return (
    <div style={{ padding: "4px 6px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "8px 8px 6px" }}>knowledge graph</div>
      {s.loading && <div style={{ padding: 8, color: "var(--t3)", fontSize: 12 }}>loading…</div>}
      {s.tree.map((f) => (
        <div key={f} onClick={() => void selectFile(f)} style={{ padding: "5px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: s.selected === f ? "var(--t1)" : "var(--t2)", background: s.selected === f ? "var(--panel2)" : "transparent" }}>{f}</div>
      ))}
      {!s.loading && !s.tree.length && <div style={{ padding: 8, color: "var(--t3)", fontSize: 12 }}>Empty — ask the agent in Chat to record something.</div>}
    </div>
  );
}

function Main() {
  const s = useStore(ws);
  if (!s.selected) return <div style={{ maxWidth: 760, margin: "0 auto", padding: "40px 24px", color: "var(--t3)", fontSize: 13 }}>Select an entity. The agent writes these from Chat.</div>;
  const { fm, body } = parseEntity(s.content ?? "");
  return (
    <div style={{ maxWidth: 760, margin: "0 auto", padding: "22px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t3)", marginBottom: 12 }}>{s.selected}</div>
      {fm.length > 0 && (
        <div style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "10px 13px", marginBottom: 14, fontSize: 13 }}>
          {fm.map(([k, v]) => (<div key={k} style={{ display: "flex", gap: 10 }}><span style={{ color: "var(--t3)", width: 96 }}>{k}</span><span style={{ color: "var(--t1)" }}>{wikilinks(v)}</span></div>))}
        </div>
      )}
      <div style={{ fontSize: 14, color: "var(--t1)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{wikilinks(body)}</div>
    </div>
  );
}

registerSurface({
  id: "workspace",
  activity: { id: "workspace", label: "Workspace", icon: "panel", order: 30 },
  views: [
    { id: "workspace.sidebar", slot: "primarySidebar", component: Sidebar },
    { id: "workspace.main", slot: "main", component: Main },
  ],
});
