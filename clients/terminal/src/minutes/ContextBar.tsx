"use client";
/** The context bar — the shell's one answer to "where am I talking, about what":
 *  Room › chat · flavor pill · the mount set. Shares the header row with the rail and pages head. */
import type { Sel } from "./types";
import { header, type as ty } from "./tokens";

export function ContextBar({ sel, flavor, mounts }: { sel: Sel; flavor: string; mounts: string }) {
  const room = sel.kind === "meeting" ? "Personal" : sel.kind === "org" ? "Organisation" : sel.label;
  const chat = sel.kind === "meeting" ? sel.label : sel.kind === "org" ? "setup" : (sel.chatLabel ?? "main");
  return (
    <div style={{ ...header, gridRow: 1, gridColumn: 2 }}>
      <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green, #5da86a)", flex: "none" }} />
      <span style={{ ...ty.title, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
        <span style={{ color: "var(--t3)", fontWeight: 400 }}>{room} › </span>{chat}
      </span>
      <span style={{ ...ty.pill, flex: "none", color: "var(--accent)", background: "var(--accentbg)", borderRadius: 5, padding: "2px 8px" }}>{flavor}</span>
      <span style={{ ...ty.mono, marginLeft: "auto", whiteSpace: "nowrap" }}>{mounts}</span>
    </div>
  );
}
