export type { TranscriptSegment, SegmentGroup, GroupingOptions, TranscriptState } from './types';
export { deduplicateSegments, upsertSegments, sortSegments, sortByStartTime, deduplicateByIdentity } from './dedup';
export { groupSegments } from './grouping';
export { parseUTCTimestamp } from './timestamps';
export {
  createTranscriptState,
  bootstrapConfirmed,
  applyTranscriptTick,
  recomputeTranscripts,
  addSegment,
  bootstrapSegments,
} from './state';
export type { TranscriptManager, TranscriptMessage } from './manager';
export { createTranscriptManager } from './manager';
