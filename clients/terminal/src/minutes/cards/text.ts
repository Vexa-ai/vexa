/** What a card says to THIS viewer (7m, 7q): the verb on its one action, who it waits on and for how
 *  long, and — once decided — the folded line ("Signed by Ana · Thu"). Pure, so every surface says
 *  the same words. Names come from the caller's `nameOf` (people the viewer shares a workspace with);
 *  an unknown person is "Someone", never an id. */
import type { CardAction, CoreCard as CardView } from "../../platform/desktop";
import { formatDate } from "../../ui-kit/format/date";

export const ACTION_LABEL: Record<CardAction, string> = {
  acknowledge: "Accept", decide: "Approve", confirm: "Confirm", answer: "Answer", deliver: "Deliver", use: "Use", decline: "Decline",
};
/** the past tense a decision folds to */
const DONE_WORD: Record<string, string> = {
  acknowledge: "Accepted", decide: "Approved", confirm: "Signed", answer: "Answered", deliver: "Delivered", use: "Used", decline: "Declined",
};
/** the order the one visible action is chosen in; Decline is never it (R11) */
const PRIMARY_ORDER: CardAction[] = ["acknowledge", "decide", "confirm", "answer", "deliver", "use"];

export const viewerActions = (c: CardView): CardAction[] => (c.open === false ? [] : c.viewer?.actions ?? []);
export function primaryAction(c: CardView): CardAction | null {
  const acts = viewerActions(c);
  return PRIMARY_ORDER.find((a) => acts.includes(a)) ?? null;
}

export type NameOf = (id: string) => string;

/** The viewer's own person id(s), learned from the cards crm-api answered: a card whose viewer role is
 *  `next_actor` names the viewer as its `next_actor`, one whose role is `requester` as its `requester`.
 *  `mentionable` never lists the viewer, so without this the viewer's own name would read "Someone". */
const selfIds = new Set<string>();
export function rememberSelf(cards: readonly (CardView | null | undefined)[]): void {
  for (const c of cards) {
    if (!c?.viewer) continue;
    if (c.viewer.role === "next_actor" && c.next_actor) selfIds.add(c.next_actor);
    if (c.viewer.role === "requester" && c.requester) selfIds.add(c.requester);
  }
}
/** tests only */
export function resetSelf(): void { selfIds.clear(); }

/** a name, "you" for the viewer, or "Someone" — never a raw id */
export const personName = (nameOf: NameOf, id: string | null | undefined, me?: boolean): string => {
  if (me || (id && selfIds.has(id))) return "you";
  if (!id) return "Someone";
  const n = nameOf(id);
  return n && n !== id ? n : "Someone";
};

/** "8 h", "25 min", "3 d" — how long something has waited */
export function waited(since: string | null | undefined, now = Date.now()): string {
  if (!since) return "";
  const t = Date.parse(since);
  if (Number.isNaN(t)) return "";
  const m = Math.max(0, Math.round((now - t) / 60_000));
  if (m < 60) return `${Math.max(1, m)} min`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h`;
  return `${Math.round(h / 24)} d`;
}

/** "Waiting on Ana · 8 h" when the viewer is not the next actor; null otherwise */
export function waitingLine(c: CardView, nameOf: NameOf, now = Date.now()): string | null {
  if (c.open === false || c.viewer?.role === "next_actor") return null;
  if (!c.next_actor) return null;
  const since = c.handoff?.since ?? c.last_decision?.at ?? null;
  const age = waited(since, now);
  return `Waiting on ${personName(nameOf, c.next_actor)}${age ? ` · ${age}` : ""}`;
}

/** "Signed by Ana · Thu" / "Accepted by Valeriia · 4:48 PM" once the card is decided; null while open */
export function decidedLine(c: CardView, nameOf: NameOf, opts: { now?: Date } = {}): string | null {
  if (c.open !== false) return null;
  const d = c.last_decision ?? c.decisions?.[0] ?? null;
  if (!d) return c.state ? c.state[0].toUpperCase() + c.state.slice(1) : "Done";
  const word = DONE_WORD[d.action] ?? d.action[0].toUpperCase() + d.action.slice(1);
  const when = d.at ? formatDate(d.at, opts.now ? { now: opts.now } : {}) : "";
  return `${word} by ${personName(nameOf, d.by)}${when ? ` · ${when}` : ""}`;
}

/** the muted tail of a row: due · workspace · room */
export function tailOf(c: CardView, wsName: (slug: string) => string, roomName?: (session: string) => string | null): string {
  const due = c.due ? `due ${formatDate(c.due)}` : "";
  const ws = c.workspaces?.[0] ? wsName(c.workspaces[0]) : "";
  const session = originSession(c.origin);
  const room = session && roomName ? roomName(session) : null;
  return [due, ws, room ? `#${room.replace(/^#/, "")}` : ""].filter(Boolean).join(" · ");
}

/** `session:<id>` or `message:<session>/<msg>` → the chat it came from */
export function originSession(origin: string | null | undefined): string | null {
  if (!origin) return null;
  if (origin.startsWith("session:")) return origin.slice(8) || null;
  if (origin.startsWith("message:")) return origin.slice(8).split("/")[0] || null;
  return null;
}
export function originTurn(origin: string | null | undefined): string | null {
  if (!origin?.startsWith("message:")) return null;
  return origin.slice(8).split("/")[1] || null;
}
