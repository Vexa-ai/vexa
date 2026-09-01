"use client";
/** The rail: ONE flat list of CHATS. No Meetings/Projects switcher, no Live/Upcoming/Past buckets —
 *  a meeting is a chat with a meeting ref, and opening it opens the meeting layout. Order is
 *  recency; the only structure is a single filter chip, because auto-created chats (email
 *  deeplinks, `?ask=` presets, flows) can arrive faster than anyone reads them.
 *
 *  Beneath the list sits the WORKSPACE inventory — the shared folders themselves. That is not a
 *  chat and never was: projects died, workspaces did not. */
import type { CSSProperties } from "react";
import type { Membership } from "../surfaces/workspaceApi";
import type { Row } from "./chats";
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
const lensRow: CSSProperties = { ...ty.lens, display: "flex", alignItems: "center", padding: `2px ${T.rowPadX}px 4px` };
const wsRow: CSSProperties = { ...ty.chip, display: "flex", alignItems: "baseline", gap: 8, padding: "3px 8px", color: "var(--t2)" };
const liveDot: CSSProperties = { width: 6, height: 6, borderRadius: "50%", flex: "none", background: "var(--accent)", alignSelf: "center" };

export function Rail(p: {
  rows: Row[]; hidden: number;
  all: boolean; onAll: (v: boolean) => void;
  selKey: string | null; onSelect: (r: Row) => void;
  onNewChat: () => void; onDeleteChat: (chatId: string) => void;
  memberships: Membership[]; onNewWorkspace: () => void;
  onDeleteWorkspace: (workspaceId: string) => void; onResetWorkspace: (target: "personal" | "_global") => void;
  scaffolded: { global: boolean | null; personal: boolean | null }; onSetupGlobal: () => void; onSetupPersonal: () => void;
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
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "10px", display: "flex", flexDirection: "column", gap: 2 }}>
        {p.rows.map(chatRow)}
        {p.rows.length === 0 && <div style={{ ...ty.chip, padding: "2px 8px", color: "var(--t3)", lineHeight: 1.5 }}>Nothing yet. Meetings arrive by invitation; “+” starts a chat.</div>}

        <h2 style={{ ...lensRow, marginTop: 18 }}>Workspaces<button title="New workspace — a conversation scaffolds it" aria-label="New workspace" onClick={p.onNewWorkspace} style={row.ghostPlus}>+</button></h2>
        {p.scaffolded.personal === false
          ? <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <button onClick={p.onSetupPersonal} disabled={p.scaffolded.global === false}
                title={p.scaffolded.global === false ? "Finish the organisation setup first" : "Run your personal onboarding"}
                style={{ ...wsRow, flex: 1, textAlign: "left", background: "var(--accentbg)", border: "none", borderRadius: 7, color: p.scaffolded.global === false ? "var(--t3)" : "var(--accent)", cursor: p.scaffolded.global === false ? "default" : "pointer", fontWeight: 600 }}>
                Set up personal workspace…</button>
              <button title="Reset your personal workspace to the seed" aria-label="Reset personal workspace" onClick={() => p.onResetWorkspace("personal")}
                style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, padding: "0 4px", lineHeight: 1, flex: "none" }}>×</button>
            </div>
          : <div style={wsRow}>personal<span style={{ ...ty.meta, marginLeft: "auto", fontSize: 10 }}>you</span>
              <button title="Reset your personal workspace to the seed" aria-label="Reset personal workspace" onClick={() => p.onResetWorkspace("personal")}
                style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, padding: "0 2px", lineHeight: 1 }}>×</button>
            </div>}
        {p.memberships.map((m) => (
          <div key={"ws-" + m.workspace_id} className="vx-ws-row" style={wsRow}>{m.workspace_id}<span style={{ ...ty.meta, marginLeft: "auto", fontSize: 10 }}>{m.role}</span>
            {m.role === "owner" && (
              <button title={`Delete workspace ${m.workspace_id} — removes its data for every member`} aria-label={`Delete workspace ${m.workspace_id}`}
                onClick={() => p.onDeleteWorkspace(m.workspace_id)}
                className="vx-ws-del" style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, padding: "0 2px", lineHeight: 1 }}>×</button>
            )}
          </div>
        ))}
        {p.scaffolded.global === false
          ? <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <button onClick={p.onSetupGlobal} title="The organisation tier is not set up — finish this first"
                style={{ ...wsRow, flex: 1, textAlign: "left", background: "var(--accentbg)", border: "none", borderRadius: 7, color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>
                Set up global workspace…</button>
              <button title="Wipe the organisation tier to the empty seed (admins only)" aria-label="Reset _global" onClick={() => p.onResetWorkspace("_global")}
                style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, padding: "0 4px", lineHeight: 1, flex: "none" }}>×</button>
            </div>
          : <div style={wsRow}>_global<span style={{ ...ty.meta, marginLeft: "auto", fontSize: 10 }}>everyone · ro</span>
              <button title="Reset the organisation tier to the seed (admins only)" aria-label="Reset _global" onClick={() => p.onResetWorkspace("_global")}
                style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, padding: "0 2px", lineHeight: 1 }}>×</button>
            </div>}
      </div>

      <div style={{ ...ty.meta, flex: "none", padding: "10px", lineHeight: 1.55, borderTop: "1px solid var(--line)" }}>
        Every chat, newest first. A meeting is a chat too — opening it opens the meeting.
      </div>
    </nav>
  );
}
