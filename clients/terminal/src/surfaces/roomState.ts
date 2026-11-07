/** What each chat is as a room, as this tab last read it (R1, `rooms` feature).
 *
 *  One small store with a hook, like the call state: the header, the composer's @ and the sidebar all
 *  ask the same question ("is this a room, and how is it set?") and must agree. A chat the server does
 *  not know yet (a draft) is not a room. Re-read on demand (after a change) and when the tab regains
 *  focus; never polled. */
import { useEffect, useSyncExternalStore } from "react";
import { readRoom, type RoomSettings } from "./roomsApi";

const cache = new Map<string, RoomSettings | null>();
const inflight = new Map<string, Promise<void>>();
const subs = new Set<() => void>();
let version = 0;
const emit = () => { version += 1; subs.forEach((f) => f()); };

export function refreshRoom(session: string): Promise<void> {
  if (!session) return Promise.resolve();
  const running = inflight.get(session);
  if (running) return running;
  const p = readRoom(session)
    .then((r) => { cache.set(session, r); })
    .catch(() => { cache.set(session, null); })      // a draft, or a server without rooms: not a room
    .finally(() => { inflight.delete(session); emit(); });
  inflight.set(session, p);
  return p;
}

/** Write what a PATCH answered, so every reader updates at once. */
export function rememberRoom(session: string, r: RoomSettings): void { cache.set(session, r); emit(); }

/** the room as last read, or null (not a room / not read yet) */
export function roomOf(session: string | undefined): RoomSettings | null {
  return (session && cache.get(session)) || null;
}
export const isRoom = (session: string | undefined): boolean => !!roomOf(session)?.room;

export function useRoom(session: string | undefined, on: boolean): RoomSettings | null {
  useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => version, () => version);
  useEffect(() => {
    if (!on || !session) return;
    if (!cache.has(session)) void refreshRoom(session);
    const again = () => { void refreshRoom(session); };
    window.addEventListener("focus", again);
    return () => window.removeEventListener("focus", again);
  }, [session, on]);
  return on ? roomOf(session) : null;
}

/** tests */
export function resetRoomState(): void { cache.clear(); inflight.clear(); emit(); }
