/** What a room's call lane reads (R1 2.3, `rooms`; ADR "Chats can be calls…", founder 2026-10-15).
 *
 *  - WHO IS A GUEST: anyone on the room's call who is not one of the room's members. The call's
 *    identities are account ids (agent-api's call token), the same ids `GET /api/rooms/{s}/members`
 *    answers; the hidden listener and the meeting service are never people.
 *  - WHAT VEXA PROPOSED during this call: the room's open proposals from the one proposal object (D1,
 *    `GET /api/changes?room=<s>`), from the call's start on — with a guest present, Vexa's writes
 *    become these, for a member to accept.
 *  - WHAT VEXA CHANGED during this call, members only: the call's own change list (R1 1.5,
 *    `GET /api/calls/{room}/changes`), each undoable through the page write path
 *    (`POST …/changes/{id}/undo`; refused when the page changed since, with a restore proposal).
 *  A failed read is an empty lane, never an error over the call. */
import { useEffect, useState } from "react";
import { readRoomMembers } from "../../surfaces/roomsApi";

const NOT_PEOPLE = /^vexa-(listener|meeting-api)/;
/** the event D1's surfaces re-read on after a decision (`surfaces/changesApi.ts` CHANGES_EVENT) */
export const CHANGES_EVENT = "vexa:terminal:changes-changed";

export interface OnCall { id: string; name: string }

/** The people on the call who are not members of the room. Until the members are known, nobody is. */
export function guestsOn(onCall: OnCall[], members: string[] | null): OnCall[] {
  if (!members) return [];
  const m = new Set(members.map(String));
  return onCall.filter((p) => !NOT_PEOPLE.test(p.id) && !m.has(p.id));
}

export function useRoomMembers(session: string, on: boolean): string[] | null {
  const [members, setMembers] = useState<string[] | null>(null);
  useEffect(() => {
    if (!on || !session) { setMembers(null); return; }
    let live = true;
    const read = () => readRoomMembers(session).then((ms) => { if (live) setMembers(ms.map((x) => x.principal)); }).catch(() => {});
    void read();
    const t = window.setInterval(read, 30_000);   // someone may be invited into the room mid-call
    return () => { live = false; window.clearInterval(t); };
  }, [session, on]);
  return members;
}

/** One line of the lane. */
export interface LaneItem {
  id: string; kind: "proposed" | "updated"; label: string; reason: string; at: string; acts: ("accept" | "decline")[];
  /** an "updated" change that can still be undone */
  undoable?: boolean;
  /** an "updated" change already undone */
  undone?: boolean;
}

interface Envelope {
  id: string; status: string; reason?: string; created_at?: string | null; decided_at?: string | null;
  proposer?: { kind?: string }; acts?: string[];
  target?: { label?: string; path?: string | null; ref?: string };
}

const labelOf = (e: Envelope) => e.target?.label || (e.target?.path ?? "").split("/").pop()?.replace(/\.md$/, "") || e.target?.ref || "a page";

/** The open proposals Vexa made since the call started, newest first (people's own are not Vexa's). */
export function laneItems(list: Envelope[], sinceMs: number): LaneItem[] {
  const out: LaneItem[] = [];
  for (const e of list) {
    if (!e || typeof e.id !== "string" || e.proposer?.kind === "person" || e.status !== "open") continue;
    const t = e.created_at ? Date.parse(e.created_at) : NaN;
    if (!Number.isFinite(t) || t < sinceMs) continue;
    out.push({ id: e.id, kind: "proposed", label: labelOf(e), reason: e.reason ?? "", at: e.created_at!, acts: (e.acts ?? []).filter((a): a is "accept" | "decline" => a === "accept" || a === "decline") });
  }
  return out.sort((a, b) => b.at.localeCompare(a.at));
}

/** A change the room's agent made during the call (`GET /api/calls/{room}/changes`). */
export interface CallChange { id: string; workspace: string; path: string; at: number; created?: boolean; undoable?: boolean; undone_at?: number | null }

/** The call's own changes, newest first, as lane lines. */
export function changeItems(changes: CallChange[]): LaneItem[] {
  return changes.filter((c) => c && typeof c.id === "string").map((c) => ({
    id: c.id, kind: "updated" as const, label: (c.path.split("/").pop() || c.path).replace(/\.md$/, ""),
    reason: c.created ? "a new page" : "", at: new Date(c.at * 1000).toISOString(), acts: [],
    undoable: !!c.undoable, undone: !!c.undone_at,
  })).sort((a, b) => b.at.localeCompare(a.at));
}

export function useCallLane(session: string, room: string, sinceMs: number, on: boolean): { items: LaneItem[]; reload: () => void } {
  const [proposed, setProposed] = useState<LaneItem[]>([]);
  const [changed, setChanged] = useState<LaneItem[]>([]);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!on || !session) { setProposed([]); setChanged([]); return; }
    let live = true;
    const read = async () => {
      try {
        const r = await fetch(`/api/changes?room=${encodeURIComponent(session)}`, { cache: "no-store" });
        if (r.ok) { const d = (await r.json()) as { proposals?: Envelope[] }; if (live) setProposed(laneItems(d.proposals ?? [], sinceMs)); }
      } catch { /* an unread lane is an empty one */ }
      if (!room) return;
      try {
        const r = await fetch(`/api/calls/${encodeURIComponent(room)}/changes`, { cache: "no-store" });
        if (r.ok) { const d = (await r.json()) as { changes?: CallChange[] }; if (live) setChanged(changeItems(d.changes ?? [])); }
      } catch { /* the same */ }
    };
    void read();
    const t = window.setInterval(read, 10_000);
    const again = () => void read();
    window.addEventListener(CHANGES_EVENT, again);
    return () => { live = false; window.clearInterval(t); window.removeEventListener(CHANGES_EVENT, again); };
  }, [session, room, sinceMs, on, tick]);
  return { items: [...proposed, ...changed].sort((a, b) => b.at.localeCompare(a.at)), reload: () => setTick((n) => n + 1) };
}

/** Undo one of Vexa's changes during the call. One line when refused: the server's own words
 *  (`said`) — when the page changed since, it has offered a proposal to restore it instead. */
export async function undoChange(room: string, id: string): Promise<string | null> {
  try {
    const r = await fetch(`/api/calls/${encodeURIComponent(room)}/changes/${encodeURIComponent(id)}/undo`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { id } }));
    if (r.ok) return null;
    let said = "";
    try { said = ((await r.json()) as { detail?: { said?: string } })?.detail?.said ?? ""; } catch { /* no body */ }
    if (said) return said;
    if (r.status === 409) return "This was already undone.";
    if (r.status === 403) return "Only a member who can edit it undoes this.";
    return "That couldn't be undone just now.";
  } catch { return "That couldn't be undone just now."; }
}

/** Accept or decline one proposal through its owner's write path (D1). One line when refused. */
export async function decide(id: string, act: "accept" | "decline"): Promise<string | null> {
  try {
    const r = await fetch(`/api/changes/${encodeURIComponent(id)}/${act}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    window.dispatchEvent(new CustomEvent(CHANGES_EVENT, { detail: { id } }));
    if (r.ok) return null;
    if (r.status === 403) return "Only a member who can edit it decides this.";
    if (r.status === 409) return "Someone already decided this.";
    return "That couldn't be done just now.";
  } catch { return "That couldn't be done just now."; }
}
