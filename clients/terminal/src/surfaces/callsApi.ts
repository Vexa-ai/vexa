/** callsApi — a call held in a session (ADR-0048 decision 6) and the review after it (decision 7).
 *
 *  Two domains, each for what it owns: the CALL is a meeting (`POST /api/bots {platform:"vexa_call"}`
 *  → meeting-api creates the LiveKit room and its listener); the SESSION is agent-api's (bind the call
 *  to it, mint a participant's room token, post a line, review proposals). Every request goes through
 *  the one gateway edge with no subject: the gateway signs the caller. Fail-loud: errors throw. */
import { getJson } from "../data/apiClient";

export interface SessionCall {
  room: string;
  /** null for an untranscribed call: it has no transcriber and no meeting */
  meeting_id: number | null;
  owner: string;
  url: string;
  started: number;
  transcribed: boolean;
  participants: number;
  /** who is in the call now, by name — the listener is never listed (absent on an older server) */
  in_now?: { subject: string; name: string }[];
}
/** `you` is the reader's own account id (absent on an older server): with `call.owner` it tells the
 *  reader's own call from someone else's. */
export interface CallState { session_id: string; calls: boolean; you?: string; call: SessionCall | null }
export interface CallJoin { session_id: string; url: string; token: string; role: string; call: SessionCall }
export interface Proposal {
  id: string; session_id: string; meeting_id: number | null; owner: string;
  kind: "page" | "entity" | "commitment" | "note"; workspace: string | null; path: string | null;
  title: string; body: string; status: "open" | "accepted" | "rejected";
  decided_by: string | null; decided: number | null; created: number;
}

const seg = (s: string) => encodeURIComponent(s);
const json = (body: unknown): RequestInit => ({
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

/** Does this deployment have calls, and is one live in this session? */
export function getCall(session: string): Promise<CallState> {
  return getJson<CallState>(`/api/sessions/${seg(session)}/call`);
}

/** The longest call is four hours; the share that lets participants read its transcript outlives it. */
export const CALL_SHARE_TTL_SEC = 6 * 3600;

/** Start a call in this session: the meeting (room + listener), a transcript share for whoever joins,
 *  and the binding that makes it this session's call. `sessionId` is the store id (`s-…`). */
export async function startCall(session: string, sessionId: string, opts: { transcribe?: boolean } = {}): Promise<CallState> {
  // AN UNTRANSCRIBED CALL (a private or incognito chat's) starts no transcriber and has no meeting:
  // nothing to share, and it binds by its room alone.
  if (opts.transcribe === false) {
    const quiet = await getJson<{ id: null; native_meeting_id: string }>(
      "/api/bots", json({ platform: "vexa_call", session_id: sessionId, transcribe: false }));
    return getJson<CallState>(`/api/sessions/${seg(session)}/call`, json({ room: quiet.native_meeting_id }));
  }
  const meeting = await getJson<{ id: number; native_meeting_id: string }>(
    "/api/bots", json({ platform: "vexa_call", session_id: sessionId }));
  const share = await getJson<{ token: string }>(
    `/api/meetings/${meeting.id}/share`, json({ mode: "open", expires_in_sec: CALL_SHARE_TTL_SEC }));
  return getJson<CallState>(`/api/sessions/${seg(session)}/call`, json({
    room: meeting.native_meeting_id, meeting_id: meeting.id, share_token: share.token,
  }));
}

/** A room token for me, in this session's call. */
export function joinCall(session: string): Promise<CallJoin> {
  return getJson<CallJoin>(`/api/sessions/${seg(session)}/call/token`, { method: "POST" });
}

/** Share to the room: one line into the shared chat, no agent turn. */
export function shareToRoom(session: string, text: string): Promise<unknown> {
  return getJson(`/api/sessions/${seg(session)}/messages`, json({ text }));
}

export async function listProposals(session: string): Promise<{ you?: string; proposals: Proposal[] }> {
  const d = await getJson<{ you?: string; proposals?: Proposal[] }>(`/api/sessions/${seg(session)}/proposals`);
  return { you: d.you, proposals: d.proposals ?? [] };
}

export function decideProposal(session: string, id: string, accept: boolean, body?: string): Promise<{ proposal: Proposal }> {
  return getJson(`/api/sessions/${seg(session)}/proposals/${seg(id)}/${accept ? "accept" : "reject"}`,
                 accept ? json(body !== undefined ? { body } : {}) : { method: "POST" });
}

/** SHOW A PAGE TO EVERYONE in this session's call (founder 2026-10-15). The server sends it to the
 *  room; a page not everyone in the chat can read is refused with who cannot (`refused`). */
export interface ShowRefusal { workspace: string; missing: { subject: string; name: string }[]; message: string }
export async function showToCall(session: string, page: { workspace: string; path: string; anchor?: string }):
  Promise<{ ok: true } | { refused: ShowRefusal }> {
  try {
    await getJson(`/api/sessions/${seg(session)}/call/show`, json(page));
    return { ok: true };
  } catch (e) {
    const body = (e as { status?: number; body?: { detail?: { error?: string } & ShowRefusal } });
    if (body.status === 409 && body.body?.detail?.error === "not_shared") return { refused: body.body.detail };
    throw e;
  }
}
