"use client";
/** THE DESKTOP (#1783, desktop ADR §2.3a, D1/D2): the person's sections in their order — Workspaces,
 *  Agents, Files, Cards by default (Cards only while `desktop_cards` is on, i.e. the desk read carried
 *  them) — each with a header that collapses it and a "…" that moves or hides it. A hidden section
 *  comes back from the desktop's own "…". What waits for the person's word — a desk arrangement or a
 *  card an agent proposed, a card someone added them to — is a quiet row above the sections.
 *
 *  Arranging by hand writes the agent's ops: dragging a section header writes `section_order`,
 *  dragging a tile onto another in its section writes `move … before`, Collapse writes `collapse`.
 *  One path, one history, Undo on each (desktopApi). What an agent proposed is a quiet row with
 *  Apply / Not now; an active filter is a chip whose × clears it, so nothing seems to vanish. */
import { useEffect, useRef, useState, type DragEvent } from "react";
import { readScope, setWorkspaceState, type ScopeRow } from "../../data/workspaceApi";
import { toast } from "../../ui-kit/primitives/Toast";
import { ChevronDown, ChevronRight } from "lucide-react";
import type { Desktop, DesktopSection } from "../../platform/desktop";
import { ContextMenu, type ContextMenuItem } from "../../ui-kit/ContextMenu";
import { arrange, decideProposal, loadDesktop } from "./desktopApi";
import { AgentTile, ChatTile, FileTile, WorkspaceTile, type TileDrag } from "./DesktopTiles";
import { placeTarget, readDesktopDrag, sectionForTarget } from "./useDesktopItems";
import { CardTile } from "./CardFace";
import { InviteRow, ProposalRow } from "./CardRows";
import { loadPeople } from "./cardsApi";
import { useFeature } from "../../platform/features";
import type { WorkspaceItem } from "../../platform/desktop";
import { HomeCards } from "./home2/StatusCard";
import { NowStrip } from "./home2/NowStrip";
import { useHomeFocus, useHomeTarget } from "./home2/HomeTarget";
import { PersonMergeCard } from "../PersonMergeCard";

const DRAG_TYPE = "application/x-vexa-desk";
type Payload = { type: "section"; id: string } | { type: "tile"; section: string; ref: string };

function read(e: DragEvent<HTMLElement>): Payload | null {
  try { return JSON.parse(e.dataTransfer?.getData(DRAG_TYPE) || "null") as Payload | null; } catch { return null; }
}
function write(e: DragEvent<HTMLElement>, p: Payload): void {
  e.dataTransfer?.setData(DRAG_TYPE, JSON.stringify(p));
  if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
}
const allowDrop = (e: DragEvent<HTMLElement>) => { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "move"; };

/** The section ids with `id` moved to just before `before` (the whole list, hidden ones included). */
export function reorderedSections(ids: string[], id: string, before: string): string[] {
  const rest = ids.filter((x) => x !== id);
  const at = rest.indexOf(before);
  return at < 0 ? ids : [...rest.slice(0, at), id, ...rest.slice(at)];
}

function Menu({ label, items }: { label: string; items: ContextMenuItem[] }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button type="button" ref={btn} className="vx-kh-icon" aria-label={label} aria-haspopup="menu"
        onClick={() => { const r = btn.current!.getBoundingClientRect(); setAt({ x: r.left, y: r.bottom + 4 }); }}>…</button>
      {at && <ContextMenu x={at.x} y={at.y} label={label} onClose={() => setAt(null)} items={items} />}
    </>
  );
}

// Each names only acts that exist: Add to desktop is in every chat's, routine's and file's "…", and a
// chat-list row or a file-tree row can be dragged onto its section.
const EMPTY: Record<string, string> = {
  workspaces: "No workspaces yet.",
  agents: "Pin a chat or routine here — choose Add to desktop in its …, or drag it here from the chat list.",
  files: "Pin a file here — choose Add to desktop in its …, or drag it here from Files.",
  cards: "No cards yet — choose Make a card on a chat's or a page's …",
};

