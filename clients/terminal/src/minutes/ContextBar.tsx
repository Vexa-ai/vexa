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
import { Identity } from "../ui-kit/Identity";
import { ChatName } from "./ChatName";
import { displayLabel } from "./chats";
import { Participants } from "./Participants";
import { CallButton } from "./CallButton";
import { useEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { ChevronDown, MoreHorizontal, PenLine, Plus, X } from "lucide-react";
import { Fold, Menu, type MenuItem } from "../ui-kit";
import type { Membership, WritableWorkspace } from "../data/workspaceApi";
import type { Sel } from "./types";
import { header, surface, type as ty } from "./tokens";
import { WorkspaceName } from "../ui-kit/WsLink";
import { workspaceBySlug } from "../ui-kit/wsLinks";
import { COMPANY_WORD } from "./vocabulary";
import { folderFromPicked, type DroppedFolder } from "../surfaces/folderDrop";
import { useFeature } from "../platform/features";
import { ScopeLine } from "./ScopeLine";
import { useDesktopItems } from "./desktop/useDesktopItems";

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
  // THE ADMIN ACCOUNT HAS ONE WORKSPACE (founder ruling 2026-10-11): the company layer, always the
  // target, whatever the chat's stored set says. No desk chip, no group chip.
  if (opts.admin === true) return [GLOBAL_MOUNT];
  return workspaces.filter((w) => !IMPLICIT_MOUNTS.includes(w));
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

/** What the server says about one workspace in this chat's set, for THIS reader. */
export type WsAccess = { access: "readable" | "not-yours" | "gone"; writable: boolean };

/** WHAT THE HEADER MAY NAME. A chip is shown for a workspace the server says this reader can
 *  read; one it answers `not-yours` or `gone` for is not named at all — the reader has no claim on
 *  it, and a stored chat set (or another account's browser state) is not the server's view. The
 *  desk and the company layer are not registry workspaces and are decided above. Unknown yet
 *  (`undefined`) is not shown either: naming it would be a guess. */
export const namedSet = (set: string[], seen: Record<string, WsAccess | undefined>) =>
  set.filter((w) => w === DESK || w === GLOBAL_MOUNT || seen[w]?.access === "readable");

/** WHERE THIS CHAT IS SHOWN TO WRITE. "Writes to X" only when the server says X is writable for
 *  this reader; otherwise the desk, and X is not mentioned. The server refuses the write either
 *  way — this is so the header never says a place the reader cannot write. */
export const shownTarget = (target: string | undefined, seen: Record<string, WsAccess | undefined>, admin = false) => {
  if (admin) return GLOBAL_MOUNT;
  const t = target || DESK;
  if (t === DESK) return DESK;
  return seen[t]?.writable === true && seen[t]?.access === "readable" ? t : DESK;
};

/** The server's access answer for each registry workspace in `slugs` (by-slug, cached). */
function useWorkspaceAccess(slugs: string[]): Record<string, WsAccess | undefined> {
  const [seen, setSeen] = useState<Record<string, WsAccess | undefined>>({});
  const key = slugs.filter((w) => w !== DESK && w !== GLOBAL_MOUNT).sort().join("|");
  useEffect(() => {
    let on = true;
    const want = key ? key.split("|") : [];
    void Promise.all(want.map(async (w) => {
      const rec = await workspaceBySlug(w).catch(() => null);
      return [w, rec ? { access: rec.access, writable: rec.writable === true } : undefined] as const;
    })).then((rows) => { if (on) setSeen(Object.fromEntries(rows)); });
    return () => { on = false; };
  }, [key]);
  return seen;
}

export function ContextBar(p: {
  sel: Sel; flavor: string;
  onRename?: (name: string) => Promise<void>;
  /** a new chat nobody has written in yet: it has no title, so the header draws none (founder,
   *  2026-10-14: the empty home showed the previous chat's title) */
  untitled?: boolean;
  memberships: Membership[];
  /** Every workspace this reader can WRITE, by display name (the server's `all` listing,
   *  Vexa-ai/vexa#1783) — the reader's own private workspaces included, which `memberships` (the
   *  shared-with-me listing) never carried, so they could not be chosen as where a chat writes. */
  writable?: WritableWorkspace[];
  /** THE PERSONAL CHAT HEADER (R1, `rooms`): your face, the title, "Writes to" and "…" only — the
   *  call, "Take to a room…", pinned pages and Activity move into "…" (a chat is always private). */
  personal?: { face: ReactNode; more: MenuItem[] };
  /** Mount one of `writable` in this chat and make it where the chat writes — one act. */
  onWriteTo?: (id: string) => void;
  onAddWorkspace: (id: string) => void; onRemoveWorkspace: (id: string) => void;
  onSetTarget?: (id: string) => void;
  onAttachRepo?: (workspaceId?: string) => void;
  /** "New workspace from folder…" (founder 2026-10-13): a folder picked with the system picker —
   *  the way in on a phone, and anywhere a drag is not possible. Absent → no such entry. */
  onFolderPicked?: (folder: DroppedFolder) => void;
  /** Is the signed-in account the deployment's ADMIN ACCOUNT (identity's `admin_account`; founder
   *  ruling 2026-10-11)? Then the header is the company layer and nothing else: one Company chip,
   *  always the target, and no `+` menu. Absent or false → the company layer is never offered and
   *  never shown, which is every everyday account's whole experience of this file — the instance
   *  admin's included. Three-valued upstream; only a literal `true` reaches here. */
  adminAccount?: boolean;
  /** Aim this chat at the company layer and open it — the menu entry's click and the chip's, which
   *  are the same act by design. */
  onTargetGlobal?: () => void;
  /** The workspace this person's NEW chats write to (`""` = Personal), as the server re-checked it,
   *  and how to change it — the menu's "Make default for new chats". Absent → no such entry. */
  defaultTarget?: string;
  onMakeDefault?: (id: string) => void;
  /** Pane controls the shell puts at the header's edges where a pane is an overlay (the chat list
   *  in single mode at the left; the pages sheet at the right). */
  leading?: ReactNode;
  trailing?: ReactNode;
  /** THE PROFILE CHIP (Vexa-ai/vexa#1783): when given, the header shows ONE read-only chip — the
   *  chat's profile and what it means, from the SERVER — in place of the workspace chips and the `+`
   *  menu. Everything is changed in the chat (founder 2026-10-14). */
  scope?: { session: string; onReads?: (reads: string[], view?: import("../data/workspaceApi").ScopeView) => void };
}) {
  const [picking, setPicking] = useState(false);
  // `folder_drop` off (#1783): no "New workspace from folder…", whatever the host passed.
  const onFolderPicked = useFeature("folder_drop") ? p.onFolderPicked : undefined;
  const box = useRef<HTMLDivElement | null>(null);
  const folderInput = useRef<HTMLInputElement | null>(null);
  const pickFolder = () => folderInput.current?.click();
  useEffect(() => {
    if (!picking) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setPicking(false); };
    window.addEventListener("mousedown", away);
    return () => window.removeEventListener("mousedown", away);
  }, [picking]);

  const admin = p.adminAccount === true;
  // THE CHAT'S OWN "…" (#1783): Add to desktop / Remove from desktop for this chat. Drawn only when
  // it has something in it — today, only with the `desktop` feature on — and never for a new chat
  // nobody has written in (there is nothing to place yet).
  const desktopItems = useDesktopItems();
  const chatMore: MenuItem[] = p.sel.chatId && !p.untitled
    ? desktopItems({ kind: "chat", id: p.sel.chatId }).map((it) => ({ key: it.id, label: it.label, data: { "data-chat-act": it.id }, onSelect: () => { void it.onSelect(); } }))
    : [];
  const seen = useWorkspaceAccess(p.sel.workspaces);
  const set = namedSet(focusSet(p.sel.workspaces, { admin, target: p.sel.target }), seen);
  // The target as the server allows it to be shown (see `shownTarget`): every chip, menu row and
  // the folded trigger below read this, never `p.sel.target` directly.
  const shown = shownTarget(p.sel.target, seen, admin);
  // WHAT ELSE THIS CHAT CAN BE OVER: the reader's writable workspaces (own private ones included),
  // by name, then any shared one they only read. A writable one is offered as a place to WRITE.
  const writable = [...(p.writable ?? [])].sort((a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  const writeName = new Map(writable.map((w) => [w.slug, w.name] as const));
  const addable = [...new Set([...writable.map((w) => w.slug), ...p.memberships.map((m) => m.workspace_id)])]
    .filter((id) => !p.sel.workspaces.includes(id));
  const canWriteTo = (id: string) => !!p.onWriteTo && writeName.has(id);
  const pick = (id: string) => { if (canWriteTo(id)) p.onWriteTo?.(id); else p.onAddWorkspace(id); };
  const addLabel = (id: string) => canWriteTo(id) ? <>{writeName.get(id)}</> : <>Add <WorkspaceName slug={id} /></>;
  // The admin account's one chip IS its target, and nothing is ever offered beside it — so it has no
  // `+` menu at all. Everybody else is never offered the company layer (it is not theirs to write).

  // THE HEADER FOLDS BY ITS OWN WIDTH (guidelines §3.3): below 520px of header the workspace chips
  // become ONE control — "Writes to <target>" — whose menu holds every act the chips offered:
  // retarget, remove, add, attach. Same handlers, same labels; the chips and the menu are two forms
  // of one control, never two controls.
  const target = shown;
  // THE DEFAULT FOR NEW CHATS (founder 2026-10-13): the target shown here can become the one every
  // new chat starts on, and the menu says when it already is. Never for the admin account, whose one
  // workspace is fixed.
  const defaultNow = (p.defaultTarget || "").trim() || DESK;
  const makeDefault = p.onMakeDefault && !admin ? {
    isDefault: defaultNow === shown,
    label: defaultNow === shown ? "Default for new chats" : "Make default for new chats",
    hint: defaultNow === shown ? undefined : <>now <WorkspaceName slug={defaultNow} /></>,
    select: () => p.onMakeDefault?.(shown === DESK ? "" : shown),
  } : null;
  const items: MenuItem[] = [
    ...set.map((w): MenuItem => {
      const isGlobal = w === GLOBAL_MOUNT;
      return {
        key: `t:${w}`, checked: isTargetChip(shown, w), data: { "data-ws-target": w },
        label: <WorkspaceName slug={w} fallback={isGlobal ? COMPANY_WORD : undefined} />,
        onSelect: () => { if (isGlobal) p.onTargetGlobal?.(); else if (!isTargetChip(shown, w)) p.onSetTarget?.(w); },
      };
    }),
    ...set.filter((w) => w !== DESK && w !== GLOBAL_MOUNT).map((w, i): MenuItem => ({
      key: `r:${w}`, separatorBefore: i === 0, label: <>Remove <WorkspaceName slug={w} /></>, onSelect: () => p.onRemoveWorkspace(w),
    })),
    ...addable.map((id, i): MenuItem => ({
      key: `a:${id}`, separatorBefore: i === 0, label: addLabel(id), onSelect: () => pick(id),
      data: canWriteTo(id) ? { "data-ws-write": id } : undefined,
    })),
    ...(p.onAttachRepo ? [{ key: "attach", separatorBefore: true, data: { "data-ctx": "attach" }, label: "Attach existing repo…", onSelect: () => p.onAttachRepo?.(undefined) } as MenuItem] : []),
    ...(onFolderPicked ? [{ key: "folder", separatorBefore: !p.onAttachRepo, data: { "data-ctx": "folder" }, label: "New workspace from folder…", onSelect: pickFolder } as MenuItem] : []),
    ...(makeDefault ? [{ key: "default", separatorBefore: true, data: { "data-ctx": "make-default" },
      disabled: makeDefault.isDefault, label: makeDefault.label, hint: makeDefault.hint,
      onSelect: makeDefault.select } as MenuItem] : []),
  ];

  return (
    <div className="vx-pane pt-0 pr-3 pb-0 pl-4" data-pane="header" style={{ ...header, gridRow: 1, gridColumn: 2, gap: 8 }}>
      {p.leading}
      {/* THE TITLE, AND NOTHING IN FRONT OF IT (founder, 2026-10-14: "total mess here"). The status
          dot was always green and "Chat ›" said what the whole screen says; both are gone. The title
          keeps a floor (`.vx-ctx-title`) and truncates — the chips beside it give way first. */}
      {p.personal?.face}
      <div className="vx-ctx-title" style={{ ...ty.title, display: "flex", alignItems: "center", gap: 4 }}>
        {!p.untitled && <ChatName key={p.sel.chatId} label={displayLabel({ label: p.sel.label, meetingId: p.sel.meetingId })} onRename={p.onRename} />}
      </div>
      {p.sel.kind === "chat" && !p.personal && <Participants key={`people:${p.sel.chatId}`} session={p.sel.chatId} />}
      {p.sel.kind === "chat" && !p.personal && <CallButton key={`call:${p.sel.chatId}`} session={p.sel.chatId} label={p.sel.label} />}
      {(chatMore.length > 0 || !!p.personal?.more.length) && <Menu label="More for this chat" variant="ghost" align="end" triggerData={{ "data-chat-more": "" }}
        trigger={<MoreHorizontal size={15} strokeWidth={1.75} aria-hidden />} items={[...(p.personal?.more ?? []), ...chatMore]} />}
      {/* The meeting's phase is said ONCE, by the live-meeting chip at the header's end (founder
          2026-10-14); a "Live" badge here as well was the duplicate he pointed at. An upcoming or
          held meeting has no chip and no badge — its page says it. */}

      {p.scope && !admin ? (
        <span className="ml-auto vx-scope-cluster" data-scope-cluster="">
          <ScopeLine key={p.scope.session} session={p.scope.session} onReads={p.scope.onReads} />
        </span>
      ) : <Fold at={520} wide={
      <div ref={box} className="ml-auto" style={{ position: "relative", display: "flex", alignItems: "center", gap: 5, flex: "none" }}>
        {set.map((w) => {
          // NO TARGET IS THE DESK. One spelling of the default, here as everywhere: the record
          // stores an absence and this is where the absence becomes a chip somebody can see.
          const isTarget = admin ? w === GLOBAL_MOUNT : isTargetChip(shown, w);
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
                <Identity kind="workspace" id={w} slug={w} name={w} size={14} />
                <span className="vx-chip-label">{isTarget ? <>Saves to <WorkspaceName slug={w} fallback={isGlobal ? COMPANY_WORD : undefined} /></> : <WorkspaceName slug={w} fallback={isGlobal ? COMPANY_WORD : undefined} />}</span>
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
        {!admin && <>
        <button aria-label="Add a workspace to this chat" title="Add a workspace to this chat"
          onClick={() => setPicking((v) => !v)}
          className="vx-chip vx-chip-add" data-ghost=""><Plus size={14} strokeWidth={1.75} aria-hidden /></button>
        {picking && (
          <div role="menu" className="bg-1 bd-strong r-lg p-1_5" style={{ position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 30, minWidth: 200, boxShadow: "0 8px 24px rgba(0,0,0,.35)" }}>
            {addable.length === 0
              ? <div className="pt-1_5 pr-2 pb-1_5 pl-2 lh-snug" style={{ ...ty.meta }}>No other workspace to add. New ones are made in conversation.</div>
              : addable.map((id) => (
                  <button key={id} role="menuitem" data-ws-write={canWriteTo(id) ? id : undefined}
                    onClick={() => { pick(id); setPicking(false); }}
                    style={menuItem}
                    onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>{canWriteTo(id) ? writeName.get(id) : <WorkspaceName slug={id} />}</button>
                ))}
            {p.onAttachRepo && (
              <button role="menuitem" data-ctx="attach"
                onClick={() => { p.onAttachRepo?.(undefined); setPicking(false); }}
                className="bg-none bd-none bd-t r-0 pt-1_5 pr-2 pb-1_5 pl-2 mt-1 c-2" style={{ ...ty.chip, display: "block", width: "100%", textAlign: "left", cursor: "pointer" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>Attach existing repo…</button>
            )}
            {onFolderPicked && (
              <button role="menuitem" data-ctx="folder"
                onClick={() => { pickFolder(); setPicking(false); }}
                className="bg-none bd-none r-0 pt-1_5 pr-2 pb-1_5 pl-2 c-2" style={{ ...ty.chip, display: "block", width: "100%", textAlign: "left", cursor: "pointer" }}
                onMouseEnter={(e) => { e.currentTarget.style.background = surface.raised; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}>New workspace from folder…</button>
            )}
            {makeDefault && (
              <button role="menuitem" data-ctx="make-default" aria-disabled={makeDefault.isDefault || undefined}
                onClick={() => { if (makeDefault.isDefault) return; makeDefault.select(); setPicking(false); }}
                className="bg-none bd-none bd-t r-0 pt-1_5 pr-2 pb-1_5 pl-2 mt-1 c-2" style={{ ...ty.chip, display: "block", width: "100%", textAlign: "left", cursor: makeDefault.isDefault ? "default" : "pointer" }}>
                {makeDefault.label}{makeDefault.hint ? <span className="c-3"> · {makeDefault.hint}</span> : null}
              </button>
            )}
          </div>
        )}
        </>}
      </div>} narrow={
        <Menu label="Workspaces for this chat" variant="chip" align="end" triggerData={{ "data-ws-fold": "" }}
          title="Where this chat writes, and what it can read"
          trigger={<><PenLine size={14} strokeWidth={1.75} aria-hidden /><span style={{ minWidth: 0, maxWidth: 140, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Saves to <Identity kind="workspace" id={target} slug={target} name={target} size={14} className="vx-id-inline" /><WorkspaceName slug={target} fallback={target === GLOBAL_MOUNT ? COMPANY_WORD : undefined} /></span><ChevronDown size={14} strokeWidth={1.75} aria-hidden /></>}
          items={items}
          footer={addable.length === 0 ? "New workspaces are made in conversation." : undefined} />
      } />}
      {onFolderPicked && (
        // THE SYSTEM FOLDER PICKER. `webkitdirectory` is the attribute every current browser reads,
        // phones included; the files arrive with `webkitRelativePath`, which `folderFromPicked` reads.
        <input ref={folderInput} type="file" multiple hidden data-folder-picker=""
          {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
          onChange={async (e) => {
            const input = e.currentTarget;
            const folder = input.files ? await folderFromPicked(input.files) : null;
            input.value = "";
            if (folder) onFolderPicked?.(folder);
          }} />
      )}
      {p.trailing}
    </div>
  );
}
