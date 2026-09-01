"use client";
/** The context bar — the shell's one answer to "where am I talking, about what":
 *  Chat/Meeting › label · flavor pill · THE FOCUS SET.
 *
 *  The mount indicator used to read `[personal · _global · _system]`. Two things were wrong with
 *  that (founder ruling 2026-09-01: "chat focus specified here = can add/remove workspaces here,
 *  and global and system are hidden anyways"):
 *
 *    `_global` and `_system` are mounted in EVERY chat. A constant is not information, so they are
 *    gone from the display — what remains is the set this chat actually chose.
 *
 *    It was read-only, and this is the natural place to change it: the workspaces a chat is over
 *    are a property of the chat, so the control belongs beside the chat's name and nowhere else.
 *    This is where the rail's deleted workspace column went — per chat, where it means something.
 *
 *  `personal` is not removable: a chat over nothing has nowhere to write, and every conversation
 *  writes somewhere. Everything else in the set is one × away. */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { Membership } from "../surfaces/workspaceApi";
import type { Sel } from "./types";
import { header, surface, type as ty } from "./tokens";

/** Always mounted, never chosen — so never shown. */
export const IMPLICIT_MOUNTS = ["_global", "_system"];
export const focusSet = (workspaces: string[]) => workspaces.filter((w) => !IMPLICIT_MOUNTS.includes(w));

const wsChip: CSSProperties = {
  ...ty.mono, display: "inline-flex", alignItems: "center", gap: 2, background: surface.raised,
  border: "1px solid var(--line)", borderRadius: 6, padding: "2px 4px 2px 8px", color: "var(--t2)", flex: "none",
};
const xBtn: CSSProperties = { background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, lineHeight: 1, padding: "0 3px", fontFamily: "var(--sans)" };

export function ContextBar(p: {
  sel: Sel; flavor: string;
  memberships: Membership[];
  onAddWorkspace: (id: string) => void; onRemoveWorkspace: (id: string) => void;
}) {
  const [picking, setPicking] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!picking) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setPicking(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [picking]);

  const set = focusSet(p.sel.workspaces);
  const addable = p.memberships.map((m) => m.workspace_id).filter((id) => !p.sel.workspaces.includes(id));

  return (
    <div style={{ ...header, gridRow: 1, gridColumn: 2 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green, #5da86a)", flex: "none" }} />
      <span style={{ ...ty.title, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        <span style={{ color: "var(--t3)", fontWeight: 400 }}>{p.sel.kind === "meeting" ? "Meeting" : "Chat"} › </span>{p.sel.label}
      </span>
      <span style={{ ...ty.pill, flex: "none", color: "var(--accent)", background: "var(--accentbg)", borderRadius: 5, padding: "2px 8px" }}>{p.flavor}</span>

      <div ref={box} style={{ position: "relative", marginLeft: "auto", display: "flex", alignItems: "center", gap: 5, flex: "none" }}>
        {set.map((w) => (
          <span key={w} style={wsChip} title={w === "personal" ? "Your own workspace — always in focus" : `${w} — in this chat's focus`}>
            {w}
            {w !== "personal" && (
              <button aria-label={`Remove ${w} from this chat`} title={`Remove ${w} from this chat`} style={xBtn}
                onClick={() => p.onRemoveWorkspace(w)}>×</button>
            )}
          </span>
        ))}
        <button aria-label="Add a workspace to this chat" title="Add a workspace to this chat"
          onClick={() => setPicking((v) => !v)}
          style={{ ...wsChip, padding: "2px 7px", color: "var(--t3)", cursor: "pointer", fontSize: 13, lineHeight: 1.15 }}>+</button>
        {picking && (
          <div role="menu" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 30, minWidth: 200, background: "var(--sidebar)", border: "1px solid var(--line2)", borderRadius: 10, padding: 6, boxShadow: "0 8px 24px rgba(0,0,0,.35)" }}>
            {addable.length === 0
              ? <div style={{ ...ty.meta, padding: "6px 8px", lineHeight: 1.5 }}>No other workspace to add. New ones are made in conversation.</div>
              : addable.map((id) => (
                  <button key={id} role="menuitem" onClick={() => { p.onAddWorkspace(id); setPicking(false); }}
                    style={{ ...ty.chip, display: "block", width: "100%", textAlign: "left", background: "transparent", border: "none", borderRadius: 6, padding: "6px 8px", color: "var(--t1)", cursor: "pointer" }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>{id}</button>
                ))}
          </div>
        )}
      </div>
    </div>
  );
}
