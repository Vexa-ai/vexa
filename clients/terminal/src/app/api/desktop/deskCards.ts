/** The cards on the person's desk, as the attention rules need them (#1783, R3 `home2`, E1).
 *  Not a route file. crm-api is the only owner of cards and agent-api cannot read them, so the BFF
 *  reads the desk with the person's own key and hands agent-api only the fields its rules use —
 *  never a card's summary, face or activity. A crm that refuses or is down answers no cards: cards
 *  add items to the home, they never gate it. */
import { gatewayPost } from "./desk";

export type CardIn = {
  id: string; title: string; state: string; next: string | null; waiting: string | null; due: string | null;
  workspaces: string[]; members: { subject: string; status: string }[];
};

const MAX_CARDS = 100;
const str = (v: unknown): string | null => (typeof v === "string" ? v.slice(0, 500) : null);

export function cardsIn(raw: unknown): CardIn[] {
  const list = Array.isArray(raw) ? raw : [];
  const out: CardIn[] = [];
  for (const c of list.slice(0, MAX_CARDS)) {
    if (!c || typeof c !== "object" || typeof (c as { id?: unknown }).id !== "string") continue;
    const o = c as Record<string, unknown>;
    out.push({
      id: String(o.id), title: str(o.title) ?? "", state: str(o.state) ?? "", next: str(o.next), waiting: str(o.waiting), due: str(o.due),
      workspaces: (Array.isArray(o.workspaces) ? o.workspaces : []).filter((w): w is string => typeof w === "string").slice(0, 20),
      members: (Array.isArray(o.members) ? o.members : [])
        .filter((m): m is { subject: string; status?: unknown } => !!m && typeof (m as { subject?: unknown }).subject === "string")
        .slice(0, 50).map((m) => ({ subject: m.subject, status: typeof m.status === "string" ? m.status : "member" })),
    });
  }
  return out;
}

export async function deskCards(key: string): Promise<CardIn[]> {
  try {
    const r = await gatewayPost("/crm/desk/read", key, {});
    if (!r.ok) return [];
    return cardsIn(((await r.json()) as { cards?: unknown })?.cards);
  } catch {
    return [];
  }
}
