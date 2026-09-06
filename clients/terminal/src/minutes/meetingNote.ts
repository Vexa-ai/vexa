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

/** The workspace-relative path of this meeting's record on the reader's own desk, or null. */
export async function fetchMeetingNotePath(
  meetingId: string,
  fetcher: typeof fetch = fetch,
): Promise<string | null> {
  const id = String(meetingId ?? "").trim();
  if (!id) return null;
  try {
    const res = await fetcher(`/api/meeting/note?meeting_id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = await res.json() as { path?: unknown };
    const path = typeof body?.path === "string" ? body.path.trim() : "";
    // A path that walks out of the workspace is not a path we asked for. The server composes this
    // from its own directory listing, so this can only fire if something else is answering — and a
    // panel that renders whatever a reply hands it is the shape of the next seam failure.
    return path && !path.split("/").includes("..") ? path : null;
  } catch {
    return null;
  }
}
