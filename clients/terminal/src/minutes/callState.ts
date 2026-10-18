/** The call this tab is in — one at a time, held outside any chat so it survives moving between
 *  chats (the shared chat and the private one both sit beside it). ADR-0048 decision 6.
 *
 *  A module-level store with a hook, like the voice-note recorder: the dock renders from it, the Call
 *  button writes it. Joining is two steps the server owns — start (if the session has no call yet)
 *  and a room token — and the store only remembers the result. */
import { useSyncExternalStore } from "react";
import { getCall, joinCall, startCall, type CallJoin } from "../surfaces/callsApi";
import { readScope } from "../data/workspaceApi";
import { roomOf } from "../surfaces/roomState";

export interface ActiveCall {
  /** the chat the call belongs to (its id as the rail knows it) */
  session: string;
  /** the shared chat's label, for the dock's header */
  label: string;
  join: CallJoin;
  /** connect at once, without the pre-join bar — the caller's own call the agent just started
   *  (`callPlaced.ts`): a call nobody joins ends within 30 s, so the bar's Join would come too late */
  autoJoin?: boolean;
  /** this person is calling someone and nobody else has joined yet: the dock shows "Calling <to>…"
   *  with Cancel until another person is in the call */
  outgoing?: { to: string };
}

export interface EnterOpts {
  autoJoin?: boolean;
  outgoing?: { to: string };
}

export interface CallDeps {
  getCall: typeof getCall;
  startCall: typeof startCall;
  joinCall: typeof joinCall;
  /** the chat runs under the incognito profile: its calls are not transcribed */
  incognito?: (session: string) => Promise<boolean>;
}
/** An incognito chat (the profile chip) — its call starts no transcriber. Unreadable → not incognito:
 *  the server's own default (transcribed, with the notice every participant sees) applies. */
export async function chatIsIncognito(session: string): Promise<boolean> {
  // A ROOM WITH "TRANSCRIBE CALLS" OFF (UI 7hh, 0.13.4 S4): its calls have audio only. The server
  // refuses a transcribed call there, so the call starts quiet.
  if (roomOf(session)?.transcribe === false) return true;
  try { return (await readScope(session, false)).chat?.profile === "incognito"; } catch { return false; }
}
export const liveCallDeps: CallDeps = { getCall, startCall, joinCall, incognito: chatIsIncognito };

let current: ActiveCall | null = null;
let error = "";
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
let snapshot: { current: ActiveCall | null; error: string } = { current, error };
const set = (c: ActiveCall | null, e = "") => { current = c; error = e; snapshot = { current, error }; emit(); };

export function useActiveCall(): { current: ActiveCall | null; error: string } {
  return useSyncExternalStore(
    (l) => { listeners.add(l); return () => listeners.delete(l); },
    () => snapshot, () => snapshot);
}

export function activeCall(): ActiveCall | null { return current; }

/** Join this session's call, starting it first when there is none. One call per tab: joining another
 *  session's call leaves the current one (the dock unmounts the room). */
export async function enterCall(session: string, label: string, deps: CallDeps = liveCallDeps, opts: EnterOpts = {}): Promise<ActiveCall> {
  try {
    const state = await deps.getCall(session);
    if (!state.calls) throw new Error("Calls are not available on this deployment.");
    if (!state.call) {
      const quiet = deps.incognito ? await deps.incognito(session) : false;
      await deps.startCall(session, state.session_id, quiet ? { transcribe: false } : {});
    }
    const join = await deps.joinCall(session);
    const next: ActiveCall = { session, label, join,
      ...(opts.autoJoin ? { autoJoin: true } : {}), ...(opts.outgoing ? { outgoing: opts.outgoing } : {}) };
    set(next);
    return next;
  } catch (e) {
    set(current, e instanceof Error ? e.message : "Could not join the call.");
    throw e;
  }
}

export function leaveCall(): void { set(null); }

/** Somebody else is in the call now: it is no longer an outgoing call (the "Calling …" line goes). */
export function callAnswered(session: string): void {
  if (current?.session === session && current.outgoing) {
    const { outgoing: _gone, ...rest } = current;
    set(rest, error);
  }
}
