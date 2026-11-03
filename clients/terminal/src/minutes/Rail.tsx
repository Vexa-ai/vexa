"use client";
/** The rail: the person's CHATS, in their own sections (#1783).
 *
 *  Pinned · Shared with me · the person's folders · Ungrouped · Hidden, each collapsible and capped with "Show N
 *  more" (`railFolders.ts`). A chat is dragged between sections (long-press then drag on a phone),
 *  or moved with "Move to…" in its menu, which is also the keyboard way. Hidden is a folder, not a
 *  filter: hiding a chat moves it there. No Meetings/Projects switcher and no Active/All tabs — a
 *  meeting is a chat with a meeting ref, and a live one says so on its row.
 *
 *  No workspace chrome either (founder ruling: "remove workspaces, they can do that via MCP if they
 *  need"). A chat still carries the workspaces it is over — that is data on the chat, and the header
 *  shows the mount set — but creating, inviting to, resetting and deleting a folder is a job for the
 *  MCP verbs and the conversation, not for a column of × buttons beside the reading list. */
import { Identity } from "../ui-kit/Identity";
import { useEffect, useRef, useState } from "react";
import type React from "react";
import { useChatActive } from "../surfaces/chatActivity";
import type { Row } from "./chats";
import { AccountBadge } from "./AccountBadge";
import { CollapseButton } from "./Collapse";
import { Check, ChevronDown, ChevronRight, Compass, FolderInput, FolderPlus, Hash, Lock, Mic, MoreHorizontal, PanelLeft, Phone, Plus, Trash2, UserPlus, Users, X } from "lucide-react";
import { roomOf } from "../surfaces/roomState";
import { WorkspaceMark } from "./rooms/WorkspaceMark";
import { displayLabel } from "./chats";

/** The row's date, for its tooltip only: "Thu, Oct 15, 9:13 AM". */
const rowDate = (ms: number) => (Number.isFinite(ms) && ms > 0
  ? new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(ms))
  : undefined);
import { HIDDEN, NO_FOLDERS, PINNED, ROUTINES, SHARED, UNGROUPED, sections, type RailFolder, type RailFolders } from "./railFolders";

/** UI 7e: the Recent section — the newest chats across everything */
const RECENT = "recent";
const RECENT_CAP = 8;
import { NotificationBell } from "./NotificationBell";
import { ContextMenu, type ContextMenuItem } from "../ui-kit/ContextMenu";
import { RAIL_SHORTCUTS, isTypingTarget, matchesShortcut, shortcutAria, shortcutLabel, type Shortcut } from "../ui-kit/shortcuts";
import { Button, ConfirmDialog, Dialog, IconButton, Input, Menu, Switch, Tabs, Tag } from "../ui-kit";
import { SidebarNav } from "./shell2/SidebarNav";
import { PairFace, isPairRoom, pairPeerOf } from "./shell2/pairRooms";
import { usePresence } from "./shell2/PeoplePage";
import { refreshRoom } from "../surfaces/roomState";
import { ImportMeetingButton } from "../surfaces/MeetingBundleActions";
import { clearVoiceNoteError, clock, stopVoiceNote, useVoiceNote } from "./voiceNote";
import { useFeature } from "../platform/features";
import { useDesktopItems, useDesktopOn, writeDesktopDrag, type DesktopTarget } from "./desktop/useDesktopItems";
import { useCardItems } from "./desktop/useCardItems";

/** THE SECTIONS READ Pinned · Routines · (your folders) · Recent · Hidden (founder 2026-10-14). The
 *  catch-all keeps its id (`ungrouped`) and its server name; it is SAID as what it is — the recent
 *  conversations not filed anywhere. */
export const sectionName = (f: RailFolder) => (f.builtin && f.id === UNGROUPED ? "Recent" : f.name);

/** The row's one glyph: call › voice note › shared › routine › concern; none for a plain chat. */
function RowIcon({ r }: { r: Row }) {
  // ROOMS (R1): a chat with people in it is a room — # when open, a lock when closed
  const roomsOn = useFeature("rooms");
  const kind = r.kind === "call" ? "call" : r.kind === "voice" ? "voice" : r.shared ? "shared"
    : r.routine ? "routine" : r.concern ? "concern" : null;
  if (!kind) return null;
  const title = kind === "call" ? "A call" : kind === "voice" ? "A voice note"
    : kind === "shared" ? (r.sharedBy !== undefined ? `Shared with you${r.sharedBy ? ` by ${r.sharedBy}` : ""}` : "You shared this chat")
    : kind === "routine" ? (r.routinePaused ? `Routine ${r.routine?.flow ?? ""}: paused` : `Routine ${r.routine?.flow ?? ""}: wakes on a timer and reports here`)
    : `A concern chat, ${r.concern?.stage ?? ""}`;
  return (
    <span data-row-icon={kind} data-paused={kind === "routine" && r.routinePaused ? "" : undefined} className="vx-rail-icon" title={title} role="img" aria-label={title}>
      {kind === "shared" && r.sharedBy ? <Identity kind="person" id={r.sharedBy} name={r.sharedBy} size={14} />
        : kind === "call" ? <Phone size={12} strokeWidth={1.75} aria-hidden />
        : kind === "voice" ? <Mic size={12} strokeWidth={1.75} aria-hidden />
        : kind === "shared" ? (roomsOn ? (roomOf(r.chatId ?? undefined)?.visibility === "closed" ? <Lock size={12} strokeWidth={1.75} aria-hidden /> : <Hash size={12} strokeWidth={1.75} aria-hidden />) : <Users size={12} strokeWidth={1.75} aria-hidden />)
        : <Identity kind="agent" id={r.routine?.flow || r.key} name={r.routine?.flow || r.label} size={14} />}
    </span>
  );
}

/** A voice note recording in this tab (#1783): the rail shows it is live — the running time — and
 *  stops it from anywhere; a note that could not start says why (the tooltip) until dismissed. It starts nothing: a note starts from the empty chat's "Record a voice
 *  note" chip or the agent's `record_voice_note` tool (founder 2026-10-13), so there is no idle mic. */
function VoiceNoteLive() {
  const vn = useVoiceNote();
  const live = vn.phase === "recording" || vn.phase === "starting" || vn.phase === "stopping";
  // A note that failed before it existed has no meeting view to explain it: the reason is here, once.
  if (vn.phase === "error" && !vn.meetingId) {
    return <button type="button" data-voice-note="error" className="vx-rail-voice" title={vn.error}
      aria-label={`Voice note did not start: ${vn.error ?? ""}. Dismiss`} onClick={clearVoiceNoteError}>
      Not recording
    </button>;
  }
  if (!live) return null;
  return <button type="button" data-voice-note="recording" aria-label="Stop voice note" title="Stop voice note"
    disabled={vn.phase !== "recording"} onClick={() => void stopVoiceNote()} className="vx-rail-voice">
    <span aria-hidden className="vx-rail-voice-dot" style={{ opacity: 0.4 + Math.min(0.6, vn.level * 2) }} />
    {vn.phase === "starting" ? "…" : vn.phase === "stopping" ? "Saving" : clock(vn.sentSec)}
  </button>;
}

