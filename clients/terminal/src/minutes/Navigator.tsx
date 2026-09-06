"use client";
/** THE NAVIGATOR — the right panel's own left rail (PRD decision 27).
 *
 *  Founder: *"we need this workspaces view — default hidden left-side bar of the right sidebar,
 *  that will have collapsed workspaces and filter search."* A file tree beside the open file, and
 *  nothing more: open, filter, read. No rename, no delete, no drag — decision 27.6, "not an IDE".
 *
 *  DEFAULT HIDDEN, remembered per browser (27.4). It is a way to go looking, not a thing to look at:
 *  the panel still opens on the desk README (decision 26.4) and the rail stays folded until asked.
 *
 *  A CLICK NAVIGATES, IT DOES NOT COLLECT (decision 28). Walking a tree is browsing, and browsing
 *  that mints a tab per curiosity leaves a strip nobody asked for; so a row moves the panel's single
 *  view slot, and a TAB is minted only when the reader says so — middle-click, or the ⧉ on the row.
 *  Either way the write goes through `minutes/roomView`'s seam and the panel's own `onOpen`, never
 *  into state this component keeps: what is in front belongs to the chat record.
 *
 *  WHAT IS NOT LISTED: machinery and dotfiles, from the one list in `./machinery` that the panel's
 *  folder listing reads too — and any workspace this reader cannot open. Founder ruling 2026-09-06
 *  (Vexa-ai/vexa#1585): *"no point of showing workspaces not available to you"*. There is no greyed
 *  row here any more, so every row expands and every row wears a name; which workspaces qualify is
 *  decided once, in `./navigatorApi`. */
import type { CSSProperties, ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../ui-kit";
import type { Page } from "./types";
import { navigateView } from "./roomView";
import {
  MAX_HITS, filterByName, loadNavTree, loadNavWorkspaces, parseQuery, treeFrom,
  type NavWorkspace, type TreeNode,
} from "./navigatorApi";
import { surface, type as ty } from "./tokens";

/** FIXED (founder: a "left-side bar", not a second resizable pane). The pages panel's own drag is
 *  the one width the reader sets here; a rail with its own handle would put two grips on one edge. */
export const NAV_W = 236;

const wsRow: CSSProperties = {
  ...ty.body, display: "flex", alignItems: "center", gap: 6, width: "100%", textAlign: "left",
  padding: "4px 6px", borderRadius: 6, border: "none", background: "transparent",
  color: "var(--t1)", cursor: "pointer",
  fontWeight: 600, fontSize: 12, minWidth: 0,
};
const entry: CSSProperties = {
  display: "flex", alignItems: "center", width: "100%", textAlign: "left", background: "transparent",
  border: "none", padding: "3px 6px", borderRadius: 6, cursor: "pointer",
  fontFamily: "var(--mono)", fontSize: 11.5, minWidth: 0,
};
const nameS: CSSProperties = { flex: "1 1 0%", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const caret = (open: boolean): CSSProperties => ({ flex: "none", width: 10, color: "var(--t3)", fontFamily: "var(--sans)", fontSize: 9, lineHeight: 1, transform: open ? "rotate(90deg)" : "none", transition: "transform .1s" });
const hoverOn = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.background = surface.raised; };
const hoverOff = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.background = "transparent"; };

const named = (path: string) => (path.split("/").pop() ?? path).replace(/\.md$/i, "");
const pageFor = (ws: NavWorkspace, path: string): Page => ({ path, slug: ws.slug, label: named(path) });

