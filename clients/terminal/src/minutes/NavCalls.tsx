"use client";
/** THE NAVIGATOR'S CALLS VIEW, and the Files | Calls switch above it.
 *
 *  Founder, 2026-10-11: *"we probably want to have a list of calls here that will open transcripts"*,
 *  then *"for list of calls let's have a switch files vs calls here"*. So the rail has two views
 *  behind one switch: Files is the tree exactly as it was; Calls is the reader's calls, newest first,
 *  grouped by date, and a click opens the call's transcript in the panel's existing meeting canvas.
 *
 *  NO SECOND VIEWER, NO SECOND READ PATH. Rows come from the meetings store (`GET /api/meetings`,
 *  owned + shared, as meeting-api authorises the meeting list); a click is an `open-page` event for
 *  `meeting:<row id>`, the same route the agent's `open_page` takes, so it lands in the same canvas,
 *  the same history and the same tab strip. Decisions live in `./callsList`, which is pure. */
import type { CSSProperties, KeyboardEvent } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Icon } from "../ui-kit";
import { ContextMenu } from "../ui-kit/ContextMenu";
import { OPEN_PAGE_EVENT } from "../platform";
import { useLiveMeetings, useLiveMeetingsLoaded } from "../surfaces/liveMeetings";
import type { Page } from "./types";
import {
  CALLS_PAGE, MONTH_GROUPING_AT, callRows, callWhen, callWorkspace, contentMatches, groupCalls,
  matchCalls, searchTranscripts, type CallRow, type ContentMatch,
} from "./callsList";
import type { NavWorkspace, ParsedQuery } from "./navigatorApi";
import { canRenameCall } from "./callRename";
import { CallNameEditor } from "./CallName";
import { surface, type as ty } from "./tokens";

// ── the switch ───────────────────────────────────────────────────────────────────────────────────

export type NavView = "files" | "calls";
/** Per-browser memory of the reader's last view — a convenience, so absent or throwing storage
 *  simply means Files, the view the rail always opened on. */
export const NAV_VIEW_KEY = "vexa.minutes.navigator.view";
export function loadNavView(): NavView {
  try { return localStorage.getItem(NAV_VIEW_KEY) === "calls" ? "calls" : "files"; } catch { return "files"; }
}
export function saveNavView(v: NavView): void {
  try { localStorage.setItem(NAV_VIEW_KEY, v); } catch { /* locked-down storage */ }
}

const VIEWS: { key: NavView; label: string }[] = [{ key: "files", label: "Files" }, { key: "calls", label: "Calls" }];

/** A two-way segmented switch with the WAI-ARIA tabs pattern — the same API and keyboard model as
 *  the design catalogue's `Tabs` primitive (roving tabindex, ←/→ and Home/End), drawn on this
 *  shell's tokens until that primitive is on this branch. */
export function NavSwitch(p: { value: NavView; onChange: (v: NavView) => void }) {
  const list = useRef<HTMLDivElement>(null);
  const onKey = (e: KeyboardEvent) => {
    const i = VIEWS.findIndex((v) => v.key === p.value);
    const to = e.key === "ArrowRight" ? (i + 1) % VIEWS.length : e.key === "ArrowLeft" ? (i - 1 + VIEWS.length) % VIEWS.length
      : e.key === "Home" ? 0 : e.key === "End" ? VIEWS.length - 1 : -1;
    if (to < 0) return;
    e.preventDefault();
    p.onChange(VIEWS[to].key);
    list.current?.querySelectorAll<HTMLElement>("[role=tab]")[to]?.focus();
  };
  return (
    <div ref={list} role="tablist" aria-label="Files or calls" data-nav-switch onKeyDown={onKey}
      style={{ display: "flex", gap: 2, padding: 2, borderRadius: 7, background: surface.raised, border: "1px solid var(--line)" }}>
      {VIEWS.map((v) => {
        const on = v.key === p.value;
        return (
          <button key={v.key} type="button" role="tab" aria-selected={on} tabIndex={on ? 0 : -1}
            data-nav-view={v.key} onClick={() => p.onChange(v.key)}
            style={{
              ...ty.chip, flex: "1 1 0%", minWidth: 0, minHeight: 24, padding: "2px 8px", borderRadius: 5, border: "none",
              cursor: "pointer", color: on ? "var(--t1)" : "var(--t3)",
              background: on ? surface.raisedHi : "transparent",
              fontWeight: on ? 600 : 500,
            }}>{v.label}</button>
        );
      })}
    </div>
  );
}

