/** The card core's client half (0.13.4 S5; D12–D14, P21, R11).
 *
 *  ONE store for the Inbox read (`inbox {}` → `{items, counts}`): the Now strip, the Inbox header
 *  and a room's badge all read `useCardInbox`, so their counts cannot disagree. The verbs go through
 *  the desktop BFF (`/api/desktop/card/<op>`, the person's own key): `seen` (only from `useSeenInView`),
 *  `nonce` (what the person is looking at: `{nonce, content_hash, revision}`), `decide`.
 *
 *  This module stores nothing durable: crm-api owns cards and enforces every rule. */
import { useEffect, useSyncExternalStore } from "react";
import type { CardAction, CoreCard as CardView } from "../../platform/desktop";
import { cardCall, type CardResult } from "../desktop/cardsApi";
import { rememberSelf, resetSelf } from "./text";

export type InboxCounts = { waiting_on_me: number; due_soon: number; i_wait_on: number };
export type InboxState =
  | { phase: "idle" | "loading" | "failed"; items: CardView[]; counts: InboxCounts | null }
  | { phase: "ready"; items: CardView[]; counts: InboxCounts };

const EMPTY: InboxState = { phase: "idle", items: [], counts: null };
let state: InboxState = EMPTY;
let inflight: Promise<InboxState> | null = null;
const subs = new Set<() => void>();
const emit = (s: InboxState) => { state = s; subs.forEach((cb) => cb()); };

const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Read the Inbox. One request at a time; `force` re-reads even when ready. */
export function loadInbox(opts: { force?: boolean } = {}): Promise<InboxState> {
  if (inflight) return inflight;
  if (!opts.force && state.phase === "ready") return Promise.resolve(state);
  if (state.phase !== "ready") emit({ ...state, phase: "loading" } as InboxState);
  inflight = (async () => {
    const r = await cardCall("inbox", {});
    const items = Array.isArray(r.body.items) ? (r.body.items as CardView[]).filter((c) => c && typeof c.id === "string") : null;
    const c = r.body.counts as Partial<InboxCounts> | undefined;
    if (r.status >= 200 && r.status < 300 && items && c && typeof c === "object") {
      rememberSelf(items);
      emit({ phase: "ready", items, counts: { waiting_on_me: num(c.waiting_on_me), due_soon: num(c.due_soon), i_wait_on: num(c.i_wait_on) } });
    } else if (state.phase !== "ready") {
      emit({ phase: "failed", items: [], counts: null });
    }
    inflight = null;
    return state;
  })();
  return inflight;
}

/** Put a card the server just answered (e.g. after `decide`) into the store, then re-read the counts. */
export function putCard(card: CardView): void {
  if (state.phase === "ready" && state.items.some((x) => x.id === card.id)) {
    emit({ ...state, items: state.items.map((x) => (x.id === card.id ? card : x)) });
  }
  void loadInbox({ force: true });
}

/** tests only */
export function resetInbox(): void { state = EMPTY; inflight = null; resetSelf(); subs.forEach((cb) => cb()); }

export function useCardInbox(load = true): InboxState {
  const s = useSyncExternalStore((cb) => { subs.add(cb); return () => { subs.delete(cb); }; }, () => state, () => EMPTY);
  useEffect(() => { if (load) void loadInbox(); }, [load]);
  return s;
}

/** THE predicate behind `waiting_on_me` — the same one crm-api applies: an open card whose next actor
 *  is this viewer. A room's badge counts it over that workspace's items; the totals come from the
 *  server's `counts`, so a surface never computes a different number from the same answer. */
export const waitingOnMe = (c: CardView): boolean => c.open !== false && c.viewer?.role === "next_actor";

/** The count a surface shows: the server's total, or — for one workspace (a room's badge) — the same
 *  predicate over that workspace's items. `null` until the Inbox answers (nothing is drawn). */
export function useWaitingCount(workspace?: string): number | null {
  const s = useCardInbox();
  if (s.phase !== "ready") return null;
  if (!workspace) return s.counts.waiting_on_me;
  return s.items.filter((c) => (c.workspaces ?? []).includes(workspace) && waitingOnMe(c)).length;
}

// ── seen (D14) ───────────────────────────────────────────────────────────────────────────────
export async function postSeen(card: string): Promise<boolean> {
  const r = await cardCall("seen", { card });
  return r.status >= 200 && r.status < 300;
}

// ── nonce + decide (D13) ─────────────────────────────────────────────────────────────────────
export type Nonce = { nonce: string; content_hash: string; revision: number; expires_at?: string };
export async function fetchNonce(card: string): Promise<Nonce | null> {
  const r = await cardCall("nonce", { card });
  const b = r.body;
  if (r.status < 200 || r.status >= 300 || typeof b.nonce !== "string" || typeof b.content_hash !== "string") return null;
  return { nonce: b.nonce, content_hash: b.content_hash, revision: num(b.revision), ...(typeof b.expires_at === "string" ? { expires_at: b.expires_at } : {}) };
}

export type DecideInput = { action: CardAction; evidence?: string; receipt?: string; answer?: string; reason?: string };
export type DecideResult =
  | { ok: true; card: CardView | null }
  | { ok: false; stale: boolean; line: string };

export const STALE_LINE = "This card changed — check it again";

/** One line for a refusal, never a code. */
export function decideRefusal(r: CardResult): string {
  if (r.status === 409) return STALE_LINE;
  const d = r.body.detail;
  if (typeof d === "string" && d.trim()) return d.trim();
  if (r.status === 403) return "You can't decide this card";
  if (r.status === 0) return "Couldn't reach cards just now";
  return "The decision couldn't be saved just now";
}

export async function decide(card: string, n: Nonce, input: DecideInput): Promise<DecideResult> {
  const r = await cardCall("decide", { card, nonce: n.nonce, content_hash: n.content_hash, ...input });
  if (r.status >= 200 && r.status < 300 && r.body.status === "saved") {
    const c = r.body.card && typeof r.body.card === "object" ? (r.body.card as CardView) : null;
    rememberSelf([c]);
    if (c) putCard(c); else void loadInbox({ force: true });
    return { ok: true, card: c };
  }
  return { ok: false, stale: r.status === 409, line: decideRefusal(r) };
}

export async function readCardCore(card: string): Promise<CardView | null> {
  const r = await cardCall("read", { card });
  if (r.status < 200 || r.status >= 300) return null;
  const c = (r.body.card && typeof r.body.card === "object" ? r.body.card : r.body) as CardView;
  if (!c || typeof c.id !== "string") return null;
  rememberSelf([c]);
  return c;
}

// ── opening + discussing ─────────────────────────────────────────────────────────────────────
/** Opens the card sheet (7q). A row or title press dispatches this and NOTHING else (R11). */
export const OPEN_CARD_SHEET_EVENT = "vexa:terminal:open-card-sheet";
/** "Discuss" / "Resume chat · n" (7p, 7q): the shell opens a chat with the card attached. */
export const CARD_DISCUSS_EVENT = "vexa:terminal:card-discuss";

export function openCardSheet(id: string): void {
  window.dispatchEvent(new CustomEvent(OPEN_CARD_SHEET_EVENT, { detail: { id } }));
}
export function discussCard(id: string): void {
  window.dispatchEvent(new CustomEvent(CARD_DISCUSS_EVENT, { detail: { card: id } }));
}
