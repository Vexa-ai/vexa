/** "MESSAGE <person>" — the room of two (S4 `s4-rooms`, UI 7hh; feature `shell2`).
 *
 *  S4 serves `POST /api/rooms/direct {subject}` → `{session_id, created, with}`: the same pair always
 *  lands in the same room. Whether it is offered is the `rooms` feature (`GET /api/features`), the
 *  same switch the server's route answers to — no probe request. */
import { featureOn, useFeature } from "../../platform/features";

/** Is "Message" offered? The `rooms` feature, as the server answered it. */
export function roomsOfTwoAvailable(): boolean { return featureOn("rooms"); }

/** The same answer as a hook (re-renders when the features answer arrives). */
export function useRoomsOfTwo(): boolean { return useFeature("rooms"); }

/** Open (or make, once) the room of two with this account; resolves to its session id. */
export async function openRoomOfTwo(subject: string): Promise<string> {
  const r = await fetch("/api/rooms/direct", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ subject }), cache: "no-store" });
  const d = (await r.json().catch(() => ({}))) as { session_id?: string; detail?: unknown };
  if (!r.ok || !d.session_id) throw new Error(typeof d.detail === "string" ? d.detail : "The room of two could not be opened.");
  return d.session_id;
}

/** Test seam (nothing is cached here). */
export function resetRoomsOfTwo(): void { /* the features answer owns it */ }
