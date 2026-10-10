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
import { useState } from "react";
import { useChatActive } from "../surfaces/chatActivity";
import type { Row } from "./chats";
import { AccountBadge } from "./AccountBadge";
import { CollapseButton } from "./Collapse";
import { PanelLeft, Plus, X } from "lucide-react";
import { Badge, ConfirmDialog, IconButton, Tabs } from "../ui-kit";
import { WorkspaceName } from "../ui-kit/WsLink";

/* THE ROW'S LOOK IS THE ui-kit ROW's (guidelines §4.8): 28px, selected = a fill, weight 500 and a
 * 2px accent bar inside the radius, actions revealed on hover and focus-within without reflowing
 * the row. Status is a Badge ("Held"), the write target a muted Tag, and LIVE is the success
 * colour with its word — accent is reserved for "this is the one". */
function ActivityDot({ r }: { r: Row }) {
  const active = useChatActive(r.chatId);
  if (active) return <span data-row-activity="active" role="img" aria-label="Agent is working" title="Agent is working" className="vx-dot vx-rail-dot" data-tone="info" />;
  return <span className="vx-dot vx-rail-dot" data-tone={r.live ? "success" : undefined} data-kind={r.meetingId ? "meeting" : "chat"} aria-hidden />;
}

export function Rail(p: {
  rows: Row[]; hidden: number;
  all: boolean; onAll: (v: boolean) => void;
  selKey: string | null; onSelect: (r: Row) => void;
  onNewChat: () => void; onDeleteChat: (chatId: string) => void;
  onCollapse?: () => void;
  /** The fold control's name when folding really means closing the drawer. */
  collapseLabel?: string;
  onMove?: (from: string, to: string) => void;
}) {
  // DELETE ASKS IN A DIALOG (guidelines §4.11, S7): the row's "×" opens a ConfirmDialog whose
  // button names the act. It replaces the two-click × → ✓ that armed a control in place.
  const [deleting, setDeleting] = useState<Row | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const chatRow = (r: Row) => {
    const on = r.key === p.selKey;
    return (
      <div key={r.key} className="vx-row2" data-selected={on ? "" : undefined}
        draggable={!!p.onMove} onDragStart={e => { setDragging(r.key); e.dataTransfer.setData("text/plain", r.key); e.dataTransfer.effectAllowed="move"; }}
        onDragEnd={()=>setDragging(null)} onDragOver={e=>{if(dragging){e.preventDefault();e.dataTransfer.dropEffect="move";}}}
        onDrop={e=>{e.preventDefault();if(dragging)p.onMove?.(dragging,r.key);setDragging(null);}}>
        <button data-chat-row className="vx-row2-main" aria-current={on || undefined} onClick={() => p.onSelect(r)}>
          <ActivityDot r={r} />
          <span className="vx-row2-title">{r.label}</span>
          {/* WHERE THIS CHAT WRITES, WHEN IT IS NOT THE DESK (Vexa-ai/vexa#1611). By NAME, from the
              registry (#1585/#1602), never the slug. Rendered only for a chat working somewhere
              else, on the same argument `IMPLICIT_MOUNTS` makes about `_global` and the ContextBar
              makes about a constant: the desk is where nearly every chat writes, so a tag saying so
              on nearly every row is chrome. A row that is NOT the ordinary case is the information
              — and it is the case the founder lost a morning's files to. */}
          {r.target && <span data-row-target={r.target} className="vx-tag vx-rail-target"><WorkspaceName slug={r.target} /></span>}
          {r.status === "held" && <span data-row-status="held"><Badge>Held</Badge></span>}
          <span className="vx-row2-meta" data-live={r.live ? "" : undefined}>{r.whenLabel}</span>
        </button>
        {r.chatId && (
          <span className="vx-row2-actions">
            <IconButton data-del label={`Delete ${r.label}`} size="xs" onClick={(e) => { e.stopPropagation(); setDeleting(r); }}>
              <X size={14} strokeWidth={1.75} />
            </IconButton>
          </span>
        )}
      </div>
    );
  };

  return (
    <nav className="vx-pane vx-rail" data-pane="rail" style={{ gridRow: "1 / 3", gridColumn: 1 }} aria-label="Chats">
      {/* THE HEADER IS THE FILTER (guidelines §4.6): "Active" (chats written in, plus live and
          upcoming meetings) and "All" as underline tabs, in place of a bare "All N" pill; the count
          of chats "All" adds rides on its tab. */}
      <div className="vx-panelhead vx-rail-head" data-inset="rail">
        <span className="vx-rail-tabs">
          <Tabs label="Which chats" value={p.all ? "all" : "active"} onChange={(k) => p.onAll(k === "all")}
            items={[{ key: "active", label: "Active" }, { key: "all", label: !p.all && p.hidden > 0 ? `All · ${p.hidden}` : "All" }]} />
        </span>
        <span className="vx-panelhead-gap" />
        <IconButton label="New chat" onClick={p.onNewChat}><Plus size={16} strokeWidth={1.75} /></IconButton>
        {p.onCollapse && <CollapseButton side="left" onClick={p.onCollapse} label={p.collapseLabel} />}
      </div>

      <div className="vx-rail-list">
        {p.rows.map(chatRow)}
        {p.rows.length === 0 && <p className="vx-empty-text vx-rail-empty">Meetings arrive by invitation; “+” starts a chat.</p>}
      </div>

      {/* the person, under the list of rooms — identity, theme and the way out are properties of
          WHO is here, not of which chat is in front, so they sit at the foot of the column and
          fold away with it. */}
      <AccountBadge />
      <ConfirmDialog open={!!deleting} title="Delete this chat?" confirmLabel="Delete chat"
        consequence={<>“{deleting?.label}” and its conversation are removed from your chats.</>}
        onCancel={() => setDeleting(null)}
        onConfirm={() => { const id = deleting?.chatId; setDeleting(null); if (id) p.onDeleteChat(id); }} />
    </nav>
  );
}

/** THE RAIL, FOLDED (guidelines §3.1): a 48px icon strip — open the chat list, start a chat. Where
 *  the rail cannot dock (compact and narrow windows) the first opens it as a drawer over the
 *  conversation; where it can, it docks it again. Either way the reader's own choice is untouched. */
export function RailStrip({ open, onOpen, onNewChat }: { open: boolean; onOpen: () => void; onNewChat: () => void }) {
  return (
    <nav className="vx-rail-strip" data-pane="rail-strip" aria-label="Chats">
      <IconButton label="Show the chat list" size="md" aria-expanded={open} data-expand="left" onClick={onOpen}>
        <PanelLeft size={16} strokeWidth={1.75} />
      </IconButton>
      <IconButton label="New chat" size="md" onClick={onNewChat}>
        <Plus size={16} strokeWidth={1.75} />
      </IconButton>
    </nav>
  );
}
