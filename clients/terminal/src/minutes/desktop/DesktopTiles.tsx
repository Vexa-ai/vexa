"use client";
/** The desktop's tiles (#1783; UI decisions 15b workspaces, 15e agents and files).
 *
 *  A WORKSPACE TILE is navigation, and the WHOLE TILE is the link (founder 2026-10-15: "can't even
 *  click on those"): a click or Enter opens the workspace's front page — the Files navigator's own
 *  act — and a cmd-/middle-click opens its `/w/<workspace>/README.md` link in a new tab. What is
 *  inside the tile stays its own target and never opens the tile: the last call opens that meeting,
 *  Join joins, a person shows their hover card, "…" opens its menu. The tile shows its name, what
 *  kind it is and what the person may do there, the last activity in plain words, the last call and a live call with Join — every meeting kind (a Meet,
 *  Teams, Zoom or Jitsi bot meeting, or a Vexa call), its platform an ICON before the words (the
 *  meeting header's icon, UI decision 14), Join opening a bot meeting itself and a Vexa call's chat —
 *  and the people in it as
 *  a stack of at most three faces and "+n" — a presence dot only where the server says "now" or
 *  "today" (it applies the company's `presence_visibility` and each person's own switch). A
 *  read-only workspace says "reader" in its kind line, once.
 *
 *  An AGENT TILE says what the routine does, its status (from flows' evidence, never stored here),
 *  its last and next run and where it works; a press opens its chat. A CHAT TILE (a placed chat — the
 *  founder's "selected agents" are chats as well as routines) says its title, when it was last
 *  active and where it works; a press opens it. A FILE TILE says its title,
 *  workspace and last change; a press opens it in the panel. A placement that no longer resolves is
 *  a quiet "no longer available · Remove". */
import { useRef, useState, type DragEvent, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import { Bot, FileText, MessageSquare, Video } from "lucide-react";
import { OPEN_MEETING_EVENT, OPEN_PAGE_EVENT, OPEN_SESSION_EVENT, OPEN_WORKSPACE_FILES_EVENT } from "../../platform";
import type { AgentItem, CallPlatform, ChatItem, DesktopItem, FileItem, WorkspaceItem } from "../../platform/desktop";
import { workspacePath } from "../../platform/workspaceRoute";
import { platformLabel } from "../callsList";
import { navigateView } from "../roomView";
import { WORKSPACE_FRONT_PAGE } from "../deepLink";
import { listWorkspaceTree, setWorkspaceState } from "../../data/workspaceApi";
import { isWorkspaceReadme } from "../workspaceReadme";
import { ContextMenu, type ContextMenuItem } from "../../ui-kit/ContextMenu";
import { useHoverCard } from "../../ui-kit/hoverCard";
import { toast } from "../../ui-kit/primitives/Toast";
import { arrange, knownSubject, loadDesktop } from "./desktopApi";
import { toggleHomeTarget, useHomeTarget } from "./home2/HomeTarget";

const KIND_WORD = { private: "Private", shared: "Shared", company: "Company" } as const;
const ROLE_WORD: Record<string, string> = { owner: "Owner", contributor: "Contributor", reader: "Reader" };
/** At most this many faces on a tile; the rest are "+n". */
export const BADGES_SHOWN = 3;
const DESK = "personal";

const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

/** Drag handlers a tile carries so it can be reordered inside its section. */
export type TileDrag = {
  onDragStart: (e: DragEvent<HTMLElement>) => void;
  onDragOver: (e: DragEvent<HTMLElement>) => void;
  onDrop: (e: DragEvent<HTMLElement>) => void;
};

function MoreMenu({ label, items }: { label: string; items: ContextMenuItem[] }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button type="button" ref={btn} className="vx-kh-icon vx-pk-more" aria-label={`More for ${label}`} aria-haspopup="menu"
        onClick={(e) => { e.stopPropagation(); const r = btn.current!.getBoundingClientRect(); setAt({ x: r.left, y: r.bottom + 4 }); }}>…</button>
      {at && <ContextMenu x={at.x} y={at.y} label={label} onClose={() => setAt(null)} items={items} />}
    </>
  );
}