function Section({ s, ids }: { s: DesktopSection; ids: string[] }) {
  const home2 = useFeature("home2");
  const drawnIds = ids;
  const move = (dir: -1 | 1) => {
    const i = drawnIds.indexOf(s.id);
    const j = i + dir;
    if (i < 0 || j < 0 || j >= drawnIds.length) return;
    const next = [...drawnIds];
    [next[i], next[j]] = [next[j], next[i]];
    void arrange([{ op: "section_order", sections: next }]);
  };
  const onSectionDrop = (e: DragEvent<HTMLElement>) => {
    // A chat, a routine or a file dragged from the sidebar: placed when this is its section.
    const t = readDesktopDrag(e);
    if (t) {
      e.preventDefault();
      if (sectionForTarget(t) === s.id) void placeTarget(t);
      return;
    }
    const p = read(e);
    if (!p || p.type !== "section" || p.id === s.id) return;
    e.preventDefault();
    void arrange([{ op: "section_order", sections: reorderedSections(drawnIds, p.id, s.id) }]);
  };
  const tileDrag = (ref: string): TileDrag => ({
    onDragStart: (e) => { e.stopPropagation(); write(e, { type: "tile", section: s.id, ref }); },
    onDragOver: allowDrop,
    onDrop: (e) => {
      const p = read(e);
      if (!p || p.type !== "tile") return;
      e.preventDefault();
      e.stopPropagation();
      if (p.section !== s.id || p.ref === ref) return;
      void arrange([{ op: "move", id: p.ref, before: ref }]);
    },
  });
  return (
    <section className="vx-desk-section" aria-label={s.title} data-desk-section={s.id} data-collapsed={s.collapsed ? "" : undefined}
      data-span={s.span > 1 ? String(Math.min(s.span, 3)) : undefined} data-size={s.size !== "normal" ? s.size : undefined}
      onDragOver={allowDrop} onDrop={onSectionDrop}>
      <header className="vx-desk-head">
        <span className="vx-desk-handle" draggable data-desk-handle="" title="Drag to move this section"
          onDragStart={(e) => write(e, { type: "section", id: s.id })}>
          <span className="vx-kh-h vx-desk-title">{s.title}</span>
        </span>
        <span className="vx-desk-count">{s.items.length || ""}</span>
        <span className="vx-desk-tools">
          <button type="button" className="vx-kh-icon" aria-expanded={!s.collapsed} aria-label={`${s.collapsed ? "Expand" : "Collapse"} ${s.title}`}
            onClick={() => { void arrange([{ op: s.collapsed ? "expand" : "collapse", section: s.id }]); }}>
            {s.collapsed ? <ChevronRight size={13} aria-hidden /> : <ChevronDown size={13} aria-hidden />}
          </button>
          <Menu label={`More for ${s.title}`} items={[
            { id: "up", label: "Move up", onSelect: () => move(-1) },
            { id: "down", label: "Move down", onSelect: () => move(1) },
            { id: "top", label: "Move to the top", onSelect: () => { void arrange([{ op: "section_top", section: s.id }]); } },
            { id: "hide", label: `Hide ${s.title}`, detail: "Bring it back from the desktop's …", onSelect: () => { void arrange([{ op: "hide", section: s.id }]); } },
          ]} />
        </span>
      </header>
      {!s.collapsed && home2 && s.id === "workspaces" && s.items.length > 0
        // home2 (R3): a workspace on the home is its status card only
        ? <HomeCards items={s.items.filter((i): i is WorkspaceItem => i.kind === "workspace")} tileDrag={tileDrag} />
        : !s.collapsed && (s.items.length === 0
        ? <div className="vx-desk-empty">{EMPTY[s.id] ?? "Nothing here yet."}</div>
        : <div className={s.id === "workspaces" ? "vx-kh-grid" : s.id === "cards" ? "vx-dk-grid" : "vx-pk-grid"} role="list">
            {s.items.map((it) => (
              <div role="listitem" key={it.ref} className="vx-desk-cell">
                {it.kind === "workspace" ? <WorkspaceTile w={it} drag={tileDrag(it.ref)} />
                  : it.kind === "agent" ? <AgentTile a={it} drag={tileDrag(it.ref)} />
                  : it.kind === "chat" ? <ChatTile c={it} drag={tileDrag(it.ref)} />
                  : it.kind === "card" ? <CardTile c={it} drag={tileDrag(it.ref)} />
                  : <FileTile f={it} drag={tileDrag(it.ref)} />}
              </div>
            ))}
          </div>)}
    </section>
  );
}

/** "Hidden workspaces (n)" opens to the hidden ones, each with Show again — the workspace-states
 *  unhide (`active`, for new chats), the same verb the chat's "show finos again" runs. */
function HiddenWorkspaces({ count }: { count: number }) {
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState<ScopeRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next) {
      setFailed(false);
      void readScope("", true).then((v) => setRows((v?.workspaces ?? []).filter((w) => w.state === "hidden")))
        .catch(() => { setRows([]); setFailed(true); });
    }
  };
  const showAgain = async (w: ScopeRow) => {
    try {
      const r = await setWorkspaceState([w.slug], "active", "default", "");
      toast(r?.say || `${w.display || w.name} is back`);
      setRows((prev) => (prev ?? []).filter((x) => x.slug !== w.slug));
      await loadDesktop({ force: true });
    } catch {
      toast(`${w.display || w.name} could not be shown just now`);
    }
  };
  return (
    <div className="vx-desk-hidden" data-hidden-workspaces="">
      <button type="button" className="vx-desk-hidden-toggle" aria-expanded={open} onClick={toggle}>
        {open ? <ChevronDown size={12} aria-hidden /> : <ChevronRight size={12} aria-hidden />}Hidden workspaces ({count})
      </button>
      {open && (rows === null
        ? <div className="vx-desk-empty">…</div>
        : failed
          ? <div className="vx-desk-empty">The hidden workspaces could not be read just now.</div>
          : rows.length === 0
            ? <div className="vx-desk-empty">No hidden workspaces.</div>
            : <ul className="vx-desk-hidden-list">
                {rows.map((w) => (
                  <li key={w.slug} className="vx-desk-hidden-row" data-hidden-workspace={w.slug}>
                    <span className="vx-desk-hidden-name">{w.display || w.name}</span>
                    <button type="button" className="vx-kh-btn" aria-label={`Show ${w.display || w.name} again`}
                      onClick={() => { void showAgain(w); }}>Show again</button>
                  </li>
                ))}
              </ul>)}
    </div>
  );
}

