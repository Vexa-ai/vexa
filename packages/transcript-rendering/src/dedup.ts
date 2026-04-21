import type { TranscriptSegment } from './types';
import { parseUTCTimestamp } from './timestamps';

function normalizeText(t: string): string {
  return (t || '').trim().toLowerCase().replace(/[.,!?;:]+$/g, '').replace(/\s+/g, ' ');
}

/**
 * Deduplicate overlapping transcript segments.
 *
 * **Speaker-aware:** segments from different speakers are NEVER deduped against
 * each other, even if their timestamps overlap. This is critical for per-speaker
 * pipelines where concurrent speakers produce legitimately overlapping time ranges.
 *
 * Within the same speaker, handles:
 * - Adjacent duplicates (same text, gap ≤1s)
 * - Full containment (shorter segment inside longer)
 * - Expansion (partial → full text, e.g., draft → confirmed)
 * - Tail-repeat fragments (tiny echo already present in previous)
 *
 * Segments must be sorted by absolute_start_time before calling.
 *
 * @param segments - Array of segments sorted by absolute_start_time
 * @returns Deduplicated array preserving all original properties
 */
export function deduplicateSegments<T extends TranscriptSegment>(segments: T[]): T[] {
  if (segments.length === 0) return segments;

  const deduped: T[] = [];

  for (const seg of segments) {
    if (deduped.length === 0) {
      deduped.push(seg);
      continue;
    }

    const last = deduped[deduped.length - 1];

    // Different speakers: never dedup — overlapping timestamps are legitimate
    if ((seg.speaker || '') !== (last.speaker || '')) {
      deduped.push(seg);
      continue;
    }

    // Same speaker — apply dedup heuristics
    const segStart = parseUTCTimestamp(seg.absolute_start_time).getTime();
    const segEnd = parseUTCTimestamp(seg.absolute_end_time).getTime();
    const lastStart = parseUTCTimestamp(last.absolute_start_time).getTime();
    const lastEnd = parseUTCTimestamp(last.absolute_end_time).getTime();

    const segStartSec = segStart / 1000;
    const segEndSec = segEnd / 1000;
    const lastStartSec = lastStart / 1000;
    const lastEndSec = lastEnd / 1000;

    const sameText = (seg.text || '').trim() === (last.text || '').trim();
    const overlaps = Math.max(segStartSec, lastStartSec) < Math.min(segEndSec, lastEndSec);
    const gapSec = (segStart - lastEnd) / 1000;

    // Adjacent duplicate: same text within 1s gap
    if (!overlaps && sameText && gapSec >= 0 && gapSec <= 1) {
      // Prefer completed over draft, then longer duration
      if (preferSeg(seg, last)) {
        deduped[deduped.length - 1] = seg;
      }
      continue;
    }

    if (overlaps) {
      const segFullyInsideLast = segStartSec >= lastStartSec && segEndSec <= lastEndSec;
      const lastFullyInsideSeg = lastStartSec >= segStartSec && lastEndSec <= segEndSec;

      if (sameText) {
        if (preferSeg(seg, last)) {
          deduped[deduped.length - 1] = seg;
        }
        continue;
      }

      // Different text: containment.
      // Prefer the confirmed segment over a same-speaker draft regardless of
      // which one has the wider time range — Vexa routinely trims the
      // boundary tighter when confirming, which left the draft wider than
      // its own confirmed version and caused the pending-stuck bug.
      if (segFullyInsideLast) {
        if (seg.completed && !last.completed) {
          deduped[deduped.length - 1] = seg;
        }
        continue;
      }
      if (lastFullyInsideSeg) {
        // seg is wider. Keep it unless it's a draft while last is confirmed.
        if (last.completed && !seg.completed) {
          continue;
        }
        deduped[deduped.length - 1] = seg;
        continue;
      }

      // Partial overlap heuristics
      const segTextClean = normalizeText(seg.text || '');
      const lastTextClean = normalizeText(last.text || '');
      const segDuration = segEndSec - segStartSec;
      const lastDuration = lastEndSec - lastStartSec;
      const overlapStart = Math.max(segStartSec, lastStartSec);
      const overlapEnd = Math.min(segEndSec, lastEndSec);
      const overlapDuration = overlapEnd - overlapStart;
      const overlapRatioSeg = segDuration > 0 ? overlapDuration / segDuration : 0;
      const overlapRatioLast = lastDuration > 0 ? overlapDuration / lastDuration : 0;

      // Expansion: seg contains last's text and is longer
      const segExpandsLast =
        Boolean(lastTextClean) &&
        Boolean(segTextClean) &&
        segTextClean.includes(lastTextClean) &&
        segTextClean.length > lastTextClean.length;

      if (segExpandsLast && overlapRatioLast >= 0.5 && (seg.completed || !last.completed)) {
        deduped[deduped.length - 1] = seg;
        continue;
      }

      // Tail-repeat: seg text already inside last, and seg is tiny
      const segIsTailRepeat =
        Boolean(segTextClean) &&
        Boolean(lastTextClean) &&
        lastTextClean.includes(segTextClean);

      if (segIsTailRepeat) {
        const segWordCount = segTextClean.split(/\s+/).filter(w => w.length > 0).length;
        if (segDuration <= 1.5 && segWordCount <= 2 && overlapRatioSeg >= 0.25) {
          continue;
        }
      }
    }

    deduped.push(seg);
  }

  return deduped;
}