// ── the list ─────────────────────────────────────────────────────────────────────────────────────

const rowBtn: CSSProperties = {
  ...ty.body, display: "flex", alignItems: "center", gap: 6, width: "100%", minHeight: 28, textAlign: "left",
  padding: "3px 6px", borderRadius: 6, border: "none", background: "transparent", color: "var(--t1)",
  cursor: "pointer", minWidth: 0, fontSize: 12.5,
};
const nameS: CSSProperties = { flex: "1 1 0%", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const actBtn: CSSProperties = {
  background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 11, lineHeight: 1,
  padding: "2px 3px", fontFamily: "var(--sans)", display: "inline-flex", alignItems: "center",
};
/** THE ROW'S ACTIONS TAKE THE TIME'S PLACE. At rest a row shows its time at the right edge; under
 *  the pointer or keyboard focus the time steps aside (kept in layout, so nothing shifts) and the
 *  row's controls — workspace, open in a tab, more — stand where it was, on the hover colour. They
 *  used to be drawn OVER the time, and "10:09 PM" read through the ⧉ (founder, 2026-10-11). */
const reveal = (e: { currentTarget: HTMLElement }, on: boolean) => {
  const row = e.currentTarget;
  row.style.background = on ? surface.raised : "transparent";
  const acts = row.querySelector("[data-call-acts]") as HTMLElement | null;
  if (acts) acts.style.opacity = on ? "1" : "0";
  const when = row.querySelector("[data-call-when]") as HTMLElement | null;
  if (when) when.style.visibility = on ? "hidden" : "visible";
};

/** Open a call's transcript: the shell's own `open-page` route, `meeting:<row id>` form. */
export function openCall(row: Pick<CallRow, "id" | "label">): void {
  window.dispatchEvent(new CustomEvent(OPEN_PAGE_EVENT, { detail: { path: `meeting:${row.id}`, label: row.label } }));
}
const callPage = (row: CallRow): Page => ({ kind: "meeting", path: row.id, label: row.label });

export function CallsList(p: {
  query: ParsedQuery;
  workspaces: readonly NavWorkspace[];
  /** show only the calls bound to this workspace key (from a workspace row's badge) */
  workspaceFilter?: string | null;
  onClearWorkspaceFilter: () => void;
  /** the call row's workspace chip: show that workspace in the Files view */
  onRevealWorkspace: (key: string) => void;
  onOpenTab: (pg: Page) => void;
}) {
  const meetings = useLiveMeetings();
  const loaded = useLiveMeetingsLoaded();
  const [shown, setShown] = useState(CALLS_PAGE);
  const all = useMemo(() => callRows(meetings), [meetings]);
  const scoped = useMemo(() => {
    if (!p.workspaceFilter) return all;
    return all.filter((r) => callWorkspace(r, p.workspaces)?.key === p.workspaceFilter);
  }, [all, p.workspaceFilter, p.workspaces]);

  // `>` — content search over the reader's OWN transcripts, debounced, hits kept to the list.
  const [content, setContent] = useState<{ q: string; hits: ContentMatch[] | null } | null>(null);
  const contentQ = p.query.content ? p.query.text : "";
  useEffect(() => {
    if (!contentQ) { setContent(null); return; }
    let live = true;
    const t = setTimeout(() => {
      void searchTranscripts(contentQ).then((hits) => {
        if (live) setContent({ q: contentQ, hits: hits === null ? null : contentMatches(hits, scoped) });
      });
    }, 250);
    return () => { live = false; clearTimeout(t); };
  }, [contentQ, scoped]);

  const filtering = p.query.text.length > 0;
  const titleHits = useMemo(() => (filtering && !p.query.content ? matchCalls(p.query.text, scoped) : null), [filtering, p.query, scoped]);

  const wsName = p.workspaceFilter ? p.workspaces.find((w) => w.key === p.workspaceFilter)?.name : null;

  // Rename: one row at a time, from the row's ⋯ menu or F2 on the focused row. The editor replaces
  // the row in place; when it closes, focus returns to the row so the keyboard is where it was.
  const byId = useMemo(() => new Map(meetings.map((m) => [m.id, m] as const)), [meetings]);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const focusRow = (id: string) => requestAnimationFrame(() =>
    root.current?.querySelector<HTMLElement>(`[data-call=${JSON.stringify(id)}]`)?.focus());
  const startRename = (id: string) => { setMenu(null); setRenaming(id); };
  const endRename = (id: string) => { setRenaming(null); focusRow(id); };
  const closeMenu = (id: string, refocus: boolean) => {
    setMenu(null);
    const row = root.current?.querySelector<HTMLElement>(`[data-call-row=${JSON.stringify(id)}]`);
    if (row && !row.matches(":hover")) reveal({ currentTarget: row }, false);
    if (refocus) root.current?.querySelector<HTMLElement>(`[data-call-more=${JSON.stringify(id)}]`)?.focus();
  };

  const row = (r: CallRow, byMonth: boolean, snippet?: string) => {
    const ws = callWorkspace(r, p.workspaces);
    const when = callWhen(r, byMonth);
    const m = byId.get(r.id);
    const mayRename = canRenameCall(m);
    const notStarted = !r.live && !r.started;
    if (renaming === r.id && m && mayRename) {
      return (
        <div key={r.id} data-call-row={r.id} style={{ display: "flex", alignItems: "center", padding: "2px 4px", minHeight: 28 }}>
          <CallNameEditor compact meeting={m} label={r.label} onDone={() => endRename(r.id)} />
        </div>
      );
    }
    return (
      <div key={r.id} data-call-row={r.id} style={{ position: "relative", display: "flex", alignItems: "center", borderRadius: 6 }}
        onMouseEnter={(e) => reveal(e, true)} onMouseLeave={(e) => { if (menu?.id !== r.id) reveal(e, false); }}
        onFocus={(e) => reveal(e, true)} onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node) && menu?.id !== r.id) reveal(e, false); }}>
        <button type="button" data-call={r.id} title={notStarted ? `${r.label} — requested, never started` : r.label}
          aria-label={`${r.label}${r.live ? ", live" : when ? `, ${when}` : ""}${notStarted ? ", never started" : ""}${r.shared ? ", shared with you" : ""}`}
          aria-keyshortcuts={mayRename ? "F2" : undefined}
          onClick={() => openCall(r)}
          onKeyDown={(e) => { if (e.key === "F2" && mayRename) { e.preventDefault(); startRename(r.id); } }}
          onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); p.onOpenTab(callPage(r)); } }}
          style={{ ...rowBtn, flexDirection: snippet ? "column" : "row", alignItems: snippet ? "stretch" : "center" }}>
          <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
            {r.live
              ? <span data-call-live aria-hidden style={{ flex: "none", width: 6, height: 6, borderRadius: "50%", background: "var(--red, #e5484d)" }} />
              : <Icon name="mic" size={11} style={{ color: "var(--t3)" }} />}
            <span style={nameS}>{r.label}</span>
            {r.shared && <span aria-hidden title="Shared with you" style={{ ...ty.meta, fontSize: 10, flex: "none" }}>shared</span>}
            {when && <span data-call-when data-call-not-started={notStarted ? "" : undefined}
              style={{ ...ty.meta, flex: "none", color: r.live ? "var(--red, #e5484d)" : "var(--t3)", fontStyle: notStarted ? "italic" : undefined }}>{when}</span>}
          </span>
          {snippet && <span data-call-snippet style={{ ...ty.meta, ...nameS, paddingLeft: 17 }}>{snippet}</span>}
        </button>
        <span data-call-acts style={{ position: "absolute", right: 2, top: 3, display: "flex", alignItems: "center", gap: 1, opacity: 0, transition: "opacity .12s", background: surface.raised, borderRadius: 5, paddingLeft: 2 }}>
          {ws && (
            <button type="button" data-call-ws={ws.key} title={`In workspace ${ws.name}`}
              aria-label={`Show workspace ${ws.name}`} onClick={() => p.onRevealWorkspace(ws.key)} style={{ ...actBtn, lineHeight: 0 }}>
              <Icon name="folder" size={11} />
            </button>
          )}
          <button type="button" data-call-tab={r.id} aria-label={`Open ${r.label} in a tab`} title="Open in a tab"
            onClick={() => p.onOpenTab(callPage(r))} style={actBtn}>⧉</button>
          {mayRename && (
            <button type="button" data-call-more={r.id} aria-label={`Actions for ${r.label}`} title="Rename"
              aria-haspopup="menu" aria-expanded={menu?.id === r.id}
              onPointerDown={(e) => e.stopPropagation()}
              onClick={(e) => {
                if (menu?.id === r.id) { setMenu(null); return; }
                const b = e.currentTarget.getBoundingClientRect();
                setMenu({ id: r.id, x: b.right - 188, y: b.bottom + 4 });
              }} style={actBtn}>⋯</button>
          )}
        </span>
        {menu?.id === r.id && (
          // the chats rail's own menu primitive, so a call's menu looks and behaves like a chat's
          <ContextMenu x={menu.x} y={menu.y} onClose={() => closeMenu(r.id, true)}
            items={[
              { id: "rename", label: "Rename", detail: "F2", onSelect: () => startRename(r.id) },
              { id: "tab", label: "Open in a tab", onSelect: () => p.onOpenTab(callPage(r)) },
            ]} />
        )}
      </div>
    );
  };

  const head = (label: string, key: string) => (
    <div key={`h:${key}`} role="presentation" style={{ ...ty.lens, fontSize: 9.5, padding: "8px 6px 3px" }}>{label}</div>
  );
  const note = (text: string, attr: string) => (
    <div {...{ [attr]: "" }} style={{ ...ty.meta, padding: "6px 8px", lineHeight: 1.45 }}>{text}</div>
  );

  let body;
  if (!loaded) body = null;   // nothing until the list has answered — never a placeholder row
  else if (p.query.content && filtering) {
    body = !content || content.q !== contentQ
      ? note("Searching transcripts…", "data-calls-searching")
      : content.hits === null
        ? note("Transcript search did not answer — try again.", "data-calls-error")
        : content.hits.length === 0
          ? note("No transcript says that.", "data-calls-empty")
          : content.hits.map((h) => row(h.row, true, h.snippet));
  } else if (titleHits) {
    body = titleHits.length === 0 ? note("No call matches that.", "data-calls-empty") : titleHits.map((r) => row(r, true));
  } else if (scoped.length === 0) {
    body = note(p.workspaceFilter ? "No calls in this workspace." : "No calls yet — a meeting's transcript appears here once a bot has joined it.", "data-calls-empty");
  } else {
    const page = scoped.slice(0, shown);
    const byMonth = scoped.length > MONTH_GROUPING_AT;
    body = (
      <>
        {groupCalls(page, new Date(), scoped.length).map((g) => (
          <div key={g.key} data-calls-group={g.key} role="group" aria-label={g.label}>
            {head(g.label, g.key)}
            {g.rows.map((r) => row(r, byMonth))}
          </div>
        ))}
        {scoped.length > shown && (
          <button type="button" data-calls-more onClick={() => setShown((n) => n + CALLS_PAGE)}
            style={{ ...rowBtn, ...ty.meta, justifyContent: "center", marginTop: 4 }}>
            Show {Math.min(CALLS_PAGE, scoped.length - shown)} more of {scoped.length - shown}
          </button>
        )}
      </>
    );
  }

  return (
    <div data-calls ref={root}>
      {p.query.content && filtering && (
        <div data-calls-note style={{ ...ty.meta, padding: "0 4px 6px", lineHeight: 1.45 }}>
          Searching the transcripts of your own calls; shared calls match by title only.
        </div>
      )}
      {wsName && (
        <div data-calls-ws-filter style={{ ...ty.meta, display: "flex", alignItems: "center", gap: 6, padding: "2px 6px 4px" }}>
          <Icon name="folder" size={11} />
          <span style={nameS}>{wsName}</span>
          <button type="button" aria-label="Show all calls" onClick={p.onClearWorkspaceFilter}
            style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", padding: "0 2px", lineHeight: 0 }}>
            <Icon name="x" size={11} />
          </button>
        </div>
      )}
      {body}
    </div>
  );
}