/* THE ROW'S LOOK IS THE ui-kit ROW's (guidelines §4.8): 28px, selected = a fill, weight 500 and a
 * 2px accent bar inside the radius, actions revealed on hover and focus-within without reflowing
 * the row. The write target is a muted Tag, and LIVE is the success colour with its word — accent is
 * reserved for "this is the one". A meeting that is over carries NO status word (founder 2026-10-11:
 * *"i think this 'held' does not make sense anymore"*): it shows its date in the time column like
 * every other row, and the filled dot (a chat's is a ring) is what says it is a call. */
function ActivityDot({ r }: { r: Row }) {
  const active = useChatActive(r.chatId);
  if (active) return <span data-row-activity="active" role="img" aria-label="Agent is working" title="Agent is working" className="vx-dot vx-rail-dot" data-tone="info" />;
  return <span className="vx-dot vx-rail-dot" data-tone={r.live ? "success" : undefined} data-kind={r.meetingId ? "meeting" : "chat"} aria-hidden />;
}

/** shell2: a room of two is named by the other person, with their face, presence and unread count */
function RowTitle({ r, pair }: { r: Row; pair: boolean }) {
  const peer = pair ? pairPeerOf(r) : null;
  const presenceOf = usePresence();
  const unread = r.unread ?? 0;
  if (!pair || !peer) return <span className="vx-row2-title">{displayLabel(r)}</span>;
  return <>
    <PairFace peer={peer} presence={presenceOf(peer.principal)} />
    <span className="vx-row2-title" data-pair-room="">{peer.name}</span>
    {unread > 0 && <span className="vx-s2-unread" aria-label={`${unread} unread`}>{unread}</span>}
  </>;
}

/** A draft routine as the Routines filter lists it (`routineDraft.ts` builds it from the card). */
export type RailDraft = { name: string; label: string; when: string; thread?: string };

/** ONE DRAFT ROUTINE under the Routines filter: its name and the Draft tag, its schedule and its
 *  home chat (a press opens the chat), and the switch that turns it on. */
function DraftRow({ d, chat, onTurnOn, onOpen }: {
  d: RailDraft; chat: string; onTurnOn?: (name: string) => Promise<boolean>; onOpen?: (thread: string) => void;
}) {
  const [on, setOn] = useState(false);
  const [failed, setFailed] = useState(false);
  const flip = async (next: boolean) => {
    if (!next || !onTurnOn) return;   // a draft is off; switching it on is the only act here
    setOn(true); setFailed(false);
    let ok = false;
    try { ok = await onTurnOn(d.name); } catch { ok = false; }
    if (!ok) { setOn(false); setFailed(true); }
  };
  return (
    <div className="vx-rail-draft" data-routine-draft-row={d.name}>
      <button type="button" className="vx-rail-draft-open" disabled={!d.thread || !onOpen}
        title={d.thread ? `Open “${chat}”, the chat it runs in` : undefined}
        onClick={() => { if (d.thread) onOpen?.(d.thread); }}>
        <span className="vx-rail-draft-title"><span className="vx-row2-title">{d.label}</span><Tag>Draft</Tag></span>
        <span className="vx-rail-draft-meta" data-routine-draft-meta>{d.when} · in {chat}</span>
      </button>
      <Switch checked={on} hideLabel label={`Turn on ${d.label}`} disabled={on || !onTurnOn} onChange={(v) => void flip(v)} />
      {failed && <span role="alert" className="vx-rail-draft-meta">It did not turn on — try again.</span>}
    </div>
  );
}

/** How many rows a section shows before "Show N more". The selected row always shows. */
export const SECTION_CAP = 8;
/** How long a finger rests on a row before it lifts for dragging (a phone's long-press). */
const LONG_PRESS_MS = 450;

type Drag = { kind: "row" | "folder"; key: string };

