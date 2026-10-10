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
 *  THE COMPANY LAYER IS A PLACE THE ADMIN MAY WORK (Vexa-ai/vexa#1616). Founder, 2026-09-06 15:20Z,
 *  looking at this very menu: *"as admin i should just have global as option to choose here as
 *  workspace to write to"*. The hiding rule at the top of this file was written about ORDINARY
 *  MEMBERS, for whom `_global` is a constant — and a constant is not information. For the admin it
 *  is not a constant: it is the one workspace they may write that nobody chose, so it is offered in
 *  the `+` menu and wears a chip exactly while this chat is aimed at it. That is the rail's own
 *  rule about target tags, one level in — the case that is NOT the ordinary one is the information.
 *
 *  The menu entry and the chip are ONE act — mount · target · open its README — which is what a
 *  `focus` event has always done, and for the same reason: *"add the company layer to this chat"*
 *  and *"work in the company layer"* are not two things a person means separately. It carries no ×
 *  for the reason `personal` carries none: `_global` is mounted in every chat by construction, so a
 *  × would be a lie. Aiming somewhere else is how you leave. For everyone else nothing here
 *  changed — no entry, no chip — and the server refuses the write regardless of what a client shows.
 *
 *  The + menu's foot carries "Attach existing repo…" for the same reason the menu exists at all: the
 *  person who opens it is asking "what else can this chat be over?", and one of the true answers is a
 *  workspace that already exists on GitHub. The empty state above it is UNCHANGED — new workspaces are
 *  still made in conversation; loading an existing one is a different act, and it is additive beneath
 *  that sentence rather than a correction of it. */
import { ChatName } from "./ChatName";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ChevronDown, PenLine, Plus, X } from "lucide-react";
import { Fold, Menu, type MenuItem } from "../ui-kit";
import type { Membership } from "../surfaces/workspaceApi";
import type { Sel } from "./types";
import { header, surface, type as ty } from "./tokens";
import { WorkspaceName } from "../ui-kit/WsLink";
import { COMPANY_WORD } from "./vocabulary";

/** Always mounted, never chosen — so never shown. */
export const IMPLICIT_MOUNTS = ["_global", "_system"];
/** The org tier's slug. `_system` is never a chip; this one is — for the admin, and only
 *  while the chat writes there (Vexa-ai/vexa#1616). */
export const GLOBAL_MOUNT = "_global";
/** The chips this chat shows.
 *
 *  `opts` is how `_global` earns one: the ADMIN, and only while this chat is aimed at it. It is
 *  APPENDED rather than read out of `workspaces`, because a chat restored from the server can carry
 *  the target without the mount in its set — and a header that stayed silent then would be the
 *  exact failure #1611 exists to end: writes landing somewhere the header does not name. */
export const focusSet = (workspaces: string[], opts: { admin?: boolean; target?: string } = {}) => {
  const set = workspaces.filter((w) => !IMPLICIT_MOUNTS.includes(w));
  return opts.admin === true && opts.target === GLOBAL_MOUNT ? [...set, GLOBAL_MOUNT] : set;
};

const menuItem: CSSProperties = {
  ...ty.chip, display: "block", width: "100%", textAlign: "left", background: "transparent",
  border: "none", borderRadius: 6, padding: "6px 8px", color: "var(--t1)", cursor: "pointer",
};
/** The client's name for the person's own desk in a chat's mount set — not a registry slug, which
 *  is why it is a literal here and why it is the one chip with no `×`. */
const DESK = "personal";

/** WHICH CHIP IS THE TARGET. Absent means the desk — the default has ONE spelling and this is where
 *  it becomes something a reader can see. Exported because the header and the rail must agree, and
 *  because a rule about what is highlighted is worth a test that does not render a component. */
export const isTargetChip = (target: string | undefined, slug: string) => (target || DESK) === slug;

