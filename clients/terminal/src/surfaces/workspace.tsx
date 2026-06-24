"use client";
/** Workspace — the git knowledge graph as: a "Files" LIST (left), a "doc" center TAB-kind (renders an
 *  entity: frontmatter + wikilinked body), and a "doc-context" RIGHT context (frontmatter + related
 *  [[links]]). Clicking a file opens a Doc tab that carries its own context. Reuses /api/workspace/*. */
import { useEffect, useState, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList, registerTab, registerContext, type TabProps, type ContextProps } from "../contributions";

const SUBJECT = "u_jane";
const base = (p: string) => p.split("/").pop() ?? p;
const docTab = (path: string) => ({ id: `doc:${path}`, title: base(path), kind: "doc", params: { path }, context: { kind: "doc-context", params: { path } } });

function parseEntity(text: string): { fm: [string, string][]; body: string } {
  const m = text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!m) return { fm: [], body: text };
  const fm: [string, string][] = [];
  for (const l of m[1].split("\n")) { const i = l.indexOf(":"); if (i > 0) fm.push([l.slice(0, i).trim(), l.slice(i + 1).trim()]); }
  return { fm, body: m[2] };
}
function wikilinks(text: string): ReactNode[] {
  return text.split(/(\[\[[^\]]+\]\])/).map((part, i) => part.startsWith("[[") ? <span key={i} style={{ color: "var(--blue)" }}>{part}</span> : <span key={i}>{part}</span>);
}
async function readFile(path: string): Promise<string> {
  try { const r = await fetch(`/api/workspace/file?subject=${SUBJECT}&path=${encodeURIComponent(path)}`); return r.ok ? (await r.json()).content ?? "" : "(not found)"; } catch { return "(error)"; }
}

// ── Files LIST (left) ───────────────────────────────────────────────────────────
function FilesList() {
  const layout = useService(LayoutServiceId);
  const [tree, setTree] = useState<string[]>([]);
  useEffect(() => { void (async () => { try { setTree((await (await fetch(`/api/workspace/tree?subject=${SUBJECT}`)).json()).files ?? []); } catch { /* offline */ } })(); }, []);
  return (
    <div style={{ padding: "6px 8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 8px" }}>knowledge graph</div>
      {tree.map((f) => (
        <div key={f} onClick={() => layout.openTab(docTab(f))} style={{ padding: "5px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>{f}</div>
      ))}
      {tree.length === 0 && <div style={{ padding: 8, color: "var(--t3)", fontSize: 12 }}>Empty — ask the agent in Chat to record something.</div>}
    </div>
  );
}

// ── Doc TAB (center, kind "doc") ─────────────────────────────────────────────────
function DocTab({ params }: TabProps) {
  const path = params.path as string;
  const [content, setContent] = useState<string | null>(null);
  useEffect(() => { void readFile(path).then(setContent); }, [path]);
  const { fm, body } = parseEntity(content ?? "");
  return (
    <div style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "22px 24px" }}>
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t3)", marginBottom: 12 }}>{path}</div>
        {fm.length > 0 && (
          <div style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "10px 13px", marginBottom: 14, fontSize: 13 }}>
            {fm.map(([k, v]) => <div key={k} style={{ display: "flex", gap: 10 }}><span style={{ color: "var(--t3)", width: 96 }}>{k}</span><span style={{ color: "var(--t1)" }}>{wikilinks(v)}</span></div>)}
          </div>
        )}
        <div style={{ fontSize: 14, color: "var(--t1)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>{content === null ? "loading…" : wikilinks(body)}</div>
      </div>
    </div>
  );
}

// ── Doc CONTEXT (right, kind "doc-context") ──────────────────────────────────────
function DocContext({ params }: ContextProps) {
  const path = params.path as string;
  const [content, setContent] = useState<string | null>(null);
  useEffect(() => { void readFile(path).then(setContent); }, [path]);
  const { fm, body } = parseEntity(content ?? "");
  const links = [...(body.matchAll(/\[\[([^\]]+)\]\]/g))].map((m) => m[1]);
  return (
    <div style={{ padding: "14px 16px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 8 }}>{base(path)}</div>
      {fm.map(([k, v]) => <div key={k} style={{ display: "flex", gap: 8, fontSize: 12.5, marginBottom: 4 }}><span style={{ color: "var(--t3)", width: 70 }}>{k}</span><span style={{ color: "var(--t1)" }}>{v}</span></div>)}
      {links.length > 0 && <>
        <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", margin: "14px 0 6px" }}>related</div>
        {links.map((l, i) => <div key={i} style={{ fontSize: 12.5, color: "var(--blue)", marginBottom: 3 }}>[[{l}]]</div>)}
      </>}
    </div>
  );
}

registerList({ id: "files", label: "Files", icon: "panel", order: 30, component: FilesList });
registerTab("doc", DocTab);
registerContext("doc-context", DocContext);
