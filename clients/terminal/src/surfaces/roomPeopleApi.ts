/** roomPeopleApi — who is in a room and how someone gets in (0.13.4 S4: D21, D36, FD4, UI 7hh).
 *
 *  The server's contract is `core/agent/control_plane/routers/room_people.py`:
 *    POST   /api/rooms/{s}/tag                    what @mentioning this person does (the tag card)
 *    POST   /api/rooms/{s}/members                the card's answer: add (and tell) · invite
 *    DELETE /api/rooms/{s}/members/{principal}    remove (a closed room's owner) — the add's Undo
 *    GET    /api/rooms/{s}/people                 the members panel
 *    POST   /api/rooms/direct                     "Message <person>": the room of two
 *    GET    /api/rooms/{s}/switches               "Vexa in this room" · "Transcribe calls"
 *    POST   /api/rooms/{s}/switches               turn one on or off (an ask, in a room of two)
 *    POST   /api/rooms/{s}/switches/{sw}/answer   the other person's consent
 *  Every act here is a person's click; a refusal throws (P18) so the card can say why. */
import { getJson } from "../data/apiClient";

const seg = encodeURIComponent;
const json = (method: string, body: unknown): RequestInit =>
  ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export type TagCase = "member" | "workspace_member" | "known" | "unknown_email" | "pair_full" | "unknown" | "added_by_rule";

/** The card only the tagger sees (7g). `actions` is the exact set of buttons to draw. */
export interface TagCardSpec {
  kind: "invitation";
  case: TagCase;
  only_you: true;
  title: string;
  who: string;
  gets: string;
  actions: ("add" | "always" | "invite" | "mention")[];
  always_line?: string;
  never_always?: string;
}

export interface PersonRef { subject?: string; email?: string; name?: string; known?: boolean; origin?: "person" | "agent" }

export interface TagAnswer {
  case: TagCase;
  subject?: string;
  name?: string;
  email?: string;
  workspace?: string;
  workspace_name?: string;
  card?: TagCardSpec | null;
  call_live?: boolean;
  said?: string;
  /** added_by_rule: the person was added by the tagger's own "always" line; this undoes it */
  undo?: { method: "DELETE"; path: string };
  rang?: string[];
}

export async function tagPerson(session: string, who: PersonRef): Promise<TagAnswer> {
  return getJson(`/api/rooms/${seg(session)}/tag`, json("POST", { origin: "person", ...who }));
}

export interface AddAnswer {
  case: TagCase;
  added?: boolean;
  invited?: boolean;
  name?: string;
  rang?: string[];
  always?: { applied: boolean; proposal?: string; decides?: string; already?: boolean };
  expires_at?: number;
}

export async function addRoomMember(session: string, who: PersonRef & { tell?: boolean; always?: boolean }): Promise<AddAnswer> {
  return getJson(`/api/rooms/${seg(session)}/members`, json("POST", { origin: "person", ...who }));
}

export async function removeRoomMember(session: string, principal: string): Promise<{ removed: boolean }> {
  return getJson(`/api/rooms/${seg(session)}/members/${seg(principal)}`, { method: "DELETE" });
}

export interface PanelMember { principal: string; name: string; role: string; kind: string; derived: boolean;
  added?: number | null; opened?: number | null; added_by_name?: string }
export interface RoomPeople {
  session_id: string; you: string; workspace: string; workspace_name: string;
  visibility: "open" | "closed" | "direct"; pair: boolean;
  members: PanelMember[];
  invited: { invite_id: string; expires_at: number; label: string; by: string }[];
  workspace_others: { principal: string; name: string }[];
}

export async function readRoomPeople(session: string): Promise<RoomPeople> {
  return getJson(`/api/rooms/${seg(session)}/people`);
}

export async function openRoomOfTwo(subject: string): Promise<{ session_id: string; created: boolean; with: { subject: string; name: string } }> {
  return getJson(`/api/rooms/direct`, json("POST", { subject }));
}

export type SwitchName = "vexa" | "transcribe";
export interface RoomSwitches {
  session_id: string;
  vexa: boolean;
  transcribe: boolean;
  asks: Partial<Record<SwitchName, { by: string; by_name: string; mine: boolean; at: number }>>;
  pair: boolean;
  can_switch: boolean;
  /** the rule that locks each direction, or null */
  locks: Record<SwitchName, { on: string | null; off: string | null }>;
  applied?: boolean;
  asked?: string;
}

export async function readSwitches(session: string): Promise<RoomSwitches> {
  return getJson(`/api/rooms/${seg(session)}/switches`);
}

export async function setSwitch(session: string, sw: SwitchName, on: boolean): Promise<RoomSwitches> {
  return getJson(`/api/rooms/${seg(session)}/switches`, json("POST", { switch: sw, on }));
}

export async function answerSwitch(session: string, sw: SwitchName, accept: boolean): Promise<RoomSwitches> {
  return getJson(`/api/rooms/${seg(session)}/switches/${seg(sw)}/answer`, json("POST", { accept }));
}

/** The words a switch's lock is told with — never the raw rule id alone. */
export function lockWords(rule: string | null | undefined): string {
  if (!rule) return "";
  return `locked by policy (${rule.split("@")[0]})`;
}

/** The server's own sentence for a refusal (`{detail: {said}}`), or "" — never a status code. */
export function saidOf(e: unknown): string {
  const body = (e as { body?: unknown } | null)?.body as { detail?: { said?: unknown } | string } | undefined;
  const d = body?.detail;
  return d && typeof d === "object" && typeof d.said === "string" ? d.said : "";
}
