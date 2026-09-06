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
export interface MeetingNote { path: string | null; transcript: string; cursor: string }

const EMPTY: MeetingNote = { path: null, transcript: "", cursor: "" };

export async function fetchMeetingNote(
  meetingId: string,
  fetcher: typeof fetch = fetch,
): Promise<MeetingNote> {
  const id = String(meetingId ?? "").trim();
  if (!id) return EMPTY;
  try {
    const res = await fetcher(`/api/meeting/note?meeting_id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return EMPTY;
    const body = await res.json() as { path?: unknown; transcript?: unknown; cursor?: unknown };
    const path = typeof body?.path === "string" ? body.path.trim() : "";
    // A path that walks out of the workspace is not a path we asked for. The server composes this
    // from its own directory listing, so this can only fire if something else is answering — and a
    // panel that renders whatever a reply hands it is the shape of the next seam failure.
    const clean = path && !path.split("/").includes("..") ? path : null;
    return {
      path: clean,
      // A widget on no page is not a widget: a `transcript` without a `path` cannot be honoured, and
      // carrying it would let a malformed reply hide the room's transcript tab with nothing behind it.
      transcript: clean && typeof body?.transcript === "string" ? body.transcript.trim() : "",
      cursor: clean && typeof body?.cursor === "string" ? body.cursor.trim() : "",
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
