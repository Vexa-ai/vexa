/** roomsApi — the rooms surface's data access (R1, behind the `rooms` feature).
 *
 *  Rooms are made in a workspace, open by default; chats stay private (founder 2026-10-15). The
 *  server's contract is `core/agent/control_plane/routers/rooms.py`:
 *    POST  /api/rooms {workspace, name, visibility, members?}   make a room (you own it)
 *    GET   /api/rooms?workspace=                               the rooms you can see there
 *    POST  /api/rooms/{session}/join                           an open room's self-join
 *    GET   /api/sessions/{session}/room                        what a chat is as a room
 *    PATCH /api/sessions/{session}/room                        proactivity · visibility · level · brief
 *    POST  /api/rooms/{session}/plan                           plan a call (nothing is sent)
 *    GET   /api/rooms/{session}/members                        who is in the room
 *  No `subject` is ever sent (P20); every refusal throws (P18) so the dialog can say why. */
import { getJson } from "../data/apiClient";

export type Proactivity = "off" | "quiet" | "active";
export type Visibility = "open" | "closed";

/** What a chat is as a room. A private chat answers `room: false` (visibility "private"). */
export interface RoomSettings {
  session_id: string;
  room: boolean;
  humans: number;
  visibility: Visibility | "private" | "direct";
  proactivity: Proactivity;
  workspace: string;
  listen?: Record<string, unknown>;
  listen_version?: number;
  /** the room's agent is working on something nobody in this tab asked for (a room wake, or another
   *  member's turn) — the header says "Vexa is checking…". Absent from a server that does not say. */
  agent_busy?: boolean;
  /** "thinking" from the moment a wake for this room is accepted; "nothing" briefly after a turn that
   *  said nothing; absent otherwise (`rooms.turn_status`). */
  agent_turn?: "thinking" | "nothing" | null;
  /** the room's model (a catalog id), one for everybody in it; empty or absent = the deployment default */
  level?: string | null;
}

/** A room as a workspace lists it. `member` = you are in it; an open room you are not in can be joined. */
export interface RoomListing {
  id: string;
  title?: string | null;
  owner?: string | null;
  last_active?: number | null;
  visibility: Visibility;
  member: boolean;
}

const seg = encodeURIComponent;
const json = (method: string, body: unknown): RequestInit =>
  ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export async function createRoom(p: { workspace: string; name: string; visibility: Visibility; members?: string[] }): Promise<RoomSettings & { name?: string }> {
  const body: Record<string, unknown> = { workspace: p.workspace, name: p.name, visibility: p.visibility };
  if (p.members?.length) body.members = p.members;
  return getJson(`/api/rooms`, json("POST", body));
}

export async function listRooms(workspace: string): Promise<RoomListing[]> {
  const d = await getJson<{ rooms?: RoomListing[] }>(`/api/rooms?workspace=${seg(workspace)}`);
  return d.rooms ?? [];
}

export async function joinRoom(session: string): Promise<RoomSettings & { joined?: boolean }> {
  return getJson(`/api/rooms/${seg(session)}/join`, { method: "POST" });
}

export async function readRoom(session: string): Promise<RoomSettings> {
  return getJson(`/api/sessions/${seg(session)}/room`);
}

export async function setRoomProactivity(session: string, proactivity: Proactivity): Promise<RoomSettings> {
  return getJson(`/api/sessions/${seg(session)}/room`, json("PATCH", { proactivity }));
}

export async function setRoomVisibility(session: string, visibility: Visibility): Promise<RoomSettings> {
  return getJson(`/api/sessions/${seg(session)}/room`, json("PATCH", { visibility }));
}

/** The room's model, one for everybody in it, from its next turn; `""` = the deployment default. */
export async function setRoomLevel(session: string, level: string): Promise<RoomSettings> {
  return getJson(`/api/sessions/${seg(session)}/room`, json("PATCH", { level }));
}

/** CLOSED → OPEN shows the room's history to every member of the workspace: the server refuses
 *  (409 `confirm_history`) until the person has been told that and said yes. */
export async function openRoomToWorkspace(session: string, p: { workspace: string; confirmed: boolean }): Promise<RoomSettings> {
  return getJson(`/api/sessions/${seg(session)}/room`, json("PATCH", { visibility: "open", workspace: p.workspace, confirm_history: p.confirmed }));
}

/** Leave a room. The person who made it stays its owner and cannot leave it (403). */
export async function leaveRoom(session: string): Promise<{ session_id: string; left: boolean }> {
  return getJson(`/api/rooms/${seg(session)}/leave`, { method: "POST" });
}

/** The event a planned call's creator sends from their own calendar, on their own click. */
export interface CalendarDraft { summary: string; start: string | null; end: string | null; attendees: string[]; description: string }

/** What planning a call answers: the room's planned meeting row, the calendar draft and its `.ics`. */
export interface PlannedCall {
  session_id: string;
  meeting: { id: number; data?: Record<string, unknown> } & Record<string, unknown>;
  calendar_draft: CalendarDraft;
  /** the gateway path of the planned call's iCalendar file (`/meetings/{id}/ics`) */
  ics: string;
}

/** Record the event the creator's own calendar made as the call's source of truth (8.4). */
export async function recordRoomCallEvent(session: string, meetingId: number, eventRef: string): Promise<unknown> {
  return getJson(`/api/rooms/${seg(session)}/plan/${encodeURIComponent(String(meetingId))}/calendar`, json("POST", { event_ref: eventRef }));
}

/** Plan a call in this room (1.4, `POST /api/rooms/{s}/plan`): a planned meeting the room owns. NOTHING
 *  is sent — the creator adds it to their own calendar (the `.ics`, or the draft) on their own click. */
export async function planRoomCall(session: string, p: { title: string; start: string; end?: string; invitees?: string[]; agenda?: string }): Promise<PlannedCall> {
  const body: Record<string, unknown> = { title: p.title, start: p.start };
  if (p.end) body.end = p.end;
  if (p.invitees?.length) body.invitees = p.invitees;
  if (p.agenda) body.agenda = p.agenda;
  return getJson(`/api/rooms/${seg(session)}/plan`, json("POST", body));
}

/** A room's member, as `GET /api/rooms/{s}/members` answers it. `principal` is the account id — the
 *  same id a member's call identity carries, so anyone on the room's call who is not here is a guest. */
export interface RoomMember { principal: string; kind?: string; role?: string }

export async function readRoomMembers(session: string): Promise<RoomMember[]> {
  const d = await getJson<{ members?: RoomMember[] }>(`/api/rooms/${seg(session)}/members`);
  return d.members ?? [];
}