export function Navigator(p: {
  /** the panel's own tab route — the chat record's `artifacts[]`. Used ONLY on an explicit
   *  open-in-tab, never on a plain click (decision 28). */
  onOpenTab: (pg: Page) => void;
  onClose: () => void;
  /** test seam: the workspaces, already resolved. Absent → fetched on mount. */
  workspaces?: NavWorkspace[];
  /** test seam: file lists by workspace key. A key present here is never fetched. */
  trees?: Record<string, string[]>;
}) {
  const [workspaces, setWorkspaces] = useState<NavWorkspace[] | null>(p.workspaces ?? null);
  const [trees, setTrees] = useState<Record<string, string[]>>(p.trees ?? {});
  const [openWs, setOpenWs] = useState<Set<string>>(() => new Set());
  const [openDirs, setOpenDirs] = useState<Set<string>>(() => new Set());
  const [q, setQ] = useState("");
  const box = useRef<HTMLInputElement>(null);
  const seeded = useRef(!!p.workspaces);
  /** workspace keys whose file list has been read (or handed in) — never read back from state */
  const fetched = useRef<Set<string>>(new Set(Object.keys(p.trees ?? {})));

  useEffect(() => {
    if (seeded.current) return;
    let live = true;
    // A failure here is an EMPTY rail, never a missing one: `loadNavWorkspaces` already degrades to
    // the desk alone, so the only way to land on [] is a browser with no session at all.
    void loadNavWorkspaces().then((ws) => { if (live) setWorkspaces(ws); }).catch(() => { if (live) setWorkspaces([]); });
    return () => { live = false; };
  }, []);

  /** Read a workspace's files ONCE, when something needs them (an expand, or a filter that must
   *  search everywhere). Lazy per decision 27.2's spirit — the tree endpoint walks a whole
   *  workspace, and a rail that opens should not walk five. */
  const ensureTree = useCallback(async (ws: NavWorkspace) => {
    // "have we read this one?" is asked OUTSIDE React state on purpose: a state updater is not a
    // place to read from — it may run later, or twice — and a wrong answer here is either a
    // duplicated walk of a whole workspace or a tree that never arrives.
    if (fetched.current.has(ws.key)) return;
    fetched.current.add(ws.key);
    const files = await loadNavTree(ws);
    setTrees((prev) => ({ ...prev, [ws.key]: files }));
  }, []);

  const parsed = useMemo(() => parseQuery(q), [q]);
  const filtering = parsed.text.length > 0;

  // A filter reaches ACROSS the listed workspaces (27.3), so it needs their file lists — including
  // the ones nobody expanded. Typing is what pays for that walk, and it is paid once.
  useEffect(() => {
    if (!filtering || !workspaces) return;
    for (const ws of workspaces) void ensureTree(ws);
  }, [filtering, workspaces, ensureTree]);

  // The close handler as a REF: the key listener is registered once, and a parent that re-renders
  // (every keystroke in the filter does) must not cost a listener churn.
  const onCloseRef = useRef(p.onClose);
  onCloseRef.current = p.onClose;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onCloseRef.current(); return; }
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      const tag = t?.tagName;
      // `/` is a character before it is a shortcut — never steal it out of a composer.
      if (tag === "INPUT" || tag === "TEXTAREA" || t?.isContentEditable) return;
      e.preventDefault();
      box.current?.focus();
      box.current?.select();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggleWs = (ws: NavWorkspace) => {
    const next = new Set(openWs);
    if (next.has(ws.key)) next.delete(ws.key); else { next.add(ws.key); void ensureTree(ws); }
    setOpenWs(next);
  };
  const toggleDir = (key: string) => setOpenDirs((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  /** THE CLICK. It moves the panel's view slot and mints nothing (decision 28). */
  const go = (ws: NavWorkspace, path: string) => navigateView(ws.slug, path);
  /** THE EXPLICIT KEEP. Middle-click, or the ⧉ — a tab through the panel's own route. */
  const keep = (ws: NavWorkspace, path: string) => p.onOpenTab(pageFor(ws, path));

  const fileRow = (ws: NavWorkspace, path: string, label: string, depth: number) => (
    <div key={`${ws.key}|${path}`} style={{ position: "relative", display: "flex" }}
      onMouseEnter={(e) => { const b = e.currentTarget.querySelector("[data-nav-tab]") as HTMLElement | null; if (b) b.style.opacity = "1"; }}
      onMouseLeave={(e) => { const b = e.currentTarget.querySelector("[data-nav-tab]") as HTMLElement | null; if (b) b.style.opacity = "0"; }}>
      <button data-nav-file={`${ws.key}|${path}`} title={ws.slug ? `${ws.slug} › ${path}` : path}
        onClick={() => go(ws, path)}
        onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); keep(ws, path); } }}
        style={{ ...entry, color: "var(--t1)", paddingLeft: 6 + depth * 11, paddingRight: 22 }}
        onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
        <span style={nameS}>{label}</span>
      </button>
      <button data-nav-tab={`${ws.key}|${path}`} aria-label={`Open ${label} in a tab`} title="Open in a tab"
        onClick={(e) => { e.stopPropagation(); keep(ws, path); }}
        style={{ position: "absolute", right: 2, top: "50%", transform: "translateY(-50%)", opacity: 0, transition: "opacity .12s", background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 11, lineHeight: 1, padding: "2px 4px", fontFamily: "var(--sans)" }}>⧉</button>
    </div>
  );

  const nodes = (ws: NavWorkspace, list: TreeNode[], depth: number): ReactNode[] =>
    list.flatMap((n) => {
      if (!n.dir) return [fileRow(ws, n.path, n.name, depth)];
      const key = `${ws.key}|${n.path}`;
      const open = openDirs.has(key);
      return [
        <button key={`d:${key}`} data-nav-dir={key} onClick={() => toggleDir(key)}
          style={{ ...entry, color: "var(--t2)", paddingLeft: 6 + depth * 11 }}
          onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
          <span style={caret(open)} aria-hidden>▶</span>
          <span style={{ ...nameS, paddingLeft: 4 }}>{n.name}</span>
        </button>,
        ...(open ? nodes(ws, n.children, depth + 1) : []),
      ];
    });

  const result = useMemo(
    () => (filtering && workspaces ? filterByName(q, workspaces, trees) : null),
    [filtering, workspaces, trees, q],
  );

  return (
    <div data-navigator style={{
      flex: "none", width: NAV_W, display: "flex", flexDirection: "column", minHeight: 0,
      background: surface.rail, borderRight: "1px solid var(--line)",
    }}>
      <div style={{ flex: "none", display: "flex", alignItems: "center", gap: 6, padding: "8px 8px 6px" }}>
        <Icon name="search" size={12} style={{ flex: "none", color: "var(--t3)" }} />
        <input ref={box} data-nav-filter value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); if (q) setQ(""); else p.onClose(); } }}
          placeholder="Filter files  (> content)" aria-label="Filter files"
          style={{
            ...ty.body, flex: "1 1 0%", minWidth: 0, fontSize: 12, background: surface.raised,
            border: "1px solid var(--line)", borderRadius: 6, padding: "3px 7px", color: "var(--t1)", outline: "none",
          }} />
      </div>
      {/* CONTENT SEARCH IS NOT ON THIS BUILD. There is no workspace-content route to call, so the
          `>` is honoured as far as it can be — the names are still matched — and the reader is TOLD,
          rather than shown an empty result that reads as "no such text anywhere". */}
      {parsed.content && (
        <div data-nav-note style={{ ...ty.meta, padding: "0 10px 6px", lineHeight: 1.45 }}>
          Content search is not available yet — matching names.
        </div>
      )}
      <div style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "0 6px 12px" }}>
        {workspaces === null
          ? null   /* nothing until the state is known — never a placeholder row (decision 23.1) */
          : result
            ? (result.groups.length === 0
                ? <div data-nav-empty style={{ ...ty.meta, padding: "6px 8px" }}>No file matches that.</div>
                : <>
                    {result.groups.map((g) => {
                      const ws = workspaces.find((w) => w.key === g.key);
                      if (!ws) return null;
                      return (
                        <div key={g.key} data-nav-group={g.key} style={{ paddingBottom: 4 }}>
                          <div style={{ ...ty.lens, fontSize: 9.5, padding: "8px 6px 3px" }}>{g.name}</div>
                          {g.paths.map((path) => fileRow(ws, path, path, 0))}
                        </div>
                      );
                    })}
                    {result.truncated && (
                      <div data-nav-truncated style={{ ...ty.meta, padding: "6px 8px" }}>
                        First {MAX_HITS} matches — keep typing.
                      </div>
                    )}
                  </>)
            : workspaces.map((ws) => {
                const open = openWs.has(ws.key);
                return (
                  <div key={ws.key}>
                    <button data-nav-ws={ws.key} aria-expanded={open} title={ws.name}
                      onClick={() => toggleWs(ws)} style={wsRow}
                      onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
                      <span style={caret(open)} aria-hidden>▶</span>
                      <span style={{ ...nameS, paddingLeft: 2 }}>{ws.name}</span>
                    </button>
                    {open && (
                      trees[ws.key]
                        ? nodes(ws, treeFrom(trees[ws.key], ws.slug), 1)
                        : <div style={{ ...ty.meta, padding: "3px 6px 3px 17px" }}>…</div>
                    )}
                  </div>
                );
              })}
      </div>
    </div>
  );
}
