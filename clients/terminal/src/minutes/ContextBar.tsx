"use client";
/** The context bar — the shell's one answer to "where am I talking, about what":
 *  Chat/Meeting › label · flavor pill · the mount set. Shares the header row with the rail and
 *  pages head. There is no project above the chat any more, so the prefix says which KIND of chat
 *  this is rather than which folder it sat in. */
import type { Sel } from "./types";
import { header, type as ty } from "./tokens";

export function ContextBar({ sel, flavor, mounts }: { sel: Sel; flavor: string; mounts: string }) {
  return (
    <div style={{ ...header, gridRow: 1, gridColumn: 2 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green, #5da86a)", flex: "none" }} />
      <span style={{ ...ty.title, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        <span style={{ color: "var(--t3)", fontWeight: 400 }}>{sel.kind === "meeting" ? "Meeting" : "Chat"} › </span>{sel.label}
      </span>
      <span style={{ ...ty.pill, flex: "none", color: "var(--accent)", background: "var(--accentbg)", borderRadius: 5, padding: "2px 8px" }}>{flavor}</span>
      <span style={{ ...ty.mono, marginLeft: "auto", whiteSpace: "nowrap" }}>{mounts}</span>
    </div>
  );
}
