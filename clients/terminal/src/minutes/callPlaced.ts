/** A CALL THE AGENT PLACED FOR THIS PERSON (call-person-autojoin, 2026-10-18).
 *
 *  The founder typed "Call tester-b"; the agent's `call_person` started a Vexa call in their room of
 *  two and replied with the room. meeting-api ends a call nobody joins within 30 s, so a caller slow
 *  to open the room lost the call. The chat surface now re-emits a successful `call_person` as
 *  CALL_PLACED_EVENT — only in the view whose own message triggered it, only live — and this joins it:
 *  open the room, then join as the caller, the way the room's Start call does (`enterCall`), with no
 *  pre-join bar, shown as an outgoing call with Cancel until somebody else is in it.
 *
 *  THE SERVER DECIDES WHOSE CALL IT IS, not the event: before anything opens, the call is re-read and
 *  joined only when it is still that call (`room`) and its owner is the viewer (`owner == you`). A
 *  stream event that reached the wrong tab therefore opens nothing. One join per call per tab. */
import { getCall, type CallState } from "../surfaces/callsApi";
import type { PlacedCall } from "../surfaces/chatStream";
import { activeCall, enterCall, type ActiveCall, type EnterOpts } from "./callState";

export type PlacedOutcome = "joined" | "already" | "not-mine" | "gone" | "unreadable" | "invalid";

export interface PlacedDeps {
  getCall: (session: string) => Promise<CallState>;
  enter: (session: string, label: string, opts: EnterOpts) => Promise<ActiveCall>;
  /** open the room in the panel (the shell's own opener for a shared chat) */
  open: (session: string) => void;
  active: () => ActiveCall | null;
}

export const livePlacedDeps: PlacedDeps = {
  getCall,
  enter: (session, label, opts) => enterCall(session, label, undefined, opts),
  open: () => undefined,
  active: activeCall,
};

/** Calls (LiveKit rooms) this tab already joined from an agent's call — a reconnecting stream that
 *  hands the same event over again must not join twice. */
const joined = new Set<string>();
export function __resetPlacedCalls(): void { joined.clear(); }

/** The event's detail, shape-checked, or null. */
export function readPlaced(detail: unknown): PlacedCall | null {
  if (!detail || typeof detail !== "object") return null;
  const d = detail as Record<string, unknown>;
  const id = /^[A-Za-z0-9_-]{1,80}$/;
  if (typeof d.session !== "string" || !id.test(d.session) || typeof d.room !== "string" || !id.test(d.room)) return null;
  return { session: d.session, room: d.room, with: typeof d.with === "string" ? d.with.slice(0, 200) : "", alreadyLive: d.alreadyLive === true };
}

export async function joinPlacedCall(detail: unknown, deps: PlacedDeps = livePlacedDeps): Promise<PlacedOutcome> {
  const c = readPlaced(detail);
  if (!c) return "invalid";
  if (joined.has(c.room)) return "already";
  let state: CallState;
  try { state = await deps.getCall(c.session); } catch { return "unreadable"; }
  // Still that call, and this viewer's own: anything else opens nothing at all.
  if (!state.calls || !state.call || state.call.room !== c.room) return "gone";
  if (!state.you || state.call.owner !== state.you) return "not-mine";
  joined.add(c.room);
  deps.open(c.session);
  if (deps.active()?.join.call.room === c.room) return "already";
  const to = c.with || "them";
  try {
    await deps.enter(c.session, c.with ? `Call with ${c.with}` : "Call", { autoJoin: true, outgoing: { to } });
  } catch {
    // the dock says why the call did not start (`enterCall` keeps the error); a later event may retry
    joined.delete(c.room);
    return "unreadable";
  }
  return "joined";
}
