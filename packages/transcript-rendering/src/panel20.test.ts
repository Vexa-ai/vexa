import { describe, it, expect } from 'vitest';
import { deduplicateSegments, upsertSegments, sortSegments } from './dedup';
import type { TranscriptSegment } from './types';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

// Load actual core output from the delivery module.
// Fixture lives under features/realtime-transcription/data/ which is
// .gitignore'd (dev-only replay data); test skips when absent so CI +
// prepublishOnly don't fail in clean checkouts.
const CORE_OUTPUT_PATH = join(
  __dirname,
  '../../../features/realtime-transcription/data/core/teams-7sp-panel/segments.json',
);
const FIXTURE_PRESENT = existsSync(CORE_OUTPUT_PATH);

interface CoreSegment {
  speaker: string;
  start: number;
  end: number;
  text: string;
  completed: boolean;
}

function coreToTranscript(seg: CoreSegment, i: number): TranscriptSegment {
  const base = new Date('2026-03-21T12:00:00Z');
  return {
    text: seg.text,
    speaker: seg.speaker,
    absolute_start_time: new Date(base.getTime() + seg.start * 1000).toISOString(),
    absolute_end_time: new Date(base.getTime() + seg.end * 1000).toISOString(),
    completed: seg.completed,
    segment_id: `inject-${i}-${seg.start.toFixed(1)}`,
    start_time: seg.start,
    end_time: seg.end,
  };
}

describe.skipIf(!FIXTURE_PRESENT)('panel-20 core output (43 segments, 7 speakers)', () => {
  const raw: CoreSegment[] = FIXTURE_PRESENT
    ? JSON.parse(readFileSync(CORE_OUTPUT_PATH, 'utf8'))
    : [];
  const segments = raw.map(coreToTranscript);

  it('deduplicateSegments preserves all 43 segments', () => {
    const sorted = sortSegments(segments);
    const result = deduplicateSegments(sorted);
    expect(result).toHaveLength(43);
  });

  it('deduplicateSegments preserves all 7 speakers', () => {
    const sorted = sortSegments(segments);
    const result = deduplicateSegments(sorted);
    const speakers = new Set(result.map(s => s.speaker));
    expect(speakers.size).toBe(7);
  });

  it('upsertSegments preserves all 43 segments (bootstrap path)', () => {
    const map = new Map<string, TranscriptSegment>();
    upsertSegments(map, segments);
    expect(map.size).toBe(43);
  });

  it('upsertSegments preserves all 43 on double-upsert (idempotent)', () => {
    const map = new Map<string, TranscriptSegment>();
    upsertSegments(map, segments);
    upsertSegments(map, segments); // second pass should be no-op
    expect(map.size).toBe(43);
  });

  it('upsertSegments handles draft→confirmed transition', () => {
    const map = new Map<string, TranscriptSegment>();

    // First, insert as drafts
    const drafts = segments.map(s => ({ ...s, completed: false }));
    upsertSegments(map, drafts);
    expect(map.size).toBe(43);

    // Then, upsert as confirmed (same segment_ids)
    const confirmed = segments.map(s => ({ ...s, completed: true }));
    upsertSegments(map, confirmed);
    expect(map.size).toBe(43);

    // All should now be completed
    for (const seg of map.values()) {
      expect(seg.completed).toBe(true);
    }
  });
});
