"use client";
/** Workspace — the git knowledge graph as: a "Files" LIST (left), a "doc" center TAB-kind (renders an
 *  entity: frontmatter + wikilinked body), and a "doc-context" RIGHT context (frontmatter + related
 *  [[links]]). Clicking a file opens a Doc tab that carries its own context. Reuses /api/workspace/*. */
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList, registerTab, registerContext, type TabProps, type ContextProps } from "../contributions";
import { Icon } from "../ui-kit";
import { Markdown } from "../ui-kit/Markdown";

const SUBJECT = "u_live";  // the one workspace all the user's agents (chat + meeting research) write to

interface GitState { branch: string; changes: { path: string; kind: string }[]; commits: { sha: string; msg: string; when: string }[] }
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

// ── session-persisted UI flags ───────────────────────────────────────────────────
const readSS = (k: string): string | null => { try { return sessionStorage.getItem(k); } catch { return null; } };
const writeSS = (k: string, v: string) => { try { sessionStorage.setItem(k, v); } catch { /* noop */ } };

// ── tree model: fold the flat path list into nested folder/file nodes ─────────────
interface TreeNode { name: string; path: string; isDir: boolean; children: TreeNode[] }
function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", isDir: true, children: [] };
  for (const p of [...paths].sort()) {
    const parts = p.split("/").filter(Boolean);
    let cur = root;
    parts.forEach((part, i) => {
      const isLeaf = i === parts.length - 1;
      const path = parts.slice(0, i + 1).join("/");
      let next = cur.children.find((c) => c.name === part && c.isDir !== isLeaf);
      if (!next) { next = { name: part, path, isDir: !isLeaf, children: [] }; cur.children.push(next); }
      cur = next;
    });
  }
  const sortRec = (n: TreeNode) => { n.children.sort((a, b) => (a.isDir === b.isDir ? a.name.localeCompare(b.name) : a.isDir ? -1 : 1)); n.children.forEach(sortRec); };
  sortRec(root);
  return root.children;
}

// ── recursive tree row (folders collapse, files open a doc tab) ──────────────────
function TreeRow({ node, depth, expanded, toggle, openFile, pinFile }: {
  node: TreeNode; depth: number; expanded: Set<string>; toggle: (p: string) => void; openFile: (p: string) => void; pinFile: (p: string) => void;
}) {
  const pad = 9 + depth * 13;
  const hover = { onMouseEnter: (e: MouseEvent<HTMLDivElement>) => (e.currentTarget.style.background = "var(--panel2)"), onMouseLeave: (e: MouseEvent<HTMLDivElement>) => (e.currentTarget.style.background = "transparent") };
  // single-click → preview (debounced so a double-click doesn't also leave a stray preview);
  // double-click → pinned tab.
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  if (!node.isDir) {
    return (
      <div
        onClick={() => { if (clickTimer.current) clearTimeout(clickTimer.current); clickTimer.current = setTimeout(() => { clickTimer.current = null; openFile(node.path); }, 220); }}
        onDoubleClick={() => { if (clickTimer.current) { clearTimeout(clickTimer.current); clickTimer.current = null; } pinFile(node.path); }}
        {...hover}
        style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 9px", paddingLeft: pad + 14, borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        <Icon name="file" size={13} style={{ color: "var(--t3)" }} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>
      </div>
    );
  }
  const open = expanded.has(node.path);
  return (
    <>
      <div onClick={() => toggle(node.path)} {...hover}
        style={{ display: "flex", alignItems: "center", gap: 4, padding: "4px 9px", paddingLeft: pad, borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t1)" }}>
        <Icon name="chevR" size={13} style={{ color: "var(--t3)", transform: open ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
        <Icon name="folder" size={13} style={{ color: "var(--accent)" }} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>
      </div>
      {open && node.children.map((c) => <TreeRow key={c.path} node={c} depth={depth + 1} expanded={expanded} toggle={toggle} openFile={openFile} pinFile={pinFile} />)}
    </>
  );
}

// ── Files LIST (left) ───────────────────────────────────────────────────────────
const SS_EXPANDED = "ws.tree.expanded", SS_HIDDEN = "ws.tree.hidden";
function FilesList() {
  const layout = useService(LayoutServiceId);
  const [tree, setTree] = useState<string[]>([]);
  const [hidden, setHidden] = useState<boolean>(() => readSS(SS_HIDDEN) !== "0");
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try { const a = JSON.parse(readSS(SS_EXPANDED) ?? "null"); return new Set(Array.isArray(a) ? a : []); } catch { return new Set(); }
  });
  useEffect(() => {
    void (async () => {
      try { setTree((await (await fetch(`/api/workspace/tree?subject=${SUBJECT}${hidden ? "&hidden=1" : ""}`)).json()).files ?? []); } catch { /* offline */ }
    })();
  }, [hidden]);
  const nodes = buildTree(tree);
  // default expansion: top-level folders open, deeper folders collapsed (only when no saved state yet)
  useEffect(() => {
    if (readSS(SS_EXPANDED) != null || nodes.length === 0) return;
    const top = new Set(nodes.filter((n) => n.isDir).map((n) => n.path));
    setExpanded(top); writeSS(SS_EXPANDED, JSON.stringify([...top]));
  }, [tree]);  // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (p: string) => setExpanded((prev) => {
    const next = new Set(prev); next.has(p) ? next.delete(p) : next.add(p);
    writeSS(SS_EXPANDED, JSON.stringify([...next])); return next;
  });
  const toggleHidden = () => setHidden((v) => { const n = !v; writeSS(SS_HIDDEN, n ? "1" : "0"); return n; });
  return (
    <div style={{ padding: "6px 8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 8px", display: "flex", alignItems: "center", gap: 6 }}>
        <span>knowledge graph</span>
        <span onClick={toggleHidden} title={hidden ? "Hide hidden files" : "Show hidden files"}
          style={{ marginLeft: "auto", display: "flex", cursor: "pointer", color: hidden ? "var(--accent)" : "var(--t3)" }}>
          <Icon name={hidden ? "eye" : "eyeOff"} size={13} />
        </span>
      </div>
      {nodes.map((n) => <TreeRow key={n.path} node={n} depth={0} expanded={expanded} toggle={toggle} openFile={(p) => layout.openPreview(docTab(p))} pinFile={(p) => layout.openTab(docTab(p))} />)}
      {tree.length === 0 && <div style={{ padding: 8, color: "var(--t3)", fontSize: 12 }}>Empty — ask the agent in Chat to record something.</div>}
      <GitSection />
    </div>
  );
}

