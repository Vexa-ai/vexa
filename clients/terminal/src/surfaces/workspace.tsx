"use client";
/** Workspace — the git knowledge graph as: a "Files" LIST (left), a "doc" center TAB-kind (renders an
 *  entity: frontmatter + wikilinked body). Clicking a file opens a Doc tab; the chat rail references the
 *  active file from the center tab. Reuses /api/workspace/*. */
import { useContext, useEffect, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId } from "../workbench/layout";
import { registerList, registerTab, type TabProps } from "../contributions";
import { meetingsOnly } from "../app/mode";
import { Icon } from "../ui-kit";
import { OPEN_ENTITY_EVENT } from "../canvas/actions";
import { ENTITY_CHIP, DEFAULT_ENTITY_CHIP, DocNavContext, type DocNavigate } from "../ui-kit/MdxDoc";
import { ContextMenu, copyText } from "../ui-kit/ContextMenu";
import { MdxDoc } from "../ui-kit/MdxDoc";
// Data-access lives in its own SoC module (scoped to the authed user — no client subject, P20),
// proven in isolation by workspaceApi.test.ts.
import { readWorkspaceFile, listWorkspaceTree, readWorkspaceGit, readAttachedWorkspaces, swapWorkspace, renameWorkspace, publishWorkspace, type GitState, type AttachedWorkspaces, type PublishResult } from "./workspaceApi";
const base = (p: string) => p.split("/").pop() ?? p;
const docTab = (path: string) => ({ id: `doc:${path}`, title: base(path), kind: "doc", params: { path } });

function parseEntity(text: string): { fm: [string, string][]; body: string } {
  const m = text.match(/^---\n([\s\S]*?)\n---\n([\s\S]*)$/);
  if (!m) return { fm: [], body: text };
  const fm: [string, string][] = [];
  for (const l of m[1].split("\n")) { const i = l.indexOf(":"); if (i > 0) fm.push([l.slice(0, i).trim(), l.slice(i + 1).trim()]); }
  return { fm, body: m[2] };
}
function wikilinks(text: string, navigate?: DocNavigate | null): ReactNode[] {
  // Frontmatter [[wikilinks]] are clickable: navigate the doc pane in place when it provides
  // a navigator (Obsidian-style), else fall back to the OPEN_ENTITY_EVENT tab path.
  const open = (wikilink: string) => navigate
    ? navigate({ wikilink })
    : window.dispatchEvent(new CustomEvent(OPEN_ENTITY_EVENT, { detail: { wikilink } }));
  return text.split(/(\[\[[^\]]+\]\])/).map((part, i) => part.startsWith("[[")
    ? <span key={i} onClick={() => open(part.slice(2, -2))}
        style={{ color: "var(--blue)", cursor: "pointer" }}>{part}</span>
    : <span key={i}>{part}</span>);
}

// [[Title]] → kg/entities/<type>/<slug>.md (same resolution the workbench uses for chat links)
const entitySlug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
async function resolveWikilink(title: string): Promise<string | undefined> {
  const tree = await listWorkspaceTree().catch(() => [] as string[]);
  return tree.find((p) => p.startsWith("kg/entities/") && p.endsWith(`/${entitySlug(title)}.md`));
}

