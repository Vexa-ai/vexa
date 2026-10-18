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