// ── Source control (git) — the agent's REAL commits + working changes over /api/workspace/git ──────
const SS_GIT_OPEN = "ws.git.open";
function GitSection() {
  const layout = useService(LayoutServiceId);
  const [open, setOpen] = useState<boolean>(() => readSS(SS_GIT_OPEN) === "1");  // default collapsed
  const [git, setGit] = useState<GitState>({ branch: "", changes: [], commits: [] });
  useEffect(() => {
    if (!open) return;  // only poll while expanded
    const load = () => fetch(`/api/workspace/git?subject=${SUBJECT}`).then((r) => r.json()).then(setGit).catch(() => {});
    load();
    const id = setInterval(load, 5000);  // reflect the agent's commits as they land
    return () => clearInterval(id);
  }, [open]);
  const toggle = () => setOpen((v) => { const n = !v; writeSS(SS_GIT_OPEN, n ? "1" : "0"); return n; });
  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
      <div onClick={toggle} style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "2px 8px 6px", display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
        <Icon name="chevR" size={12} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
        <Icon name="zap" size={12} />source control
        {git.branch && <span style={{ marginLeft: "auto", fontFamily: "var(--mono)", color: "var(--t2)", textTransform: "none" }}>{git.branch}</span>}
      </div>
      {!open ? null : (!git.branch && git.commits.length === 0) ? (
        <div style={{ padding: "2px 9px", fontSize: 12, color: "var(--t3)" }}>Not a repo yet.</div>
      ) : (<>
      {git.changes.length > 0 && <div style={{ fontSize: 10.5, color: "var(--t3)", padding: "2px 9px" }}>CHANGES</div>}
      {git.changes.map((c) => (
        <div key={c.path} onClick={() => layout.openPreview(docTab(c.path))} onDoubleClick={() => layout.openTab(docTab(c.path))} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12 }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <span style={{ width: 14, fontFamily: "var(--mono)", color: c.kind === "A" ? "var(--green)" : "var(--accent)", flex: "none" }}>{c.kind}</span>
          <span style={{ color: "var(--t2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{base(c.path)}</span>
        </div>
      ))}
      {git.commits.length > 0 && <div style={{ fontSize: 10.5, color: "var(--t3)", padding: "8px 9px 2px" }}>RECENT COMMITS</div>}
      {git.commits.map((c) => (
        <div key={c.sha} style={{ padding: "4px 9px", fontSize: 12 }}>
          <div style={{ color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{c.msg}</div>
          <div style={{ fontSize: 11, color: "var(--t3)", display: "flex", gap: 8 }}><span style={{ fontFamily: "var(--mono)", color: "var(--green)" }}>{c.sha}</span><span>{c.when}</span></div>
        </div>
      ))}
      </>)}
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
        <div style={{ fontSize: 14, color: "var(--t1)", lineHeight: 1.6 }}>{content === null ? "loading…" : <Markdown>{body}</Markdown>}</div>
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
