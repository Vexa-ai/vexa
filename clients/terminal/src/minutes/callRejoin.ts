/** ONE LIVE CALL PER PLACE (founder validation O1, felt on the lab mock 2026-10-18).
 *
 *  The founder pressed "Start a meeting" four times and Meetings listed four live calls with the same
 *  name. Starting a call where you already have one live now rejoins it, or asks first:
 *  "<call> is still running. Rejoin it?" — Rejoin / Start a new one. The same holds when you call a
 *  person you are already in a call with.
 *
 *  The decisions are pure and tested here; the ask is a tiny store the shell renders (`RejoinAsk`).
 *  The facts come from what the tab already has: the call this tab is in (`callState`), and the
 *  meetings store — a live Vexa call carries the session it is held in (`call_session`) and its room. */
import { useSyncExternalStore } from "react";
import type { MeetingMock } from "../surfaces/meetingModel";
import { meetingPhase } from "../surfaces/meetingModel";
import { callLabel } from "./callsList";

/** Where a call would be started. `desk` = a new call of your own, in no room (Start a meeting's
 *  quick path, "Think out loud"); `session` = this chat or room. */
export type CallContext = { kind: "desk" } | { kind: "session"; session: string };

export interface LiveOwnCall { session: string; label: string; meetingId?: string }

/** The call already live where you are about to start one, or null.
 *  - the call this tab is in counts when it is in that place (for the desk: any call not in a room);
 *  - a live Vexa call in the meetings store counts when it is yours (not shared with you), and is in
 *    that place: the same session, or — for the desk — one held in no room. */
export function liveCallIn(ctx: CallContext, meetings: readonly MeetingMock[],
  active: { session: string; label: string; room?: boolean } | null): LiveOwnCall | null {
  if (active) {
    if (ctx.kind === "session" ? active.session === ctx.session : !active.room) return { session: active.session, label: active.label };
  }
  for (const m of meetings) {
    if (m.platform !== "vexa_call" || m.shared || !m.call_session || meetingPhase(m) !== "live" || m.artifacts_deleted) continue;
    const here = ctx.kind === "session" ? m.call_session === ctx.session : !m.call_room;
    if (here) return { session: m.call_session, label: callLabel(m), meetingId: m.id };
  }
  return null;
}

export const rejoinQuestion = (label: string): string => `${label || "Your call"} is still running. Rejoin it?`;

// ── the ask ─────────────────────────────────────────────────────────────────────────────────────
export interface RejoinAskState { label: string; onRejoin: () => void; onNew: () => void }
let current: RejoinAskState | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function askRejoin(a: RejoinAskState): void { current = a; emit(); }
export function closeRejoin(): void { current = null; emit(); }
export function rejoinAskNow(): RejoinAskState | null { return current; }
export function useRejoinAsk(): RejoinAskState | null {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => current, () => current);
}

/** Start a call in `ctx` without a duplicate: no live call there → `start()` at once; one there →
 *  ask, and run `rejoin(found)` or `start()` on the answer. Returns what happened right away. */
export function startOrAsk(ctx: CallContext, deps: {
  meetings: readonly MeetingMock[];
  active: { session: string; label: string; room?: boolean } | null;
  start: () => void;
  rejoin: (c: LiveOwnCall) => void;
  ask?: (a: RejoinAskState) => void;
}): "started" | "asked" {
  const found = liveCallIn(ctx, deps.meetings, deps.active);
  if (!found) { deps.start(); return "started"; }
  (deps.ask ?? askRejoin)({
    label: found.label,
    onRejoin: () => { closeRejoin(); deps.rejoin(found); },
    onNew: () => { closeRejoin(); deps.start(); },
  });
  return "asked";
}
