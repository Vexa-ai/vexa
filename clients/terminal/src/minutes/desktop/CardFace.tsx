"use client";
/** A CARD ON THE DESKTOP (#1783, desktop ADR §2.2/§2.10, D2; UI decisions 15c).
 *
 *  The face is composed from the resolved blocks crm-api answers for this viewer, sanitized again here
 *  (`cardFace.ts`); a card that stores no face draws the type's default. Structural blocks draw from the
 *  card itself: the lifecycle stepper with the current stage, what is next, who it waits on, the latest
 *  movement (who, what, when), sub-cards as "n of m done", the members. Data blocks arrive filled; a
 *  field this viewer cannot read draws nothing (no access-request route yet, so no dead text). The tile always says its state,
 *  "Stalled" once it is past the expected cadence, and "New". Plain text only: no links, no markup. */
import { useRef, useState, type DragEvent } from "react";
import { FileText, Folder, MessageSquare, Users, Video } from "lucide-react";
import { FOCUS_WORKSPACE_EVENT, OPEN_MEETING_EVENT, OPEN_PAGE_EVENT, OPEN_SESSION_EVENT } from "../../platform";
import type { CardAbout, CardItem, CardView } from "../../platform/desktop";
import { ContextMenu } from "../../ui-kit/ContextMenu";
import { ago, dayWords, DEFAULT_FACE, sanitizeFace, type FaceBlock } from "./faceBlocks";
import { initials, openCard, usePeople } from "./cardsApi";
import { arrange, knownSubject } from "./desktopApi";

const KIND_WORD: Record<string, string> = { page: "Page", chat: "Chat", workspace: "Workspace", meeting: "Meeting", crm: "Record" };
const KIND_ICON = { page: FileText, chat: MessageSquare, workspace: Folder, meeting: Video, crm: Users } as const;

/** `page:<ws>/<path>` (or the bare target) → what to open. */
export function aboutTarget(a: CardAbout | null): { kind: string; target: string } | null {
  if (!a || typeof a.ref !== "string") return null;
  const prefix = `${a.kind}:`;
  return { kind: a.kind, target: a.ref.startsWith(prefix) ? a.ref.slice(prefix.length) : a.ref };
}

export function openAbout(a: CardAbout | null, title: string): void {
  const t = aboutTarget(a);
  if (!t) return;
  const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));
  if (t.kind === "page") {
    const cut = t.target.indexOf("/");
    const ws = cut > 0 ? t.target.slice(0, cut) : "";
    const path = cut > 0 ? t.target.slice(cut + 1) : t.target;
    fire(OPEN_PAGE_EVENT, { workspace: ws === knownSubject() || ws === "personal" ? "" : ws, path });
  } else if (t.kind === "chat") fire(OPEN_SESSION_EVENT, { session: t.target });
  else if (t.kind === "meeting") fire(OPEN_MEETING_EVENT, { ref: t.target });
  else if (t.kind === "workspace") fire(FOCUS_WORKSPACE_EVENT, { workspace: t.target, name: title });
  else if (t.kind === "crm") window.location.assign(`/crm?record=${encodeURIComponent(t.target)}`);
}

export function Stepper({ states, state }: { states: string[]; state: string }) {
  const at = states.indexOf(state);
  if (!states.length) return null;
  return (
    <span className="vx-dk-steps" data-face="stepper" aria-label={at >= 0 ? `Stage ${at + 1} of ${states.length}: ${state}` : `Stage: ${state}`}>
      {states.map((s, i) => (
        <span key={s} className="vx-dk-step" data-done={at >= 0 && i < at ? "" : undefined} data-current={i === at ? "" : undefined} title={s}>
          {i === at ? s : ""}
        </span>
      ))}
    </span>
  );
}

export function CardMembers({ card }: { card: Pick<CardView, "members"> }) {
  const { nameOf } = usePeople();
  if (!card.members.length) return null;
  const shown = card.members.slice(0, 5);
  return (
    <span className="vx-kh-badges" data-face="people" aria-label={`${card.members.length} on this card`}>
      {shown.map((m) => {
        const name = nameOf(m.subject);
        return (
          <span key={m.subject} className="vx-kh-badge vx-dk-member" data-member={m.subject} data-invited={m.status === "invited" ? "" : undefined}
            title={m.status === "invited" ? `${name} — invited` : name}>{initials(name)}</span>
        );
      })}
      {card.members.length > shown.length && <span className="vx-kh-more">+{card.members.length - shown.length}</span>}
    </span>
  );
}

export function Movement({ card }: { card: Pick<CardView, "movement"> }) {
  const { nameOf } = usePeople();
  const m = card.movement;
  if (!m) return null;
  const who = m.kind === "agent" ? "Vexa" : nameOf(m.by);
  const when = ago(m.at);
  return <span className="vx-dk-move" data-face="movement">{who}: {m.what}{when ? ` · ${when}` : ""}</span>;
}

