import type { TranscriptSegment } from "@/types/vexa";
import { parseUTCTimestamp } from "./utils";

/**
 * Deduplicates overlapping transcript segments using the same logic as the backend.
 * Handles:
 * - Full containment (one segment fully inside another)
 * - Expansion (current segment contains previous segment's text)
 * - Tail-repeat fragments (current segment is a substring of previous)
 * 
 * @param segments - Array of segments sorted by absolute_start_time
 * @returns Deduplicated array of segments
 */
export function deduplicateOverlappingSegments(
  segments: TranscriptSegment[]
): TranscriptSegment[] {
  if (segments.length === 0) return segments;

  const normalizeText = (t: string): string =>
    (t || "")
      .trim()
      .toLowerCase()
      // strip trailing punctuation
      .replace(/[.,!?;:]+$/g, "")
      .replace(/\s+/g, " ");

  const speakerIsKnown = (s?: string): boolean => {
    const v = (s || "").trim();
    return v.length > 0 && v.toLowerCase() !== "unknown";
  };

  const scoreSegment = (s: TranscriptSegment): number => {
    // Higher score = more desirable to keep when de-duping duplicates.
    const known = speakerIsKnown(s.speaker) ? 10 : 0;
    const completed = s.completed ? 5 : 0;
    const dur = Math.max(0, parseUTCTimestamp(s.absolute_end_time).getTime() - parseUTCTimestamp(s.absolute_start_time).getTime());
    // duration bucket (0..3)
    const durScore = dur >= 4000 ? 3 : dur >= 2000 ? 2 : dur >= 800 ? 1 : 0;
    return known + completed + durScore;
  };

  const deduped: TranscriptSegment[] = [];

  for (const seg of segments) {
    if (deduped.length === 0) {
      deduped.push(seg);
      continue;
    }

    const last = deduped[deduped.length - 1];

    // Parse absolute times to timestamps (milliseconds)
    const segStart = parseUTCTimestamp(seg.absolute_start_time).getTime();
    const segEnd = parseUTCTimestamp(seg.absolute_end_time).getTime();
    const lastStart = parseUTCTimestamp(last.absolute_start_time).getTime();
    const lastEnd = parseUTCTimestamp(last.absolute_end_time).getTime();

    // Convert to seconds for comparison (matching backend logic)
    const segStartSec = segStart / 1000;
    const segEndSec = segEnd / 1000;
    const lastStartSec = lastStart / 1000;
    const lastEndSec = lastEnd / 1000;

    const sameText = (seg.text || "").trim() === (last.text || "").trim();
    const overlaps =
      Math.max(segStartSec, lastStartSec) < Math.min(segEndSec, lastEndSec);

    // Adjacent duplicate (no overlap): same text repeated across a tiny gap.
    // This happens when speaker mapping updates cause the same sentence to be resent
    // as a new segment with different speaker / completion.
    const gapSec = (segStart - lastEnd) / 1000;
    if (!overlaps && sameText && gapSec >= 0 && gapSec <= 1.0) {
      const keepSeg = scoreSegment(seg) >= scoreSegment(last);
      if (keepSeg) {
        console.debug(
          `[Dedup] Replacing adjacent duplicate '${last.text}' (${lastStartSec}-${lastEndSec}) with '${seg.text}' (${segStartSec}-${segEndSec}) (gap=${gapSec.toFixed(2)}s)`
        );
        deduped[deduped.length - 1] = seg;
      } else {
        console.debug(
          `[Dedup] Dropping adjacent duplicate '${seg.text}' (${segStartSec}-${segEndSec}) (gap=${gapSec.toFixed(2)}s)`
        );
      }
      continue;
    }

    if (overlaps) {
      // Check if one segment is fully contained within another
      const segFullyInsideLast =
        segStartSec >= lastStartSec && segEndSec <= lastEndSec;
      const lastFullyInsideSeg =
        lastStartSec >= segStartSec && lastEndSec <= segEndSec;

      if (sameText) {
        // Same text: prefer the outer/longer segment
        if (segFullyInsideLast) {
          // Current is fully inside last → drop current
          console.debug(
            `[Dedup] Dropping segment '${seg.text}' (${segStartSec}-${segEndSec}) - fully contained in '${last.text}' (${lastStartSec}-${lastEndSec})`
          );
          continue;
        }
        if (lastFullyInsideSeg) {
          // Last is fully inside current → replace with current
          console.debug(
            `[Dedup] Replacing segment '${last.text}' (${lastStartSec}-${lastEndSec}) with '${seg.text}' (${segStartSec}-${segEndSec})`
          );
          deduped[deduped.length - 1] = seg;
          continue;
        }
      } else {
        // Different text: prefer the longer/outer segment
        if (segFullyInsideLast) {
          // Current is fully inside last → drop current (prefer outer segment)
          console.debug(
            `[Dedup] Dropping segment '${seg.text}' (${segStartSec}-${segEndSec}) - fully contained in '${last.text}' (${lastStartSec}-${lastEndSec})`
          );
          continue;
        }
        if (lastFullyInsideSeg) {
          // Last is fully inside current → replace with current (prefer outer segment)
          console.debug(
            `[Dedup] Replacing segment '${last.text}' (${lastStartSec}-${lastEndSec}) with '${seg.text}' (${segStartSec}-${segEndSec})`
          );
          deduped[deduped.length - 1] = seg;
          continue;
        }

        // Partial-overlap heuristics (no full containment):
        // - "expansion": current is a longer revision that contains the previous text -> replace previous with current
        // - "tail-repeat": current is a tiny suffix/echo already present in previous -> drop current
        if (!segFullyInsideLast && !lastFullyInsideSeg) {
          const segTextClean = normalizeText(seg.text || "");
          const lastTextClean = normalizeText(last.text || "");

          const segDuration = segEndSec - segStartSec;
          const lastDuration = lastEndSec - lastStartSec;
          const overlapStart = Math.max(segStartSec, lastStartSec);
          const overlapEnd = Math.min(segEndSec, lastEndSec);
          const overlapDuration = overlapEnd - overlapStart;
          const overlapRatioSeg = segDuration > 0 ? overlapDuration / segDuration : 0;
          const overlapRatioLast =
            lastDuration > 0 ? overlapDuration / lastDuration : 0;

          // Expansion: last text appears inside seg text, and seg is "more complete" -> replace last with seg.
          // This fixes cases like:
          //   last="It was a milestone." (partial) then seg="It was a milestone to get ..." (completed)
          const segExpandsLast =
            Boolean(lastTextClean) &&
            Boolean(segTextClean) &&
            lastTextClean.length > 0 &&
            segTextClean.includes(lastTextClean) &&
            segTextClean.length > lastTextClean.length;

          const lastCompleted = Boolean(last.completed);
          const segCompleted = Boolean(seg.completed);

          if (
            segExpandsLast &&
            overlapRatioLast >= 0.5 &&
            (segCompleted || !lastCompleted)
          ) {
            console.debug(
              `[Dedup] Replacing shorter/partial segment '${last.text}' (${lastStartSec}-${lastEndSec}, overlap=${(overlapRatioLast * 100).toFixed(1)}%) with expansion '${seg.text}' (${segStartSec}-${segEndSec})`
            );
            deduped[deduped.length - 1] = seg;
            continue;
          }

          // Tail-repeat: seg text already appears in last text, and seg is tiny -> drop seg.
          // Check if last segment contains current segment's text (current is a fragment/echo)
          const segIsTailRepeat =
            Boolean(segTextClean) &&
            Boolean(lastTextClean) &&
            lastTextClean.includes(segTextClean);

          if (segIsTailRepeat) {
            const segWordCount = segTextClean.split(/\s+/).filter((w) => w.length > 0).length;
            // Drop if: tiny segment (<=2 words, <1.5s) and overlaps at least a bit (>=25% of seg)
            if (
              segDuration < 1.5 &&
              segWordCount <= 2 &&
              overlapRatioSeg >= 0.25
            ) {
              console.debug(
                `[Dedup] Dropping tail-repeat fragment '${seg.text}' (${segStartSec}-${segEndSec}, ${segDuration.toFixed(2)}s, ${segWordCount} words, overlap=${(overlapRatioSeg * 100).toFixed(1)}%) - already present in '${last.text}' (${lastStartSec}-${lastEndSec})`
              );
              continue;
            }
          }
        }
      }
    }

    deduped.push(seg);
  }

  return deduped;
}
