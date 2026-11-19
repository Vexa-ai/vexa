"use client";
/** WHERE THIS MEETING'S REPORT LIVES — asked, never spelled (Vexa-ai/vexa#1588).
 *
 *  `drop_to_attendees` writes a meeting's record to `kg/entities/meeting/<meeting-day>-<title-slug>.md`
 *  — the day rendered in the ORGANISER's timezone, the slug through a server-side allow-list.
 *  Neither is derivable out here. This client used to point its Minutes tab at
 *  `kg/entities/meeting/<native>.md` instead, and the two spellings never matched: the founder
 *  opened a meeting whose 6.3 KB report had been written, mailed and dropped an hour earlier and
 *  read *"No page here yet — it appears when the conversation (or a meeting) writes one"*.
 *
 *  A chat born from a mailed link is already told (`Scaffold.refs.note_path`, composed by the step
 *  that writes the file). This is the same fact for the chat that was NOT born from a link — the
 *  meeting clicked in the rail — and it comes from the same side: the server reads the desk.
 *
 *  NULL IS THE ORDINARY ANSWER before a meeting has a report, and it is a RESOLVED one: the room
 *  opens one document fewer, which is the honest degradation `artifactFromToken` already takes for
 *  `meeting:note`. Never throws — a lookup we could not make must cost the room its Minutes tab,
 *  never its transcript and never the chat.
 */

/** WHAT THE SERVER SAYS ABOUT THIS MEETING'S PAGE.
 *
 *  `transcript` is the meeting the page's own widget slot names (Vexa-ai/vexa#1598) — non-empty
 *  means the live transcript renders INSIDE this document, so the room is ONE page and needs no
 *  separate Transcript tab. `cursor` is where the last Expand stopped reading.
 *
 *  Both are `""` for a report written before the widget existed, and that is the answer that keeps
 *  those meetings on the two-page room they have today rather than losing the transcript entirely. */
import { postIntent, clearPending } from "./extend";
import type { ChatIntent } from "../surfaces/chatIntent";

/** `workspace` is the workspace the page lives in — `""` for the person's own desk. A brief written
 *  for the call in the meeting's workspace IS the meeting's page (#1783), so it is not always the
 *  desk. `title` is the meeting's real name (the row's). The note file stays a plain page (UI
 *  decisions 9): nothing is folded into it or out of it here. */
export interface MeetingNote {
  path: string | null; workspace: string; title: string; transcript: string; cursor: string;
  /** the note's text, when the server hands it back whole: a report in a Personal workspace the
   *  person switched off, which no read route reaches (#1783) — shown, never written */
  content?: string;
  readonly?: boolean;
}

const EMPTY: MeetingNote = { path: null, workspace: "", title: "", transcript: "", cursor: "" };

/** A workspace slug as the server spells one — anything else is not an answer we asked for. */
const cleanSlug = (v: unknown): string => typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$/.test(v.trim()) ? v.trim() : "";

export async function fetchMeetingNote(
  meetingId: string,
  fetcher: typeof fetch = fetch,
): Promise<MeetingNote> {
  const id = String(meetingId ?? "").trim();
  if (!id) return EMPTY;
  try {
    const res = await fetcher(`/api/meeting/note?meeting_id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return EMPTY;
    const body = await res.json() as { path?: unknown; workspace?: unknown; title?: unknown; transcript?: unknown; cursor?: unknown; content?: unknown; readonly?: unknown };
    const path = typeof body?.path === "string" ? body.path.trim() : "";
    // A path that walks out of the workspace is not a path we asked for. The server composes this
    // from its own directory listing, so this can only fire if something else is answering — and a
    // panel that renders whatever a reply hands it is the shape of the next seam failure.
    const clean = path && !path.split("/").includes("..") ? path : null;
    return {
      path: clean,
      workspace: clean ? cleanSlug(body?.workspace) : "",
      title: typeof body?.title === "string" ? body.title.trim() : "",
      // A widget on no page is not a widget: a `transcript` without a `path` cannot be honoured, and
      // carrying it would let a malformed reply hide the room's transcript tab with nothing behind it.
      transcript: clean && typeof body?.transcript === "string" ? body.transcript.trim() : "",
      cursor: clean && typeof body?.cursor === "string" ? body.cursor.trim() : "",
      ...(clean && typeof body?.content === "string" ? { content: body.content } : {}),
      ...(clean && body?.readonly === true ? { readonly: true } : {}),
    };
  } catch {
    return EMPTY;
  }
}

/** The path alone — the shape every caller wanted before #1598, kept so they still read plainly. */
export async function fetchMeetingNotePath(
  meetingId: string,
  fetcher: typeof fetch = fetch,
): Promise<string | null> {
  return (await fetchMeetingNote(meetingId, fetcher)).path;
}

/** CREATE NOTES — the meeting's page, minted by agent-api through the route that has always minted
 *  it (`POST /api/meeting/note`). Idempotent on the server: a page already there is returned as it
 *  is. The server checks the caller may read this meeting before it writes anything; a refusal is
 *  thrown so the view can say so rather than pretend the page exists. */
export async function createMeetingNotes(meetingId: string, fetcher: typeof fetch = fetch): Promise<string> {
  return (await createMeetingPage(meetingId, fetcher)).path;
}

/** The same mint, with WHERE the page is: the workspace it answers (`""` for the desk). */
export async function createMeetingPage(meetingId: string, fetcher: typeof fetch = fetch): Promise<{ path: string; workspace: string }> {
  const r = await fetcher("/api/meeting/note", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ meeting_id: String(meetingId) }),
  });
  if (!r.ok) throw new Error(r.status === 403 ? "You can't create notes for this meeting." : "Could not create notes. Retry.");
  const body = await r.json() as { path?: unknown; workspace?: unknown };
  const path = typeof body?.path === "string" ? body.path.trim() : "";
  if (!path || path.split("/").includes("..")) throw new Error("Could not create notes. Retry.");
  return { path, workspace: cleanSlug(body?.workspace) };
}

/** UPDATE NOTES — the existing Extend act on the meeting's page (`extend` + `meeting`, which the
 *  server runs as the `extend-meeting` ask). One writer: the chat's agent, through agent-api, reading
 *  the transcript since the page's cursor. Nothing new is invented on the client.
 *
 *  The view stays where it is: an Extend normally lands the panel on the page it wrote, and here
 *  the page IS in front, inside the view — so the landing is cleared and the view re-reads the notes
 *  on the commit instead. */
export function updateMeetingNotes(meetingId: string, path: string, instruction?: string, workspace?: string): ChatIntent | null {
  const posted = postIntent({ kind: "extend", path, meeting: String(meetingId), ...(workspace ? { workspace } : {}), ...(instruction ? { instruction } : {}) });
  clearPending();
  return posted;
}
