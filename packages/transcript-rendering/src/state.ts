import type { TranscriptSegment, TranscriptState } from './types';

/**
 * Create an empty transcript state container.
 */
export function createTranscriptState<T extends TranscriptSegment = TranscriptSegment>(): TranscriptState<T> {
  return { confirmed: new Map(), pendingBySpeaker: new Map() };
}

/**
 * Segment key used for identity throughout the state functions.
 */
function segKey<T extends TranscriptSegment>(seg: T): string {
  return seg.segment_id || seg.absolute_start_time;
}

// ---------------------------------------------------------------------------
// Two-map model (confirmed + pending per speaker)
// ---------------------------------------------------------------------------

/**
 * Bootstrap confirmed segments from a REST response (or any initial load).
 *
 * Clears both maps, populates `confirmed` from `segments`, and returns the
 * recomputed sorted transcript array.
 *
 * Segments without `absolute_start_time` or with empty text are filtered out.
 */
export function bootstrapConfirmed<T extends TranscriptSegment>(
  state: TranscriptState<T>,
  segments: T[],
): T[] {
  state.confirmed.clear();
  state.pendingBySpeaker.clear();

  for (const seg of segments) {
    if (!seg.absolute_start_time || !(seg.text || '').trim()) continue;
    state.confirmed.set(segKey(seg), seg);
  }

  return recomputeTranscripts(state);
}

/**
 * Apply a single WebSocket tick.
 *
 * - Appends `confirmed` segments to the confirmed map (keyed by segment_id).
 * - If `speaker` is provided, fully replaces that speaker's pending array.
 *
 * Returns the recomputed sorted transcript array, or `null` if nothing changed
 * (callers can skip a state update in that case).
 */
export function applyTranscriptTick<T extends TranscriptSegment>(
  state: TranscriptState<T>,
  confirmed: T[],
  pending?: T[],
  speaker?: string | null,
): T[] | null {
  let changed = false;

  for (const seg of confirmed) {
    if (!seg.absolute_start_time || !(seg.text || '').trim()) continue;
    state.confirmed.set(segKey(seg), seg);
    changed = true;
  }

  if (speaker !== undefined && speaker !== null) {
    const validPending = (pending || []).filter(
      s => s.absolute_start_time && (s.text || '').trim(),
    );
    if (validPending.length > 0) {
      state.pendingBySpeaker.set(speaker, validPending);
    } else {
      state.pendingBySpeaker.delete(speaker);
    }
    changed = true;
  }

  if (!changed) return null;

  return recomputeTranscripts(state);
}

/**
 * Recompute the merged transcript array from confirmed + pending maps.
 *
 * Confirmed segments are always included. Pending segments are included only
 * if they are **not stale** — a pending segment is stale when its text matches,
 * starts with, or is a prefix of any confirmed text for the same speaker.
 *
 * Result is sorted by `absolute_start_time`.
 */
export function recomputeTranscripts<T extends TranscriptSegment>(
  state: TranscriptState<T>,
): T[] {
  // Build confirmed-text index per speaker
  const confirmedBySpeaker = new Map<string, Set<string>>();
  for (const seg of state.confirmed.values()) {
    const speaker = seg.speaker || '';
    if (!confirmedBySpeaker.has(speaker)) confirmedBySpeaker.set(speaker, new Set());
    confirmedBySpeaker.get(speaker)!.add((seg.text || '').trim());
  }

  const all: T[] = [...state.confirmed.values()];

  for (const [speaker, segs] of state.pendingBySpeaker) {
    const confirmedTexts = confirmedBySpeaker.get(speaker);
    for (const seg of segs) {
      const pt = (seg.text || '').trim();
      let isStale = false;
      if (confirmedTexts) {
        for (const ct of confirmedTexts) {
          if (pt === ct || pt.startsWith(ct) || ct.startsWith(pt)) {
            isStale = true;
            break;
          }
        }
      }
      if (isStale) continue;
      all.push(seg);
    }
  }

  all.sort((a, b) => a.absolute_start_time.localeCompare(b.absolute_start_time));
  return all;
}

// ---------------------------------------------------------------------------
// Additive model (simple array, used for lightweight live-session stores)
// ---------------------------------------------------------------------------

/**
 * Add or update a segment in an existing array.
 *
 * - If a segment with the same identity already exists, it is replaced in place.
 * - If it's new **and** confirmed, same-speaker drafts that overlap in time are
 *   removed (prevents the "show, disappear, come back" flash).
 *
 * Returns a **new** array (does not mutate the input). Does **not** sort — the
 * caller should chain with the desired sort (e.g. `sortByStartTime`).
 */
export function addSegment<T extends TranscriptSegment>(
  segments: readonly T[],
  segment: T,
): T[] {
  const key = segKey(segment);
  const existingIndex = segments.findIndex(t => segKey(t) === key);

  let updated: T[];

  if (existingIndex !== -1) {
    // Same segment — update in place (latest version wins)
    updated = [...segments];
    updated[existingIndex] = segment;
  } else {
    updated = [...segments, segment];

    // When a confirmed segment arrives, remove same-speaker overlapping drafts
    if (segment.completed && segment.speaker) {
      const segStart = segment.start_time ?? 0;
      const segEnd = segment.end_time ?? segStart;
      updated = updated.filter(t => {
        if (t === segment) return true;
        if (t.completed) return true;
        if (t.speaker !== segment.speaker) return true;
        const tStart = t.start_time ?? 0;
        const tEnd = t.end_time ?? tStart;
        const overlaps = tStart < segEnd && tEnd > segStart;
        return !overlaps;
      });
    }
  }

  return updated;
}

/**
 * Bootstrap an array of segments: filter out invalid entries and deduplicate
 * by segment identity (last occurrence wins).
 *
 * Does **not** sort — the caller should chain with the desired sort.
 */
export function bootstrapSegments<T extends TranscriptSegment>(
  segments: T[],
): T[] {
  const valid = segments.filter(
    seg => seg.absolute_start_time && (seg.text || '').trim(),
  );

  const map = new Map<string, T>();
  for (const seg of valid) {
    map.set(segKey(seg), seg);
  }

  return Array.from(map.values());
}