export function DesktopView({ desktop }: { desktop: Desktop }) {
  const home2 = useFeature("home2");
  const shell2Home = useFeature("shell2");
  const ids = desktop.sections.map((s) => s.id);
  const shown = desktop.sections.filter((s) => !s.hidden);
  const cardsOn = !!desktop.cards_on;
  // Cards is restorable only while the desk read carries cards; without them it is not the person's to show.
  const restorable = desktop.sections.filter((s) => s.hidden && (s.id !== "cards" || cardsOn));
  // home2 (R3): agents and routines show inside their workspace's card, not as a section of their own
  const shownSections = (cardsOn ? shown : shown.filter((s) => s.id !== "cards")).filter((s) => !(home2 && s.id === "agents"))
    // JTBD-1: an empty section on the new home is instructions, not content — hidden
    .filter((s) => !(home2 && s.id !== "workspaces" && s.items.length === 0));
  const workspaceItems = (desktop.sections.find((s) => s.id === "workspaces")?.items ?? []).filter((i): i is WorkspaceItem => i.kind === "workspace");
  const cardProposals = cardsOn ? desktop.card_proposals ?? [] : [];
  const invites = cardsOn ? desktop.card_invites ?? [] : [];
  const anyCards = cardsOn && (cardProposals.length > 0 || invites.length > 0
    || desktop.sections.some((s) => s.id === "cards" && s.items.length > 0));
  useEffect(() => { if (anyCards) void loadPeople(); }, [anyCards]);
  const titleOf = (id: string) => desktop.sections.find((s) => s.id === id)?.title ?? id;
  // shell2 (UI 7i/7t): FOCUS — after a workspace's "New chat" the home is only the input for that
  // workspace (above): no grid, no Now strip, no files. The × on the target chip brings it back.
  const shell2 = useFeature("shell2");
  const target = useHomeTarget();
  const focused = useHomeFocus();
  if (shell2 && home2 && focused) return <div className="vx-desk vx-desk-focus" data-desktop="" data-home-focus={target} />;
  return (
    <div className="vx-desk" data-desktop="" data-version={desktop.version}>
      {/* D35: "are these accounts all yours?" — only when this person has a merge card waiting */}
      <PersonMergeCard />
      {(desktop.proposals.length > 0 || cardProposals.length > 0 || invites.length > 0 || desktop.filters.length > 0 || restorable.length > 0) && (
        <div className="vx-desk-bar">
          {desktop.proposals.map((p) => (
            <div key={p.id} className="vx-dk-pending" data-desk-proposal={p.id}>
              <span>Vexa suggests: {p.summary || p.reason || "a new arrangement"}</span>
              {p.summary && p.reason && <span className="vx-dk-preason">· {p.reason}</span>}
              <span className="vx-dk-pacts">
                <button type="button" className="vx-kh-btn vx-kh-btn-primary" onClick={() => { void decideProposal(p.id, true); }}>Apply</button>
                <button type="button" className="vx-kh-btn" onClick={() => { void decideProposal(p.id, false); }}>Not now</button>
              </span>
            </div>
          ))}
          {cardProposals.map((p) => <ProposalRow key={p.id} p={p} />)}
          {invites.map((c) => <InviteRow key={c.id} c={c} />)}
          <span className="vx-desk-chips">
            {desktop.filters.map((f) => (
              <span key={f.section} className="vx-desk-chip" data-desk-filter={f.section}>
                Filtered: {f.where}
                <button type="button" className="vx-desk-chip-x" aria-label={`Clear filter on ${titleOf(f.section)}`}
                  onClick={() => { void arrange([{ op: "filter", section: f.section, where: null }]); }}>×</button>
              </span>
            ))}
            {restorable.length > 0 && <Menu label="More for the desktop" items={restorable.map((s) => ({
              id: `show-${s.id}`, label: `Show ${s.title}`, onSelect: () => { void arrange([{ op: "show", section: s.id }]); },
            }))} />}
          </span>
        </div>
      )}
      {/* every live meeting at the top (founder O1, 2026-10-18): on shell2's Home as well as home2's */}
      {(home2 || shell2Home) && <NowStrip workspaces={workspaceItems} />}
      <div className="vx-desk-sections">
        {shownSections.map((s) => <Section key={s.id} s={s} ids={ids} />)}
      </div>
      {desktop.hidden_workspaces > 0 && <HiddenWorkspaces count={desktop.hidden_workspaces} />}
    </div>
  );
}
