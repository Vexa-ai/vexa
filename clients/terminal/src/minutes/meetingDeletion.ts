"use client";
import { useSyncExternalStore } from "react";

/** Meetings whose data this tab just deleted. The header's Delete and the player below it are
 *  separate components; the server row (`artifacts_deleted`) is the truth after a refresh, and this
 *  covers the moment between the delete answering and the meetings list catching up. */
const deleted = new Set<string>();
const listeners = new Set<() => void>();
export function markMeetingDeleted(id: string) { deleted.add(id); listeners.forEach(fn => fn()); }
export function useMeetingDeletedHere(id: string): boolean {
  return useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    () => deleted.has(id), () => false);
}
/** Tests only: start each case with no local deletions. */
export function resetMeetingDeletions() { deleted.clear(); listeners.forEach(fn => fn()); }

/** Has this tab deleted that meeting's data? (For lists that filter outside React's render.) */
export function wasDeletedHere(id: string): boolean { return deleted.has(id); }
/** Re-render on any local deletion; returns how many there have been. */
export function useMeetingDeletions(): number {
  return useSyncExternalStore(fn => { listeners.add(fn); return () => { listeners.delete(fn); }; },
    () => deleted.size, () => 0);
}

/** Fired on `window` after a meeting's data is deleted, with `{ id }`: the page showing it closes. */
export const MEETING_DELETED_EVENT = "vexa:meeting-deleted";

/** DELETE A MEETING'S DATA (#1783, founder: "where is delete?"), through the existing
 *  `DELETE /api/meetings/{id}`. A meeting that is still saving answers 409 (its lifecycle must be
 *  terminal first): that is a wait, not an error — `onWaiting` is told once, and the delete is tried
 *  again every `everyMs` until it lands or `timeoutMs` passes. */
export async function deleteMeetingData(id: string, opts: {
  onWaiting?: () => void; fetchImpl?: typeof fetch; native?: string; sleep?: (ms: number) => Promise<void>;
  everyMs?: number; timeoutMs?: number;
} = {}): Promise<void> {
  const f = opts.fetchImpl ?? fetch;
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>(r => setTimeout(r, ms)));
  const every = opts.everyMs ?? 2000, deadline = Date.now() + (opts.timeoutMs ?? 180_000);
  let told = false;
  for (;;) {
    const r = await f(`/api/meetings/${encodeURIComponent(id)}`, { method: "DELETE" });
    if (r.ok) break;
    if (r.status !== 409) throw new Error("Could not delete the meeting. Try again.");
    if (!told) { told = true; opts.onWaiting?.(); }
    if (Date.now() > deadline) throw new Error("The meeting is still saving. Try again in a minute.");
    await sleep(every);
  }
  markMeetingDeleted(id);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(MEETING_DELETED_EVENT, { detail: { id, native: opts.native } }));
}
