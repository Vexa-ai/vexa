/** OPEN CHIPS — the two things a meeting chat can always show you (Vexa-ai/vexa#1586).
 *
 *  The founder asked a meeting chat to open its transcript. It did not, and — his second sentence,
 *  which is the one this file answers — it *"did not give a button that should open it."* A meeting
 *  chat has a transcript and a note sitting behind it, and nothing in the conversation offered
 *  either as one click.
 *
 *  THREE PROPERTIES, and each is a rule the proposal chips already earned the hard way:
 *
 *  · **STANDING, not empty-state.** `proposals()` fills the void an empty chat leaves and is gone
 *    at the first turn. These are for the chat with 677 segments behind it, so they render beside
 *    the composer in every state of the conversation.
 *  · **Read off the CHAT RECORD, no model call and no fetch.** Pure, over the pages the room
 *    already holds — the same list the panel renders (PRD decision 18). A chip that had to ask a
 *    model whether a transcript exists would cost a turn to answer a question the record answers.
 *  · **Present only when the thing exists** — F36's rule. The note page exists only when the meeting
 *    has a document at all, so presence in the record IS existence for it, and a chip that appears
 *    because the row looked short is exactly the default the founder ruled out.
 *
 *  ⚠ THE TRANSCRIPT IS NOT IN THE RECORD, IT IS THE MEETING (Vexa-ai/vexa#1597). This read the strip
 *  for it too, and the founder found the hole the same day: *"i seem to have closed the transcript
 *  and now can't find one"*. He had pressed `×` on the transcript tab — which is the reader saying
 *  "stop keeping this page", a perfectly good thing to say — and the button that would bring it back
 *  went with it, because the button was reading the strip to decide whether a transcript existed.
 *  Existence is not a property of his tabs. A meeting chat HAS a transcript once the meeting has
 *  begun, so the PHASE answers that (`live` or `post` — `pagesForPhase`'s own rule), and the strip
 *  is used only for the page it already holds, so the chip and the tab agree when both are there.
 *  With no phase in hand this falls back to the strip, which is exactly what it did before.
 *
 *  The NOTE keeps reading the strip, and the asymmetry is real rather than an oversight: its PATH is
 *  the server's answer (`/api/meeting/note`) and the record is where that answer is held, while the
 *  transcript's whole identity is the meeting id the chat already carries.
 *
 *  What a click DOES is the shell's — `openPage`, the one route into the panel, the same route the
 *  agent's own `open` event takes. Nothing here opens anything.
 */
import type { MeetingPhase } from "../surfaces/meetingModel";
import type { Page } from "./types";

export type OpenChip = {
  /** stable across renders — the React key, and what a test names */
  id: "transcript" | "note";
  label: string;
  /** the page to put in front, taken verbatim off the record */
  page: Page;
};

/** THE MEETING'S OWN DOCUMENTS live here, under one folder, whatever they are called inside it.
 *
 *  Two writers spell the note two ways (`kg/entities/meeting/<native>.md` from the transcription
 *  watcher, `kg/entities/meeting/<day>-<HHMM>-<slug>.md` from the production recipe) and this file
 *  refuses to learn either: matching the FOLDER is the one test that survives a third spelling. */
const NOTE_HOME = "kg/entities/meeting/";

/** Is this page the meeting's note? The folder, minus the two things in it that are not notes: the
 *  folder's own index, and the `?mock=1` fixture's canned transcript markdown (a real transcript is
 *  never a file — founder ruling 2026-09-01 — so a `.transcript.md` in here is only ever the mock). */
function isNote(pg: Page): boolean {
  if (pg.kind === "meeting" || !pg.path.startsWith(NOTE_HOME)) return false;
  const name = pg.path.slice(NOTE_HOME.length);
  return name.endsWith(".md") && name !== "index.md" && !name.endsWith(".transcript.md");
}

/**
 *  The chips this chat may offer, in reading order: the transcript leads, as it does in the room.
 *
 *  `meeting` absent ⇒ NOTHING. These are a meeting chat's affordance; a plain conversation that
 *  happens to have a meeting note open is not one, and offering "Open transcript" there would name
 *  a transcript that belongs to no meeting in view.
 *
 *  `phase` says whether this meeting HAS a transcript — `live` and `post` do, `prep` does not, which
 *  is `pagesForPhase`'s rule and not a second opinion about it. Absent (a caller that does not know,
 *  or a `?mock=1` room with no row behind it) the strip answers instead, as it always did.
 *
 *  The note's chip takes the label the ROOM gave the page — "Brief" before the meeting, "Minutes"
 *  after — rather than a word of its own, so the button and the tab it opens cannot disagree about
 *  what the reader is being handed.
 */
export function openChips(meeting: string | undefined, pages: Page[],
                          phase?: MeetingPhase | null): OpenChip[] {
  if (!meeting) return [];
  const out: OpenChip[] = [];
  // the strip's own entry when it has one — it carries the label the room gave it — and the meeting
  // itself when it does not, which is the case `×` on the transcript tab leaves behind.
  const inStrip = pages.find((pg) => pg.kind === "meeting");
  const transcript = inStrip
    ?? (phase === "live" || phase === "post"
      ? { kind: "meeting" as const, path: meeting, label: "Transcript" }
      : undefined);
  if (transcript) out.push({ id: "transcript", label: "Open transcript", page: transcript });
  const note = pages.find(isNote);
  if (note) out.push({ id: "note", label: `Open ${(note.label || "note").toLowerCase()}`, page: note });
  return out;
}
