/** Cards — the client half (#1783, desktop ADR §2.3, D2; feature `desktop_cards`).
 *
 *  The person's own card verbs, through the desktop BFF (`/api/desktop/card/<op>`, the person's own
 *  key): read one card, make one, update its fields or move its stage, add a member, answer an invite,
 *  and review (Add / Not now) what an agent proposed. crm-api owns cards and enforces every rule; this
 *  module stores nothing — after a change it re-reads the desktop so every surface agrees.
 *
 *  A refusal is one line, never a code. Opening a card from anywhere (a tile, the Make a card toast)
 *  dispatches `OPEN_CARD_EVENT`; the one detail host (`CardDetailHost`) shows it. */
import { useSyncExternalStore } from "react";
import { toast } from "../../ui-kit/primitives/Toast";
import type { CardView } from "../../platform/desktop";
import { loadDesktop } from "./desktopApi";

export const OPEN_CARD_EVENT = "vexa:terminal:open-card";
export type CardResult = { status: number; body: Record<string, unknown> };

export async function cardCall(op: string, body: Record<string, unknown>): Promise<CardResult> {
  try {
    const r = await fetch(`/api/desktop/card/${op}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let parsed: unknown = {};
    try { parsed = await r.json(); } catch { parsed = {}; }
    return { status: r.status, body: parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {} };
  } catch {
    return { status: 0, body: {} };
  }
}

const ok = (r: CardResult) => r.status >= 200 && r.status < 300;

/** What a refusal tells the person — one line. */
export function cardRefusalLine(r: CardResult): string {
  const reason = r.body.reason ?? (r.body.detail as { reason?: unknown } | undefined)?.reason;
  if (r.status === 409 || reason === "revision_conflict" || reason === "stale_revision") return "The card changed elsewhere — it has been reloaded";
  if (reason === "feature_disabled") return "Cards aren't switched on here";
  if (reason === "invalid_transition" || reason === "unknown_state") return "That stage isn't one this card can move to";
  if (r.status === 403) return "You can't change this card";
  if (r.status === 404) return "That card is no longer available";
  return "The card couldn't be changed just now";
}

export function openCard(id: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_CARD_EVENT, { detail: { id } }));
}

/** Opening a card reads it with the person's own key; crm-api marks it seen for them, so the desktop
 *  is reloaded and its "New" clears. */
export async function readCard(id: string): Promise<CardView | null> {
  const r = await cardCall("read", { card: id });
  if (!ok(r)) return null;
  void loadDesktop({ force: true });
  const c = (r.body.card && typeof r.body.card === "object" ? r.body.card : r.body) as CardView;
  return c && typeof c.id === "string" ? c : null;
}

async function after(r: CardResult, done?: string): Promise<boolean> {
  if (!ok(r)) { toast(cardRefusalLine(r)); return false; }
  if (done) toast(done);
  await loadDesktop({ force: true });
  return true;
}

/** Move the stage (only to a declared state; crm-api refuses anything else) or change fields. */
export async function updateCard(card: CardView, patch: { transition?: string; fields?: Record<string, unknown> }): Promise<boolean> {
  const r = await cardCall("update", { card: card.id, expected_revision: card.revision, ...patch });
  return after(r, patch.transition ? `Moved to ${patch.transition}` : "Card updated");
}

export async function addMember(card: string, subject: string): Promise<boolean> {
  return after(await cardCall("members", { card, subject, add: true }), "Added — it waits on their desktop for their Accept");
}

export async function answerInvite(card: string, accept: boolean): Promise<boolean> {
  return after(await cardCall("membership", { card, accept }), accept ? "Card added to your desktop" : undefined);
}

export async function reviewProposal(id: string, accept: boolean): Promise<boolean> {
  return after(await cardCall("review", { proposal_id: id, accept }), accept ? "Card added" : undefined);
}

/** Make a card about a chat or a page. Confirms in one line with Open. */
export async function makeCard(input: { title: string; about: string; workspace?: string | null }): Promise<boolean> {
  const body: Record<string, unknown> = { title: input.title, about: input.about };
  if (input.workspace) body.workspace = input.workspace;
  const r = await cardCall("create", body);
  if (!ok(r)) { toast(cardRefusalLine(r)); return false; }
  const made = (r.body.card && typeof r.body.card === "object" ? r.body.card : null) as CardView | null;
  const id = made && typeof made.id === "string" ? made.id : null;
  toast(`Card made: ${input.title}`, id ? { action: { label: "Open", onClick: () => openCard(id) } } : undefined);
  await loadDesktop({ force: true });
  return true;
}

// ── names for members and movements ───────────────────────────────────────────────────────────
// Cards name people by subject id. The names the person may see are the people they share a
// workspace with (`/api/people/mentionable`); anyone else is shown by id, never guessed.
let names: Record<string, string> = {};
let namesRead: Promise<void> | null = null;
const nameSubs = new Set<() => void>();
export type Person = { subject: string; name: string };
let people: Person[] = [];

export function loadPeople(): Promise<void> {
  if (!namesRead) {
    namesRead = (async () => {
      try {
        const r = await fetch("/api/people/mentionable", { cache: "no-store" });
        if (!r.ok) return;
        const body = (await r.json()) as { people?: { subject?: unknown; name?: unknown }[] };
        people = (body.people ?? []).filter((p) => typeof p?.subject === "string" && p.subject && typeof p.name === "string")
          .map((p) => ({ subject: p.subject as string, name: p.name as string }));
        names = Object.fromEntries(people.map((p) => [p.subject, p.name]));
        nameSubs.forEach((cb) => cb());
      } catch { /* names stay ids */ }
    })();
  }
  return namesRead;
}
export function resetPeople(): void { names = {}; people = []; namesRead = null; }

export function usePeople(): { nameOf: (subject: string) => string; people: Person[] } {
  useSyncExternalStore((cb) => { nameSubs.add(cb); return () => { nameSubs.delete(cb); }; }, () => names, () => names);
  return { nameOf: (s) => names[s] || s, people };
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (!parts.length) return "?";
  return (parts.length === 1 ? parts[0].slice(0, 2) : parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
