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

export { type GroupingOptions, type SegmentGroup, type TranscriptSegment, deduplicateSegments, groupSegments, parseUTCTimestamp, sortSegments, upsertSegments };
