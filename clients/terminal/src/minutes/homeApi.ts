/** homeApi — the new-chat home's two doors (#1783): read the stored composition, report an outcome.
 *
 *  THE READ IS A STORE READ. `GET /api/home` answers the latest composition the background
 *  `home_compose` flow wrote; it never asks a model (founder: no LLM call per page view). When the
 *  home is stale the server asks the flow to compose and still answers with what it has.
 *
 *  NEVER THROWS, like `proposalsApi`: a new chat that would not open because its home could not be
 *  read is worse than a new chat that shows the standing chips. `null` means "draw the chips".
 *
 *  Also here, pure and tested: the prompts a home button sends, and the id of the 1:1 shared chat
 *  two people on Vexa have with each other. */
import { sanitizeHome, type Block, type BlockCard as Card, type BlockPerson as Person, type HomeDoc } from "../ui-kit";

/** `people`: whether the People strip is drawn (`home_people`, default on). The cards themselves are
 *  whatever the server composed — none at all while `home_cards` is off (the release default). */
export type HomeRead = { doc: HomeDoc; you: string; stage: string; people: boolean };

export async function readHome(connected: string[], tz: string): Promise<HomeRead | null> {
  try {
    const q = new URLSearchParams();
    if (connected.length) q.set("connected", connected.slice(0, 8).join(","));
    if (tz) q.set("tz", tz.slice(0, 64));
    const r = await fetch(`/api/home?${q.toString()}`, { cache: "no-store" });
    if (!r.ok) return null;
    const body = (await r.json()) as { view?: unknown; you?: unknown; stage?: unknown; people?: unknown };
    const doc = sanitizeHome(body.view);
    return doc ? { doc, you: typeof body.you === "string" ? body.you : "", stage: typeof body.stage === "string" ? body.stage : "",
      people: body.people !== false } : null;
  } catch {
    return null;
  }
}

export type Signal = "click" | "dismiss" | "snooze" | "sent";
export type Reason = "wrong" | "done" | "not_now";

/** One outcome on one card. Fire-and-forget: a lost signal costs a little learning, never a click. */
export function sendFeedback(card: Card, signal: Signal, extra: { reason?: Reason; chat?: string; people?: string[]; title?: string } = {}): void {
  try {
    void fetch("/api/home/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card: card.id, kind: card.kind, signal, ...extra }),
    }).catch(() => {});
  } catch { /* see the header */ }
}

/** EXPAND IN PLACE: the card, elaborated by the cheapest configured model from what it says. */
export async function expandCard(card: Card): Promise<{ blocks: Block[] } | null> {
  try {
    const { patchedAt: _p, ...plain } = card;
    const r = await fetch("/api/home/expand", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ item: card.id, card: plain }),
    });
    if (!r.ok) return null;
    const body = (await r.json()) as { blocks?: unknown };
    return Array.isArray(body.blocks) ? { blocks: body.blocks as Block[] } : null;
  } catch {
    return null;
  }
}

/** The words a home button sends, with the item it came from — so the turn that finishes the work
 *  records where it got to (`home_item_update`) and the card continues that chat next time. */
export function homePrompt(prompt: string, item: string): string {
  return `${prompt}\n\n(This came from your home, item ${item}. When it is done, or a prep, draft or reply exists, ` +
    `call home_item_update with item "${item}", the state it reached and this chat's thread id.)`;
}

/** The 1:1 shared chat between two accounts: the SAME id from either side, so "Message" opens the
 *  existing conversation for both people rather than starting a second one. */
export function directChatId(a: string, b: string): string {
  const key = [a, b].sort().join("|");
  let h1 = 0x811c9dc5, h2 = 0x01000193;
  for (let i = 0; i < key.length; i++) {
    const c = key.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 16777619) >>> 0;
    h2 = Math.imul(h2 ^ c, 2246822519) >>> 0;
  }
  return `dm-${h1.toString(16).padStart(8, "0")}${h2.toString(16).padStart(8, "0")}`;
}

/** An external person: an agent chat addressed to them that only DRAFTS. Sending is the person's
 *  explicit go on the final text, every time. */
export function externalMessagePrompt(p: Person): string {
  const to = p.email ? `${p.name} <${p.email}>` : p.name;
  const via = p.reach ? ` Use the channel we usually use with them (${p.reach}).` : "";
  return `Draft a message to ${to}.${via} Base it on our most recent exchange and ask me what it should say if that is unclear. ` +
    "Leave it as a draft, list any commitments it makes, and do not send anything until I give my explicit go on the final text.";
}

export function personPagePrompt(p: Person): string {
  return `Open ${p.name}'s page: our meetings, chats and notes with them.`;
}

/** The chat a card's prompt is fired into takes the card's title as its name. */
export function cardTitle(c: Card): string {
  for (const b of c.blocks) {
    if (b.type === "title") return b.text;
    if (b.type === "row") return b.text;
  }
  return c.label ?? "Chat";
}
