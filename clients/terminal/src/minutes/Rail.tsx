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
import type { CSSProperties } from "react";
import type { Row } from "./chats";
import { CollapseButton } from "./Collapse";
import { T, row, surface, type as ty } from "./tokens";

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

export function Rail(p: {
  rows: Row[]; hidden: number;
  all: boolean; onAll: (v: boolean) => void;
  selKey: string | null; onSelect: (r: Row) => void;
  onNewChat: () => void; onDeleteChat: (chatId: string) => void;
  onCollapse?: () => void;
}) {
  const chatRow = (r: Row) => {
    const on = r.key === p.selKey;
    return (
      <div key={r.key} style={{ position: "relative", display: "flex" }}
        onMouseEnter={(e) => { const x = e.currentTarget.querySelector("[data-del]") as HTMLElement | null; if (x) x.style.opacity = "1"; }}
        onMouseLeave={(e) => { const x = e.currentTarget.querySelector("[data-del]") as HTMLElement | null; if (x) x.style.opacity = "0"; }}>
        <button data-chat-row style={{ ...chatRowS(on), paddingRight: r.chatId ? 22 : 9 }} onClick={() => p.onSelect(r)}>
          {r.live
            ? <span style={liveDot} aria-hidden />
            : <span style={{ ...row.dot(on), alignSelf: "center", background: r.meetingId ? "var(--line2)" : "transparent", border: r.meetingId ? "none" : "1px solid var(--line2)" }} aria-hidden />}
          <span style={{ flex: 1, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", ...(on ? ty.bodyStrong : ty.body) }}>{r.label}</span>
          <span style={{ ...ty.meta, flex: "none", fontVariantNumeric: "tabular-nums", color: r.live ? "var(--accent)" : "var(--t3)" }}>{r.whenLabel}</span>
        </button>
        {r.chatId && (
          <button data-del aria-label={`Delete ${r.label}`} title="Delete chat" onClick={(e) => { e.stopPropagation(); p.onDeleteChat(r.chatId as string); }}
            style={{ position: "absolute", right: 3, top: "50%", transform: "translateY(-50%)", opacity: 0, transition: "opacity .12s", background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 13, lineHeight: 1, padding: "2px 4px", fontFamily: "var(--sans)" }}>×</button>
        )}
      </div>
    );
  };

  return (
    <nav style={{ gridRow: "1 / 3", gridColumn: 1, borderRight: "1px solid var(--line)", background: surface.rail, display: "flex", flexDirection: "column", minHeight: 0 }} aria-label="Chats">
      <div style={{ height: T.headerH, flex: "none", display: "flex", alignItems: "center", gap: 8, padding: "0 10px", borderBottom: "1px solid var(--line)" }}>
        <span style={{ ...ty.title, flex: 1, minWidth: 0 }}>Chats</span>
        <button aria-pressed={p.all} style={chipS(p.all)} onClick={() => p.onAll(!p.all)}
          title={p.all ? "Showing every chat, including ones nothing has been said in" : "Showing chats you have written in, plus live and upcoming meetings"}>
          All{!p.all && p.hidden > 0 ? ` ${p.hidden}` : ""}
        </button>
        <button title="New chat" aria-label="New chat" onClick={p.onNewChat}
          style={{ ...row.ghostPlus, marginLeft: 0, fontSize: 17, color: "var(--t2)" }}>+</button>
        {p.onCollapse && <CollapseButton side="left" onClick={p.onCollapse} />}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "10px", display: "flex", flexDirection: "column", gap: 2 }}>
        {p.rows.map(chatRow)}
        {p.rows.length === 0 && <div style={{ ...ty.chip, padding: "2px 8px", color: "var(--t3)", lineHeight: 1.5 }}>Meetings arrive by invitation; “+” starts a chat.</div>}
      </div>

      <div style={{ ...ty.meta, flex: "none", padding: "10px", lineHeight: 1.55, borderTop: "1px solid var(--line)" }}>
        Every chat, newest first. A meeting is a chat too — opening it opens the meeting.
      </div>
    </nav>
  );
}
