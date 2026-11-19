/** SHOW A DOCUMENT TO THE ROOM (JTBD-1, `rooms`; a room whose call is on Meet, not in the app).
 *
 *  A member picks "Show to room" on an open page; agent-api keeps what the room is showing and the
 *  room's `messages` read (which every member's app polls) carries it. Here each member's app opens it
 *  in the right panel — once per show, so somebody who closes it stays closed until the next show.
 *  The presenter keeps control: only they replace or stop it (the server says so). */
import { useSyncExternalStore } from "react";
import { featureOn } from "../platform/features";
import { ROOM_SHOWING_EVENT } from "../platform/events";
import { getJson } from "../data/apiClient";
import { openDocOnRight } from "./desktop/home2/acts";

/** the room chat re-reads its lines on this event (surfaces/chat ROOM_LINES_EVENT) */
export const ROOM_LINES_REFRESH = "vexa:terminal:room-lines";

export type RoomShowing = { by: string; workspace: string; path: string; title: string; seq: number } | null;
type Entry = { showing: RoomShowing; you: string };

const state = new Map<string, Entry>();
const opened = new Map<string, number>();
const subs = new Set<() => void>();
let version = 0;
const emit = () => { version += 1; subs.forEach((f) => f()); };

/** Test seam. */
export function resetRoomShow(): void { state.clear(); opened.clear(); emit(); }

export function roomShowState(session: string): Entry | undefined { return state.get(session); }

/** One read of the room: remember what is shown, and open it the first time this show is seen. */
export function roomShowing(session: string, showing: RoomShowing, you: string, names: Record<string, string>,
  open: (slug: string, path: string, label?: string) => void = openDocOnRight): "opened" | "same" | "none" | "off" {
  if (!featureOn("rooms")) return "off";
  const prev = state.get(session);
  if (prev?.showing?.seq !== showing?.seq || prev?.you !== you) { state.set(session, { showing, you }); emit(); }
  if (!showing) return "none";
  if (opened.get(session) === showing.seq) return "same";
  opened.set(session, showing.seq);
  const who = showing.by === you ? "you" : names[showing.by] || "someone";
  open(showing.workspace, showing.path, `${showing.title} · shown by ${who}`);
  return "opened";
}

/** React: what this room is showing and whether this person is the one showing it. */
export function useRoomShow(session: string | undefined): { showing: RoomShowing; mine: boolean } {
  useSyncExternalStore((cb) => { subs.add(cb); return () => { subs.delete(cb); }; }, () => version, () => version);
  const e = session ? state.get(session) : undefined;
  return { showing: e?.showing ?? null, mine: !!e?.showing && e.showing.by === e.you };
}

/** The presenter stops showing. */
export async function stopRoomShow(session: string, page: { workspace: string; path: string }): Promise<void> {
  await getJson(`/api/sessions/${encodeURIComponent(session)}/call/show`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...page, stop: true }) });
  const e = state.get(session);
  if (e) { state.set(session, { ...e, showing: null }); emit(); }
}

// the room chat (surfaces/chat) reads the room's lines and says what is shown; this side opens it
if (typeof window !== "undefined") {
  window.addEventListener(ROOM_SHOWING_EVENT, (e) => {
    const d = (e as CustomEvent<{ session?: string; showing?: RoomShowing; you?: string; names?: Record<string, string> }>).detail;
    if (d?.session) roomShowing(d.session, d.showing ?? null, String(d.you ?? ""), d.names ?? {});
  });
}
