"use client";
/** THE MEETING VIEW'S DATA — its notes file and its transcript, kept apart (founder-approved design,
 *  2026-10-11).
 *
 *  A meeting is ONE view: header, the notes on top, the transcript under them. The two halves come
 *  from two places and must never be confused for each other:
 *
 *    · NOTES are a markdown file on the person's desk. agent-api is its one writer: it names the
 *      file (`GET /api/meeting/note`), creates it (`POST /api/meeting/note`, idempotent, never
 *      overwrites) and the chat's agent rewrites it from the transcript when the person presses
 *      Update notes (the existing Extend act on a meeting's page). This module only reads it.
 *    · The TRANSCRIPT is meeting-api's: its database and its live stream. It is rendered under the
 *      notes and is never copied into the file. A page written in the two-tab era still carries a
 *      transcript slot (`<!-- vexa:transcript … -->`); the view drops the slot when it renders the
 *      notes — the transcript is right below — and never rewrites the file to do it.
 *
 *  Everything here is pure or a thin fetch, so the view's rules are tested without a browser.
 */
import { postIntent, clearPending } from "../minutes/extend";
import type { ChatIntent } from "../surfaces/chatIntent";
import { splitTranscriptSlots } from "../ui-kit/transcriptSlot";
import type { TranscriptSegment } from "./types";

/** The notes with every transcript slot taken out. The FILE is not touched — this is what is
 *  rendered, because the transcript has its own place in the view and two copies of it is the
 *  defect the one view removes. Text around a slot is kept exactly; fenced examples of the marker
 *  stay literal (`splitTranscriptSlots` passes fences through). */
export function notesForView(body: string): string {
  return splitTranscriptSlots(body ?? "")
    .map((s) => (s.kind === "text" ? s.text : ""))
    .join("");
}

/** `m:ss` (or `h:mm:ss`) for a number of seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** How far into the meeting a segment was said, in seconds — or null when the segment carries no
 *  time. A meeting-relative `ts` wins; otherwise the wall-clock `tsMs` against the first line's. */
export function segmentOffset(segments: TranscriptSegment[], index: number): number | null {
  const s = segments[index];
  if (!s) return null;
  const rel = typeof s.ts === "number" ? s.ts : typeof s.ts === "string" && s.ts.trim() !== "" ? Number(s.ts) : NaN;
  if (Number.isFinite(rel)) return rel;
  const first = segments.find((x) => typeof x.tsMs === "number")?.tsMs;
  return typeof s.tsMs === "number" && typeof first === "number" ? (s.tsMs - first) / 1000 : null;
}

/** "Updated 14:32 · from transcript up to 12:05" — what the notes have taken in, in one line.
 *
 *  `cursor` is the page's own `transcript_cursor`: the index of the next segment the last update did
 *  NOT read, written back by the agent. So the notes cover segments `[0, cursor)`, and the time is
 *  that of segment `cursor - 1`. An empty or non-numeric cursor means the notes have not been
 *  updated from the transcript yet, and the line says so instead of guessing. Either half is left
 *  out when it is unknown; the line is never padded with a placeholder. */
export function notesFreshness(opts: {
  updatedAt?: number | null;          // epoch seconds of the file's last commit
  cursor?: string | null;
  segments: TranscriptSegment[];
  now?: Date;
}): string {
  const parts: string[] = [];
  if (opts.updatedAt) {
    const d = new Date(opts.updatedAt * 1000);
    const sameDay = (opts.now ?? new Date()).toDateString() === d.toDateString();
    const time = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(d);
    parts.push(sameDay ? `Updated ${time}`
      : `Updated ${new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(d)}, ${time}`);
  }
  const c = String(opts.cursor ?? "").trim();
  const n = /^\d+$/.test(c) ? Number(c) : NaN;
  if (Number.isFinite(n) && n > 0) {
    const at = segmentOffset(opts.segments, Math.min(n, opts.segments.length) - 1);
    parts.push(at === null ? "from the transcript" : `from transcript up to ${clock(at)}`);
  } else {
    parts.push("not yet updated from the transcript");
  }
  return parts.join(" · ");
}

/** The transcript's TAIL — the last `n` lines anyone would read, counting the line still being
 *  said. Collapsed, the view shows this and nothing else; it moves as new lines arrive. */
export function transcriptTail(segments: TranscriptSegment[], n = 3): TranscriptSegment[] {
  const said = segments.filter((s) => (s.text ?? "").trim());
  return said.slice(-n);
}

/** How many lines the transcript has — the confirmed ones, which is what "N lines" can promise. */
export function transcriptLines(segments: TranscriptSegment[]): number {
  return segments.filter((s) => s.completed !== false && (s.text ?? "").trim()).length;
}

/** Is the transcript open? COLLAPSED BY DEFAULT, live or finished (founder, 2026-10-11: during a
 *  live meeting the tail keeps updating; expanding is the person's choice). The person's own toggle
 *  is remembered per meeting for this browser session — never across sessions, and never shared. */
const OPEN_KEY = (meetingId: string) => `vexa.meetingTranscriptOpen.${meetingId}`;

export function loadTranscriptOpen(meetingId: string): boolean {
  try { return sessionStorage.getItem(OPEN_KEY(meetingId)) === "1"; } catch { return false; }
}

export function saveTranscriptOpen(meetingId: string, open: boolean): void {
  try { sessionStorage.setItem(OPEN_KEY(meetingId), open ? "1" : "0"); } catch { /* storage refused */ }
}

/** CREATE NOTES — the meeting's page, minted by agent-api through the route that has always minted
 *  it (`POST /api/meeting/note`). Idempotent on the server: a page already there is returned as it
 *  is. The server checks the caller may read this meeting before it writes anything; a refusal is
 *  thrown so the view can say so rather than pretend the page exists. */
export async function createMeetingNotes(meetingId: string, fetcher: typeof fetch = fetch): Promise<string> {
  const r = await fetcher("/api/meeting/note", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ meeting_id: String(meetingId) }),
  });
  if (!r.ok) throw new Error(r.status === 403 ? "You can't create notes for this meeting." : "Could not create notes. Retry.");
  const body = await r.json() as { path?: unknown };
  const path = typeof body?.path === "string" ? body.path.trim() : "";
  if (!path || path.split("/").includes("..")) throw new Error("Could not create notes. Retry.");
  return path;
}

/** UPDATE NOTES — the existing Extend act on the meeting's page (`extend` + `meeting`, which the
 *  server runs as the `extend-meeting` ask). One writer: the chat's agent, through agent-api, reading
 *  the transcript since the page's cursor. Nothing new is invented on the client.
 *
 *  The view stays where it is: an Extend normally lands the panel on the page it wrote, and here
 *  the page IS in front, inside the view — so the landing is cleared and the view re-reads the notes
 *  on the commit instead. */
export function updateMeetingNotes(meetingId: string, path: string, instruction?: string): ChatIntent | null {
  const posted = postIntent({ kind: "extend", path, meeting: String(meetingId), ...(instruction ? { instruction } : {}) });
  clearPending();
  return posted;
}
