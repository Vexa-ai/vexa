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
import { Identity } from "../ui-kit/Identity";
import type { CSSProperties, MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { surface } from "./tokens";
import "./minutes.css";
import { ChevronRight, MoreHorizontal, SquareArrowOutUpRight } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Icon, IconButton, SectionLabel } from "../ui-kit";
import { ContextMenu, type ContextMenuItem } from "../ui-kit/ContextMenu";
import { DeletePageDialog, offerDelete, pageTitle, type PageTarget } from "./pageDelete";
import { WORKSPACE_COMMIT_EVENT } from "../platform";
import type { Page } from "./types";
import { navigateView } from "./roomView";
import { useDesktopItems, useDesktopOn, writeDesktopDrag } from "./desktop/useDesktopItems";
import { useCardItems } from "./desktop/useCardItems";
import {
  MAX_HITS, filterByName, frontPagePath, loadNavTree, loadNavWorkspaces, parseQuery, treeFrom,
  type NavWorkspace, type TreeNode,
} from "./navigatorApi";
import { CallsList, NavSwitch, loadNavView, saveNavView, type NavView } from "./NavCalls";
import { callRows, callsPerWorkspace } from "./callsList";
import { useLiveMeetings } from "../surfaces/liveMeetings";

/** FIXED (founder: a "left-side bar", not a second resizable pane). The pages panel's own drag is
 *  the one width the reader sets here; a rail with its own handle would put two grips on one edge. */