const removeItem = (ref: string): ContextMenuItem => ({ id: "remove", label: "Remove from desktop", onSelect: () => { void arrange([{ op: "remove", ref }]); } });

/** The two letters a face shows — never more, so two faces side by side never read as one word. */
const letters = (initials: string, name: string) => (initials || name || "?").replace(/[^\p{L}\p{N}]/gu, "").slice(0, 2).toUpperCase() || "?";

function Face({ p }: { p: WorkspaceItem["members"][number] }) {
  const here = p.presence === "now" || p.presence === "today" ? p.presence : null;
  const ini = letters(p.initials, p.name);
  const hover = useHoverCard({
    name: p.name, mark: <span className="vx-kh-face-mark">{ini}</span>, cacheKey: `desk-member:${p.subject}:${p.role}:${here ?? ""}`,
    lines: [ROLE_WORD[p.role] ?? p.role, ...(here ? [`Active ${here === "now" ? "now" : "today"} here`] : [])].filter(Boolean),
  });
  return (
    <>
      <button type="button" className="vx-kh-badge" data-badge={p.subject} aria-label={p.name} {...hover.bind}
        onClick={(e) => e.stopPropagation()}>
        {ini}{here && <span className="vx-kh-dot" data-presence={here} aria-hidden />}
      </button>
      {hover.card}
    </>
  );
}

export function Badges({ people }: { people: WorkspaceItem["members"] }) {
  if (!people.length) return null;
  const sorted = [...people].sort((a, b) => Number(!!b.presence) - Number(!!a.presence));
  const shown = sorted.slice(0, BADGES_SHOWN);
  const more = sorted.length - shown.length;
  return (
    <span className="vx-kh-badges" role="group" aria-label={`${people.length} ${people.length === 1 ? "person" : "people"}`}>
      {shown.map((p) => <Face key={p.subject} p={p} />)}
      {more > 0 && <span className="vx-kh-more" data-badges-more="" title={sorted.slice(BADGES_SHOWN).map((p) => p.name).join(", ")}>+{more}</span>}
    </span>
  );
}

/** The platform as an icon, named for screen readers and on hover — the meeting header's own. */
function CallPlatformIcon({ platform }: { platform?: CallPlatform | string }) {
  if (!platform) return null;
  const label = platform === "vexa_call" ? "Vexa call" : platformLabel(platform);
  return (
    <span data-call-platform={platform} role="img" aria-label={label} title={label} className="vx-kh-platform">
      <Video size={12} aria-hidden />
    </span>
  );
}

/** The workspace a tile opens, as the panel addresses it: the person's own desk has no slug. */
const viewSlug = (w: WorkspaceItem): string | undefined => (w.slug === DESK || w.slug === knownSubject() ? undefined : w.slug);
/** The tile's canonical link: `/w/<workspace>/README.md` (the desk by the person's subject id). */
export const workspaceHref = (w: WorkspaceItem): string => workspacePath(w.slug === DESK ? w.id : w.slug, WORKSPACE_FRONT_PAGE);

/** Did this event come from one of the tile's own controls (or a menu it opened)? Then it is theirs. */
export function fromInner(e: { target: EventTarget | null; currentTarget: EventTarget | null }): boolean {
  const t = e.target as HTMLElement | null;
  const tile = e.currentTarget as HTMLElement | null;
  if (!t || !tile || !(t instanceof Node) || !tile.contains(t)) return true;   // a portal (a menu, a hover card)
  return !!t.closest?.("[data-tile-inner], button, a, input, [role=menu]") && t !== tile;
}

export async function hideWorkspace(w: WorkspaceItem): Promise<void> {
  try {
    const r = await setWorkspaceState([w.slug], "hidden", "default", "");
    toast(r?.say || `${w.name} is hidden`);
  } catch {
    toast(`${w.name} could not be hidden just now`);
  }
  await loadDesktop({ force: true });
}

export async function openWorkspace(w: WorkspaceItem): Promise<void> {
  const slug = viewSlug(w);
  const files = await listWorkspaceTree(slug ? { slug } : undefined).catch(() => null);
  const readme = files?.find((f) => isWorkspaceReadme(f));
  // a tree that could not be read: open the README anyway (the page says if it is not there)
  if (readme || files === null) { navigateView(slug, readme ?? WORKSPACE_FRONT_PAGE); return; }
  fire(OPEN_WORKSPACE_FILES_EVENT, { workspace: slug ?? "", name: w.name });
}

