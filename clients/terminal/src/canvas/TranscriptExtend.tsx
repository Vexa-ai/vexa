"use client";
import type { RefObject } from "react";
import { SelectionAct, referenceInChat } from "../minutes/ExtendAction";
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
      slot={p.meeting}
      onReference={(selection) => referenceInChat(selection, {
        meeting: p.meeting, ...segmentRef(p.segments, selection),
      })} />
  );
}