/** Return true if seg should replace last (prefer completed, then longer). */
function preferSeg<T extends TranscriptSegment>(seg: T, last: T): boolean {
  if (seg.completed && !last.completed) return true;
  if (!seg.completed && last.completed) return false;
  const segDur =
    parseUTCTimestamp(seg.absolute_end_time).getTime() -
    parseUTCTimestamp(seg.absolute_start_time).getTime();
  const lastDur =
    parseUTCTimestamp(last.absolute_end_time).getTime() -
    parseUTCTimestamp(last.absolute_start_time).getTime();
  return segDur > lastDur;
}

/**
 * Upsert segments into an existing map, handling draft→confirmed transitions.
 *
 * This is the core merge logic used by WS consumers (dashboard). Given a map
 * of existing segments (keyed by segment_id or absolute_start_time) and new
 * incoming segments, it:
 *
 * - Inserts new segments
 * - Updates existing segments when text or completed status changes
 * - Removes drafts when a confirmed segment from the same speaker arrives
 * - Deduplicates same-speaker same-text entries with different IDs
 *
 * @param existing - Map of existing segments (segment_id → segment)
 * @param incoming - New segments from WS or REST
 * @returns Updated map (mutates and returns `existing` for efficiency)
 */
export function upsertSegments<T extends TranscriptSegment>(
  existing: Map<string, T>,
  incoming: T[],
): Map<string, T> {
  for (const seg of incoming) {
    if (!seg.absolute_start_time || !(seg.text || '').trim()) continue;

    const key = seg.segment_id || seg.absolute_start_time;
    const prev = existing.get(key);

    // When a confirmed segment arrives, remove drafts from same speaker
    if (seg.completed && seg.speaker) {
      for (const [k, v] of existing.entries()) {
        if (k === key) continue;
        if (!v.completed && v.speaker === seg.speaker && k.includes(':draft:')) {
          existing.delete(k);
        }
      }
    }

    if (prev) {
      const prevText = (prev.text || '').trim();
      const newText = (seg.text || '').trim();
      const completedChanged = Boolean(prev.completed) !== Boolean(seg.completed);

      if (prevText !== newText || completedChanged) {
        existing.set(key, seg);
        continue;
      }

      // Same text — keep newer by updated_at
      if (prev.updated_at && seg.updated_at && prev.updated_at >= seg.updated_at) {
        continue;
      }
    }

    existing.set(key, seg);
  }

  // Remove draft→confirmed duplicates (same speaker + same text, different IDs)
  const textIndex = new Map<string, string>();
  for (const [key, seg] of existing.entries()) {
    const textKey = `${seg.speaker || ''}:${(seg.text || '').trim()}`;
    const existingKey = textIndex.get(textKey);

    if (existingKey && existingKey !== key) {
      const prev = existing.get(existingKey);
      if (prev) {
        if (seg.completed && !prev.completed) {
          existing.delete(existingKey);
        } else if (!seg.completed && prev.completed) {
          existing.delete(key);
          continue;
        }
      }
    }

    textIndex.set(textKey, key);
  }

  return existing;
}

/**
 * Sort segments by absolute_start_time (string comparison, ISO format).
 */
export function sortSegments<T extends TranscriptSegment>(segments: T[]): T[] {
  return [...segments].sort((a, b) =>
    a.absolute_start_time.localeCompare(b.absolute_start_time)
  );
}

/**
 * Sort segments by speech time (`start_time` seconds), not buffer confirmation time.
 *
 * `start_time` is relative to meeting start and reflects when speech occurred.
 * `absolute_start_time` reflects when the buffer was processed, which can be
 * out of order for different speakers with independent audio buffers.
 *
 * Falls back to `absolute_start_time` string comparison for segments with the
 * same `start_time`.
 */
export function sortByStartTime<T extends TranscriptSegment>(segments: T[]): T[] {
  return [...segments].sort((a, b) => {
    const aStart = a.start_time ?? 0;
    const bStart = b.start_time ?? 0;
    if (aStart !== bStart) return aStart - bStart;
    return a.absolute_start_time.localeCompare(b.absolute_start_time);
  });
}

/**
 * Deduplicate segments by identity key (`segment_id` or `absolute_start_time`).
 *
 * When two segments share the same key, the one with the newer `updated_at`
 * timestamp wins. This is a lightweight, identity-only dedup — it does not
 * inspect text overlap or time containment (use `deduplicateSegments` for that).
 *
 * Returns a new array in **encounter order** (not sorted).
 */
export function deduplicateByIdentity<T extends TranscriptSegment>(segments: T[]): T[] {
  const seen = new Map<string, T>();
  for (const seg of segments) {
    const key = seg.segment_id || seg.absolute_start_time;
    const existing = seen.get(key);
    if (!existing || (seg.updated_at && existing.updated_at && seg.updated_at > existing.updated_at)) {
      seen.set(key, seg);
    }
  }
  return Array.from(seen.values());
}