export function WorkspaceTile({ w, drag }: { w: WorkspaceItem; drag: TileDrag }) {
  const readOnly = w.write === false;
  const live = w.live_call ?? null;
  const href = workspaceHref(w);
  // ITS README AS A PAGE — the navigator's act (Navigator.openFront): the front page in the panel's
  // view slot. A workspace with no README opens its file tree instead, which offers Create README.
  // Nothing here touches the chat in front: no mount, no target, so no session is ever started.
  const open = () => { void openWorkspace(w); };
  // A CARD CLICK SETS THE TARGET (UI decisions; founder 2026-10-16): the home's chat writes there,
  // and a second click on the aimed card resets it to Personal. Never a workspace they only read.
  const aimed = useHomeTarget() === w.slug;
  const select = () => { if (!readOnly && w.slug) toggleHomeTarget(w.slug); open(); };
  const openNewTab = () => { window.open(href, "_blank", "noopener"); };
  const join = (e: MouseEvent) => {
    e.stopPropagation();
    if (live?.session) fire(OPEN_SESSION_EVENT, { session: live.session });
    else if (live?.meeting) fire(OPEN_MEETING_EVENT, { ref: live.meeting });
  };
  const onClick = (e: MouseEvent<HTMLElement>) => {
    if (fromInner(e)) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey) { e.preventDefault(); openNewTab(); return; }
    select();
  };
  const onAuxClick = (e: MouseEvent<HTMLElement>) => {
    if (e.button !== 1 || fromInner(e)) return;
    e.preventDefault();
    openNewTab();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.target !== e.currentTarget || e.key !== "Enter") return;
    e.preventDefault();
    if (e.metaKey || e.ctrlKey) openNewTab(); else select();
  };
  const menu: ContextMenuItem[] = [
    { id: "open", label: "Open", onSelect: open },
    { id: "open-tab", label: "Open in a new tab", onSelect: openNewTab },
    ...(w.slug !== DESK && w.workspace_kind !== "company" ? [{ id: "hide", label: `Hide ${w.name}`, detail: "Show it again from Hidden workspaces", onSelect: () => { void hideWorkspace(w); } }] : []),
  ];
  return (
    <article className="vx-kh-card" draggable data-desk-tile={w.ref} data-workspace-tile={w.slug} data-kind={w.workspace_kind}
      data-readonly={readOnly ? "" : undefined} data-target={aimed && !readOnly ? "" : undefined} data-href={href} role="link" tabIndex={0}
      aria-label={`Open ${w.name}${readOnly ? " (read-only)" : ""}`} title={`Open ${w.name}`}
      onClick={onClick} onAuxClick={onAuxClick} onKeyDown={onKeyDown} {...drag}>
      <div className="vx-kh-main">
        <span className="vx-kh-head">
          <span className="vx-kh-name">{w.name}</span>
          <span className="vx-kh-meta">{KIND_WORD[w.workspace_kind] ?? w.workspace_kind} · {w.access}</span>
        </span>
        <span className="vx-kh-activity" data-activity="">{w.last_activity ? w.last_activity.line : "No activity yet"}</span>
      </div>
      <span className="vx-kh-tilemore" data-tile-inner=""><MoreMenu label={w.name} items={menu} /></span>
      {(live || w.last_call) && <div className="vx-kh-calls">
        {live && <span className="vx-kh-live" data-live-call="">
          <span className="vx-kh-live-dot" aria-hidden /><CallPlatformIcon platform={live.platform} />
          Call now{live.line ? ` · ${live.line}` : ""}
          {typeof live.people === "number" && ` · ${live.people} ${live.people === 1 ? "person" : "people"}`}
          <button type="button" className="vx-kh-btn vx-kh-btn-primary vx-kh-join" data-tile-inner="" onClick={join}>Join</button>
        </span>}
        {w.last_call && !live && <span className="vx-kh-lastcall-row">
          <CallPlatformIcon platform={w.last_call.platform} />
          <button type="button" className="vx-kh-lastcall" data-tile-inner="" data-last-call={w.last_call.meeting}
            onClick={(e) => { e.stopPropagation(); fire(OPEN_MEETING_EVENT, { ref: w.last_call!.meeting }); }}>Last call · {w.last_call.line}</button>
        </span>}
      </div>}
      {w.members.length > 0 && <div className="vx-kh-foot" data-tile-inner="">
        <Badges people={w.members} />
      </div>}
    </article>
  );
}

