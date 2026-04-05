/**
 * Minimal segment interface required by the rendering pipeline.
 * Consumers extend this with their own fields — extra properties pass through untouched.
 */
export interface TranscriptSegment {
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
export interface SegmentGroup<T extends TranscriptSegment = TranscriptSegment> {
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
export interface GroupingOptions {
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
