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
import { splitTranscriptSlots } from "../ui-kit/transcriptSlot";
import type { TranscriptSegment } from "./types";

/** The notes with every transcript slot taken out. The FILE is not touched — this is what is
 *  rendered, because the transcript has its own place in the view and two copies of it is the
 *  defect the one view removes. Text around a slot is kept exactly; fenced examples of the marker
 *  stay literal (`splitTranscriptSlots` passes fences through). */
export function notesForView(body: string): string {
  // The frontmatter is the page's machinery (type, meeting id, cursor) — the reader of notes wants
  // the notes. It stays in the file; the view starts at the first line after it.
  const src = String(body ?? "").replace(/^---[ \t]*\r?\n[\s\S]*?\r?\n---[ \t]*(?:\r?\n|$)/, "");
  return splitTranscriptSlots(src)
    .map((s) => (s.kind === "text" ? s.text : ""))
    .join("");
}

/** The page's own `# Title`, when its first non-blank line is one — else "". */
export function leadingTitle(md: string): string {
  const first = String(md ?? "").split("\n").find((l) => l.trim());
  const m = first ? /^#\s+(.+?)\s*#*\s*$/.exec(first) : null;
  return m ? m[1] : "";
}

/** The notes without their `# Title` line: the meeting's name is the view's header (its real name,
 *  #1783), and a minted page's placeholder heading ("Google Meet meeting") must not be a second,
 *  wrong title under it. Only a FIRST-line H1 goes; every other line stays. */
export function withoutLeadingTitle(md: string): string {
  const lines = String(md ?? "").split("\n");
  const i = lines.findIndex((l) => l.trim());
  if (i < 0 || !/^#\s+\S/.test(lines[i])) return String(md ?? "");
  return lines.slice(0, i).concat(lines.slice(i + 1)).join("\n");
}

/** The notes without headings that head nothing. A minted page carries its sections empty until an
 *  update fills them (`meeting_doc.scaffold`), and the `report` section stays empty until the
 *  meeting's report is written — a column of bare headings reads as a form nobody filled in. A
 *  section is empty when, once its HTML comments (the region markers) are taken out, nothing but
 *  whitespace is left before the next heading of the same or a higher level. Only the heading line
 *  goes; the markers stay (they render as nothing). Fenced code is never read as headings. */
export function withoutEmptySections(md: string): string {
  const lines = String(md ?? "").split("\n");
  const fenced: boolean[] = [];
  let inFence = false;
  for (const l of lines) { if (/^\s*(```|~~~)/.test(l)) { fenced.push(true); inFence = !inFence; } else fenced.push(inFence); }
  const level = (i: number) => (fenced[i] ? 0 : (/^(#{1,6})\s+\S/.exec(lines[i])?.[1].length ?? 0));
  const drop = new Set<number>();
  for (let i = 0; i < lines.length; i++) {
    const lv = level(i);
    if (!lv) continue;
    let j = i + 1;
    while (j < lines.length && !(level(j) && level(j) <= lv)) j++;
    const rest = lines.slice(i + 1, j).join("\n").replace(/<!--[\s\S]*?-->/g, "");
    if (!rest.trim()) drop.add(i);
  }
  return lines.filter((_, i) => !drop.has(i)).join("\n");
}

/** `m:ss` (or `h:mm:ss`) for a number of seconds. */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** Below this a `ts` is seconds into the meeting; at or above it, seconds since 1970 (2001-09-09). */
const EPOCH_FLOOR_S = 1e9;

/** How far into the meeting a segment was said, in seconds — or null when the segment carries no
 *  time. A meeting-relative `ts` wins; otherwise the wall-clock `tsMs` against the first line's. */
export function segmentOffset(segments: TranscriptSegment[], index: number): number | null {
  const s = segments[index];
  if (!s) return null;
  const num = (x: TranscriptSegment) => typeof x.ts === "number" ? x.ts : typeof x.ts === "string" && x.ts.trim() !== "" ? Number(x.ts) : NaN;
  const rel = num(s);
  // A `ts` this large is a wall-clock epoch (seconds), not a place in the meeting: shown as a
  // duration it read "497792:23:39" (founder, 2026-10-15). It is measured from the first line's.
  if (Number.isFinite(rel) && rel >= EPOCH_FLOOR_S) {
    const first = segments.map(num).find((v) => Number.isFinite(v) && v >= EPOCH_FLOOR_S);
    return first === undefined ? null : Math.max(0, rel - first);
  }
  if (Number.isFinite(rel)) return rel;
  const first = segments.find((x) => typeof x.tsMs === "number")?.tsMs;
  return typeof s.tsMs === "number" && typeof first === "number" ? (s.tsMs - first) / 1000 : null;
}

/** "Updated 14:32 · transcript to 12:05" — what the notes have taken in, in one line.
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
    parts.push(at === null ? "from the transcript" : `transcript to ${clock(at)}`);
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

// CREATE / UPDATE NOTES live in `minutes/meetingNote.ts` (the page header's actions call them too).
export { createMeetingNotes, updateMeetingNotes } from "../minutes/meetingNote";