// ── reveal-in-tree: breadcrumb segments ask the Files list to expand down to a folder ──
const REVEAL_PATH_EVENT = "vexa:terminal:reveal-path";
/** All ancestor dir paths of `dir` (inclusive), e.g. "kg/entities/org" → [kg, kg/entities, kg/entities/org]. */
const ancestorDirs = (dir: string): string[] => dir.split("/").filter(Boolean).map((_, i, parts) => parts.slice(0, i + 1).join("/"));
function revealInTree(dir: string): void {
  // Persist first so a not-yet-mounted FilesList picks it up on mount; the event covers the mounted case.
  try {
    const cur = JSON.parse(readSS(SS_EXPANDED) ?? "[]") as string[];
    writeSS(SS_EXPANDED, JSON.stringify([...new Set([...(Array.isArray(cur) ? cur : []), ...ancestorDirs(dir)])]));
  } catch { writeSS(SS_EXPANDED, JSON.stringify(ancestorDirs(dir))); }
  if (!dir.startsWith("kg")) writeSS(SS_HIDDEN, "0");  // target hidden by the kg-only filter → reveal all
  window.dispatchEvent(new CustomEvent(REVEAL_PATH_EVENT, { detail: { dir } }));
}
async function readFile(path: string): Promise<string> {
  return (await readWorkspaceFile(path)) ?? "(not found)";
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
function TreeRow({ node, depth, expanded, toggle, openFile, pinFile, openMenu }: {
  node: TreeNode; depth: number; expanded: Set<string>; toggle: (p: string) => void; openFile: (p: string) => void; pinFile: (p: string) => void; openMenu: (e: MouseEvent<HTMLDivElement>, p: string) => void;
}) {
  const pad = 9 + depth * 13;
  const hover = { onMouseEnter: (e: MouseEvent<HTMLDivElement>) => (e.currentTarget.style.background = "var(--panel2)"), onMouseLeave: (e: MouseEvent<HTMLDivElement>) => (e.currentTarget.style.background = "transparent") };
  // single-click → preview (immediate — openPreview/openTab reconcile by id, so a
  // double-click harmlessly previews then pins); double-click → pinned tab.
  if (!node.isDir) {
    return (
      <div
        data-tree-path={node.path}
        onClick={() => openFile(node.path)}
        onDoubleClick={() => pinFile(node.path)}
        onContextMenu={(e) => openMenu(e, node.path)}
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
      <div data-tree-path={node.path} onClick={() => toggle(node.path)} {...hover}
        style={{ display: "flex", alignItems: "center", gap: 4, padding: "4px 9px", paddingLeft: pad, borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t1)" }}>
        <Icon name="chevR" size={13} style={{ color: "var(--t3)", transform: open ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
        <Icon name="folder" size={13} style={{ color: "var(--accent)" }} />
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{node.name}</span>
      </div>
      {open && node.children.map((c) => <TreeRow key={c.path} node={c} depth={depth + 1} expanded={expanded} toggle={toggle} openFile={openFile} pinFile={pinFile} openMenu={openMenu} />)}
    </>
  );
}

// ── Files LIST (left) ───────────────────────────────────────────────────────────
const SS_EXPANDED = "ws.tree.expanded", SS_HIDDEN = "ws.tree.hidden";
function FilesList() {
  const layout = useService(LayoutServiceId);
  const [tree, setTree] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18): a tree-load failure is shown, not hidden as "empty"
  const [menu, setMenu] = useState<{ x: number; y: number; path: string } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);  // bumped after a workspace swap → re-fetch the tree
  // Knowledge view defaults to ONLY the knowledge graph (kg/); the eye toggle reveals the rest of the
  // workspace scaffold (CLAUDE.md, agents/, skills/, views/, …). Default ON = kg-only.
  const [kgOnly, setKgOnly] = useState<boolean>(() => readSS(SS_HIDDEN) !== "0");
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    try { const a = JSON.parse(readSS(SS_EXPANDED) ?? "null"); return new Set(Array.isArray(a) ? a : []); } catch { return new Set(); }
  });
  useEffect(() => {
    // Never request dotfiles (hidden:false) — the `.git`/`.claude` listing 500s; the toggle is a client-side
    // kg-only vs full-workspace filter, not a dotfile switch.
    // The agent writes files continuously, so the tree self-refreshes: poll while the tab is
    // visible + re-fetch on window focus. setTree only on change so React skips no-op renders.
    const load = () => {
      void listWorkspaceTree({ hidden: false })
        .then((t) => { setTree((prev) => (JSON.stringify(prev) === JSON.stringify(t) ? prev : t)); setError(null); })
        .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
    };
    load();
    const id = setInterval(() => { if (!document.hidden) load(); }, 5000);
    window.addEventListener("focus", load);
    return () => { clearInterval(id); window.removeEventListener("focus", load); };
  }, [reloadKey]);
  const nodes = buildTree(kgOnly ? tree.filter((p) => p.startsWith("kg/")) : tree);
  // default expansion: top-level folders open, deeper folders collapsed (only when no saved state yet)
  useEffect(() => {
    if (readSS(SS_EXPANDED) != null || nodes.length === 0) return;
    const top = new Set(nodes.filter((n) => n.isDir).map((n) => n.path));
    setExpanded(top); writeSS(SS_EXPANDED, JSON.stringify([...top]));
  }, [tree]);  // eslint-disable-line react-hooks/exhaustive-deps
  // Breadcrumb "reveal in tree": expand every ancestor of the requested folder (and leave the
  // kg-only filter if the target lives outside kg/). sessionStorage was already updated by the sender.
  useEffect(() => {
    const onReveal = (e: Event) => {
      const dir = (e as CustomEvent<{ dir?: string }>).detail?.dir;
      if (!dir) return;
      if (!dir.startsWith("kg")) setKgOnly(false);
      setQuery("");  // a live search hides the tree — clear it so the reveal is visible
      setExpanded((prev) => new Set([...prev, ...ancestorDirs(dir)]));
      // after the expansion renders, bring the revealed row into view and flash it
      window.setTimeout(() => {
        const row = document.querySelector<HTMLElement>(`[data-tree-path="${CSS.escape(dir)}"]`);
        if (!row) return;
        row.scrollIntoView({ block: "nearest" });
        row.style.transition = "background .5s";
        row.style.background = "var(--panel2)";
        window.setTimeout(() => { row.style.background = "transparent"; }, 700);
      }, 80);
    };
    window.addEventListener(REVEAL_PATH_EVENT, onReveal);
    return () => window.removeEventListener(REVEAL_PATH_EVENT, onReveal);
  }, []);
  const toggle = (p: string) => setExpanded((prev) => {
    const next = new Set(prev); next.has(p) ? next.delete(p) : next.add(p);
    writeSS(SS_EXPANDED, JSON.stringify([...next])); return next;
  });
  const toggleKgOnly = () => setKgOnly((v) => { const n = !v; writeSS(SS_HIDDEN, n ? "1" : "0"); return n; });
  const openMenu = (e: MouseEvent<HTMLDivElement>, path: string) => {
    e.preventDefault();
    e.stopPropagation();
    setMenu({ x: e.clientX, y: e.clientY, path });
  };
  // ── instant file-name search: pure client-side filter over the already-loaded tree ──
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const scoped = kgOnly ? tree.filter((p) => p.startsWith("kg/")) : tree;
  // filename match ranks above path match, shorter names first — the exact file you typed floats up
  const matches = q
    ? scoped
        .map((p) => ({ p, name: base(p).toLowerCase() }))
        .filter(({ p, name }) => name.includes(q) || p.toLowerCase().includes(q))
        .sort((a, b) => {
          const an = a.name.includes(q) ? 0 : 1, bn = b.name.includes(q) ? 0 : 1;
          return an !== bn ? an - bn : a.name.length - b.name.length || a.p.localeCompare(b.p);
        })
        .slice(0, 60)
        .map(({ p }) => p)
    : [];
  return (
    <div style={{ padding: "6px 8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 8px", display: "flex", alignItems: "center", gap: 6 }}>
        <span>knowledge</span>
        <span onClick={() => setReloadKey((k) => k + 1)} title="Refresh the file list"
          style={{ marginLeft: "auto", display: "flex", cursor: "pointer", color: "var(--t3)" }}>
          <Icon name="refresh" size={13} />
        </span>
        <span onClick={toggleKgOnly} title={kgOnly ? "Show all workspace files" : "Show only the knowledge graph"}
          style={{ display: "flex", cursor: "pointer", color: kgOnly ? "var(--accent)" : "var(--t3)" }}>
          <Icon name={kgOnly ? "eye" : "eyeOff"} size={13} />
        </span>
      </div>
      <div style={{ padding: "0 4px 8px", position: "relative" }}>
        <Icon name="search" size={12} style={{ position: "absolute", left: 13, top: 8, color: "var(--t3)", pointerEvents: "none" }} />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") { e.stopPropagation(); setQuery(""); e.currentTarget.blur(); }
            if (e.key === "Enter" && matches[0]) layout.openPreview(docTab(matches[0]));
          }}
          placeholder="Find file…"
          spellCheck={false}
          style={{ width: "100%", boxSizing: "border-box", fontSize: 12.5, padding: "5px 8px 5px 26px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 7, color: "var(--t1)", outline: "none" }}
        />
      </div>
      {error && <div role="alert" style={{ margin: "0 8px 8px", fontSize: 12, color: "var(--live)", background: "var(--panel)", border: "1px solid var(--live)", borderRadius: 8, padding: "8px 10px" }}>⚠ Couldn’t load the workspace — {error}</div>}
      {q ? (<>
        {matches.map((p) => (
          <div key={p} onClick={() => layout.openPreview(docTab(p))} onDoubleClick={() => layout.openTab(docTab(p))} onContextMenu={(e) => openMenu(e, p)}
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
            style={{ padding: "4px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            <Icon name="file" size={13} style={{ color: "var(--t3)", flex: "none" }} />
            <span style={{ color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flex: "none", maxWidth: "60%" }}>{base(p)}</span>
            <span style={{ color: "var(--t3)", fontSize: 11, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", direction: "rtl" }}>{p.slice(0, -base(p).length).replace(/\/$/, "")}</span>
          </div>
        ))}
        {matches.length === 0 && <div style={{ padding: 8, color: "var(--t3)", fontSize: 12 }}>No files match “{query.trim()}”.</div>}
      </>) : (<>
        {nodes.map((n) => <TreeRow key={n.path} node={n} depth={0} expanded={expanded} toggle={toggle} openFile={(p) => layout.openPreview(docTab(p))} pinFile={(p) => layout.openTab(docTab(p))} openMenu={openMenu} />)}
        {!error && tree.length === 0 && <div style={{ padding: 8, color: "var(--t3)", fontSize: 12 }}>Empty — ask the agent in Chat to record something.</div>}
      </>)}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={[
          { id: "copy-reference", label: "Copy reference", detail: `@file:${menu.path}`, onSelect: () => copyText(`@file:${menu.path}`) },
          { id: "copy-path", label: "Copy path", detail: menu.path, onSelect: () => copyText(menu.path) },
        ]} />
      )}
      <WorkspaceSwitcher onSwapped={() => setReloadKey((k) => k + 1)} />
      <GitSection />
    </div>
  );
}

// ── Workspaces (attach/swap a custom git repo) — over /api/workspace/swap + /attached ──────────────
const SS_WS_OPEN = "ws.attach.open";
function WorkspaceSwitcher({ onSwapped }: { onSwapped: () => void }) {
  const [open, setOpen] = useState<boolean>(() => readSS(SS_WS_OPEN) === "1");  // default collapsed
  const [view, setView] = useState<AttachedWorkspaces>({ active: null, slots: {} });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ repo: string; ref: string; token: string } | null>(null);  // non-null = attach form shown
  // non-null = publish form shown; remoteUrl set = PUSH-UPDATES mode (plain push to the published home)
  const [pubForm, setPubForm] = useState<{ name: string; priv: boolean; token: string; remoteUrl?: string } | null>(null);
  const [published, setPublished] = useState<PublishResult | null>(null);  // last publish success (repo URL shown)
  const [renaming, setRenaming] = useState<string | null>(null);  // slug whose name is being edited inline
  const cancelled = useRef(false);  // Escape vs Enter/blur on the rename input (blur fires for both)
  const load = () => { void readAttachedWorkspaces().then((v) => { setView(v); setErr(null); }).catch((e: unknown) => setErr(e instanceof Error ? e.message : String(e))); };
  useEffect(() => { if (open) load(); }, [open]);
  const toggle = () => setOpen((v) => { const n = !v; writeSS(SS_WS_OPEN, n ? "1" : "0"); return n; });

  const doSwap = async (repo: string | undefined, ref?: string, token?: string, fresh?: boolean) => {
    setBusy(true); setErr(null);
    try { await swapWorkspace(repo, ref, token, fresh); load(); onSwapped(); setForm(null); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  // Swap to an existing slot by SLUG (restores the parked tree, no re-clone — reaches no-repo slots like
  // the seed and the 'start fresh' backup). `fresh` (seed only) rebuilds the default from the template.
  const swapToSlot = async (slug: string, fresh?: boolean) => {
    setBusy(true); setErr(null);
    try { await swapWorkspace(undefined, undefined, undefined, fresh, slug); load(); onSwapped(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  // Publish the vexa-born ACTIVE workspace to GitHub — repo created with the per-call token (never
  // stored, P15), full history pushed. `remoteUrl` set = push updates to the already-published home
  // (plain push, never force). Success shows the repo URL and reloads the view so the row flips to
  // its published state (link + push); failure shows the (redacted) error.
  const doPublish = async (f: { name: string; priv: boolean; token: string; remoteUrl?: string }) => {
    setBusy(true); setErr(null);
    try { setPublished(await publishWorkspace(f.name.trim(), f.priv, f.token.trim(), f.remoteUrl)); setPubForm(null); load(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const doRename = async (slug: string, name: string) => {
    setBusy(true); setErr(null);
    try { setView(await renameWorkspace(slug, name.trim())); setRenaming(null); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); setRenaming(null); }
    finally { setBusy(false); }
  };

  // The slots, with the seed always offered (so you can always swap back to the default).
  const slots = Object.entries(view.slots);
  if (!slots.some(([s]) => s === "seed")) slots.unshift(["seed", { repo: null, ref: null }]);
  const label = (slug: string, repo: string | null) => (slug === "seed" ? "default (seed)" : repo ?? slug);

  // Publish applies to a VEXA-BORN active workspace only — one attached from an external repo already
  // has a home (the backend refuses it too). Default repo name: the workspace's display name, slugified.
  const activeSlug = view.active ?? "seed";
  const activeBorn = !view.slots[activeSlug]?.repo;
  const defaultRepoName = (view.slots[activeSlug]?.name ?? (activeSlug === "seed" ? "vexa-workspace" : activeSlug))
    .toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "vexa-workspace";

  return (
    <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 8 }}>
      <div onClick={toggle} style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "2px 8px 6px", display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
        <Icon name="chevR" size={12} style={{ transform: open ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
        <Icon name="folder" size={12} />workspaces
      </div>
      {open && (<>
        {err && <div role="alert" style={{ padding: "2px 9px", fontSize: 12, color: "var(--live)" }}>⚠ {err}</div>}
        {slots.map(([slug, meta]) => {
          // A never-swapped subject (view.active === null) is already ON the seed, so the seed row is
          // the ACTIVE one — render it active + non-clickable. Without this it shows as ○ and clicking
          // it triggers a destructive swap that parks the live workspace and swaps in a blank re-seed.
          const active = view.active === slug || (!view.active && slug === "seed");
          const isRenaming = renaming === slug;
          const display = meta.name || label(slug, meta.repo);
          return (
            <div key={slug}
              style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 9px", borderRadius: 6, fontSize: 12, opacity: busy ? 0.6 : 1 }}
              onMouseEnter={(e) => { if (!active && !isRenaming) e.currentTarget.style.background = "var(--panel2)"; }} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
              <span style={{ width: 14, flex: "none", color: active ? "var(--green)" : "var(--t3)" }}>{active ? "●" : "○"}</span>
              {isRenaming ? (
                <input autoFocus defaultValue={meta.name ?? ""} placeholder="display name" disabled={busy}
                  onKeyDown={(e) => { if (e.key === "Enter") { cancelled.current = false; e.currentTarget.blur(); } else if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); } }}
                  onBlur={(e) => { if (cancelled.current) { cancelled.current = false; setRenaming(null); } else { void doRename(slug, e.currentTarget.value); } }}
                  style={{ flex: 1, fontSize: 12, padding: "3px 6px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 5, color: "var(--t1)" }} />
              ) : (
                <span onClick={() => !active && !busy && swapToSlot(slug)}
                  title={active ? "Active workspace" : "Swap to this workspace"}
                  style={{ flex: 1, color: active ? "var(--t1)" : "var(--t2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", cursor: active ? "default" : "pointer" }}>{display}</span>
              )}
              {/* Published-state affordances — ON the active row (publish is an action on THIS workspace,
                  not a list item): published → a link to its GitHub home (+ a secondary push-updates
                  action, re-publish is a plain push); not yet published (vexa-born only) → the publish
                  action itself. An attached workspace shows neither — it already has a home. */}
              {!isRenaming && active && view.published_url && (
                <a href={view.published_url} target="_blank" rel="noreferrer"
                  title={`Published — open on GitHub (${view.published_url})`}
                  style={{ flex: "none", color: "var(--t3)", cursor: "pointer", padding: "0 3px", display: "flex", alignItems: "center" }}>
                  <Icon name="github" size={12} />
                </a>
              )}
              {!isRenaming && active && activeBorn && !busy && (
                <span onClick={() => { setPublished(null); setPubForm({ name: defaultRepoName, priv: true, token: "", remoteUrl: view.published_url ?? undefined }); }}
                  title={view.published_url ? "Push updates to GitHub" : "Publish this workspace to GitHub…"}
                  style={{ flex: "none", color: "var(--t3)", cursor: "pointer", padding: "0 3px", display: "flex", alignItems: "center" }}>
                  <Icon name="upload" size={12} />
                </span>
              )}
              {!isRenaming && (
                <span onClick={() => setRenaming(slug)} title="Rename (display label only)"
                  style={{ flex: "none", color: "var(--t3)", cursor: "pointer", padding: "0 3px", fontSize: 11 }}>✎</span>
              )}
              {!isRenaming && slug === "seed" && !busy && (
                <span onClick={() => { if (window.confirm("Start fresh? The default workspace is rebuilt from the template. Your current default is kept under a recoverable backup ('default (previous)').")) void swapToSlot("seed", true); }}
                  title="Start fresh — rebuild the default from the template (current default kept as a backup)"
                  style={{ flex: "none", color: "var(--t3)", cursor: "pointer", padding: "0 3px", fontSize: 12 }}>↻</span>
              )}
            </div>
          );
        })}
        {form === null ? (
          <div onClick={() => setForm({ repo: "", ref: "", token: "" })} style={{ padding: "5px 9px", fontSize: 12, color: "var(--accent)", cursor: "pointer", display: "flex", alignItems: "center", gap: 6 }}>
            <Icon name="plus" size={12} /> Attach repo…
          </div>
        ) : (
          <div style={{ padding: "6px 9px", display: "flex", flexDirection: "column", gap: 6 }}>
            <input autoFocus value={form.repo} placeholder="git repo URL" disabled={busy}
              onChange={(e) => setForm({ ...form, repo: e.target.value })}
              onKeyDown={(e) => { if (e.key === "Enter" && form.repo.trim()) void doSwap(form.repo.trim(), form.ref.trim() || undefined, form.token.trim() || undefined); if (e.key === "Escape") setForm(null); }}
              style={{ fontSize: 12, padding: "5px 7px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" }} />
            <input value={form.ref} placeholder="ref (optional, default main)" disabled={busy}
              onChange={(e) => setForm({ ...form, ref: e.target.value })}
              style={{ fontSize: 12, padding: "5px 7px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" }} />
            <input type="password" value={form.token} placeholder="access token (optional, for private repos)" disabled={busy}
              onChange={(e) => setForm({ ...form, token: e.target.value })}
              style={{ fontSize: 12, padding: "5px 7px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" }} />
            <div style={{ display: "flex", gap: 8 }}>
              <button disabled={busy || !form.repo.trim()} onClick={() => void doSwap(form.repo.trim(), form.ref.trim() || undefined, form.token.trim() || undefined)}
                style={{ fontSize: 12, padding: "4px 10px", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 6, cursor: "pointer", opacity: busy || !form.repo.trim() ? 0.5 : 1 }}>{busy ? "Attaching…" : "Attach"}</button>
              <button disabled={busy} onClick={() => setForm(null)} style={{ fontSize: 12, padding: "4px 10px", background: "transparent", color: "var(--t2)", border: "1px solid var(--line)", borderRadius: 6, cursor: "pointer" }}>Cancel</button>
            </div>
          </div>
        )}
        {/* Publish / push-updates form — opened from the ACTIVE row's ↑ action (no list-level trigger:
            publish is an action on the active workspace, not a new list entry). Push-updates mode
            (remoteUrl set) skips repo creation: token only, plain push to the published home. */}
        {pubForm !== null && (() => {
          const pushMode = !!pubForm.remoteUrl;
          const ready = !!pubForm.token.trim() && (pushMode || !!pubForm.name.trim());
          const onKey = (e: React.KeyboardEvent) => { if (e.key === "Enter" && ready) void doPublish(pubForm); if (e.key === "Escape") setPubForm(null); };
          return (
            <div style={{ padding: "6px 9px", display: "flex", flexDirection: "column", gap: 6 }}>
              {pushMode ? (
                <div title={pubForm.remoteUrl} style={{ fontSize: 12, color: "var(--t2)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  push updates → {pubForm.remoteUrl}
                </div>
              ) : (<>
                <input autoFocus value={pubForm.name} placeholder="repo name" disabled={busy}
                  onChange={(e) => setPubForm({ ...pubForm, name: e.target.value })} onKeyDown={onKey}
                  style={{ fontSize: 12, padding: "5px 7px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" }} />
                <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--t2)", cursor: "pointer" }}>
                  <input type="checkbox" checked={pubForm.priv} disabled={busy} onChange={(e) => setPubForm({ ...pubForm, priv: e.target.checked })} />
                  private repo
                </label>
              </>)}
              <input autoFocus={pushMode} type="password" value={pubForm.token} placeholder="GitHub token (repo scope — used once, never stored)" disabled={busy}
                onChange={(e) => setPubForm({ ...pubForm, token: e.target.value })} onKeyDown={onKey}
                style={{ fontSize: 12, padding: "5px 7px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" }} />
              <div style={{ display: "flex", gap: 8 }}>
                <button disabled={busy || !ready} onClick={() => void doPublish(pubForm)}
                  style={{ fontSize: 12, padding: "4px 10px", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 6, cursor: "pointer", opacity: busy || !ready ? 0.5 : 1 }}>
                  {busy ? (pushMode ? "Pushing…" : "Publishing…") : (pushMode ? "Push updates" : "Publish")}
                </button>
                <button disabled={busy} onClick={() => setPubForm(null)} style={{ fontSize: 12, padding: "4px 10px", background: "transparent", color: "var(--t2)", border: "1px solid var(--line)", borderRadius: 6, cursor: "pointer" }}>Cancel</button>
              </div>
            </div>
          );
        })()}
        {published && (
          <div style={{ padding: "4px 9px", fontSize: 12, color: "var(--t2)", display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ color: "var(--green)" }}>✓</span> published →&nbsp;
            <a href={published.repo_url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{published.repo_url}</a>
          </div>
        )}
      </>)}
    </div>
  );
}

// ── Source control (git) — the agent's REAL commits + working changes over /api/workspace/git ──────
const SS_GIT_OPEN = "ws.git.open";
function GitSection() {
  const layout = useService(LayoutServiceId);
  const [open, setOpen] = useState<boolean>(() => readSS(SS_GIT_OPEN) === "1");  // default collapsed
  const [git, setGit] = useState<GitState>({ branch: "", changes: [], commits: [] });
  const [gitError, setGitError] = useState<string | null>(null);  // fail-loud (P18): show git failures, never crash/blank
  useEffect(() => {
    if (!open) return;  // only poll while expanded
    const load = () => { void readWorkspaceGit().then((g) => { setGit(g); setGitError(null); }).catch((e: unknown) => setGitError(e instanceof Error ? e.message : String(e))); };
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
      {open && gitError && <div role="alert" style={{ padding: "2px 9px", fontSize: 12, color: "var(--live)" }}>⚠ git unavailable — {gitError}</div>}
      {!open || gitError ? null : (!git.branch && git.commits.length === 0) ? (
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
/** The doc header path, clickable per segment: folder segments reveal that folder in the
 *  Files tree; the file segment pins the (possibly preview) tab. */
function PathBreadcrumb({ path }: { path: string }) {
  const layout = useService(LayoutServiceId);
  const parts = path.split("/").filter(Boolean);
  // right-click a segment → the same copy-reference menu as the sidebar rows
  const [menu, setMenu] = useState<{ x: number; y: number; target: string } | null>(null);
  const seg = { cursor: "pointer" } as const;
  const hover = {
    onMouseEnter: (e: MouseEvent<HTMLSpanElement>) => { e.currentTarget.style.color = "var(--t1)"; e.currentTarget.style.textDecoration = "underline"; },
    onMouseLeave: (e: MouseEvent<HTMLSpanElement>) => { e.currentTarget.style.color = ""; e.currentTarget.style.textDecoration = "none"; },
  };
  // any segment surfaces the tree in the LEFT SIDEBAR: un-collapse it, switch to the
  // Knowledge list, expand down to the clicked folder (or the file itself) and flash it.
  const reveal = (target: string) => {
    layout.showLeft();
    layout.setActiveList("files");
    revealInTree(target);
  };
  return (
    <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t3)", display: "flex", flexWrap: "wrap", alignItems: "center" }}>
      {parts.map((name, i) => {
        const isFile = i === parts.length - 1;
        const prefix = parts.slice(0, i + 1).join("/");
        return (
          <span key={prefix} style={{ display: "inline-flex", alignItems: "center" }}>
            {i > 0 && <span style={{ padding: "0 2px", userSelect: "none" }}>/</span>}
            <span {...hover} style={seg}
              title={`Reveal ${prefix}${isFile ? "" : "/"} in the sidebar · right-click to copy a reference`}
              onClick={() => reveal(prefix)}
              onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY, target: prefix }); }}>
              {name}
            </span>
          </span>
        );
      })}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={[
          { id: "copy-reference", label: "Copy reference", detail: `@file:${menu.target}`, onSelect: () => copyText(`@file:${menu.target}`) },
          { id: "copy-path", label: "Copy path", detail: menu.target, onSelect: () => copyText(menu.target) },
        ]} />
      )}
    </div>
  );
}

// ── frontmatter card: the file head is STRUCTURED data — render it as such, not raw strings ──
const pill = (color: string, bg: string): CSSProperties => ({
  display: "inline-flex", alignItems: "center", gap: 5, background: bg, border: `1px solid ${color}`,
  borderRadius: 999, padding: "1px 9px", color, fontSize: 12, fontWeight: 500, lineHeight: 1.6, whiteSpace: "nowrap",
});

/** One frontmatter value, rendered by SHAPE: type → colored entity chip; [a, b] lists → tag
 *  pills; URLs/domains → external links; dates → mono; booleans → check; [[wikilinks]] and
 *  plain text → the existing clickable-wikilink path. */
function FmValue({ k, v }: { k: string; v: string }) {
  const navigate = useContext(DocNavContext);
  if (k === "type") {
    const c = ENTITY_CHIP[v] ?? DEFAULT_ENTITY_CHIP;
    return <span style={pill(c.color, c.bg)}><Icon name={c.icon} size={11} />{v}</span>;
  }
  const list = v.match(/^\[(.*)\]$/);
  if (list) {
    const items = list[1].split(",").map((s) => s.trim().replace(/^["']|["']$/g, "")).filter(Boolean);
    return (
      <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 5 }}>
        {items.map((t) => <span key={t} style={{ background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 999, padding: "1px 9px", color: "var(--t2)", fontSize: 12, lineHeight: 1.6, whiteSpace: "nowrap" }}>{t}</span>)}
      </span>
    );
  }
  if (/^https?:\/\/\S+$/.test(v)) {
    return <a href={v} target="_blank" rel="noreferrer noopener" style={{ color: "var(--blue)", textDecoration: "none" }}
      onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")} onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}>{v.replace(/^https?:\/\//, "").replace(/\/$/, "")} ↗</a>;
  }
  if (/^[a-z0-9-]+(\.[a-z0-9-]+)+$/i.test(v)) {
    return <a href={`https://${v}`} target="_blank" rel="noreferrer noopener" style={{ color: "var(--blue)", textDecoration: "none" }}
      onMouseEnter={(e) => (e.currentTarget.style.textDecoration = "underline")} onMouseLeave={(e) => (e.currentTarget.style.textDecoration = "none")}>{v} ↗</a>;
  }
  if (/^\d{4}-\d{2}-\d{2}/.test(v)) return <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t2)" }}>{v}</span>;
  if (v === "true" || v === "false") return <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: v === "true" ? "var(--green)" : "var(--t3)" }}>{v === "true" ? "✓ true" : "✗ false"}</span>;
  if (k === "id") return <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t2)" }}>{v}</span>;
  if (k === "title") return <span style={{ color: "var(--t1)", fontWeight: 600 }}>{wikilinks(v, navigate)}</span>;
  return <span style={{ color: "var(--t1)" }}>{wikilinks(v, navigate)}</span>;
}

function FrontmatterCard({ fm }: { fm: [string, string][] }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "11px 13px", marginBottom: 14, fontSize: 13, display: "flex", flexDirection: "column", gap: 6 }}>
      {fm.map(([k, v]) => (
        <div key={k} style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
          <span style={{ color: "var(--t3)", width: 96, flex: "none", fontSize: 12 }}>{k}</span>
          <span style={{ minWidth: 0, lineHeight: 1.55 }}><FmValue k={k} v={v} /></span>
        </div>
      ))}
    </div>
  );
}

/** ‹ › nav arrow, Obsidian-style: enabled only when the pane history has somewhere to go. */
function NavArrow({ dir, enabled, onGo }: { dir: -1 | 1; enabled: boolean; onGo: () => void }) {
  return (
    <button aria-label={dir === -1 ? "Back" : "Forward"} title={dir === -1 ? "Back" : "Forward"}
      onClick={onGo} disabled={!enabled}
      style={{ background: "none", border: "none", padding: 3, display: "flex", borderRadius: 6,
        color: enabled ? "var(--t1)" : "var(--t3)", opacity: enabled ? 1 : 0.35,
        cursor: enabled ? "pointer" : "default" }}
      onMouseEnter={(e) => { if (enabled) e.currentTarget.style.background = "var(--panel2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "none"; }}>
      <Icon name="arrowR" size={15} style={dir === -1 ? { transform: "scaleX(-1)" } : undefined} />
    </button>
  );
}

function DocTab({ id, params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const docked = params.path as string;
  // Obsidian-style per-pane history: links inside the doc navigate THIS pane in place
  // (layout.retargetTab keeps the dockview panel's params/title in sync); the ‹ › arrows
  // walk the pane's own back/forward stack.
  const [nav, setNav] = useState<{ stack: string[]; idx: number }>({ stack: [docked], idx: 0 });
  const path = nav.stack[nav.idx];
  // retargeted from OUTSIDE (preview swap, layout restore) → a different doc: reset history
  useEffect(() => { setNav((n) => (n.stack[n.idx] === docked ? n : { stack: [docked], idx: 0 })); }, [docked]);
  const [content, setContent] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    setContent(null);
    scroller.current?.scrollTo(0, 0);
    void readFile(path).then(setContent);
  }, [path]);
  const show = (p: string) => layout.retargetTab(id, docTab(p));
  const navigate: DocNavigate = (detail) => {
    void (async () => {
      const p = detail.path ?? (detail.wikilink ? await resolveWikilink(detail.wikilink) : undefined);
      if (!p || p === path) return;
      setNav((n) => ({ stack: [...n.stack.slice(0, n.idx + 1), p], idx: n.idx + 1 }));
      show(p);
    })();
  };
  const go = (dir: -1 | 1) => {
    const i = nav.idx + dir;
    if (i < 0 || i >= nav.stack.length) return;
    setNav({ ...nav, idx: i });
    show(nav.stack[i]);
  };
  const { fm, body } = parseEntity(content ?? "");
  return (
    <DocNavContext.Provider value={navigate}>
      <div ref={scroller} style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
        <div style={{ maxWidth: 760, margin: "0 auto", padding: "22px 24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 12 }}>
            <span style={{ display: "inline-flex", gap: 0, flex: "none" }}>
              <NavArrow dir={-1} enabled={nav.idx > 0} onGo={() => go(-1)} />
              <NavArrow dir={1} enabled={nav.idx < nav.stack.length - 1} onGo={() => go(1)} />
            </span>
            <div style={{ minWidth: 0, flex: 1 }}><PathBreadcrumb path={path} /></div>
          </div>
          {fm.length > 0 && <FrontmatterCard fm={fm} />}
          <div style={{ fontSize: 14, color: "var(--t1)", lineHeight: 1.6 }}>{content === null ? "loading…" : <MdxDoc>{body}</MdxDoc>}</div>
        </div>
      </div>
    </DocNavContext.Provider>
  );
}

// Agent surface — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings).
if (!meetingsOnly()) {
  registerList({ id: "files", label: "Knowledge", icon: "panel", order: 30, component: FilesList });
  registerTab("doc", DocTab);
}
