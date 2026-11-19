/** THE CARD SEAM for page blocks (PRD 0.13.4 S7 ↔ S5). Not a route file.
 *
 *  `{{commitments}}`, `{{waiting-on-others}}`, `{{card id}}` and `{{cards …}}` render the card read
 *  interface S5 delivers (card kinds, next actor, per-viewer state). Until it merges, this seam reads
 *  what crm-api serves today with the viewer's own key (`/crm/card/list`, `/crm/card/read`) and maps
 *  it to rows; a crm that refuses or is down answers `null`, which the page draws as one quiet
 *  "pending" state, the same for every viewer. S5 replaces `cardRowsFor` and nothing else.
 *
 *  Rows never carry a card's summary, face or activity: title, state, next actor, due, link. */
import type { Block } from "../../../minutes/readme/grammar";
import { gatewayPost } from "./desk";

export type CardRow = { key: string; title: string; state?: string; detail?: string; who?: string; ref: string; act?: { kind: "open-card"; card: string } };

type CardView = { id?: unknown; title?: unknown; state?: unknown; next?: unknown; waiting?: unknown; due?: unknown; next_actor?: unknown; kind?: unknown };
const s = (v: unknown, n = 200): string => (typeof v === "string" ? v.slice(0, n) : "");

function row(c: CardView): CardRow | null {
  const id = s(c.id, 128);
  if (!id) return null;
  const who = s(c.next_actor) || s(c.waiting);
  const detail = [who ? `waiting on ${who}` : "", s(c.due, 40) ? `due ${s(c.due, 40)}` : ""].filter(Boolean).join(" · ");
  return { key: id, title: s(c.title) || "A card", ...(s(c.state, 60) ? { state: s(c.state, 60) } : {}),
    ...(detail ? { detail } : {}), ...(who ? { who } : {}), ref: `card:${id}`, act: { kind: "open-card", card: id } };
}

async function list(key: string, body: Record<string, unknown>): Promise<CardView[] | null> {
  try {
    const r = await gatewayPost("/crm/card/list", key, body);
    if (!r.ok) return null;
    const d = (await r.json()) as { cards?: unknown };
    return Array.isArray(d?.cards) ? (d.cards as CardView[]) : [];
  } catch { return null; }
}

/** The rows for one card block, for this viewer; `null` = the card interface cannot answer. */
export async function cardRowsFor(block: Block, key: string, workspace: string): Promise<CardRow[] | null> {
  if (block.name === "card") {
    const id = block.positional[0] || block.args.id;
    if (!id) return [];
    try {
      const r = await gatewayPost("/crm/card/read", key, { card: id });
      if (r.status === 404 || r.status === 403) return [];          // not theirs: absent, like nothing
      if (!r.ok) return null;
      const c = (await r.json()) as CardView & { card?: CardView };
      const one = row(c.card ?? c);
      return one ? [one] : [];
    } catch { return null; }
  }
  const cards = await list(key, { ...(workspace && workspace !== "personal" ? { workspace } : { mine: true }), limit: 50 });
  if (cards === null) return null;
  // waiting-on-others keeps cards someone is named on; agent-api drops the ones naming the viewer
  const rows = block.name === "waiting-on-others" ? cards.filter((c) => !!(s(c.next_actor) || s(c.waiting))) : cards;
  return rows.map(row).filter((r): r is CardRow => r !== null);
}
