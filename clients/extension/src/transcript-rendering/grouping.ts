import type { TranscriptSegment, SegmentGroup, GroupingOptions } from './types';

const DEFAULT_MAX_CHARS = 512;

function defaultGetGroupKey(segment: TranscriptSegment): string {
  return segment.speaker || 'Unknown';
}

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
export function groupSegments<T extends TranscriptSegment>(
  segments: T[],
  options: GroupingOptions = {},
): SegmentGroup<T>[] {
  if (!segments || segments.length === 0) return [];

  const getGroupKey = options.getGroupKey ?? defaultGetGroupKey;
  const maxChars = options.maxCharsPerGroup ?? DEFAULT_MAX_CHARS;

  const sorted = [...segments].sort((a, b) =>
    a.absolute_start_time.localeCompare(b.absolute_start_time),
  );

  // Collect raw groups of consecutive same-key segments
  const rawGroups: { key: string; segments: T[] }[] = [];
  let current: { key: string; segments: T[] } | null = null;

  for (const seg of sorted) {
    const text = (seg.text || '').trim();
    if (!text) continue;

    const key = getGroupKey(seg);
    if (current && current.key === key) {
      current.segments.push(seg);
    } else {
      if (current) rawGroups.push(current);
      current = { key, segments: [seg] };
    }
  }
  if (current) rawGroups.push(current);

  // Split large groups at segment boundaries
  const groups: SegmentGroup<T>[] = [];

  for (const raw of rawGroups) {
    if (raw.segments.length === 0) continue;

    let chunkSegments: T[] = [];
    let chunkText = '';

    const flushChunk = () => {
      if (chunkSegments.length === 0) return;
      const first = chunkSegments[0];
      const last = chunkSegments[chunkSegments.length - 1];
      groups.push({
        key: raw.key,
        startTime: first.absolute_start_time,
        endTime: last.absolute_end_time || last.absolute_start_time,
        startTimeSeconds: first.start_time ?? 0,
        endTimeSeconds: last.end_time ?? 0,
        combinedText: chunkText.trim(),
        segments: chunkSegments,
      });
      chunkSegments = [];
      chunkText = '';
    };

    for (const seg of raw.segments) {
      const segText = (seg.text || '').trim();
      if (!segText) continue;

      const candidate = chunkText ? `${chunkText} ${segText}` : segText;
      if (chunkSegments.length > 0 && candidate.length > maxChars) {
        flushChunk();
      }
      chunkSegments.push(seg);
      chunkText = chunkText ? `${chunkText} ${segText}` : segText;
    }
    flushChunk();
  }

  return groups;
}
