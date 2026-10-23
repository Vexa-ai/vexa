/** The card surfaces' shared hooks: seen-in-view (D14, P21), the held Decline (R11), the nonce (D13),
 *  and the names a card shows. */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";
import type { CoreCard as CardView } from "../../platform/desktop";
import { dismissToast, toast } from "../../ui-kit/primitives/Toast";
import { loadPeople, usePeople } from "../desktop/cardsApi";
import { useDesktopState } from "../desktop/desktopApi";
import { fetchNonce, postSeen, type Nonce } from "./api";
import type { NameOf } from "./text";

// ── seen (D14, P21) ──────────────────────────────────────────────────────────────────────────
export const SEEN_RATIO = 0.5;
export const SEEN_DWELL_MS = 1000;

/** Writes `seen` ONLY when the card is actually in view: at least half of it visible for a full
 *  second while the document is visible. Never on fetch, never on hover. Once per card id per mount;
 *  a card the viewer has already seen is not written again. */
export function useSeenInView(ref: RefObject<Element | null>, card: CardView | null): void {
  const posted = useRef<Set<string>>(new Set());
  const id = card?.id ?? null;
  const already = !!card?.viewer?.seen;
  useEffect(() => {
    const el = ref.current;
    if (!el || !id || already || posted.current.has(id) || typeof IntersectionObserver === "undefined") return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let inView = false;
    const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
    const arm = () => {
      clear();
      if (!inView || document.visibilityState !== "visible" || posted.current.has(id)) return;
      timer = setTimeout(() => {
        timer = null;
        if (!inView || document.visibilityState !== "visible" || posted.current.has(id)) return;
        posted.current.add(id);
        void postSeen(id);
      }, SEEN_DWELL_MS);
    };
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) inView = e.isIntersecting && e.intersectionRatio >= SEEN_RATIO;
      arm();
    }, { threshold: [0, SEEN_RATIO, 1] });
    io.observe(el);
    const vis = () => arm();
    document.addEventListener("visibilitychange", vis);
    return () => { clear(); io.disconnect(); document.removeEventListener("visibilitychange", vis); };
  }, [ref, id, already]);
}

// ── the held Decline (R11) ───────────────────────────────────────────────────────────────────
/** A Decline is HELD in this browser for `DECLINE_HOLD_MS` with an Undo, and only then sent. The hold
 *  lives at module level, so closing the sheet or scrolling the row away does not cancel the send;
 *  only Undo does. Nothing reaches crm-api during the window. */
export const DECLINE_HOLD_MS = 5000;
const held = new Map<string, { timer: ReturnType<typeof setTimeout>; toastId?: string | number }>();
const heldSubs = new Set<() => void>();
let heldVersion = 0;
const heldEmit = () => { heldVersion++; heldSubs.forEach((cb) => cb()); };

export function holdDecline(card: string, send: () => void, label = "Declined"): void {
  if (held.has(card)) return;
  const timer = setTimeout(() => { held.delete(card); heldEmit(); send(); }, DECLINE_HOLD_MS);
  const entry: { timer: ReturnType<typeof setTimeout>; toastId?: string | number } = { timer };
  held.set(card, entry);
  heldEmit();
  entry.toastId = toast(label, { undo: () => undoDecline(card) });
}
export function undoDecline(card: string): void {
  const h = held.get(card);
  if (!h) return;
  clearTimeout(h.timer);
  held.delete(card);
  heldEmit();
  // an Undo pressed in the row or the sheet takes the toast down too, so it never says "Declined" after
  if (h.toastId !== undefined) dismissToast(h.toastId);
}
export function useDeclineHeld(card: string): boolean {
  useSyncExternalStore((cb) => { heldSubs.add(cb); return () => { heldSubs.delete(cb); }; }, () => heldVersion, () => 0);
  return held.has(card);
}

// ── the nonce (D13) ──────────────────────────────────────────────────────────────────────────
/** The nonce + content hash of the face the person is looking at: fetched when a decision block is
 *  rendered for a card with actions, and again whenever the card's revision changes. */
export function useNonce(card: string, revision: number, enabled: boolean): { nonce: Nonce | null; failed: boolean; refetch: () => Promise<Nonce | null> } {
  const [nonce, setNonce] = useState<Nonce | null>(null);
  const [failed, setFailed] = useState(false);
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  const refetch = useCallback(async () => {
    const n = await fetchNonce(card);
    if (live.current) { setNonce(n); setFailed(!n); }
    return n;
  }, [card]);
  useEffect(() => { if (enabled) { setNonce(null); void refetch(); } }, [enabled, revision, refetch]);
  return { nonce, failed, refetch };
}

// ── names ────────────────────────────────────────────────────────────────────────────────────
/** person ids → names (the people the viewer shares a workspace with); unknown → "Someone" */
export function useNames(): NameOf {
  const { nameOf } = usePeople();
  useEffect(() => { void loadPeople(); }, []);
  return nameOf;
}

/** workspace slug → its name, from the desktop the shell already read (no extra request) */
export function useWorkspaceNames(): (slug: string) => string {
  const d = useDesktopState(false);
  const names = new Map<string, string>();
  if (d.desktop) for (const s of d.desktop.sections) for (const it of s.items) if (it.kind === "workspace") names.set(it.slug || "personal", it.name);
  return (slug) => names.get(slug) ?? slug;
}
