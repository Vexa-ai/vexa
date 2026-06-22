/**
 * Minimal segment interface required by the rendering pipeline.
 * Consumers extend this with their own fields — extra properties pass through untouched.
 */
interface TranscriptSegment {
    text: string;
    speaker?: string;
    absolute_start_time: string;
    absolute_end_time: string;
    completed?: boolean;
    /** Stable segment identity (e.g., "speakerA:3" or "inject-0-10.5") */
    segment_id?: string;
    /** Relative start time in seconds (used by grouping) */
    start_time?: number;
    /** Relative end time in seconds (used by grouping) */
    end_time?: number;
    /** ISO timestamp of last update */
    updated_at?: string;
}
/**
 * A group of consecutive segments merged together (e.g., by speaker).
 */
interface SegmentGroup<T extends TranscriptSegment = TranscriptSegment> {
    /** Grouping key (e.g., speaker name) */
    key: string;
    /** ISO absolute timestamp of the first segment */
    startTime: string;
    /** ISO absolute timestamp of the last segment */
    endTime: string;
    /** Relative start time in seconds */
    startTimeSeconds: number;
    /** Relative end time in seconds */
    endTimeSeconds: number;
    /** Combined text from all segments in the group */
    combinedText: string;
    /** Original segments that make up this group */
    segments: T[];
}
/**
 * Mutable state container for the two-map transcript model.
 *
 * - `confirmed`: segments keyed by segment_id (or absolute_start_time fallback).
 *   Append-only — each confirmed segment upserts by key.
 * - `pendingBySpeaker`: per-speaker array of draft segments, fully replaced on
 *   each WebSocket tick for that speaker.
 *
 * Passed to `bootstrapConfirmed`, `applyTranscriptTick`, and `recomputeTranscripts`.
 */
interface TranscriptState<T extends TranscriptSegment = TranscriptSegment> {
    confirmed: Map<string, T>;
    pendingBySpeaker: Map<string, T[]>;
}
/**
 * Configuration for segment grouping.
 */
interface GroupingOptions {
    /**
     * Returns the grouping key for a segment.
     * Consecutive segments with the same key are grouped together.
     * Default: groups by speaker.
     */
    getGroupKey?: (segment: TranscriptSegment) => string;
    /**
     * Maximum characters in a single group's combined text before splitting.
     * Default: 512
     */
    maxCharsPerGroup?: number;
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
declare function deduplicateSegments<T extends TranscriptSegment>(segments: T[]): T[];
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
declare function upsertSegments<T extends TranscriptSegment>(existing: Map<string, T>, incoming: T[]): Map<string, T>;
/**
 * Sort segments by absolute_start_time (string comparison, ISO format).
 */
declare function sortSegments<T extends TranscriptSegment>(segments: T[]): T[];
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
declare function sortByStartTime<T extends TranscriptSegment>(segments: T[]): T[];
/**
 * Deduplicate segments by identity key (`segment_id` or `absolute_start_time`).
 *
 * When two segments share the same key, the one with the newer `updated_at`
 * timestamp wins. This is a lightweight, identity-only dedup — it does not
 * inspect text overlap or time containment (use `deduplicateSegments` for that).
 *
 * Returns a new array in **encounter order** (not sorted).
 */
declare function deduplicateByIdentity<T extends TranscriptSegment>(segments: T[]): T[];

/**
 * Group consecutive segments by a configurable key (default: speaker).
 *
 * Consecutive segments with the same key are merged into a single group.
 * Long groups are split into chunks at segment boundaries when combined text
 * exceeds `maxCharsPerGroup`.
 *
 * @param segments - Array of segments (will be sorted by absolute_start_time)
 * @param options - Grouping configuration
 * @returns Array of segment groups
 */
declare function groupSegments<T extends TranscriptSegment>(segments: T[], options?: GroupingOptions): SegmentGroup<T>[];

/**
 * Parse a timestamp string as UTC.
 *
 * Many transcription APIs return timestamps without timezone suffix
 * (e.g., "2025-12-11T14:20:25.222296") which JavaScript interprets as local time.
 * This function ensures UTC interpretation by appending 'Z' when no timezone is present.
 */
declare function parseUTCTimestamp(timestamp: string): Date;

/**
 * Create an empty transcript state container.
 */
declare function createTranscriptState<T extends TranscriptSegment = TranscriptSegment>(): TranscriptState<T>;
/**
 * Bootstrap confirmed segments from a REST response (or any initial load).
 *
 * Clears both maps, populates `confirmed` from `segments`, and returns the
 * recomputed sorted transcript array.
 *
 * Segments without `absolute_start_time` or with empty text are filtered out.
 */
declare function bootstrapConfirmed<T extends TranscriptSegment>(state: TranscriptState<T>, segments: T[]): T[];
/**
 * Apply a single WebSocket tick.
 *
 * - Appends `confirmed` segments to the confirmed map (keyed by segment_id).
 * - If `speaker` is provided, fully replaces that speaker's pending array.
 *
 * Returns the recomputed sorted transcript array, or `null` if nothing changed
 * (callers can skip a state update in that case).
 */
declare function applyTranscriptTick<T extends TranscriptSegment>(state: TranscriptState<T>, confirmed: T[], pending?: T[], speaker?: string | null): T[] | null;
/**
 * Recompute the merged transcript array from confirmed + pending maps.
 *
 * Confirmed segments are always included. Pending segments are included only
 * if they are **not stale** — a pending segment is stale when its text matches,
 * starts with, or is a prefix of any confirmed text for the same speaker.
 *
 * Result is sorted by `absolute_start_time`.
 */
declare function recomputeTranscripts<T extends TranscriptSegment>(state: TranscriptState<T>): T[];
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
declare function addSegment<T extends TranscriptSegment>(segments: readonly T[], segment: T): T[];
/**
 * Bootstrap an array of segments: filter out invalid entries and deduplicate
 * by segment identity (last occurrence wins).
 *
 * Does **not** sort — the caller should chain with the desired sort.
 */
declare function bootstrapSegments<T extends TranscriptSegment>(segments: T[]): T[];

/**
 * Raw WebSocket transcript message from the Vexa gateway.
 *
 * Format: `{ type: "transcript", speaker, confirmed: [...], pending: [...] }`
 */
interface TranscriptMessage {
    type: 'transcript';
    meeting?: {
        id?: number;
    };
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
interface TranscriptManager<T extends TranscriptSegment = TranscriptSegment> {
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
declare function createTranscriptManager<T extends TranscriptSegment = TranscriptSegment>(): TranscriptManager<T>;

export { type GroupingOptions, type SegmentGroup, type TranscriptManager, type TranscriptMessage, type TranscriptSegment, type TranscriptState, addSegment, applyTranscriptTick, bootstrapConfirmed, bootstrapSegments, createTranscriptManager, createTranscriptState, deduplicateByIdentity, deduplicateSegments, groupSegments, parseUTCTimestamp, recomputeTranscripts, sortByStartTime, sortSegments, upsertSegments };