export function Rail(p: {
  rows: (Row & { folder?: string })[];
  /** kept for older callers; the sections replaced the Active/All filter */
  hidden?: number; all?: boolean; onAll?: (v: boolean) => void;
  selKey: string | null; onSelect: (r: Row) => void;
  onNewChat: () => void;
  /** `retireRoutine` is the answer to the question a routine thread's delete asks (ADR-0046): the
   *  chat is never deleted with its routine silently left running, nor the routine silently killed. */
  onDeleteChat: (chatId: string, opts?: { retireRoutine?: boolean }) => void;
  onCollapse?: () => void;
  /** The fold control's name when folding really means closing the drawer. */
  collapseLabel?: string;
  /** drag one PINNED row onto another (the older pinned-order path, used without `onMoveRows`) */
  onMove?: (from: string, to: string) => void;
  /** pin/unpin and hide/unhide — the person's own marks, saved on the server */
  onPin?: (r: Row, on: boolean) => void;
  onHide?: (r: Row, on: boolean) => void;
  /** kept for older callers; Hidden is a section now */
  hiddenView?: boolean; onHiddenView?: (v: boolean) => void; hiddenMarked?: number;
  /** THE PERSON'S SECTIONS (`railFolders.ts`). Absent: Pinned · Ungrouped · Hidden from the marks. */
  folders?: RailFolders;
  /** file rows in a section (`before` places them ahead of that row in its manual order) */
  onMoveRows?: (rows: string[], to: string, before?: string) => void;
  onFolderCreate?: (name: string, then?: string[]) => void;
  /** R1 (`rooms`): make a room in a workspace / browse a workspace's open rooms */
  onNewRoom?: () => void;
  onBrowseRooms?: () => void;
  /** home2: back to the home page (the Home entry and the Chats title) */
  onHome?: () => void;
  /** shell2: a nav row was used inside the drawer (the drawer closes) */
  onNavigate?: () => void;
  /** home2: the + New menu's New workspace */
  onNewWorkspace?: () => void;
  onFolderRename?: (id: string, name: string) => void;
  onFolderDelete?: (id: string) => void;
  onFolderCollapse?: (id: string, collapsed: boolean) => void;
  onFolderMove?: (id: string, position: number) => void;
  /** MANY AT ONCE (#1783): share the chosen chats with a person by email; delete several (one
   *  confirmation). Absent, the selection offers only Move to…. */
  onShareChats?: (rows: Row[], email: string) => void;
  onDeleteChats?: (chatIds: string[]) => void;
  /** A MEETING ROW'S OWN ACTS (#1783): the same set the meeting's page header offers
   *  (`meetingActions.ts`), appended to the row's menu. */
  meetingActsFor?: (r: Row) => ContextMenuItem[];
  /** shell2: routines an agent PROPOSED and nobody has turned on yet (D24), listed under the
   *  Routines filter marked Draft — schedule, home chat and an on/off switch (live app.dev
   *  2026-10-18: a draft showed nowhere its person could reach). */
  routineDrafts?: RailDraft[];
  /** the switch: the person's own press, from this session; `true` when the routine took it */
  onTurnOnRoutine?: (name: string) => Promise<boolean>;
  /** open a draft's home chat */
  onOpenThread?: (thread: string) => void;
}) {
  const vn = useVoiceNote();
  const folders = p.folders ?? NO_FOLDERS;
  const [deleting, setDeleting] = useState<Row | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const [folderMenu, setFolderMenu] = useState<{ id: string; x: number; y: number } | null>(null);
  const [moving, setMoving] = useState<Row[] | null>(null);
  // THE SELECTION (#1783, founder: "choose multiple chats at a time to move them or whatever"):
  // Cmd/Ctrl-click toggles, Shift-click takes a range, Esc clears. On a phone a long press starts
  // picking: rows show a checkbox and a tap toggles instead of opening.
  const [pickedRaw, setPicked] = useState<string[]>([]);
  const [picking, setPicking] = useState(false);
  const anchor = useRef<string | null>(null);
  const [bulkDeleting, setBulkDeleting] = useState<Row[] | null>(null);
  const [sharing, setSharing] = useState<Row[] | null>(null);
  const [shareTo, setShareTo] = useState("");
  // `shared_chats` off (#1783): "Share N chats…" is not offered; Leave on a shared chat stays.
  // ROOMS (R1): chats are private — no "Share N chats…"; HOME2: the sidebar by workspace, no folders
  const roomsOn = useFeature("rooms");
  const home2 = useFeature("home2");
  const sharingOn = useFeature("shared_chats") && !roomsOn;
  // SHELL2 (UI 7k/7x, founder 2026-10-17): the sidebar is ONE list — + New · Inbox · Meetings — with
  // the chat list under it, filtered All · Rooms · Routines (remembered per browser). Routines are a
  // filter, not a group; meetings are not chats (they live on the Meetings page).
  const shell2 = useFeature("shell2") && home2 && !!p.onHome;
  const [railFilter, setRailFilterState] = useState<"all" | "rooms" | "routines">(() => {
    try { const v = localStorage.getItem("vexa.rail.filter"); return v === "rooms" || v === "routines" ? v : "all"; } catch { return "all"; }
  });
  // shell2 + S4: which shared chats are rooms of two is the room read's to say — read each once
  const sharedIds = shell2 && roomsOn ? p.rows.filter((r) => r.shared && r.chatId).map((r) => r.chatId as string).slice(0, 60).join("|") : "";
  const readRooms = useRef(new Set<string>());
  useEffect(() => {
    for (const id of sharedIds ? sharedIds.split("|") : []) {
      if (readRooms.current.has(id)) continue;
      readRooms.current.add(id);
      void refreshRoom(id);
    }
  }, [sharedIds]);
  const setRailFilter = (k: string) => {
    const v = k === "rooms" || k === "routines" ? k : "all";
    setRailFilterState(v);
    try { localStorage.setItem("vexa.rail.filter", v); } catch { /* storage off: the filter is not remembered */ }
  };
  const [naming, setNaming] = useState<{ id: string | null; name: string; then?: string[] } | null>(null);
  const [dissolving, setDissolving] = useState<RailFolder | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // A section's fold is the person's, saved on the server; until the save answers, the click shows.
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const isFolded = (f: RailFolder) => folded[f.id] ?? f.collapsed;
  const menuButtons = useRef(new Map<string, HTMLButtonElement>());
  const closeMenu = (key: string, refocus = true) => { setMenu(null); if (refocus) menuButtons.current.get(key)?.focus(); };
  const sectionOf = (r: Row & { folder?: string }) => r.folder ?? (r.hidden ? HIDDEN : r.pinned ? PINNED : UNGROUPED);
  const move = (rows: string[], to: string, before?: string) => {
    if (p.onMoveRows) { p.onMoveRows(rows, to, before); return; }
    // Without folders: Pinned, Ungrouped and Hidden are the two marks.
    for (const key of rows) {
      const r = p.rows.find((x) => x.key === key);
      if (!r) continue;
      if (to === HIDDEN) p.onHide?.(r, true);
      else if (to === PINNED) { if (r.hidden) p.onHide?.(r, false); p.onPin?.(r, true); if (before) p.onMove?.(key, before); }
      else { if (r.hidden) p.onHide?.(r, false); if (r.pinned) p.onPin?.(r, false); }
    }
  };
  const canMove = !!(p.onMoveRows || p.onPin || p.onHide);
  // ONE LIST OF ACTS PER ROW: the menu draws it, and the keyboard shortcuts run the same entries,
  // so a shortcut can only do what the row's menu offers for that row (no Pin on a hidden row, no
  // Delete on a meeting with no chat).
  const desktopItems = useDesktopItems();
  const desktopOn = useDesktopOn();
  const deskTarget = (r: Row): DesktopTarget | null =>
    r.routine?.flow ? { kind: "agent", id: r.routine.flow } : r.chatId ? { kind: "chat", id: r.chatId } : null;
  const cardItems = useCardItems();
  const itemsFor = (r: Row): ContextMenuItem[] => {
    const items: ContextMenuItem[] = [];
    if (!r.hidden && (p.onPin || p.onMoveRows)) items.push({ id: "pin", label: r.pinned ? "Unpin" : "Pin to top", shortcut: RAIL_SHORTCUTS.pin, letter: "P",
      onSelect: () => p.onMoveRows ? move([r.key], r.pinned ? UNGROUPED : PINNED) : p.onPin?.(r, !r.pinned) });
    if (canMove) items.push({ id: "move", label: "Move to…", letter: "M", onSelect: () => setMoving([r]) });
    if (p.onHide || p.onMoveRows) items.push({ id: "hide", label: r.hidden ? "Unhide" : "Hide", detail: r.hidden ? undefined : "Bring it back from Hidden", shortcut: RAIL_SHORTCUTS.hide, letter: "H",
      onSelect: () => p.onMoveRows ? move([r.key], r.hidden ? UNGROUPED : HIDDEN) : p.onHide?.(r, !r.hidden) });
    if (r.meetingId && p.meetingActsFor) items.push(...p.meetingActsFor(r).map((it) => ({ ...it, id: `meeting-${it.id}` })));
    // Add to desktop / Remove from desktop (desktop ADR D1, 15e): a routine's thread places the
    // routine; any other chat places the chat itself (the founder's "selected agents")
    const placeAs = deskTarget(r);
    if (placeAs) items.push(...desktopItems(placeAs));
    // a chat: Make a card (desktop ADR D2, 15c/15d) — a CRM card about it
    if (r.chatId) items.push(...cardItems({ kind: "chat", id: r.chatId, title: displayLabel(r), workspace: r.target ?? null }));
    // the destructive act last, below everything that only adds (lab 2026-10-15)
    if (r.chatId) items.push({ id: "delete", label: "Delete…", danger: true, shortcut: RAIL_SHORTCUTS.delete, letter: "D", onSelect: () => setDeleting(r) });
    return items;
  };
  // THE SHORTCUTS ACT ON THE SELECTED ROW (Vexa-ai/vexa#1829), and only while the rail is on screen.
  // Nothing fires while a dialog or a row menu is open (the menu has its own letters), and the
  // editing key (Backspace) is never taken out of a text field. Delete only OPENS the confirm dialog.
  const navRef = useRef<HTMLElement>(null);
  const live = useRef({ p, menu, itemsFor });
  live.current = { p, menu, itemsFor };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const { p, menu, itemsFor } = live.current;
      if (e.defaultPrevented || e.repeat || menu) return;
      const nav = navRef.current;
      if (!nav || nav.closest("[inert],[aria-hidden=true]")) return;
      if ([...document.querySelectorAll("[aria-modal=true]")].some((m) => !m.contains(nav))) return;
      if (matchesShortcut(e, RAIL_SHORTCUTS.newChat)) { e.preventDefault(); p.onNewChat(); return; }
      const id = (["pin", "hide", "delete"] as const).find((k) => matchesShortcut(e, RAIL_SHORTCUTS[k]));
      if (!id) return;
      if ((RAIL_SHORTCUTS[id] as Shortcut).editing && isTypingTarget(e.target)) return;
      const r = p.rows.find((x) => x.key === p.selKey);
      const item = r && itemsFor(r).find((i) => i.id === id);
      if (!r || !item) return;
      e.preventDefault();
      void item.onSelect();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // ── DROPPING ─────────────────────────────────────────────────────────────────────────────
  // A drop names a section (its header or its body) and, on a row, the row it lands ahead of.
  const userFolders = folders.folders.filter((f) => !f.builtin);
  const dropAt = (target: { folder: string; before?: string }, d: Drag | null = drag) => {
    setDrag(null); setOver(null);
    if (!d) return;
    if (d.kind === "folder") {
      if (target.folder === d.key) return;
      const at = userFolders.findIndex((f) => f.id === target.folder);
      if (at >= 0) p.onFolderMove?.(d.key, at);
      return;
    }
    if (target.before === d.key) return;
    const from = p.rows.find((r) => r.key === d.key);
    if (!from) return;
    if (!p.onMoveRows && target.folder === PINNED && from.pinned && target.before) { p.onMove?.(d.key, target.before); return; }
    if (sectionOf(from) === target.folder && target.folder !== PINNED && !(picked.length > 1 && picked.includes(d.key))) return;
    const keys = picked.length > 1 && picked.includes(d.key) ? picked.filter((k) => k !== target.before) : [d.key];
    move(keys, target.folder, target.folder === PINNED ? target.before : undefined);
    if (keys.length > 1) clearPicked();
  };
  const dropProps = (target: { folder: string; before?: string }) => ({
    "data-drop-folder": target.folder,
    "data-drop-before": target.before,
    onDragOver: (e: React.DragEvent) => {
      if (!drag) return;
      if (drag.kind === "folder" && (folders.folders.find((f) => f.id === target.folder)?.builtin ?? true)) return;
      e.preventDefault(); e.dataTransfer.dropEffect = "move"; if (over !== target.folder) setOver(target.folder);
    },
    onDrop: (e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); dropAt(target); },
  });
  // A ROW CAN ALSO BE DRAGGED ONTO THE DESKTOP (#1783): it carries its desktop target beside the
  // rail's own payload. A row the rail itself does not move is draggable for that alone, and then
  // the rail's drag state stays empty, so nothing in the rail treats it as a move.
  const dragProps = (d: Drag, enabled: boolean, desk: DesktopTarget | null = null) => enabled ? {
    draggable: true,
    onDragStart: (e: React.DragEvent<HTMLElement>) => {
      e.stopPropagation(); setDrag(d); e.dataTransfer.setData("text/plain", d.key); e.dataTransfer.effectAllowed = "move";
      if (desk) writeDesktopDrag(e, desk);
    },
    onDragEnd: () => { setDrag(null); setOver(null); },
    onTouchStart: (e: React.TouchEvent) => touchStart(e, d),
  } : desk ? {
    draggable: true,
    onDragStart: (e: React.DragEvent<HTMLElement>) => { e.stopPropagation(); e.dataTransfer.effectAllowed = "copyMove"; writeDesktopDrag(e, desk); },
  } : {};

  // TOUCH: a phone has no drag events. A finger that rests LONG_PRESS_MS on a row (or a folder's
  // header) lifts it; moving it then follows the finger, and lifting the finger drops it on the
  // section under it. A finger that moves first is a scroll, and nothing is lifted.
  const touch = useRef<{ timer: number | null; x: number; y: number; d: Drag | null; lifted: boolean; moved: boolean }>({ timer: null, x: 0, y: 0, d: null, lifted: false, moved: false });
  const touchTarget = (x: number, y: number) => {
    const el = document.elementFromPoint(x, y)?.closest("[data-drop-folder]") as HTMLElement | null;
    return el ? { folder: el.dataset.dropFolder as string, before: el.dataset.dropBefore || undefined } : null;
  };
  const touchStart = (e: React.TouchEvent, d: Drag) => {
    const t = e.touches[0];
    if (!t || e.touches.length > 1) return;
    const cur = touch.current;
    if (cur.timer) window.clearTimeout(cur.timer);
    cur.x = t.clientX; cur.y = t.clientY; cur.d = d; cur.lifted = false; cur.moved = false;
    cur.timer = window.setTimeout(() => {
      cur.lifted = true; cur.timer = null; setDrag(d);
      // A LONG PRESS ON A ROW ALSO PICKS IT: let go where you are and the phone is in picking mode
      // with this chat checked; move the finger and the selection goes with it.
      if (d.kind === "row") { setPicking(true); setPicked((prev) => prev.includes(d.key) ? prev : [...prev, d.key]); }
      try { navigator.vibrate?.(10); } catch { /* no haptics */ }
    }, LONG_PRESS_MS);
  };
  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const onMove = (e: TouchEvent) => {
      const cur = touch.current, t = e.touches[0];
      if (!t || !cur.d) return;
      if (!cur.lifted) {
        if (Math.abs(t.clientX - cur.x) > 8 || Math.abs(t.clientY - cur.y) > 8) { if (cur.timer) window.clearTimeout(cur.timer); cur.timer = null; cur.d = null; }
        return;
      }
      e.preventDefault();
      if (Math.abs(t.clientX - cur.x) > 8 || Math.abs(t.clientY - cur.y) > 8) cur.moved = true;
      setOver(touchTarget(t.clientX, t.clientY)?.folder ?? null);
    };
    const onEnd = (e: TouchEvent) => {
      const cur = touch.current;
      if (cur.timer) window.clearTimeout(cur.timer);
      cur.timer = null;
      if (cur.lifted && cur.d) {
        e.preventDefault();
        const t = e.changedTouches[0];
        const moved = cur.moved;
        const at = t && moved ? touchTarget(t.clientX, t.clientY) : null;
        const d = cur.d;
        cur.d = null; cur.lifted = false; cur.moved = false;
        if (at && moved) dropAt(at, d); else { setDrag(null); setOver(null); }
        return;
      }
      cur.d = null; cur.lifted = false;
    };
    nav.addEventListener("touchmove", onMove, { passive: false });
    nav.addEventListener("touchend", onEnd, { passive: false });
    nav.addEventListener("touchcancel", onEnd);
    return () => { nav.removeEventListener("touchmove", onMove); nav.removeEventListener("touchend", onEnd); nav.removeEventListener("touchcancel", onEnd); };
  });

  const byKey = new Map(p.rows.map((x) => [x.key, x]));
  const picked = pickedRaw.filter((k) => byKey.has(k));
  const pickedRows = picked.map((k) => byKey.get(k) as Row);
  const clearPicked = () => { setPicked([]); setPicking(false); anchor.current = null; };
  const toggle = (key: string) => setPicked((prev) => {
    const base = prev.length === 0 && p.selKey && p.selKey !== key && byKey.has(p.selKey) && !picking ? [p.selKey] : prev;
    return base.includes(key) ? base.filter((k) => k !== key) : [...base, key];
  });
  const rowClick = (e: React.MouseEvent, r: Row) => {
    if (e.metaKey || e.ctrlKey) { e.preventDefault(); toggle(r.key); anchor.current = r.key; return; }
    const from = anchor.current ?? p.selKey;
    if (e.shiftKey && from) {
      e.preventDefault();
      const order = drawnOrder();
      const a = order.indexOf(from), b = order.indexOf(r.key);
      if (a >= 0 && b >= 0) { setPicked(order.slice(Math.min(a, b), Math.max(a, b) + 1)); return; }
    }
    if (picking) { toggle(r.key); return; }
    if (picked.length) setPicked([]);
    anchor.current = r.key;
    p.onSelect(r);
  };
  const chatRow = (drawn: Row & { folder?: string }) => {
    // the caller's own row object goes back out through every callback, never the drawn copy
    const r = byKey.get(drawn.key) ?? drawn;
    const on = r.key === p.selKey;
    const open = menu?.key === r.key;
    const canDrag = p.onMoveRows ? true : !!p.onMove && !!r.pinned && !r.hidden;
    const isPicked = picked.includes(r.key);
    return (
      <div key={r.key} className="vx-row2 vx-rail-row" data-selected={on ? "" : undefined} data-picked={isPicked ? "" : undefined} data-row-key={r.key}
        data-row-target={r.target || undefined} data-row-routine={r.routine?.flow || undefined} data-row-stage={r.concern?.stage || undefined}
        data-row-shared={r.shared ? (r.sharedBy !== undefined ? "from" : "mine") : undefined} data-row-pinned={r.pinned ? "" : undefined}
        data-dragging={drag?.kind === "row" && drag.key === r.key ? "" : undefined}
        {...dragProps({ kind: "row", key: r.key }, canDrag, desktopOn ? deskTarget(r) : null)} {...dropProps({ folder: sectionOf(r), before: r.key })}>
        <button data-chat-row className="vx-row2-main" title={rowDate(r.when)} aria-current={on || undefined} aria-pressed={picking || picked.length ? isPicked : undefined} onClick={(e) => rowClick(e, r)}>
          {picking && !isPicked && <input type="checkbox" className="vx-rail-check" checked={false} readOnly tabIndex={-1} aria-hidden />}
          {isPicked && <span className="vx-rail-picked" aria-hidden><Check size={11} strokeWidth={2.25} /></span>}
          {!(shell2 && isPairRoom(r.chatId, r.visibility)) && <ActivityDot r={r} />}
          {shell2 ? <RowTitle r={r} pair={isPairRoom(r.chatId, r.visibility)} /> : <span className="vx-row2-title">{displayLabel(r)}</span>}
          {/* ONE ICON, NO TEXT CHIPS (founder 2026-10-14: "total mess here" — his shared routine row read
              "ait · ⏱ routine · shared · live" and its name was gone). A call, a voice note, a chat
              with other people in it, a routine's thread: one glyph, in that order, its meaning on
              hover. Where it writes is in the chat's own header; a pinned row is in Pinned. */}
          <RowIcon r={r} />
          {home2 && r.target && r.target !== "personal" && <span className="vx-rail-row-ws" title={r.target}><WorkspaceMark id={r.target} name={r.target} size={16} /></span>}
          {/* NO TIME COLUMN (founder 2026-10-15: "this is bloated — remove time"); the date is in the
              row's tooltip, and only a live row still says so */}
          {r.live && <span className="vx-row2-meta" data-live="">{r.whenLabel}</span>}
        </button>
        <span className="vx-row2-actions" data-open={open ? "" : undefined}>
          <IconButton ref={(el: HTMLButtonElement | null) => { if (el) menuButtons.current.set(r.key, el); else menuButtons.current.delete(r.key); }}
            label={`Actions for ${r.label}`} size="xs" aria-haspopup="menu" aria-expanded={open}
            onClick={(e) => {
              e.stopPropagation();
              if (open) { closeMenu(r.key, false); return; }
              const b = e.currentTarget.getBoundingClientRect();
              setMenu({ key: r.key, x: b.right - 188, y: b.bottom + 4 });
            }}>
            <MoreHorizontal size={14} strokeWidth={1.75} />
          </IconButton>
        </span>
        {open && menu && <ContextMenu x={menu.x} y={menu.y} label={`Actions for ${r.label}`} items={isPicked && picked.length > 1 ? bulkItems() : itemsFor(r)} onClose={() => closeMenu(r.key)} />}
      </div>
    );
  };

  // ── SECTIONS ─────────────────────────────────────────────────────────────────────────────
  // HOME2: inside a workspace's group — rooms, then your chats, then calls and voice notes
  const rank = (r: Row) => (r.shared ? 0 : r.kind === "call" || r.kind === "voice" ? 2 : 1);
  // SHELL2: the list holds chats, rooms and routines — no meetings or voice notes (the Meetings page
  // has them; a live call is reached through its room) — narrowed by the filter
  const chatRows = shell2 ? p.rows.filter((r) => !r.meetingId && r.kind !== "call" && r.kind !== "voice") : p.rows;
  const railRows = !shell2 || railFilter === "all" ? chatRows : chatRows.filter((r) => (railFilter === "rooms" ? !!r.shared : !!r.routine));
  const ordered = home2 ? [0, 1, 2].flatMap((k) => railRows.filter((r) => rank(r) === k)) : railRows;
  // under the filter there is no separate Routines group: a routine's chat sits where any chat would
  const sec = (r: Row & { folder?: string }) => { const f = sectionOf(r); return shell2 && f === ROUTINES ? UNGROUPED : f; };
  const baseGroups = sections(ordered.map((r) => ({ ...r, folder: sec(r) })), folders);
  // UI 7e (founder 2026-10-16): Pinned → RECENT (the newest chats across everything, so the last chat
  // is always the top row, filed or not) → the person's folders → the rest → Hidden.
  // shell2 + S4: under the Rooms filter, the rooms of two sit together under "People"
  const PEOPLE_SEC = "people-rooms";
  const pairRows = shell2 && railFilter === "rooms" ? railRows.filter((r) => isPairRoom(r.chatId, r.visibility) && !r.hidden) : [];
  const pairKeys = new Set(pairRows.map((r) => r.key));
  const groups0 = home2 ? (() => {
    const by = (id: string) => baseGroups.find((g) => g.folder.id === id);
    const recent = railRows.filter((r) => !r.hidden && sectionOf(r) !== HIDDEN).slice().sort((x, y) => (y.when || 0) - (x.when || 0)).slice(0, RECENT_CAP);
    const recentKeys = new Set(recent.map((r) => r.key));
    const rest = by(UNGROUPED);
    return [
      ...(by(PINNED) ? [by(PINNED)!] : []),
      { folder: { id: RECENT, name: "Recent", builtin: true, collapsed: false } as RailFolder, rows: recent },
      ...baseGroups.filter((g) => !g.folder.builtin && !g.folder.id.startsWith("ws:")),
      ...(rest ? [{ folder: { ...rest.folder, name: "Other chats" }, rows: rest.rows.filter((r) => !recentKeys.has(r.key)) }] : []),
      ...baseGroups.filter((g) => (g.folder.id === ROUTINES && !shell2) || g.folder.id === SHARED),
      ...(by(HIDDEN) ? [by(HIDDEN)!] : []),
    ];
  })() : baseGroups;
  const groups = pairRows.length
    ? [{ folder: { id: PEOPLE_SEC, name: "People", builtin: true, collapsed: false } as RailFolder, rows: pairRows },
      ...groups0.map((g) => ({ ...g, rows: g.rows.filter((r) => !pairKeys.has(r.key)) }))]
    : groups0;
  const section = ({ folder, rows }: { folder: RailFolder; rows: (Row & { folder?: string })[] }) => {
    // THE LAYOUT NEVER MOVES UNDER A DRAG: which sections are drawn does not depend on whether
    // something is being dragged, so the place a finger or pointer aims at stays where it was. Every
    // section is drawn, empty or not, except an empty Routines folder (made for everybody, useful
    // only to people with routines; Move to… still reaches it).
    if ((folder.id === ROUTINES || folder.id === SHARED) && rows.length === 0) return null;
    if (folder.id === RECENT && rows.length === 0) return null;
    // HOME2: a workspace group shows only when it holds something — or while a chat is dragged
    if (home2 && folder.id.startsWith("ws:") && rows.length === 0 && !drag) return null;
    const shut = isFolded(folder);
    const all = expanded[folder.id];
    const keep = rows.findIndex((r) => r.key === p.selKey);
    const shown = all || rows.length <= SECTION_CAP ? rows
      : rows.filter((_, i) => i < SECTION_CAP || i === keep);
    const more = rows.length - shown.length;
    const fOpen = folderMenu?.id === folder.id;
    const toggle = () => { const next = !shut; setFolded((m) => ({ ...m, [folder.id]: next })); p.onFolderCollapse?.(folder.id, next); };
    return (
      <section key={folder.id} className="vx-rail-section" data-section={folder.id} aria-label={sectionName(folder)}
        data-over={over === folder.id ? "" : undefined} {...(folder.id === RECENT || folder.id === "people-rooms" ? {} : dropProps({ folder: folder.id }))}>
        <div className="vx-rail-section-head" {...dragProps({ kind: "folder", key: folder.id }, !folder.builtin && !!p.onFolderMove)}>
          <button type="button" className="vx-rail-section-toggle" aria-expanded={!shut} onClick={toggle}>
            {shut ? <ChevronRight size={12} strokeWidth={1.75} aria-hidden /> : <ChevronDown size={12} strokeWidth={1.75} aria-hidden />}
            {home2 && folder.id.startsWith("ws:") && <WorkspaceMark id={folder.id.slice(3)} name={sectionName(folder)} size={16} />}
            <span className="vx-rail-section-name">{folder.id === UNGROUPED && home2 ? "Other chats" : sectionName(folder)}</span>
            <span className="vx-rail-section-count">{rows.length || ""}</span>
          </button>
          {!folder.builtin && (p.onFolderRename || p.onFolderDelete) && (
            <IconButton label={`Actions for folder ${folder.name}`} size="xs" aria-haspopup="menu" aria-expanded={fOpen}
              onClick={(e) => { e.stopPropagation(); if (fOpen) { setFolderMenu(null); return; } const b = e.currentTarget.getBoundingClientRect(); setFolderMenu({ id: folder.id, x: b.right - 188, y: b.bottom + 4 }); }}>
              <MoreHorizontal size={14} strokeWidth={1.75} />
            </IconButton>
          )}
          {fOpen && folderMenu && <ContextMenu x={folderMenu.x} y={folderMenu.y} label={`Actions for folder ${folder.name}`} onClose={() => setFolderMenu(null)}
            items={[
              ...(p.onFolderRename ? [{ id: "rename", label: "Rename…", letter: "R", onSelect: () => setNaming({ id: folder.id, name: folder.name }) }] : []),
              ...(p.onFolderDelete ? [{ id: "delete", label: "Delete folder…", danger: true, letter: "D", detail: "Its chats move to Recent", onSelect: () => setDissolving(folder) }] : []),
            ]} />}
        </div>
        {!shut && <div className="vx-rail-section-rows">
          {shown.map(chatRow)}
          {rows.length === 0 && !folder.builtin && (!home2 || drag) && <p className="vx-rail-section-empty">{home2 ? "Drop here" : "Drag chats here"}</p>}
          {(more > 0 || (all && rows.length > SECTION_CAP)) && (
            <button type="button" className="vx-rail-more" onClick={() => setExpanded((m) => ({ ...m, [folder.id]: !all }))}>
              {all ? "Show less" : `Show ${more} more`}
            </button>
          )}
        </div>}
      </section>
    );
  };

  const destinations = (rows: (Row & { folder?: string })[]) => {
    const from = new Set(rows.map(sectionOf));
    return folders.folders.filter((f) => !(from.size === 1 && from.has(f.id)));
  };
  /** The rows in the order they are drawn (open sections, "Show N more" respected) — Shift-click's range. */
  function drawnOrder(): string[] {
    return [...(navRef.current?.querySelectorAll<HTMLElement>("[data-row-key]") ?? [])].map((el) => el.dataset.rowKey as string);
  }
  const chatIds = (rows: Row[]) => rows.map((r) => r.chatId).filter((x): x is string => !!x);
  function bulkItems(): ContextMenuItem[] {
    const n = picked.length;
    const items: ContextMenuItem[] = [];
    if (canMove) items.push({ id: "move", label: `Move ${n} chats to…`, letter: "M", onSelect: () => setMoving(pickedRows) });
    if (p.onShareChats && sharingOn && chatIds(pickedRows).length) items.push({ id: "share", label: `Share ${n} chats…`, letter: "S", onSelect: () => { setShareTo(""); setSharing(pickedRows); } });
    if (chatIds(pickedRows).length) items.push({ id: "delete", label: `Delete ${chatIds(pickedRows).length} chats…`, danger: true, letter: "D", onSelect: () => setBulkDeleting(pickedRows) });
    return items;
  }
  useEffect(() => {
    if (!picked.length && !picking) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || document.querySelector("[aria-modal=true]")) return;
      clearPicked();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <nav ref={navRef} className="vx-pane vx-rail" data-pane="rail" data-dragging={drag ? "" : undefined} style={{ gridRow: "1 / 3", gridColumn: 1 }} aria-label="Chats">
      {shell2 ? (
        // SHELL2: no header row — the list starts with + New · Inbox · Meetings, then the filter
        <><div className="vx-s2-top"><SidebarNav onNew={p.onHome!} onNavigate={p.onNavigate} /><VoiceNoteLive /></div>
          <div className="vx-s2-filter" data-rail-filter="">
            <Tabs label="Show chats" value={railFilter} onChange={setRailFilter} items={[
              { key: "all", label: <>All<span className="vx-s2-tabcount">{chatRows.filter((r) => !r.hidden).length}</span></> },
              { key: "rooms", label: <>Rooms<span className="vx-s2-tabcount">{chatRows.filter((r) => r.shared && !r.hidden).length}</span></> },
              { key: "routines", label: <>Routines<span className="vx-s2-tabcount">{chatRows.filter((r) => r.routine && !r.hidden).length + (p.routineDrafts?.length ?? 0)}</span></> }]} />
          </div></>
      ) : home2 && p.onHome ? (
        // UI 7e (founder 2026-10-16): ONE entry in the header — "+ New", which is home (a fresh chat
        // whose empty view is the home). Notifications live in the home's "Needs you"; rooms are
        // reached from their workspace.
        <div className="vx-panelhead vx-rail-head" data-inset="rail">
          <span className="vx-panelhead-title vx-rail-title">Chats</span>
          <span className="vx-panelhead-gap" />
          <VoiceNoteLive />
          {/* + NEW is a small menu (founder 2026-10-16: "I do not understand how to create a room") */}
          <span className="vx-rail-new-wrap" data-rail-act="home"><Menu label="New" align="end" variant="ghost"
            trigger={<span className="vx-rail-new"><Plus size={14} strokeWidth={2} aria-hidden />New</span>}
            items={[
              { key: "chat", label: "New chat", hint: shortcutLabel(RAIL_SHORTCUTS.newChat), onSelect: p.onHome! },
              ...(p.onNewRoom ? [{ key: "room", label: "New room", onSelect: p.onNewRoom }] : []),
              ...(p.onNewWorkspace ? [{ key: "workspace", label: "New workspace", onSelect: p.onNewWorkspace }] : []),
            ]} /></span>
        </div>
      ) : (
      <div className="vx-panelhead vx-rail-head" data-inset="rail">
        <span className="vx-panelhead-title vx-rail-title">Chats</span>
        <span className="vx-panelhead-gap" />
        <VoiceNoteLive />
        <NotificationBell />
        {p.onFolderCreate && !home2 && <IconButton label="New folder" onClick={() => setNaming({ id: null, name: "" })}><FolderPlus size={16} strokeWidth={1.75} /></IconButton>}
        {p.onBrowseRooms && <IconButton label="Browse open rooms" data-rail-act="browse-rooms" onClick={p.onBrowseRooms}><Compass size={16} strokeWidth={1.75} /></IconButton>}
        {p.onNewRoom && <IconButton label="New room" data-rail-act="new-room" onClick={p.onNewRoom}><Hash size={16} strokeWidth={1.75} /></IconButton>}
        <IconButton label="New chat" title={`New chat (${shortcutLabel(RAIL_SHORTCUTS.newChat)})`} aria-keyshortcuts={shortcutAria(RAIL_SHORTCUTS.newChat)} onClick={p.onNewChat}><Plus size={16} strokeWidth={1.75} /></IconButton>
        {p.onCollapse && <CollapseButton side="left" onClick={p.onCollapse} label={p.collapseLabel} />}
      </div>)}

      {vn.phase === "error" && vn.error && <div role="alert" className="vx-rail-note" data-tone="danger">{vn.error}</div>}
      {vn.phase === "recording" && vn.warning && <div role="status" className="vx-rail-note" data-tone="danger">{vn.warning}</div>}
      {vn.phase === "stopped" && vn.error && <div role="status" className="vx-rail-note">{vn.error}</div>}
      {(picked.length > 0 || picking) && (
        <div className="vx-rail-bulk" role="toolbar" aria-label="Selected chats">
          <span className="vx-rail-bulk-count" aria-live="polite">{picked.length} selected</span>
          <span className="vx-panelhead-gap" />
          {/* ICONS THAT FIT THE RAIL (founder: the bar overflowed): each names itself in its tooltip
              and to assistive tech; Delete is neutral — its confirmation carries the weight. */}
          {bulkItems().map((it) => {
            const name = it.id === "move" ? "Move to…" : it.id === "share" ? "Share…" : "Delete…";
            const Glyph = it.id === "move" ? FolderInput : it.id === "share" ? UserPlus : Trash2;
            return <IconButton key={it.id} label={name} title={name} size="xs" data-bulk={it.id} disabled={!picked.length}
              onClick={() => void it.onSelect()}><Glyph size={14} strokeWidth={1.75} /></IconButton>;
          })}
          <IconButton label="Clear selection" size="xs" onClick={clearPicked}><X size={14} strokeWidth={1.75} /></IconButton>
        </div>
      )}
      <div className="vx-rail-list">
        {shell2 && railFilter === "routines" && !!p.routineDrafts?.length && (
          <section className="vx-rail-section" data-section="routine-drafts" aria-label="Drafts — waiting for you to turn them on">
            <div className="vx-rail-section-head"><span className="vx-rail-section-toggle">
              <span className="vx-rail-section-name">Drafts</span>
              <span className="vx-rail-section-count">{p.routineDrafts.length}</span></span></div>
            <div className="vx-rail-section-rows">
              {p.routineDrafts.map((d) => {
                const home = d.thread ? p.rows.find((r) => r.chatId === d.thread) : undefined;
                return <DraftRow key={d.name} d={d} chat={home ? displayLabel(home) : "its own chat"}
                  onTurnOn={p.onTurnOnRoutine} onOpen={p.onOpenThread} />;
              })}
            </div>
          </section>
        )}
        {groups.map(section)}
        {home2 && p.onFolderCreate && <button type="button" className="vx-rail-more" aria-label="New folder" onClick={() => setNaming({ id: null, name: "" })}>+ Folder</button>}
        {p.rows.length === 0 && <p className="vx-empty-text vx-rail-empty">Meetings arrive by invitation; “+” starts a chat.</p>}
      </div>

      {/* the person, under the list of rooms — identity, theme and the way out are properties of
          WHO is here, not of which chat is in front, so they sit at the foot of the column and
          fold away with it. */}
      <AccountBadge />
      {/* MOVE TO… — the keyboard's and the phone's way to file a chat, one button per section. */}
      <Dialog open={!!moving} onClose={() => setMoving(null)} title={moving && moving.length > 1 ? `Move ${moving.length} chats to…` : `Move “${moving?.[0]?.label ?? ""}” to…`} width="confirm"
        footer={<Button variant="ghost" onClick={() => setMoving(null)}>Cancel</Button>}>
        <div className="vx-rail-dest" role="group" aria-label="Sections">
          {moving && destinations(moving).map((f) => (
            <Button key={f.id} variant="ghost" data-move-to={f.id} onClick={() => { const k = moving.map((r) => r.key); setMoving(null); move(k, f.id); if (k.length > 1) clearPicked(); }}>{sectionName(f)}</Button>
          ))}
          {moving && p.onFolderCreate && <Button variant="ghost" data-move-to="new" onClick={() => { const k = moving.map((r) => r.key); setMoving(null); setNaming({ id: null, name: "", then: k }); if (k.length > 1) clearPicked(); }}>New folder…</Button>}
        </div>
      </Dialog>
      <Dialog open={!!naming} onClose={() => setNaming(null)} title={naming?.id ? "Rename folder" : "New folder"} width="confirm"
        footer={<>
          <Button variant="ghost" onClick={() => setNaming(null)}>Cancel</Button>
          <Button data-folder-save disabled={!naming?.name.trim()} onClick={() => {
            const n = naming; setNaming(null);
            if (!n || !n.name.trim()) return;
            if (n.id) p.onFolderRename?.(n.id, n.name.trim()); else p.onFolderCreate?.(n.name.trim(), n.then);
          }}>{naming?.id ? "Rename" : "Create"}</Button>
        </>}>
        <form onSubmit={(e) => { e.preventDefault(); const n = naming; if (!n?.name.trim()) return; setNaming(null); if (n.id) p.onFolderRename?.(n.id, n.name.trim()); else p.onFolderCreate?.(n.name.trim(), n.then); }}>
          <Input label="Folder name" value={naming?.name ?? ""} maxLength={60} autoFocus data-autofocus=""
            onChange={(e) => setNaming((n) => n ? { ...n, name: e.target.value } : n)} />
        </form>
      </Dialog>
      <Dialog open={!!sharing} onClose={() => setSharing(null)} title={`Share ${sharing?.length ?? 0} chats`} width="confirm"
        footer={<>
          <Button variant="ghost" onClick={() => setSharing(null)}>Cancel</Button>
          <Button data-share-send disabled={!/^\S+@\S+\.\S+$/.test(shareTo.trim())} onClick={() => {
            const rows = sharing; setSharing(null); if (rows) { p.onShareChats?.(rows, shareTo.trim()); clearPicked(); }
          }}>Share</Button>
        </>}>
        <form onSubmit={(e) => { e.preventDefault(); const rows = sharing; if (!rows || !/^\S+@\S+\.\S+$/.test(shareTo.trim())) return; setSharing(null); p.onShareChats?.(rows, shareTo.trim()); clearPicked(); }}>
          <Input label="Their email" type="email" value={shareTo} autoFocus data-autofocus="" onChange={(e) => setShareTo(e.target.value)}
            hint="They are added to each chat and see it on their own rail. A private chat becomes a new shared chat." />
        </form>
      </Dialog>
      <ConfirmDialog open={!!bulkDeleting} title={`Delete ${chatIds(bulkDeleting ?? []).length} chats?`} confirmLabel={`Delete ${chatIds(bulkDeleting ?? []).length} chats`}
        consequence={<>They and their conversations are deleted for good, on every device. Pages they wrote into a workspace stay; a routine keeps running into a new chat.</>}
        onCancel={() => setBulkDeleting(null)}
        onConfirm={() => {
          const ids = chatIds(bulkDeleting ?? []); setBulkDeleting(null); clearPicked();
          if (p.onDeleteChats) p.onDeleteChats(ids); else ids.forEach((id) => p.onDeleteChat(id, { retireRoutine: false }));
        }} />
      <ConfirmDialog open={!!dissolving} title={`Delete the folder “${dissolving?.name ?? ""}”?`} confirmLabel="Delete folder" focusConfirm
        consequence={<>Its chats move to Recent. No chat is deleted.</>}
        onCancel={() => setDissolving(null)}
        onConfirm={() => { const f = dissolving; setDissolving(null); if (f) p.onFolderDelete?.(f.id); }} />
      {/* A ROUTINE'S THREAD ASKS ABOUT THE ROUTINE (ADR-0046): one "Delete chat" would either leave
          the routine running into a chat that is gone or kill it silently. Its delete is two named
          acts instead, and neither is the default a stray Enter answers — focus starts on keeping
          the routine, the smaller of the two. */}
      <Dialog open={!!deleting?.routine} onClose={() => setDeleting(null)} title="Delete this routine's chat?" width="confirm"
        footer={<>
          <Button variant="ghost" onClick={() => setDeleting(null)}>Cancel</Button>
          <Button data-routine-keep data-autofocus="" onClick={() => { const id = deleting?.chatId; setDeleting(null); if (id) p.onDeleteChat(id, { retireRoutine: false }); }}>Delete chat, keep routine</Button>
          <Button variant="danger" data-routine-retire onClick={() => { const id = deleting?.chatId; setDeleting(null); if (id) p.onDeleteChat(id, { retireRoutine: true }); }}>Delete chat and retire routine</Button>
        </>}>
        <div className="vx-dialog-text">“{deleting?.label}” is the chat of the routine <strong>{deleting?.routine?.flow}</strong>. The chat and its conversation are deleted for good either way; the routine keeps running into a new chat unless it is retired too.</div>
      </Dialog>
      <ConfirmDialog open={!!deleting && !deleting.routine} title="Delete this chat?" confirmLabel="Delete chat" focusConfirm
        consequence={<>“{deleting?.label}” and its conversation are deleted for good, on every device. Pages it wrote into a workspace stay.{deleting?.meetingId ? " The meeting itself is not deleted; its row moves to Hidden." : ""}</>}
        onCancel={() => setDeleting(null)}
        onConfirm={() => { const id = deleting?.chatId; setDeleting(null); if (id) p.onDeleteChat(id); }} />
    </nav>
  );
}

/** THE RAIL, FOLDED (guidelines §3.1): a 48px icon strip — open the chat list, start a chat. Where
 *  the rail cannot dock (compact and narrow windows) the first opens it as a drawer over the
 *  conversation; where it can, it docks it again. Either way the reader's own choice is untouched. */
export function RailStrip({ open, onOpen, onNewChat, extra }: { open: boolean; onOpen: () => void; onNewChat: () => void;
  /** shell2: the strip's own Inbox and Meetings, so a narrow window still reaches them in one click */
  extra?: React.ReactNode }) {
  return (
    <nav className="vx-rail-strip" data-pane="rail-strip" aria-label="Chats">
      <IconButton label="Show the chat list" size="md" aria-expanded={open} data-expand="left" onClick={onOpen}>
        <PanelLeft size={16} strokeWidth={1.75} />
      </IconButton>
      <IconButton label="New chat" size="md" onClick={onNewChat}>
        <Plus size={16} strokeWidth={1.75} />
      </IconButton>
      {extra}
    </nav>
  );
}
