"use client";
/** EXTEND ON A TRANSCRIPT SELECTION (Vexa-ai/vexa#1596). Founder, 2026-09-06, in a live meeting with
 *  the canvas open: *"we also want extend on transcript when i can select some text and push the
 *  button"*.
 *
 *  THE SAME CONTROL A PAGE HAS, not one that looks like it: the button, the rect, the reading of the
 *  selection and the optional one-line field (#1593) all come from `minutes/ExtendAction`'s
 *  `SelectionAct` — so what lands on the page's Extend lands on this one too, without a second
 *  implementation to remember. What is different here is only what the press MEANS, which is the one
 *  thing a room and a file genuinely disagree about.
 *
 *  IT WRITES NOTHING HERE. The act carries the passage, the meeting and where in the room it was
 *  said; the pages it produces are written by the agent through the `extend-transcript` ask, and the
 *  terms it names reach the transcript through the annotation layer (#1595). The transcript itself
 *  is never rewritten — a record of what was heard that gets edited afterwards is no longer a
 *  record.
 *
 *  A SEPARATE FILE, deliberately, and not a few lines inside the engine: `LiveTranscriptEngine` is
 *  "the ONE live-transcript render engine — it renders, it does not re-derive", and a control that
 *  posts intents is neither rendering nor derivation. The engine stays untouched.
 */
import type { RefObject } from "react";
import { SelectionAct } from "../minutes/ExtendAction";
import { postIntent } from "../minutes/extend";
import { segmentRef } from "./segmentSelection";
import type { TranscriptSegment } from "./types";

export function TranscriptExtend(p: {
  /** the transcript's own box — a selection anywhere else is not this transcript's */
  containerRef: RefObject<HTMLElement | null>;
  /** the meeting ROW id, the same one Highlight and a term chip send */
  meeting: string;
  /** the rendered segments, for locating the passage — see `segmentSelection.ts` */
  segments: TranscriptSegment[];
}) {
  return (
    <SelectionAct containerRef={p.containerRef} act="extend-transcript"
      hint="Extend — ask this chat to go further on what was said here"
      fieldLabel="What to do with what was said (optional)"
      // A NEW MEETING IS A NEW ROOM: a line typed about one transcript must never fire against the
      // one that replaced it in the same pane.
      slot={p.meeting}
      // The intent goes BACK to the control (Vexa-ai/vexa#1604): the state it shows while the act
      // runs is keyed by the act's target, and only the posted intent knows what that is.
      onFire={(selection, instruction) => postIntent({
        kind: "extend_transcript", meeting: p.meeting, selection,
        // WHERE IT WAS SAID, or nothing. `segmentRef` returns `{}` rather than a guess when the
        // passage cannot be found in exactly one segment, and `normalizeIntent` drops empties.
        ...segmentRef(p.segments, selection),
        ...(instruction ? { instruction } : {}),
      })} />
  );
}
