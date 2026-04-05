import { describe, it, expect } from 'vitest';
import { deduplicateSegments, upsertSegments, sortSegments } from './dedup';
import type { TranscriptSegment } from './types';

function seg(
  speaker: string,
  startSec: number,
  endSec: number,
  text: string,
  opts: Partial<TranscriptSegment> = {},
): TranscriptSegment {
  const base = new Date('2026-03-21T12:00:00Z');
  return {
    text,
    speaker,
    absolute_start_time: new Date(base.getTime() + startSec * 1000).toISOString(),
    absolute_end_time: new Date(base.getTime() + endSec * 1000).toISOString(),
    completed: true,
    start_time: startSec,
    end_time: endSec,
    ...opts,
  };
}

describe('deduplicateSegments', () => {
  it('passes through non-overlapping segments', () => {
    const input = sortSegments([
      seg('Alice', 0, 5, 'hello'),
      seg('Alice', 6, 10, 'world'),
    ]);
    expect(deduplicateSegments(input)).toHaveLength(2);
  });

  it('NEVER dedupes different speakers even when timestamps overlap', () => {
    const input = sortSegments([
      seg('Alice', 0, 10, 'I was saying something'),
      seg('Bob', 2, 8, 'Me too'),
    ]);
    const result = deduplicateSegments(input);
    expect(result).toHaveLength(2);
    expect(result[0].speaker).toBe('Alice');
    expect(result[1].speaker).toBe('Bob');
  });

  it('dedupes same-speaker same-text overlap', () => {
    const input = sortSegments([
      seg('Alice', 0, 10, 'hello world'),
      seg('Alice', 1, 9, 'hello world'),
    ]);
    const result = deduplicateSegments(input);
    expect(result).toHaveLength(1);
    // Prefers the longer/outer segment
    expect(result[0].start_time).toBe(0);
  });

  it('dedupes same-speaker containment (different text)', () => {
    const input = sortSegments([
      seg('Alice', 0, 20, 'This is a long segment with many words'),
      seg('Alice', 5, 10, 'short'),
    ]);
    const result = deduplicateSegments(input);
    expect(result).toHaveLength(1);
    expect(result[0].text).toContain('long segment');
  });

  it('expansion: replaces partial with full text', () => {
    const input = sortSegments([
      seg('Alice', 0, 5, 'It was a milestone.', { completed: false }),
      seg('Alice', 0, 10, 'It was a milestone to get the project done.', { completed: true }),
    ]);
    const result = deduplicateSegments(input);
    expect(result).toHaveLength(1);
    expect(result[0].text).toContain('project done');
  });

  it('tail-repeat: drops tiny echo already in previous', () => {
    const input = sortSegments([
      seg('Alice', 0, 10, 'The quick brown fox jumps over the lazy dog'),
      seg('Alice', 9, 10.5, 'dog'),
    ]);
    const result = deduplicateSegments(input);
    expect(result).toHaveLength(1);
  });

  it('adjacent duplicate: same text within 1s gap', () => {
    const input = sortSegments([
      seg('Alice', 0, 5, 'hello', { completed: false }),
      seg('Alice', 5.5, 10, 'hello', { completed: true }),
    ]);
    const result = deduplicateSegments(input);
    expect(result).toHaveLength(1);
    expect(result[0].completed).toBe(true);
  });

  it('preserves all 7 speakers from panel-20 dataset', () => {
    // Simulates the 43-segment panel-20 core output with 7 speakers
    const speakers = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];
    const input: TranscriptSegment[] = [];
    let t = 0;
    for (let i = 0; i < 43; i++) {
      const sp = speakers[i % speakers.length];
      const dur = 2 + Math.random() * 8;
      input.push(seg(sp, t, t + dur, `Utterance ${i} from speaker ${sp}`));
      t += dur + 0.5; // small gap
    }
    const result = deduplicateSegments(sortSegments(input));
    expect(result).toHaveLength(43);
  });

  it('handles cross-speaker overlapping timestamps correctly', () => {
    // Real scenario: speakers talking over each other
    const input = sortSegments([
      seg('Speaker A', 120.0, 124.0, 'Don\'t call it out'),
      seg('Speaker A', 120.2, 124.0, 'from me? Oh, okay.'),
      seg('Speaker B', 120.5, 125.0, 'I was saying something else'),
    ]);
    const result = deduplicateSegments(input);
    // Speaker B must survive (different speaker)
    expect(result.find(s => s.speaker === 'Speaker B')).toBeTruthy();
  });
});

describe('upsertSegments', () => {
  it('inserts new segments', () => {
    const map = new Map<string, TranscriptSegment>();
    upsertSegments(map, [
      seg('Alice', 0, 5, 'hello', { segment_id: 'seg-1' }),
    ]);
    expect(map.size).toBe(1);
  });

  it('updates text changes', () => {
    const map = new Map<string, TranscriptSegment>();
    map.set('seg-1', seg('Alice', 0, 5, 'hell', { segment_id: 'seg-1', completed: false }));

    upsertSegments(map, [
      seg('Alice', 0, 5, 'hello world', { segment_id: 'seg-1', completed: true }),
    ]);
    expect(map.get('seg-1')!.text).toBe('hello world');
    expect(map.get('seg-1')!.completed).toBe(true);
  });

  it('dedupes same-speaker same-text with different IDs (draft→confirmed)', () => {
    const map = new Map<string, TranscriptSegment>();
    map.set('draft-1', seg('Alice', 0, 5, 'hello', { segment_id: 'draft-1', completed: false }));
    map.set('confirmed-1', seg('Alice', 0, 5, 'hello', { segment_id: 'confirmed-1', completed: true }));

    upsertSegments(map, []);
    // After dedup pass, draft should be gone
    const values = Array.from(map.values());
    const aliceHellos = values.filter(s => s.text === 'hello' && s.speaker === 'Alice');
    expect(aliceHellos).toHaveLength(1);
    expect(aliceHellos[0].completed).toBe(true);
  });

  it('skips segments without absolute_start_time', () => {
    const map = new Map<string, TranscriptSegment>();
    upsertSegments(map, [
      { text: 'no timestamp', speaker: 'Alice', absolute_start_time: '', absolute_end_time: '' },
    ]);
    expect(map.size).toBe(0);
  });
});
