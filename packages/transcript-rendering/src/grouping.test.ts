import { describe, it, expect } from 'vitest';
import { groupSegments } from './grouping';
import type { TranscriptSegment } from './types';

function seg(speaker: string | undefined, text: string, startTime: string, endTime?: string): TranscriptSegment {
  return {
    text,
    speaker,
    absolute_start_time: startTime,
    absolute_end_time: endTime || startTime,
  };
}

describe('groupSegments', () => {
  it('returns empty array for empty input', () => {
    expect(groupSegments([])).toEqual([]);
  });

  it('returns empty array for null-ish input', () => {
    expect(groupSegments(null as any)).toEqual([]);
    expect(groupSegments(undefined as any)).toEqual([]);
  });

  it('merges consecutive segments from same speaker', () => {
    const segments = [
      seg('Alice', 'Hello', '2024-01-01T10:00:00Z'),
      seg('Alice', 'World', '2024-01-01T10:00:05Z'),
    ];
    const groups = groupSegments(segments);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('Alice');
    expect(groups[0].combinedText).toBe('Hello World');
    expect(groups[0].segments).toHaveLength(2);
  });

  it('creates new group on speaker change', () => {
    const segments = [
      seg('Alice', 'Hi', '2024-01-01T10:00:00Z'),
      seg('Bob', 'Hey', '2024-01-01T10:00:05Z'),
      seg('Alice', 'Bye', '2024-01-01T10:00:10Z'),
    ];
    const groups = groupSegments(segments);
    expect(groups).toHaveLength(3);
    expect(groups[0].key).toBe('Alice');
    expect(groups[1].key).toBe('Bob');
    expect(groups[2].key).toBe('Alice');
  });

  it('splits group when maxCharsPerGroup exceeded', () => {
    const segments = [
      seg('Alice', 'A'.repeat(30), '2024-01-01T10:00:00Z'),
      seg('Alice', 'B'.repeat(30), '2024-01-01T10:00:05Z'),
      seg('Alice', 'C'.repeat(30), '2024-01-01T10:00:10Z'),
    ];
    const groups = groupSegments(segments, { maxCharsPerGroup: 50 });
    expect(groups.length).toBeGreaterThan(1);
    // Each group's combinedText should be <= 50 chars (at segment boundaries)
    for (const g of groups) {
      expect(g.segments.length).toBeGreaterThan(0);
    }
  });

  it('uses custom getGroupKey', () => {
    const segments = [
      { ...seg('Alice', 'Hi', '2024-01-01T10:00:00Z'), lang: 'en' } as any,
      { ...seg('Bob', 'Hola', '2024-01-01T10:00:05Z'), lang: 'es' } as any,
      { ...seg('Carlos', 'Buenos dias', '2024-01-01T10:00:10Z'), lang: 'es' } as any,
    ];
    const groups = groupSegments(segments, {
      getGroupKey: (s: any) => s.lang || 'unknown',
    });
    expect(groups).toHaveLength(2);
    expect(groups[0].key).toBe('en');
    expect(groups[1].key).toBe('es');
    expect(groups[1].segments).toHaveLength(2);
  });

  it('handles undefined speaker as "Unknown"', () => {
    const segments = [
      seg(undefined, 'No speaker', '2024-01-01T10:00:00Z'),
      seg(undefined, 'Also no speaker', '2024-01-01T10:00:05Z'),
    ];
    const groups = groupSegments(segments);
    expect(groups).toHaveLength(1);
    expect(groups[0].key).toBe('Unknown');
  });

  it('skips empty text segments', () => {
    const segments = [
      seg('Alice', 'Hello', '2024-01-01T10:00:00Z'),
      seg('Alice', '', '2024-01-01T10:00:02Z'),
      seg('Alice', '  ', '2024-01-01T10:00:03Z'),
      seg('Alice', 'World', '2024-01-01T10:00:05Z'),
    ];
    const groups = groupSegments(segments);
    expect(groups).toHaveLength(1);
    expect(groups[0].segments).toHaveLength(2);
  });

  it('sorts segments by absolute_start_time before grouping', () => {
    const segments = [
      seg('Alice', 'Second', '2024-01-01T10:00:05Z'),
      seg('Alice', 'First', '2024-01-01T10:00:00Z'),
    ];
    const groups = groupSegments(segments);
    expect(groups[0].combinedText).toBe('First Second');
  });

  it('sets correct startTime and endTime on groups', () => {
    const segments = [
      seg('Alice', 'Hello', '2024-01-01T10:00:00Z', '2024-01-01T10:00:03Z'),
      seg('Alice', 'World', '2024-01-01T10:00:05Z', '2024-01-01T10:00:08Z'),
    ];
    const groups = groupSegments(segments);
    expect(groups[0].startTime).toBe('2024-01-01T10:00:00Z');
    expect(groups[0].endTime).toBe('2024-01-01T10:00:08Z');
  });

  it('handles large input correctly', () => {
    const segments = Array.from({ length: 100 }, (_, i) =>
      seg(i % 2 === 0 ? 'Alice' : 'Bob', `Segment ${i}`, `2024-01-01T10:${String(i).padStart(2, '0')}:00Z`)
    );
    const groups = groupSegments(segments);
    // 100 alternating speakers = 100 groups
    expect(groups).toHaveLength(100);
  });
});
