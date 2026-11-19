/** WHAT THE CENTRE SHOWS BESIDES A CONVERSATION (UI 7k amended, founder 2026-10-17; feature `shell2`).
 *
 *  The centre is the home, a conversation (chat or room), or one of two LISTS opened from the
 *  sidebar: the Inbox and Meetings. Items inside a list follow 7d — a card, a page or a meeting opens
 *  in the right panel, a chat opens in the centre (which closes the list).
 *
 *  One writer: `openCentre` / `closeCentre`. A list pushes one history entry, so the system back
 *  (Android back, the iOS edge swipe) closes it instead of leaving the app (mobile G2). */
import { useSyncExternalStore } from "react";

export type CentreList = "inbox" | "meetings" | "people";
let current: CentreList | null = null;
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());
const HISTORY_KEY = "vexaCentre";

export function openCentre(kind: CentreList): void {
  if (current === kind) return;
  const fresh = current === null;
  current = kind;
  try {
    // the list's own entry: it inherits the router's state but not another layer's mark, so closing
    // the drawer it was opened from does not pop it
    const st: Record<string, unknown> = { ...((window.history.state as Record<string, unknown> | null) ?? {}), [HISTORY_KEY]: kind };
    delete st.vexaLayer;
    if (fresh) window.history.pushState(st, ""); else window.history.replaceState(st, "");
  } catch { /* no history (tests) */ }
  emit();
}

/** Close the list. `fromHistory`: the back button already popped our entry. `handOver`: another
 *  layer (the drawer) takes the entry over — mark it, never pop it (a pop would race that layer). */
export function closeCentre(fromHistory = false, handOver = false): void {
  if (current === null) return;
  current = null;
  if (handOver) {
    try { const st = { ...((window.history.state as Record<string, unknown> | null) ?? {}) }; delete st[HISTORY_KEY]; window.history.replaceState(st, ""); } catch { /* no history */ }
  } else if (!fromHistory) {
    try { if ((window.history.state as Record<string, unknown> | null)?.[HISTORY_KEY]) window.history.back(); } catch { /* no history */ }
  }
  emit();
}

export function centreNow(): CentreList | null { return current; }

if (typeof window !== "undefined") {
  window.addEventListener("popstate", (e) => {
    const k = (e.state as Record<string, unknown> | null)?.[HISTORY_KEY];
    if (k === "inbox" || k === "meetings" || k === "people") { current = k; emit(); return; }
    if (current !== null) closeCentre(true);
  });
}

const sub = (f: () => void) => { subs.add(f); return () => { subs.delete(f); }; };
const snap = () => current;
export function useCentre(): CentreList | null { return useSyncExternalStore(sub, snap, () => null); }

/** Test seam. */
export function resetCentre(): void { current = null; emit(); }
