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
 *  writes somewhere. Everything else in the set is one × away.
 *
 *  ONE OF THEM IS THE TARGET (Vexa-ai/vexa#1611) — the workspace this chat WRITES to; the rest are
 *  readable. Founder, 2026-09-06, in a chat whose chip read `personal` while the conversation was
 *  about a customer's workspace: *"it creates files in the wrong workspace… we probably should be
 *  able to set a workspace that we are targeting (other workspaces still available to read and even
 *  to write, if explicit ask and purpose)"*. The target chip is visibly the target and clicking any
 *  other makes it one — which is the whole control, and it is here for the reason the × is: what a
 *  chat is over, and where it works, are properties of the chat and belong beside its name.
 *
 *  THE CHIPS ARE BUTTONS NOW, all of them, `personal` included — because targeting the desk is a
 *  real choice (it is where you go BACK to) and a chip you cannot click is a chip that says the
 *  desk is not a place. `aria-current` marks the target rather than a colour alone.
 *
 *  The + menu's foot carries "Attach existing repo…" for the same reason the menu exists at all: the
 *  person who opens it is asking "what else can this chat be over?", and one of the true answers is a
 *  workspace that already exists on GitHub. The empty state above it is UNCHANGED — new workspaces are
 *  still made in conversation; loading an existing one is a different act, and it is additive beneath
 *  that sentence rather than a correction of it. */
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { Membership } from "../surfaces/workspaceApi";
import type { Sel } from "./types";
import { header, surface, type as ty } from "./tokens";
import { WorkspaceName } from "../ui-kit/WsLink";

/** Always mounted, never chosen — so never shown. */
export const IMPLICIT_MOUNTS = ["_global", "_system"];
export const focusSet = (workspaces: string[]) => workspaces.filter((w) => !IMPLICIT_MOUNTS.includes(w));

const wsChip: CSSProperties = {
  ...ty.mono, display: "inline-flex", alignItems: "center", gap: 2, background: surface.raised,
  border: "1px solid var(--line)", borderRadius: 6, padding: "2px 4px 2px 8px", color: "var(--t2)", flex: "none",
};
/** THE TARGET, VISIBLY THE TARGET. Accent on the border and the text — the same accent the rail's
 *  selected row wears, because it answers the same question in the same glance: this is the one. */
const targetChip: CSSProperties = {
  ...wsChip, borderColor: "var(--accent)", color: "var(--accent)", background: "var(--accentbg)",
};
const xBtn: CSSProperties = { background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12, lineHeight: 1, padding: "0 3px", fontFamily: "var(--sans)" };
/** The client's name for the person's own desk in a chat's mount set — not a registry slug, which
 *  is why it is a literal here and why it is the one chip with no `×`. */
const DESK = "personal";

/** WHICH CHIP IS THE TARGET. Absent means the desk — the default has ONE spelling and this is where
 *  it becomes something a reader can see. Exported because the header and the rail must agree, and
 *  because a rule about what is highlighted is worth a test that does not render a component. */
export const isTargetChip = (target: string | undefined, slug: string) => (target || DESK) === slug;

export function ContextBar(p: {
  sel: Sel; flavor: string;
  memberships: Membership[];
  onAddWorkspace: (id: string) => void; onRemoveWorkspace: (id: string) => void;
  onSetTarget?: (id: string) => void;
  onAttachRepo?: (workspaceId?: string) => void;
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
        {set.map((w) => {
          // NO TARGET IS THE DESK. One spelling of the default, here as everywhere: the record
          // stores an absence and this is where the absence becomes a chip somebody can see.
          const isTarget = isTargetChip(p.sel.target, w);
          return (
            <span key={w} style={isTarget ? targetChip : wsChip} data-ws={w}
              data-target={isTarget ? "1" : undefined} aria-current={isTarget ? "true" : undefined}>
              {/* F49: this printed the workspace's SLUG — for a desk, the opaque subject id (`126`)
                  the reader has never seen. Names live in the registry precisely because slugs and
                  directories change and names are what a person reads. */}
              <button data-ws-target={w} style={{ ...xBtn, color: "inherit", padding: 0, cursor: isTarget ? "default" : "pointer", font: "inherit" }}
                aria-label={isTarget ? `Writes go to ${w}` : `Write into ${w} from now on`}
                title={isTarget
                  ? "Writes in this chat go here. The others are mounted to read."
                  : "Mounted to read. Click to make this where this chat writes."}
                onClick={() => { if (!isTarget) p.onSetTarget?.(w); }}>
                <WorkspaceName slug={w} />
              </button>
              {w !== DESK && (
                <button aria-label={`Remove ${w} from this chat`} title={`Remove ${w} from this chat`} style={xBtn}
                  onClick={() => p.onRemoveWorkspace(w)}>×</button>
              )}
            </span>
          );
        })}
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
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}><WorkspaceName slug={id} /></button>
                ))}
            {p.onAttachRepo && (
              <button role="menuitem" data-ctx="attach"
                onClick={() => { p.onAttachRepo?.(undefined); setPicking(false); }}
                style={{ ...ty.chip, display: "block", width: "100%", textAlign: "left", background: "transparent", border: "none", borderTop: "1px solid var(--line)", borderRadius: 0, padding: "7px 8px", marginTop: 5, color: "var(--t2)", cursor: "pointer" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>Attach existing repo…</button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