/** One face block, drawn from the card. Exported for the card detail. */
export function FaceView({ b, c }: { b: FaceBlock; c: CardView }) {
  switch (b.type) {
    case "stepper": return c.side_states.includes(c.state) ? null : <Stepper states={c.states} state={c.state} />;
    case "status": return <span className="vx-dk-status" data-face="status">{c.state}</span>;
    case "next": return c.next ? <span className="vx-dk-next" data-face="next">Next: {c.next}</span> : null;
    case "waiting": return c.waiting ? <span className="vx-dk-next" data-face="waiting">Waiting on {c.waiting}</span> : null;
    case "movement": return <Movement card={c} />;
    case "people": return <CardMembers card={c} />;
    case "subcards": {
      const kids = c.children;
      if (!kids || !kids.total) return null;
      return (
        <span className="vx-dk-subs" data-face="subcards">
          <span className="vx-dk-plabel">{kids.done} of {kids.total} sub-cards done</span>
          <progress className="vx-dk-bar" max={kids.total} value={kids.done} aria-hidden />
        </span>
      );
    }
    case "ref": {
      const t = aboutTarget(c.about);
      if (!t) return null;
      const Icon = KIND_ICON[t.kind as keyof typeof KIND_ICON] ?? FileText;
      return <span className="vx-dk-about" data-face="ref"><Icon size={12} aria-hidden />{KIND_WORD[t.kind] ?? t.kind}</span>;
    }
    case "metric": return <span className="vx-dk-metric" data-face="metric"><span className="v">{b.value}</span>{b.label && <span className="l">{b.label}</span>}</span>;
    case "text": return <span className="vx-dk-text" data-face="text">{b.label ? `${b.label}: ` : ""}{b.text}</span>;
    case "progress": return (
      <span className="vx-dk-progress" data-face="progress">
        <progress className="vx-dk-bar" max={100} value={Math.round(b.value * 100)} aria-label={b.label ?? "Progress"} />
        <span className="vx-dk-plabel">{b.label ?? `${Math.round(b.value * 100)}%`}</span>
      </span>);
    case "due": return <span className="vx-dk-due" data-face="due">{b.label ?? "Due"} {dayWords(b.at)}</span>;
    case "checklist": {
      const done = b.items.filter((x) => x.done).length;
      return (
        <span className="vx-dk-check" data-face="checklist">
          <span className="vx-dk-plabel">{b.label ? `${b.label} · ` : ""}{done} of {b.items.length} done</span>
          {b.items.filter((x) => !x.done).slice(0, 2).map((x, i) => <span key={i} className="vx-dk-checkrow"><span className="box" aria-hidden />{x.text}</span>)}
        </span>);
    }
    case "list": return (
      <span className="vx-dk-list" data-face="list">
        {b.label && <span className="vx-dk-plabel">{b.label}</span>}
        {b.rows.map((r, i) => <span key={i}>· {r}</span>)}
      </span>);
    // A field this viewer cannot read draws nothing until an access request exists (no dead text).
    case "locked": return null;
    default: return null;
  }
}

export function faceOf(c: CardView): FaceBlock[] {
  const face = sanitizeFace(c.face);
  return face.length ? face : DEFAULT_FACE;
}

export function StateChip({ c }: { c: Pick<CardView, "state" | "side_states"> }) {
  return <span className="vx-dk-state" data-card-state="" data-side={c.side_states.includes(c.state) ? "" : undefined}>{c.state}</span>;
}

export type CardDrag = {
  onDragStart: (e: DragEvent<HTMLElement>) => void;
  onDragOver: (e: DragEvent<HTMLElement>) => void;
  onDrop: (e: DragEvent<HTMLElement>) => void;
};

export function CardTile({ c, drag }: { c: CardItem; drag: CardDrag }) {
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const more = useRef<HTMLButtonElement>(null);
  return (
    <article className="vx-dk-card" draggable data-desk-tile={c.ref} data-card-tile={c.id} data-new={c.new ? "" : undefined}
      data-stalled={c.stalled ? "" : undefined} {...drag}>
      <button type="button" className="vx-dk-main" onClick={() => openCard(c.id)} aria-label={`Open card ${c.title}`}>
        <span className="vx-dk-top">
          <StateChip c={c} />
          {c.new && <span className="vx-dk-new" data-new-chip="">New</span>}
          {c.stalled && <span className="vx-dk-stalled" data-stalled-chip="" title="No movement for longer than this kind of card usually goes">Stalled</span>}
          {c.due && <span className="vx-dk-duechip">Due {dayWords(c.due)}</span>}
        </span>
        <span className="vx-dk-title">{c.title}</span>
        {faceOf(c).map((b, i) => <FaceView key={i} b={b} c={c} />)}
      </button>
      <button type="button" ref={more} className="vx-kh-icon vx-pk-more" aria-label={`More for ${c.title}`} aria-haspopup="menu"
        onClick={() => { const r = more.current!.getBoundingClientRect(); setMenu({ x: r.left, y: r.bottom + 4 }); }}>…</button>
      {menu && <ContextMenu x={menu.x} y={menu.y} label={c.title} onClose={() => setMenu(null)} items={[
        { id: "open", label: "Open card", onSelect: () => openCard(c.id) },
        ...(c.about ? [{ id: "about", label: `Open the ${(KIND_WORD[c.about.kind] ?? "item").toLowerCase()}`, onSelect: () => openAbout(c.about, c.title) }] : []),
        { id: "off", label: "Take off my desktop", detail: "The card stays, with its other members", onSelect: () => { void arrange([{ op: "remove", ref: c.ref }]); } },
      ]} />}
    </article>
  );
}