export function ContextBar(p: {
  sel: Sel; flavor: string;
  onRename?: (name: string) => Promise<void>;
  memberships: Membership[];
  onAddWorkspace: (id: string) => void; onRemoveWorkspace: (id: string) => void;
  onSetTarget?: (id: string) => void;
  onAttachRepo?: (workspaceId?: string) => void;
  /** Is the signed-in person this instance's admin (Vexa-ai/vexa#1616)? Absent or false → the
   *  company layer is not offered and not shown, which is every other person's whole experience
   *  of this file. Three-valued upstream; only a literal `true` reaches here. */
  isAdmin?: boolean;
  /** Aim this chat at the company layer and open it — the menu entry's click and the chip's, which
   *  are the same act by design. */
  onTargetGlobal?: () => void;
  /** Pane controls the shell puts at the header's edges where a pane is an overlay (the chat list
   *  in single mode at the left; the pages sheet at the right). */
  leading?: ReactNode;
  trailing?: ReactNode;
}) {
  const [picking, setPicking] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!picking) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setPicking(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [picking]);

  const admin = p.isAdmin === true;
  const set = focusSet(p.sel.workspaces, { admin, target: p.sel.target });
  const addable = p.memberships.map((m) => m.workspace_id).filter((id) => !p.sel.workspaces.includes(id));
  // Offered while it is not already where this chat writes — an entry that would change nothing is
  // a menu row that reads as broken.
  const offerGlobal = admin && !!p.onTargetGlobal && p.sel.target !== GLOBAL_MOUNT;

  // THE HEADER FOLDS BY ITS OWN WIDTH (guidelines §3.3): below 520px of header the workspace chips
  // become ONE control — "Writes to <target>" — whose menu holds every act the chips offered:
  // retarget, remove, add, attach. Same handlers, same labels; the chips and the menu are two forms
  // of one control, never two controls.
  const target = p.sel.target || DESK;
  const items: MenuItem[] = [
    ...set.map((w): MenuItem => {
      const isGlobal = w === GLOBAL_MOUNT;
      return {
        key: `t:${w}`, checked: isTargetChip(p.sel.target, w), data: { "data-ws-target": w },
        label: <WorkspaceName slug={w} fallback={isGlobal ? COMPANY_WORD : undefined} />,
        onSelect: () => { if (isGlobal) p.onTargetGlobal?.(); else if (!isTargetChip(p.sel.target, w)) p.onSetTarget?.(w); },
      };
    }),
    ...set.filter((w) => w !== DESK && w !== GLOBAL_MOUNT).map((w, i): MenuItem => ({
      key: `r:${w}`, separatorBefore: i === 0, label: <>Remove <WorkspaceName slug={w} /></>, onSelect: () => p.onRemoveWorkspace(w),
    })),
    ...(offerGlobal ? [{ key: "g", separatorBefore: true, data: { "data-ctx": "global" }, label: <>Add <WorkspaceName slug={GLOBAL_MOUNT} fallback={COMPANY_WORD} /></>, onSelect: () => p.onTargetGlobal?.() } as MenuItem] : []),
    ...addable.map((id, i): MenuItem => ({
      key: `a:${id}`, separatorBefore: i === 0 && !offerGlobal, label: <>Add <WorkspaceName slug={id} /></>, onSelect: () => p.onAddWorkspace(id),
    })),
    ...(p.onAttachRepo ? [{ key: "attach", separatorBefore: true, data: { "data-ctx": "attach" }, label: "Attach existing repo…", onSelect: () => p.onAttachRepo?.(undefined) } as MenuItem] : []),
  ];
  // A meeting's phase is information, so it is a quiet badge; "chat" said nothing the breadcrumb
  // ("Chat ›") had not, so the pill that printed it is gone (guidelines §4.5).
  const phase = p.sel.kind === "meeting" ? p.flavor.split(" · ")[1] : undefined;

  return (
    <div className="vx-pane pt-0 pr-3 pb-0 pl-4" data-pane="header" style={{ ...header, gridRow: 1, gridColumn: 2, gap: 8 }}>
      {p.leading}
      <span className="r-full bg-success" style={{ width: 7, height: 7, flex: "none" }} />
      <div style={{ ...ty.title, flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 4 }}>
        <span className="c-3 fw-400" style={{ whiteSpace: "nowrap", flex: "none" }}>{p.sel.kind === "meeting" ? "Meeting" : "Chat"} › </span><ChatName key={p.sel.chatId} label={p.sel.label} onRename={p.onRename} />
      </div>
      {phase && <span data-flavor-badge className="c-2 r-sm pt-0 pr-1_5 pb-0 pl-1_5" style={{ ...ty.meta, flex: "none", background: surface.raised, lineHeight: "18px", whiteSpace: "nowrap" }}>{phase[0].toUpperCase() + phase.slice(1)}</span>}

      <Fold at={520} wide={
      <div ref={box} className="ml-auto" style={{ position: "relative", display: "flex", alignItems: "center", gap: 5, flex: "none" }}>
        {set.map((w) => {
          // NO TARGET IS THE DESK. One spelling of the default, here as everywhere: the record
          // stores an absence and this is where the absence becomes a chip somebody can see.
          const isTarget = isTargetChip(p.sel.target, w);
          // THE COMPANY LAYER'S CHIP IS ALSO ITS DOOR (#1616): it is only on screen while this chat
          // writes there, so a click that did nothing would leave the reader with a chip that names
          // a place and refuses to open it. It opens `_global/README.md` and re-states the target.
          const isGlobal = w === GLOBAL_MOUNT;
          return (
            // A WORKSPACE IS A CHIP (guidelines §4.5): sans, sentence case, radius 6; the TARGET is
            // the selected chip — accent tint, primary text and a 2px accent bar, plus the pen icon
            // and "Writes to", so it is never colour alone.
            <span key={w} className="vx-chip" data-selected={isTarget ? "" : undefined} data-ws={w}
              data-target={isTarget ? "1" : undefined} aria-current={isTarget ? "true" : undefined}>
              {/* F49: this printed the workspace's SLUG — for a desk, the opaque subject id (`126`)
                  the reader has never seen. Names live in the registry precisely because slugs and
                  directories change and names are what a person reads. */}
              <button data-ws-target={w} className="vx-chip-main" data-static={isTarget && !isGlobal ? "" : undefined}
                aria-label={isGlobal ? `Open ${COMPANY_WORD}` : isTarget ? `Writes go to ${w}` : `Write into ${w} from now on`}
                title={isGlobal
                  ? "The company layer. Writes in this chat go here — click to open it."
                  : isTarget
                    ? "Writes in this chat go here. The others are mounted to read."
                    : "Mounted to read. Click to make this where this chat writes."}
                onClick={() => {
                  if (isGlobal) p.onTargetGlobal?.();
                  else if (!isTarget) p.onSetTarget?.(w);
                }}>
                {isTarget && <PenLine size={14} strokeWidth={1.75} aria-hidden />}
                <span className="vx-chip-label">{isTarget ? <>Writes to <WorkspaceName slug={w} fallback={isGlobal ? COMPANY_WORD : undefined} /></> : <WorkspaceName slug={w} fallback={isGlobal ? COMPANY_WORD : undefined} />}</span>
              </button>
              {/* `_global` has no × for the reason the desk has none: it is mounted in every chat by
                  construction, so removing it is not a thing that can happen. Aim elsewhere. */}
              {w !== DESK && !isGlobal && (
                <button aria-label={`Remove ${w} from this chat`} title={`Remove ${w} from this chat`} className="vx-chip-x"
                  onClick={() => p.onRemoveWorkspace(w)}><X size={12} strokeWidth={1.75} aria-hidden /></button>
              )}
            </span>
          );
        })}
        <button aria-label="Add a workspace to this chat" title="Add a workspace to this chat"
          onClick={() => setPicking((v) => !v)}
          className="vx-chip vx-chip-add" data-ghost=""><Plus size={14} strokeWidth={1.75} aria-hidden /></button>
        {picking && (
          <div role="menu" className="bg-1 bd-strong r-lg p-1_5" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 30, minWidth: 200, boxShadow: "0 8px 24px rgba(0,0,0,.35)" }}>
            {/* THE ADMIN'S ONE EXTRA ANSWER (#1616), above the memberships because it is the one
                workspace in the list nobody had to be invited to. */}
            {offerGlobal && (
              <button role="menuitem" data-ctx="global" onClick={() => { p.onTargetGlobal?.(); setPicking(false); }}
                style={menuItem}
                onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>
                <WorkspaceName slug={GLOBAL_MOUNT} fallback={COMPANY_WORD} />
              </button>
            )}
            {addable.length === 0 && !offerGlobal
              ? <div className="pt-1_5 pr-2 pb-1_5 pl-2 lh-snug" style={{ ...ty.meta }}>No other workspace to add. New ones are made in conversation.</div>
              : addable.map((id) => (
                  <button key={id} role="menuitem" onClick={() => { p.onAddWorkspace(id); setPicking(false); }}
                    style={menuItem}
                    onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}><WorkspaceName slug={id} /></button>
                ))}
            {p.onAttachRepo && (
              <button role="menuitem" data-ctx="attach"
                onClick={() => { p.onAttachRepo?.(undefined); setPicking(false); }}
                className="bg-none bd-none bd-t r-0 pt-1_5 pr-2 pb-1_5 pl-2 mt-1 c-2" style={{ ...ty.chip, display: "block", width: "100%", textAlign: "left", cursor: "pointer" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>Attach existing repo…</button>
            )}
          </div>
        )}
      </div>} narrow={
        <Menu label="Workspaces for this chat" variant="chip" align="end" triggerData={{ "data-ws-fold": "" }}
          title="Where this chat writes, and what it can read"
          trigger={<><PenLine size={14} strokeWidth={1.75} aria-hidden /><span style={{ minWidth: 0, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Writes to <WorkspaceName slug={target} fallback={target === GLOBAL_MOUNT ? COMPANY_WORD : undefined} /></span><ChevronDown size={14} strokeWidth={1.75} aria-hidden /></>}
          items={items}
          footer={addable.length === 0 && !offerGlobal ? "New workspaces are made in conversation." : undefined} />
      } />
      {p.trailing}
    </div>
  );
}