// The workspace row's own controls (the calls count, the README caret) keep their inline look.
const nameS: CSSProperties = { flex: "1 1 0%", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const caret = (open: boolean): CSSProperties => ({ flex: "none", width: 10, transform: open ? "rotate(90deg)" : "none" });
const hoverOn = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.background = surface.raised; };
const hoverOff = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.background = "transparent"; };
export const NAV_W = 236;


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
  /** open on this folder of this workspace — a page's path line asked for it ("" = its root) */
  reveal?: { slug?: string; dir: string; at?: number } | null;
}) {
  const [workspaces, setWorkspaces] = useState<NavWorkspace[] | null>(p.workspaces ?? null);
  const [trees, setTrees] = useState<Record<string, string[]>>(p.trees ?? {});
  const [openWs, setOpenWs] = useState<Set<string>>(() => new Set());
  const [openDirs, setOpenDirs] = useState<Set<string>>(() => new Set());
  const [q, setQ] = useState("");
  // A FILE ROW'S MENU (founder 2026-10-11: *"we probably need a delete for a note"*) — Open in a
  // tab, and Delete… where the reader may edit that workspace (`./pageDelete`).
  const [menu, setMenu] = useState<{ x: number; y: number; ws: NavWorkspace; path: string; canDelete: boolean } | null>(null);
  const [deleting, setDeleting] = useState<PageTarget | null>(null);
  const dialogUp = useRef(false);
  dialogUp.current = !!deleting;
  // FILES | CALLS (founder, 2026-10-11: *"for list of calls let's have a switch files vs calls
  // here"*). Files is the tree, unchanged; Calls is `./NavCalls`. The last choice is remembered per
  // browser, and the filter box filters whichever view is in front.
  const [view, setViewState] = useState<NavView>(() => loadNavView());
  const [callsWs, setCallsWs] = useState<string | null>(null);
  const setView = (v: NavView) => { setViewState(v); saveNavView(v); if (v === "files") setCallsWs(null); };
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

  // A PAGE'S PATH LINE OPENS FILES ON ONE OF ITS FOLDERS: that workspace open, each folder down
  // to it expanded.
  useEffect(() => {
    const r = p.reveal;
    if (!r || !workspaces) return;
    const ws = workspaces.find((w) => (w.slug ?? "") === (r.slug ?? "")) ?? (r.slug ? undefined : workspaces.find((w) => !w.slug));
    if (!ws) return;
    setOpenWs((prev) => new Set(prev).add(ws.key));
    void ensureTree(ws);
    const parts = r.dir.split("/").filter(Boolean);
    setOpenDirs((prev) => {
      const next = new Set(prev);
      parts.forEach((_, i) => next.add(`${ws.key}|${parts.slice(0, i + 1).join("/")}`));
      return next;
    });
  }, [p.reveal, workspaces, ensureTree]);

  // A TURN COMMITTED, AND THE TREE MAY BE ONE PAGE SHORTER (Vexa-ai/vexa#1621). `workspace_delete`
  // and `workspace_move` take a page OUT of a workspace — the first acts this rail has ever had to
  // survive that REMOVE a file rather than add one. Each workspace's list is read exactly once
  // (`fetched`), so without this a removed page keeps its row until the browser is reloaded, and
  // clicking it opens "no page here yet": the reader is shown a file that is gone, in the one
  // surface whose whole job is saying what is there. The document in front already re-reads on this
  // event (`MinutesShell`); this is the same fact reaching the other surface that shows the file.
  //
  // RE-READ IN PLACE rather than dropping the cache: clearing it would blank every expanded
  // workspace until its fetch came back, which is a worse answer than a list one second stale.
  // Only what has actually been read is re-read — a collapsed workspace still costs nothing, and a
  // list handed in by a test is left alone, because a seeded key is one this component never owned.
  useEffect(() => {
    const seeded = new Set(Object.keys(p.trees ?? {}));
    const onCommit = () => {
      for (const ws of workspaces ?? []) {
        if (!fetched.current.has(ws.key) || seeded.has(ws.key)) continue;
        void loadNavTree(ws).then((files) => setTrees((prev) => ({ ...prev, [ws.key]: files })));
      }
    };
    window.addEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
    return () => window.removeEventListener(WORKSPACE_COMMIT_EVENT, onCommit);
  }, [workspaces, p.trees]);

  const parsed = useMemo(() => parseQuery(q), [q]);
  const filtering = parsed.text.length > 0;

  // A filter reaches ACROSS the listed workspaces (27.3), so it needs their file lists — including
  // the ones nobody expanded. Typing is what pays for that walk, and it is paid once.
  useEffect(() => {
    if (view !== "files" || !filtering || !workspaces) return;
    for (const ws of workspaces) void ensureTree(ws);
  }, [view, filtering, workspaces, ensureTree]);

  // The close handler as a REF: the key listener is registered once, and a parent that re-renders
  // (every keystroke in the filter does) must not cost a listener churn.
  const onCloseRef = useRef(p.onClose);
  onCloseRef.current = p.onClose;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { if (!dialogUp.current) onCloseRef.current(); return; }
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
  /** THE NAME OPENS THE FRONT PAGE (founder, 2026-10-11: *"click on workspace should open the
   *  readme"*). The click also expands the row — it never collapses it — so the files are there
   *  under the page that was just put in front. The chevron alone is still the plain toggle. */
  const openFront = (ws: NavWorkspace) => {
    if (!openWs.has(ws.key)) { setOpenWs((prev) => new Set(prev).add(ws.key)); void ensureTree(ws); }
    navigateView(ws.slug, frontPagePath(trees[ws.key]));
  };
  const setWsOpen = (ws: NavWorkspace, open: boolean) => { if (openWs.has(ws.key) !== open) toggleWs(ws); };
  const toggleDir = (key: string) => setOpenDirs((prev) => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  /** THE CLICK. It moves the panel's view slot and mints nothing (decision 28). */
  const go = (ws: NavWorkspace, path: string) => navigateView(ws.slug, path);
  /** THE EXPLICIT KEEP. Middle-click, or the ⧉ — a tab through the panel's own route. */
  const keep = (ws: NavWorkspace, path: string) => p.onOpenTab(pageFor(ws, path));

  // THE TREE ON THE ROW CLASSES (guidelines §4.8): 28px rows, the name ellipsizes, depth is an
  // indent the stylesheet reads from `--depth`; the "open in a tab" action appears on hover and
  // focus and keeps its space; carets and the tab icon are lucide, not text glyphs.
  const desktopOn = useDesktopOn();
  const depthStyle = (depth: number) => ({ "--depth": depth } as CSSProperties);
  // A FILE ROW CAN BE DRAGGED ONTO THE DESKTOP'S Files (#1783) — never within the tree (27.6).
  const fileRow = (ws: NavWorkspace, path: string, label: string, depth: number) => (
    <div key={`${ws.key}|${path}`} className="vx-row2 vx-nav-row">
      <button data-nav-file={`${ws.key}|${path}`} title={ws.slug ? `${ws.slug} › ${path}` : path}
        draggable={desktopOn || undefined}
        onDragStart={desktopOn ? (e) => { e.dataTransfer.effectAllowed = "copy"; writeDesktopDrag(e, { kind: "file", slug: ws.slug, path }); } : undefined}
        onClick={() => go(ws, path)}
        onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); keep(ws, path); } }}
        onContextMenu={(e) => { e.preventDefault(); openMenu(ws, path, e.clientX, e.clientY); }}
        className="vx-row2-main vx-nav-entry" data-file="" style={depthStyle(depth)}>
        <span className="vx-row2-title">{label}</span>
      </button>
      <span className="vx-row2-actions">
        <button data-nav-tab={`${ws.key}|${path}`} aria-label={`Open ${label} in a tab`} title="Open in a tab"
          className="vx-doctab-act" onClick={(e) => { e.stopPropagation(); keep(ws, path); }}>
          <SquareArrowOutUpRight size={12} strokeWidth={1.75} aria-hidden />
        </button>
        <IconButton data-nav-more={`${ws.key}|${path}`} label={`Actions for ${label}`} size="xs" aria-haspopup="menu"
          onClick={(e: ReactMouseEvent<HTMLButtonElement>) => { e.stopPropagation(); const b = e.currentTarget.getBoundingClientRect(); openMenu(ws, path, b.right - 188, b.bottom + 4); }}>
          <MoreHorizontal size={14} strokeWidth={1.75} />
        </IconButton>
      </span>
    </div>
  );
  /** Whether the menu offers Delete is asked before it opens, so no item appears under the pointer. */
  function openMenu(ws: NavWorkspace, path: string, x: number, y: number) {
    void offerDelete(ws.slug).catch(() => false).then((canDelete) => setMenu({ x, y, ws, path, canDelete }));
  }
  const desktopItems = useDesktopItems();
  const cardItems = useCardItems();
  const menuItems = (m: NonNullable<typeof menu>): ContextMenuItem[] => [
    { id: "open-tab", label: "Open in a tab", letter: "t", onSelect: () => keep(m.ws, m.path) },
    // Add to desktop / Remove from desktop (desktop ADR D1, 15e); the desk row has no slug
    ...desktopItems({ kind: "file", slug: m.ws.slug, path: m.path }),
    // Make a card (desktop ADR D2, 15c/15d): a CRM card about this page
    ...cardItems({ kind: "page", slug: m.ws.slug, path: m.path, title: pageTitle(m.path) }),
    ...(m.canDelete ? [{ id: "delete", label: "Delete…", detail: m.path, mono: true, danger: true, letter: "d", onSelect: () => setDeleting({
      path: m.path, slug: m.ws.slug, title: pageTitle(m.path),
      workspace: m.ws.kind === "desk" ? "your personal workspace" : m.ws.name,
    }) }] : []),
  ];

  const nodes = (ws: NavWorkspace, list: TreeNode[], depth: number): ReactNode[] =>
    list.flatMap((n) => {
      if (!n.dir) return [fileRow(ws, n.path, n.name, depth)];
      const key = `${ws.key}|${n.path}`;
      const open = openDirs.has(key);
      return [
        <button key={`d:${key}`} data-nav-dir={key} aria-expanded={open} onClick={() => toggleDir(key)}
          className="vx-row2-main vx-nav-entry" style={depthStyle(depth)}>
          <ChevronRight size={12} strokeWidth={1.75} className="vx-nav-caret" data-open={open ? "" : undefined} aria-hidden />
          <span className="vx-row2-title">{n.name}</span>
        </button>,
        ...(open ? nodes(ws, n.children, depth + 1) : []),
      ];
    });

  const result = useMemo(
    () => (view === "files" && filtering && workspaces ? filterByName(q, workspaces, trees) : null),
    [view, filtering, workspaces, trees, q],
  );

  // The badge on a workspace row counts the calls bound to it. Read from the same store the Calls
  // view lists — nothing is copied onto the workspace.
  const meetings = useLiveMeetings();
  const wsCalls = useMemo(
    () => callsPerWorkspace(callRows(meetings), workspaces ?? []),
    [meetings, workspaces],
  );
  /** A call row's workspace chip: show that workspace in Files, expanded. */
  const revealWorkspace = (key: string) => {
    const ws = workspaces?.find((w) => w.key === key);
    if (!ws) return;
    setView("files"); setQ("");
    setOpenWs((prev) => new Set(prev).add(ws.key));
    void ensureTree(ws);
    requestAnimationFrame(() => [...document.querySelectorAll<HTMLElement>("[data-nav-ws]")]
      .find((el) => el.getAttribute("data-nav-ws") === key)?.scrollIntoView?.({ block: "nearest" }));
  };

  return (
    <div data-navigator className="vx-nav">
      <div className="vx-nav-switch-wrap" style={{ flex: "none" }}>
        <NavSwitch value={view} onChange={setView} />
      </div>
      <div className="vx-nav-search">
        <Icon name="search" size={12} />
        <input ref={box} data-nav-filter value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); if (q) setQ(""); else p.onClose(); } }}
          placeholder={view === "calls" ? "Filter calls  (> transcripts)" : "Filter files  (> content)"}
          aria-label={view === "calls" ? "Filter calls" : "Filter files"} className="vx-input vx-nav-filter" />
      </div>
      {/* CONTENT SEARCH IS NOT ON THIS BUILD. There is no workspace-content route to call, so the
          `>` is honoured as far as it can be — the names are still matched — and the reader is TOLD,
          rather than shown an empty result that reads as "no such text anywhere". */}
      {view === "files" && parsed.content && (
        <div data-nav-note className="vx-nav-note">
          Content search is not available yet — matching names.
        </div>
      )}
      <div className="vx-nav-list">
        {view === "calls"
          ? <CallsList query={parsed} workspaces={workspaces ?? []} workspaceFilter={callsWs}
              onClearWorkspaceFilter={() => setCallsWs(null)} onRevealWorkspace={revealWorkspace} onOpenTab={p.onOpenTab} />
          : workspaces === null
          ? null   /* nothing until the state is known — never a placeholder row (decision 23.1) */
          : result
            ? (result.groups.length === 0
                ? <div data-nav-empty className="vx-nav-note">No file matches that.</div>
                : <>
                    {result.groups.map((g) => {
                      const ws = workspaces.find((w) => w.key === g.key);
                      if (!ws) return null;
                      return (
                        <div key={g.key} data-nav-group={g.key} className="vx-nav-group">
                          <SectionLabel as="div">{g.name}</SectionLabel>
                          {g.paths.map((path) => fileRow(ws, path, path, 0))}
                        </div>
                      );
                    })}
                    {result.truncated && (
                      <div data-nav-truncated className="vx-nav-note">
                        First {MAX_HITS} matches — keep typing.
                      </div>
                    )}
                  </>)
            : workspaces.map((ws) => {
                const open = openWs.has(ws.key);
                const calls = wsCalls.get(ws.key) ?? 0;
                return (
                  <div key={ws.key} data-nav-ws-item={ws.key} style={{ position: "relative" }}>
                    {/* NAME → front page + expand; CHEVRON → toggle only. Keyboard: Enter opens
                        the front page, → / ← expand / collapse. */}
                    <button data-nav-ws={ws.key} aria-expanded={open} title={ws.name}
                      onClick={(e) => {
                        if ((e.target as HTMLElement).closest?.("[data-nav-ws-caret]")) toggleWs(ws);
                        else openFront(ws);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { e.preventDefault(); openFront(ws); }
                        else if (e.key === "ArrowRight") { e.preventDefault(); setWsOpen(ws, true); }
                        else if (e.key === "ArrowLeft") { e.preventDefault(); setWsOpen(ws, false); }
                      }}
                      className="vx-row2-main vx-nav-entry vx-nav-ws" style={depthStyle(0)}
                      onMouseEnter={hoverOn} onMouseLeave={hoverOff}>
                      <span data-nav-ws-caret={ws.key} title={open ? "Collapse" : "Expand"}
                        className="vx-nav-ws-caret" style={caret(open)} aria-hidden>▶</span>
                      <Identity kind="workspace" id={ws.slug ?? "personal"} slug={ws.slug ?? "personal"} name={ws.name} size={16} className="vx-id-nav" />
                      <span className="vx-nav-ws-name" style={nameS}>{ws.name}</span>
                    </button>
                    {/* THE MEETING ↔ WORKSPACE LINK, seen from the folder: the calls bound to this
                        workspace (the meeting's own `workspace_id`), one click to list them. */}
                    {calls > 0 && (
                      <button type="button" data-nav-ws-calls={ws.key} title={`${calls} call${calls === 1 ? "" : "s"} in ${ws.name}`}
                        aria-label={`Show ${calls} call${calls === 1 ? "" : "s"} in ${ws.name}`}
                        onClick={() => { setCallsWs(ws.key); setViewState("calls"); saveNavView("calls"); }}
                        className="vx-nav-ws-calls" style={{ position: "absolute", right: 4, top: 4, display: "flex", alignItems: "center", gap: 3 }}>
                        <Icon name="mic" size={10} />{calls}
                      </button>
                    )}
                    {open && (
                      trees[ws.key]
                        ? nodes(ws, treeFrom(trees[ws.key], ws.slug), 1)
                        : <div className="vx-nav-note">…</div>
                    )}
                  </div>
                );
              })}
      </div>
      {menu && <ContextMenu x={menu.x} y={menu.y} label={`Actions for ${pageTitle(menu.path)}`} items={menuItems(menu)} onClose={() => setMenu(null)} />}
      {deleting && <DeletePageDialog target={deleting} onClose={() => setDeleting(null)} />}
    </div>
  );
}
