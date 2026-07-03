import type { TranscriptSegment, TranscriptState } from './types';
import { createTranscriptState, bootstrapConfirmed, applyTranscriptTick, recomputeTranscripts } from './state';
import { deduplicateByIdentity, deduplicateSegments, sortSegments, sortByStartTime } from './dedup';

/**
 * Raw WebSocket transcript message from the Vexa gateway.
 *
 * Format: `{ type: "transcript", speaker, confirmed: [...], pending: [...] }`
 */
export interface TranscriptMessage {
  type: 'transcript';
  meeting?: { id?: number };
  speaker?: string;
  confirmed?: TranscriptSegment[];
  pending?: TranscriptSegment[];
  ts?: string;
}

/**
 * High-level transcript manager that encapsulates the full pipeline.
 *
 * Consumers feed it raw WS messages or REST bootstrap data and get back
 * deduplicated, sorted segments ready for rendering.
 *
 * ```ts
 * const manager = createTranscriptManager();
 *
 * // Bootstrap from REST
 * const segments = manager.bootstrap(restSegments);
 * render(segments);
 *
 * // On each WS message
 * ws.onmessage = (e) => {
 *   const segments = manager.handleMessage(JSON.parse(e.data));
 *   if (segments) render(segments);
 * };
 * ```
 */
export interface TranscriptManager<T extends TranscriptSegment = TranscriptSegment> {
  /** Load initial segments from REST. Clears previous state. Returns ready-to-render segments. */
  bootstrap(segments: T[]): T[];
  /** Process a raw WS message. Returns updated segments if state changed, null otherwise. */
  handleMessage(message: TranscriptMessage): T[] | null;
  /** Get current deduplicated, sorted segments without processing a new message. */
  getSegments(): T[];
  /** Access the underlying state (for advanced use cases). */
  getState(): TranscriptState<T>;
  /** Reset all state. */
  clear(): void;
}

/**
 * Create a transcript manager that handles the full pipeline:
 * WS message parsing → confirmed/pending state → dedup → sort.
 */
export function createTranscriptManager<
  T extends TranscriptSegment = TranscriptSegment,
>(): TranscriptManager<T> {
  let state: TranscriptState<T> = createTranscriptState<T>();

  function finalize(segments: T[]): T[] {
    // 1. Identity dedup (by segment_id, keeps newer by updated_at)
    // 2. Sort by absolute_start_time (required input for overlap dedup)
    // 3. Overlap dedup (same speaker: adjacent duplicates, containment, expansion, tail-repeat)
    // 4. Sort by speech time for display
    return sortByStartTime(deduplicateSegments(sortSegments(deduplicateByIdentity(segments))));
  }

  return {
    bootstrap(segments: T[]): T[] {
      return finalize(bootstrapConfirmed(state, segments));
    },

    handleMessage(message: TranscriptMessage): T[] | null {
      if (message.type !== 'transcript') return null;

      const confirmed = (message.confirmed || []) as T[];
      const pending = (message.pending || []) as T[];
      const speaker = message.speaker ?? undefined;

      const result = applyTranscriptTick(state, confirmed, pending, speaker);
      return result ? finalize(result) : null;
    },

    getSegments(): T[] {
      return finalize(recomputeTranscripts(state));
    },

    getState(): TranscriptState<T> {
      return state;
    },

    clear(): void {
      state = createTranscriptState<T>();
    },
  };
}