function Missing({ item, drag, label }: { item: DesktopItem; drag: TileDrag; label: string }) {
  return (
    <article className="vx-pk-tile vx-desk-missing" draggable data-desk-tile={item.ref} data-missing="" {...drag}>
      <span className="vx-pk-line">{label} · no longer available</span>
      <button type="button" className="vx-kh-btn" onClick={() => { void arrange([{ op: "remove", ref: item.ref }]); }}
        aria-label={`Remove ${label} from desktop`}>Remove</button>
    </article>
  );
}

function Tile({ item, drag, label, menu, onOpen, children }: { item: DesktopItem; drag: TileDrag; label: string; menu: ContextMenuItem[]; onOpen: () => void; children: ReactNode }) {
  return (
    <article className="vx-pk-tile" draggable data-desk-tile={item.ref} {...drag}>
      <button type="button" className="vx-pk-main" onClick={onOpen} aria-label={label}>{children}</button>
      <MoreMenu label={label} items={menu} />
    </article>
  );
}

export function AgentTile({ a, drag }: { a: AgentItem; drag: TileDrag }) {
  if (a.missing) return <Missing item={a} drag={drag} label={a.name || a.id} />;
  const open = () => { if (a.thread) fire(OPEN_SESSION_EVENT, { session: a.thread }); };
  const name = a.name || a.id;
  return (
    <Tile item={a} drag={drag} label={name} onOpen={open}
      menu={[...(a.thread ? [{ id: "open", label: "Open its chat", onSelect: open }] : []), removeItem(a.ref)]}>
      <span className="vx-pk-head"><Bot size={13} aria-hidden /><span className="vx-pk-name">{name}</span>
        {a.status && <span className="vx-pk-status" data-status={a.status}>{a.status}</span>}</span>
      {a.unavailable && <span className="vx-pk-meta">Status unavailable right now</span>}
      {a.what && <span className="vx-pk-line">{a.what}</span>}
      {(a.last || a.next) && <span className="vx-pk-meta">{a.last ? `Last: ${a.last}` : ""}{a.last && a.next ? " · " : ""}{a.next ? `Next: ${a.next}` : ""}</span>}
      {a.where && <span className="vx-pk-meta">{a.where}</span>}
    </Tile>
  );
}

export function ChatTile({ c, drag }: { c: ChatItem; drag: TileDrag }) {
  if (c.missing) return <Missing item={c} drag={drag} label={c.title || "A chat"} />;
  const open = () => { fire(OPEN_SESSION_EVENT, { session: c.id }); };
  return (
    <Tile item={c} drag={drag} label={c.title} onOpen={open} menu={[{ id: "open", label: "Open", onSelect: open }, removeItem(c.ref)]}>
      <span className="vx-pk-head"><MessageSquare size={13} aria-hidden /><span className="vx-pk-name">{c.title}</span></span>
      {(c.last || c.where) && <span className="vx-pk-meta">{c.last}{c.last && c.where ? " · " : ""}{c.where}</span>}
    </Tile>
  );
}

export function FileTile({ f, drag }: { f: FileItem; drag: TileDrag }) {
  if (f.missing) return <Missing item={f} drag={drag} label={f.title || f.path} />;
  // the person's own desk is addressed with no slug
  const open = () => { fire(OPEN_PAGE_EVENT, { workspace: f.slug === knownSubject() ? "" : f.slug, path: f.path }); };
  return (
    <Tile item={f} drag={drag} label={f.title} onOpen={open} menu={[{ id: "open", label: "Open", onSelect: open }, removeItem(f.ref)]}>
      <span className="vx-pk-head"><FileText size={13} aria-hidden /><span className="vx-pk-name">{f.title}</span></span>
      {(f.where || f.changed) && <span className="vx-pk-meta">{f.where}{f.where && f.changed ? " · " : ""}{f.changed}</span>}
    </Tile>
  );
}
