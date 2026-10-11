"use client";
/** The rail: ONE flat list of CHATS, and nothing else.
 *
 *  No Meetings/Projects switcher, no Live/Upcoming/Past buckets — a meeting is a chat with a meeting
 *  ref, and opening it opens the meeting layout. Order is recency; the only structure is a single
 *  filter chip, because auto-created chats (email deeplinks, `?ask=` presets, flows) can arrive
 *  faster than anyone reads them.
 *
 *  No workspace chrome either (founder ruling: "remove workspaces, they can do that via MCP if they
 *  need"). A chat still carries the workspaces it is over — that is data on the chat, and the header
 *  shows the mount set — but creating, inviting to, resetting and deleting a folder is a job for the
 *  MCP verbs and the conversation, not for a column of × buttons beside the reading list. */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { useChatActive } from "../surfaces/chatActivity";
import type { Row } from "./chats";
import { AccountBadge } from "./AccountBadge";
import { CollapseButton } from "./Collapse";
import { T, row, surface, type as ty } from "./tokens";
import { WorkspaceName } from "../ui-kit/WsLink";
import { ContextMenu, type ContextMenuItem } from "../ui-kit/ContextMenu";
import { MoreHorizontal, Pin } from "lucide-react";

const chipS = (on: boolean): CSSProperties => ({
  ...ty.control, fontSize: 11.5, color: on ? "var(--t1)" : "var(--t3)",
  background: on ? surface.raisedHi : surface.raised, border: "1px solid var(--line)",
  borderRadius: 999, padding: "3px 10px", cursor: "pointer", flex: "none", lineHeight: 1.35,
});
const chatRowS = (on: boolean): CSSProperties => ({
  fontFamily: "var(--sans)", display: "flex", alignItems: "baseline", gap: 8, padding: "6px 9px",
  borderRadius: 8, color: on ? "var(--t1)" : "var(--t2)", cursor: "pointer",
  background: on ? surface.raised : "transparent", border: "none",
  borderLeft: `2px solid ${on ? "var(--accent)" : "transparent"}`, width: "100%", textAlign: "left",
});
const liveDot: CSSProperties = { width: 6, height: 6, borderRadius: "50%", flex: "none", background: "var(--accent)", alignSelf: "center" };
/** THE MEETING'S STATUS, ON THE ROW (Vexa-ai/vexa#1597) — *"just attach the status to it"*.
 *
 *  Rendered for `held` only, and that is not half a job: `live` is already on the row twice over —
 *  the accent dot and the accent word where the time goes — so a third "live" beside them would be
 *  noise in 248px. `held` had no rendering at all, which is why a chat that owns a finished meeting
 *  read as an ordinary conversation. `Row.status` names both states so a test, and anything else
 *  that ever asks, reads ONE field rather than reassembling it from a dot and a label. */
const statusTag: CSSProperties = {
  ...ty.meta, flex: "none", color: "var(--t3)", background: surface.raised,
  border: "1px solid var(--line)", borderRadius: 4, padding: "0 4px", lineHeight: 1.5,
};
/** THE TARGET WORKSPACE'S NAME on a row that has one (Vexa-ai/vexa#1611). The accent the header's
 *  target chip wears, so the two surfaces say the same thing in the same colour; capped in width
 *  because the rail is 248px and a long workspace name must not push the row's own name out. */
const targetTag: CSSProperties = {
  ...ty.meta, flex: "none", color: "var(--accent)", background: "var(--accentbg)",
  border: "1px solid var(--accent)", borderRadius: 4, padding: "0 4px", lineHeight: 1.5,
  maxWidth: 96, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
};
/** The row's one trailing control — the actions menu, or the armed delete's ✓. */
const rowActS = (armed: boolean): CSSProperties => ({
  position: "absolute", right: 3, top: "50%", transform: "translateY(-50%)", background: "transparent",
  border: "none", color: armed ? "var(--red, #e57373)" : "var(--t3)", cursor: "pointer", fontSize: 13,
  lineHeight: 1, padding: "3px 4px", fontFamily: "var(--sans)", display: "flex", alignItems: "center", borderRadius: 4,
});

