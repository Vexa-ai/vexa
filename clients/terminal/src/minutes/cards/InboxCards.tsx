"use client";
/** The surfaces that list cards from the ONE Inbox store (`useCardInbox`), so their counts agree:
 *  - `InboxCards` — the Inbox list the shell mounts: a header with "n waiting on you" (kept on phone),
 *    then Waiting on you · Due soon · I wait on · Decided, each as calm rows (7m);
 *  - `NowCards` — the Now strip's count and first rows;
 *  - `RoomBadge` — a room's / workspace's "waiting on you" badge.
 *  The card sheet opens above the list (`CardSheetHost`, drawn once however many are mounted). */
import type { CoreCard as CardView } from "../../platform/desktop";
import { useCardInbox, useWaitingCount, waitingOnMe } from "./api";
import { CardRow } from "./CardRow";
import { CardSheetHost } from "./CardSheet";
import "./cards.css";

const isDueSoon = (c: CardView, now = Date.now()) => {
  if (!c.due || c.open === false) return false;
  const t = Date.parse(c.due);
  return !Number.isNaN(t) && t - now < 3 * 86_400_000;
};

function Section({ title, items, roomName }: { title: string; items: CardView[]; roomName?: (s: string) => string | null }) {
  if (!items.length) return null;
  return <section className="vx-cin-sec" aria-label={title}>
    <h3 className="vx-cin-h">{title} · {items.length}</h3>
    <div className="vx-cin-rows">{items.map((c) => <CardRow key={c.id} card={c} roomName={roomName} />)}</div>
  </section>;
}

export function InboxCards({ roomName }: { roomName?: (session: string) => string | null }) {
  const s = useCardInbox();
  const count = useWaitingCount();
  const items = s.items;
  const mine = items.filter(waitingOnMe);
  const due = items.filter((c) => !waitingOnMe(c) && isDueSoon(c));
  const theirs = items.filter((c) => c.open !== false && !waitingOnMe(c) && !isDueSoon(c) && c.viewer?.role === "requester");
  const done = items.filter((c) => c.open === false);
  return <div className="vx-cin" data-inbox-cards="">
    <header className="vx-cin-head"><h2>Inbox</h2>{count !== null && <span className="vx-cin-count" data-count={count}>{count} waiting on you</span>}</header>
    {s.phase === "failed" && <p className="vx-cin-note">The Inbox couldn't be read just now.</p>}
    {s.phase === "ready" && !items.length && <p className="vx-cin-note">Nothing waits on you.</p>}
    <Section title="Waiting on you" items={mine} roomName={roomName} />
    <Section title="Due soon" items={due} roomName={roomName} />
    <Section title="You wait on" items={theirs} roomName={roomName} />
    <Section title="Decided" items={done} roomName={roomName} />
    <CardSheetHost />
  </div>;
}

/** The Now strip's slice: the shared count and the first rows that wait on you; nothing until the
 *  Inbox answers. */
export function NowCards({ limit = 3 }: { limit?: number }) {
  const s = useCardInbox();
  const count = useWaitingCount();
  if (s.phase !== "ready" || !count) return null;
  return <div className="vx-cin-now" data-now-cards="">
    <p className="vx-cin-nowcount" data-count={count}>{count} waiting on you</p>
    {s.items.filter(waitingOnMe).slice(0, limit).map((c) => <CardRow key={c.id} card={c} />)}
    <CardSheetHost />
  </div>;
}

/** A room's badge: how many cards in that workspace wait on you (same predicate as the server). */
export function RoomBadge({ workspace }: { workspace: string }) {
  const n = useWaitingCount(workspace);
  if (!n) return null;
  return <span className="vx-cin-badge" data-room-badge={workspace} data-count={n} aria-label={`${n} waiting on you`}>{n}</span>;
}
