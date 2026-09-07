/** WHERE A SELECTED PASSAGE WAS SAID — the transcript's answer to `sourceRange` (Vexa-ai/vexa#1596).
 *
 *  Extend on a page carries an offset into the file, because a file is what it acts on. Extend on a
 *  transcript carries the SEGMENT the passage starts in — who was speaking, and when — because that
 *  is what "where" means in a room, and it is the provenance the pages the act writes will cite.
 *
 *  IT RE-FINDS THE WORDS, IT DOES NOT JOIN ON IDS. The same rule `TermText` already follows: the
 *  renderer draws text, the gateway's rows and the live SSE do not share an id space, and a DOM
 *  attribute carrying a segment id would be a second answer to a question the text already answers.
 *  So the passage is located in the segments themselves — and only when it occurs there exactly
 *  ONCE. Two occurrences is not a near-miss, it is an unknown answer (F63), and an unknown answer is
 *  omitted: the selection and the meeting still travel, the speaker and the timestamp do not.
 *
 *  WHY JOINING EVERY SEGMENT WITH ONE SPACE IS THE RIGHT HAYSTACK. `LiveTranscriptEngine` merges
 *  consecutive same-speaker segments into a block with exactly that join, so any selection made
 *  inside a rendered block is a contiguous substring of this string — without this module having to
 *  keep its own copy of the merge rule, which would be a second writer of it.
 */
import type { TranscriptSegment } from "./types";

/** What a passage's origin amounts to. Every field is optional and every absent one means "not
 *  established", never "none" — the intent omits them rather than sending an empty string. */
export interface SegmentRef {
  segment?: string;
  speaker?: string;
  /** ISO 8601, UTC. The engine renders the same instant in the reader's own zone; an act is read by
   *  an agent, and a wall-clock time with no zone is the ambiguity this avoids. */
  at?: string;
}

/** A rendered selection arrives with the whitespace of the layout in it — line breaks between
 *  blocks, indentation, a double space after a merge. The segments carry the whitespace of the ASR.
 *  Neither is a fact about the words, so both are flattened before they are compared. */
const flat = (s: string): string => String(s ?? "").replace(/\s+/g, " ").trim();

function refOf(segment: TranscriptSegment): SegmentRef {
  const at = typeof segment.tsMs === "number" && Number.isFinite(segment.tsMs)
    ? new Date(segment.tsMs).toISOString()
    : "";
  return {
    ...(segment.id ? { segment: String(segment.id) } : {}),
    ...(segment.speaker ? { speaker: String(segment.speaker) } : {}),
    ...(at ? { at } : {}),
  };
}

/** The segment a selection STARTS in — or `{}` when that cannot be established exactly.
 *
 *  Pending segments (`completed === false`) are skipped, exactly as the engine skips them when it
 *  builds its blocks: the live tail is a re-forming guess at what is being said right now, it has no
 *  stable id, and a passage anchored to it would name a segment that no longer exists a second
 *  later. A selection that reaches into the live tail simply does not match, which is the honest
 *  outcome — the words still travel, their origin does not.
 */
export function segmentRef(segments: TranscriptSegment[] | undefined | null, selection: string): SegmentRef {
  const needle = flat(selection);
  if (!needle) return {};
  const said = (segments ?? []).filter((s) => s && s.completed !== false && flat(s.text));
  if (!said.length) return {};

  const texts = said.map((s) => flat(s.text));
  const joined = texts.join(" ");
  const first = joined.indexOf(needle);
  if (first < 0) return {};
  if (joined.indexOf(needle, first + 1) >= 0) return {};    // said twice → we do not know which

  let at = 0;
  for (let i = 0; i < texts.length; i++) {
    const end = at + texts[i].length;
    if (first < end) return refOf(said[i]);
    at = end + 1;                                            // + the one joining space
  }
  return {};
}