function ActivityDot({ r, selected }: { r: Row; selected: boolean }) {
  const active = useChatActive(r.chatId);
  if (active) return <span data-row-activity="active" role="img" aria-label="Agent is working" title="Agent is working" style={{ ...liveDot, background: "var(--blue)" }} />;
  return r.live ? <span style={liveDot} aria-hidden /> : <span style={{ ...row.dot(selected), alignSelf: "center", background: r.meetingId ? "var(--line2)" : "transparent", border: r.meetingId ? "none" : "1px solid var(--line2)" }} aria-hidden />;
}

export function Rail(p: {
  rows: Row[]; hidden: number;
  all: boolean; onAll: (v: boolean) => void;
  selKey: string | null; onSelect: (r: Row) => void;
  onNewChat: () => void; onDeleteChat: (chatId: string) => void;
  onCollapse?: () => void;
  /** drag one PINNED row onto another: unpinned rows are placed by recency, not by hand */
  onMove?: (from: string, to: string) => void;
  /** pin/unpin and hide/unhide — the person's own marks, saved on the server */
  onPin?: (r: Row, on: boolean) => void;
  onHide?: (r: Row, on: boolean) => void;
  /** the Hidden view: only hidden rows, each with Unhide; `hiddenMarked` is how many there are */
  hiddenView?: boolean; onHiddenView?: (v: boolean) => void; hiddenMarked?: number;
}) {
  const [deleting, setDeleting] = useState<Row | null>(null);
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const menuButtons = useRef(new Map<string, HTMLButtonElement>());
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!deleting) return;
    const outside = (e: PointerEvent) => {
      if (e.target instanceof Node && !confirmRef.current?.contains(e.target)) setDeleting(null);
    };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setDeleting(null); };
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside, true);
      document.removeEventListener("keydown", escape);
    };
  }, [deleting]);
  const [dragging, setDragging] = useState<string | null>(null);
  const closeMenu = (key: string, refocus = true) => {
    setMenu(null);
    if (refocus) menuButtons.current.get(key)?.focus();
  };
  const itemsFor = (r: Row): ContextMenuItem[] => {
    const items: ContextMenuItem[] = [];
    if (!r.hidden && p.onPin) items.push({ id: "pin", label: r.pinned ? "Unpin" : "Pin to top", onSelect: () => p.onPin?.(r, !r.pinned) });
    if (p.onHide) items.push({ id: "hide", label: r.hidden ? "Unhide" : "Hide", detail: r.hidden ? undefined : "Bring it back from Hidden", onSelect: () => p.onHide?.(r, !r.hidden) });
    if (r.chatId) items.push({ id: "delete", label: "Delete…", danger: true, onSelect: () => setDeleting(r) });
    return items;
  };
  const chatRow = (r: Row) => {
    const on = r.key === p.selKey;
    const armed = deleting?.key === r.key;
    const open = menu?.key === r.key;
    // ONLY A PINNED ROW IS DRAGGED, and only onto another pinned row: every other row's place is its
    // recency, so a drop there would be a promise the list does not keep.
    const canDrag = !!p.onMove && !!r.pinned && !r.hidden;
    return (
      <div key={r.key} className="vx-row" data-row-key={r.key} draggable={canDrag} onDragStart={e => { if (!canDrag) return; setDeleting(null); setDragging(r.key); e.dataTransfer.setData("text/plain", r.key); e.dataTransfer.effectAllowed="move"; }}
        onDragEnd={()=>setDragging(null)} onDragOver={e=>{if(dragging&&r.pinned){e.preventDefault();e.dataTransfer.dropEffect="move";}}}
        onDrop={e=>{e.preventDefault();if(dragging&&r.pinned)p.onMove?.(dragging,r.key);setDragging(null);}}
        style={{ position: "relative", display: "flex" }}>
        <button data-chat-row style={{ ...chatRowS(on), paddingRight: 26 }} onClick={() => { setDeleting(null); p.onSelect(r); }}>
          <ActivityDot r={r} selected={on} />
          <span style={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", ...(on ? ty.bodyStrong : ty.body) }}>{r.label}</span>
          {/* WHERE THIS CHAT WRITES, WHEN IT IS NOT THE DESK (Vexa-ai/vexa#1611). By NAME, from the
              registry (#1585/#1602), never the slug. Rendered only for a chat working somewhere
              else, on the same argument `IMPLICIT_MOUNTS` makes about `_global` and the ContextBar
              makes about a constant: the desk is where nearly every chat writes, so a tag saying so
              on nearly every row is chrome. A row that is NOT the ordinary case is the information
              — and it is the case the founder lost a morning's files to. */}
          {r.target && <span data-row-target={r.target} style={targetTag}><WorkspaceName slug={r.target} /></span>}
          {r.status === "held" && <span data-row-status="held" style={statusTag}>held</span>}
          {r.pinned && <Pin data-row-pinned role="img" aria-label="Pinned" size={12} strokeWidth={1.75} style={{ flex: "none", alignSelf: "center", color: "var(--t3)" }} />}
          <span style={{ ...ty.meta, flex: "none", fontVariantNumeric: "tabular-nums", color: r.live ? "var(--accent)" : "var(--t3)" }}>{r.whenLabel}</span>
        </button>
        {armed ? (
          <button data-del data-armed className="vx-row-act" ref={confirmRef} autoFocus
            aria-label={`Confirm delete ${r.label}`} title="Click again to delete chat"
            onBlur={() => setDeleting(null)}
            onClick={(e) => { e.stopPropagation(); setDeleting(null); p.onDeleteChat(r.chatId as string); }}
            style={rowActS(true)}>✓</button>
        ) : (
          <button className="vx-row-act" ref={(el) => { if (el) menuButtons.current.set(r.key, el); else menuButtons.current.delete(r.key); }}
            aria-label={`Actions for ${r.label}`} title="Pin, hide or delete" aria-haspopup="menu" aria-expanded={open}
            onClick={(e) => {
              e.stopPropagation(); setDeleting(null);
              if (open) { closeMenu(r.key, false); return; }
              const b = e.currentTarget.getBoundingClientRect();
              setMenu({ key: r.key, x: b.right - 188, y: b.bottom + 4 });
            }}
            style={rowActS(false)}><MoreHorizontal size={14} strokeWidth={1.75} aria-hidden /></button>
        )}
        {open && menu && <ContextMenu x={menu.x} y={menu.y} label={`Actions for ${r.label}`} items={itemsFor(r)} onClose={() => closeMenu(r.key)} />}
      </div>
    );
  };

  return (
    <nav style={{ gridRow: "1 / 3", gridColumn: 1, borderRight: "1px solid var(--line)", background: surface.rail, display: "flex", flexDirection: "column", minHeight: 0 }} aria-label="Chats">
      <div style={{ height: T.headerH, flex: "none", display: "flex", alignItems: "center", gap: 8, padding: "0 10px", borderBottom: "1px solid var(--line)" }}>
        <span style={{ ...ty.title, flex: 1, minWidth: 0 }}>{p.hiddenView ? "Hidden" : "Chats"}</span>
        {!p.hiddenView && <button aria-pressed={p.all} style={chipS(p.all)} onClick={() => p.onAll(!p.all)}
          title={p.all ? "Showing every chat, including ones nothing has been said in" : "Showing chats you have written in, plus live and upcoming meetings"}>
          All{!p.all && p.hidden > 0 ? ` ${p.hidden}` : ""}
        </button>}
        {p.onHiddenView && (p.hiddenView || (p.hiddenMarked ?? 0) > 0) && (
          <button aria-pressed={!!p.hiddenView} style={chipS(!!p.hiddenView)} onClick={() => p.onHiddenView?.(!p.hiddenView)}
            title={p.hiddenView ? "Back to your chats" : "Show the chats and meetings you hid"}>
            {p.hiddenView ? "Done" : `Hidden ${p.hiddenMarked}`}
          </button>
        )}
        <button title="New chat" aria-label="New chat" onClick={p.onNewChat}
          style={{ ...row.ghostPlus, marginLeft: 0, fontSize: 17, color: "var(--t2)" }}>+</button>
        {p.onCollapse && <CollapseButton side="left" onClick={p.onCollapse} />}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "10px", display: "flex", flexDirection: "column", gap: 2 }}>
        {p.rows.map(chatRow)}
        {p.rows.length === 0 && <div style={{ ...ty.chip, padding: "2px 8px", color: "var(--t3)", lineHeight: 1.5 }}>{p.hiddenView ? "Nothing is hidden." : "Meetings arrive by invitation; “+” starts a chat."}</div>}
      </div>

      {/* the person, under the list of rooms — identity, theme and the way out are properties of
          WHO is here, not of which chat is in front, so they sit at the foot of the column and
          fold away with it. */}
      <AccountBadge />
    </nav>
  );
}
