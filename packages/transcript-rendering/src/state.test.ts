import { describe, it, expect } from 'vitest';
import type { TranscriptSegment, TranscriptState } from './types';
import {
  createTranscriptState,
  bootstrapConfirmed,
  applyTranscriptTick,
  recomputeTranscripts,
  addSegment,
  bootstrapSegments,
} from './state';

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

describe('createTranscriptState', () => {
  it('returns empty maps', () => {
    const state = createTranscriptState();
    expect(state.confirmed.size).toBe(0);
    expect(state.pendingBySpeaker.size).toBe(0);
  });
});

describe('bootstrapConfirmed', () => {
  it('populates confirmed from segments', () => {
    const state = createTranscriptState();
    const result = bootstrapConfirmed(state, [
      seg('Alice', 0, 5, 'hello', { segment_id: 'seg-1' }),
      seg('Bob', 5, 10, 'world', { segment_id: 'seg-2' }),
    ]);
    expect(state.confirmed.size).toBe(2);
    expect(result).toHaveLength(2);
  });

  it('clears previous state', () => {
    const state = createTranscriptState();
    state.confirmed.set('old', seg('Alice', 0, 5, 'old'));
    state.pendingBySpeaker.set('Alice', [seg('Alice', 10, 15, 'pending')]);

    bootstrapConfirmed(state, [
      seg('Alice', 0, 5, 'new', { segment_id: 'seg-1' }),
    ]);
    expect(state.confirmed.size).toBe(1);
    expect(state.confirmed.get('seg-1')!.text).toBe('new');
    expect(state.pendingBySpeaker.size).toBe(0);
  });

  it('filters out segments without absolute_start_time or empty text', () => {
    const state = createTranscriptState();
    const result = bootstrapConfirmed(state, [
      seg('Alice', 0, 5, 'valid', { segment_id: 'seg-1' }),
      { text: '', speaker: 'Alice', absolute_start_time: '2026-03-21T12:00:06Z', absolute_end_time: '2026-03-21T12:00:10Z' },
      { text: 'no timestamp', speaker: 'Bob', absolute_start_time: '', absolute_end_time: '' },
      { text: '   ', speaker: 'Carol', absolute_start_time: '2026-03-21T12:00:11Z', absolute_end_time: '2026-03-21T12:00:15Z' },
    ]);
    expect(state.confirmed.size).toBe(1);
    expect(result).toHaveLength(1);
  });

  it('deduplicates by segment_id (last wins)', () => {
    const state = createTranscriptState();
    bootstrapConfirmed(state, [
      seg('Alice', 0, 5, 'version 1', { segment_id: 'seg-1' }),
      seg('Alice', 0, 5, 'version 2', { segment_id: 'seg-1' }),
    ]);
    expect(state.confirmed.size).toBe(1);
    expect(state.confirmed.get('seg-1')!.text).toBe('version 2');
  });

  it('returns segments sorted by absolute_start_time', () => {
    const state = createTranscriptState();
    const result = bootstrapConfirmed(state, [
      seg('Bob', 10, 15, 'second', { segment_id: 'seg-2' }),
      seg('Alice', 0, 5, 'first', { segment_id: 'seg-1' }),
    ]);
    expect(result[0].text).toBe('first');
    expect(result[1].text).toBe('second');
  });
});

describe('applyTranscriptTick', () => {
  it('appends confirmed segments', () => {
    const state = createTranscriptState();
    bootstrapConfirmed(state, [
      seg('Alice', 0, 5, 'existing', { segment_id: 'seg-1' }),
    ]);

    const result = applyTranscriptTick(state, [
      seg('Bob', 5, 10, 'new confirmed', { segment_id: 'seg-2' }),
    ]);
    expect(result).not.toBeNull();
    expect(result).toHaveLength(2);
    expect(state.confirmed.size).toBe(2);
  });

  it('replaces pending for the given speaker only', () => {
    const state = createTranscriptState();
    state.pendingBySpeaker.set('Alice', [seg('Alice', 10, 15, 'alice pending')]);
    state.pendingBySpeaker.set('Bob', [seg('Bob', 10, 15, 'bob pending')]);

    applyTranscriptTick(
      state,
      [], // no confirmed
      [seg('Alice', 20, 25, 'alice new pending')],
      'Alice',
    );

    expect(state.pendingBySpeaker.get('Alice')).toHaveLength(1);
    expect(state.pendingBySpeaker.get('Alice')![0].text).toBe('alice new pending');
    // Bob's pending is untouched
    expect(state.pendingBySpeaker.get('Bob')).toHaveLength(1);
    expect(state.pendingBySpeaker.get('Bob')![0].text).toBe('bob pending');
  });

  it('deletes pending for speaker when pending is empty', () => {
    const state = createTranscriptState();
    state.pendingBySpeaker.set('Alice', [seg('Alice', 10, 15, 'old pending')]);

    applyTranscriptTick(state, [], [], 'Alice');
    expect(state.pendingBySpeaker.has('Alice')).toBe(false);
  });

  it('returns null when nothing changed', () => {
    const state = createTranscriptState();
    const result = applyTranscriptTick(state, []);
    expect(result).toBeNull();
  });

  it('filters invalid segments from confirmed and pending', () => {
    const state = createTranscriptState();
    const result = applyTranscriptTick(
      state,
      [{ text: '', speaker: 'Alice', absolute_start_time: '2026-03-21T12:00:00Z', absolute_end_time: '2026-03-21T12:00:05Z' }],
      [{ text: '  ', speaker: 'Alice', absolute_start_time: '2026-03-21T12:00:10Z', absolute_end_time: '2026-03-21T12:00:15Z' }],
      'Alice',
    );
    // Speaker was provided so pending was touched (changed=true), but no valid segments
    expect(result).toEqual([]);
    expect(state.confirmed.size).toBe(0);
    expect(state.pendingBySpeaker.has('Alice')).toBe(false);
  });
});

describe('recomputeTranscripts', () => {
  it('includes confirmed and non-stale pending', () => {
    const state = createTranscriptState();
    state.confirmed.set('seg-1', seg('Alice', 0, 5, 'confirmed text', { segment_id: 'seg-1' }));
    state.pendingBySpeaker.set('Alice', [
      seg('Alice', 10, 15, 'unique pending text'),
    ]);

    const result = recomputeTranscripts(state);
    expect(result).toHaveLength(2);
  });

  it('filters stale pending — exact match', () => {
    const state = createTranscriptState();
    state.confirmed.set('seg-1', seg('Alice', 0, 5, 'hello world', { segment_id: 'seg-1' }));
    state.pendingBySpeaker.set('Alice', [
      seg('Alice', 0, 5, 'hello world'),
    ]);

    const result = recomputeTranscripts(state);
    expect(result).toHaveLength(1); // only confirmed
    expect(result[0].segment_id).toBe('seg-1');
  });

  it('filters stale pending — pending is prefix of confirmed', () => {
    const state = createTranscriptState();
    state.confirmed.set('seg-1', seg('Alice', 0, 10, 'hello world how are you', { segment_id: 'seg-1' }));
    state.pendingBySpeaker.set('Alice', [
      seg('Alice', 0, 5, 'hello world'),
    ]);

    const result = recomputeTranscripts(state);
    expect(result).toHaveLength(1); // pending filtered as stale
  });

  it('filters stale pending — confirmed is prefix of pending', () => {
    const state = createTranscriptState();
    state.confirmed.set('seg-1', seg('Alice', 0, 5, 'hello', { segment_id: 'seg-1' }));
    state.pendingBySpeaker.set('Alice', [
      seg('Alice', 0, 10, 'hello world'),
    ]);

    const result = recomputeTranscripts(state);
    expect(result).toHaveLength(1); // pending is expansion of confirmed — still stale
  });

  it('does not filter pending from different speaker', () => {
    const state = createTranscriptState();
    state.confirmed.set('seg-1', seg('Alice', 0, 5, 'hello world', { segment_id: 'seg-1' }));
    state.pendingBySpeaker.set('Bob', [
      seg('Bob', 0, 5, 'hello world'), // same text but different speaker
    ]);

    const result = recomputeTranscripts(state);
    expect(result).toHaveLength(2);
  });

  it('sorts by absolute_start_time', () => {
    const state = createTranscriptState();
    state.confirmed.set('seg-2', seg('Bob', 10, 15, 'second', { segment_id: 'seg-2' }));
    state.confirmed.set('seg-1', seg('Alice', 0, 5, 'first', { segment_id: 'seg-1' }));
    state.pendingBySpeaker.set('Carol', [
      seg('Carol', 5, 8, 'middle'),
    ]);

    const result = recomputeTranscripts(state);
    expect(result[0].text).toBe('first');
    expect(result[1].text).toBe('middle');
    expect(result[2].text).toBe('second');
  });
});

describe('addSegment', () => {
  it('appends new segment', () => {
    const segments = [seg('Alice', 0, 5, 'hello', { segment_id: 'seg-1' })];
    const result = addSegment(segments, seg('Bob', 5, 10, 'world', { segment_id: 'seg-2' }));
    expect(result).toHaveLength(2);
  });

  it('updates existing segment by segment_id', () => {
    const segments = [seg('Alice', 0, 5, 'hello', { segment_id: 'seg-1' })];
    const result = addSegment(segments, seg('Alice', 0, 5, 'hello updated', { segment_id: 'seg-1' }));
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('hello updated');
  });

  it('updates existing segment by absolute_start_time fallback', () => {
    const s = seg('Alice', 0, 5, 'hello');
    const segments = [s];
    const updated = { ...s, text: 'hello updated' };
    const result = addSegment(segments, updated);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('hello updated');
  });

  it('removes same-speaker overlapping drafts when confirmed arrives', () => {
    const draft = seg('Alice', 2, 7, 'draft text', { segment_id: 'draft-1', completed: false });
    const otherDraft = seg('Alice', 20, 25, 'other draft', { segment_id: 'draft-2', completed: false });
    const bobDraft = seg('Bob', 3, 6, 'bob draft', { segment_id: 'bob-draft', completed: false });
    const confirmed = seg('Alice', 0, 8, 'confirmed text', { segment_id: 'seg-1', completed: true });
    const segments = [draft, otherDraft, bobDraft];

    const result = addSegment(segments, confirmed);

    // draft (2-7) overlaps with confirmed (0-8) → removed
    // otherDraft (20-25) does NOT overlap → kept
    // bobDraft is different speaker → kept
    // confirmed itself → kept
    expect(result).toHaveLength(3);
    expect(result.find(s => s.segment_id === 'draft-1')).toBeUndefined();
    expect(result.find(s => s.segment_id === 'draft-2')).toBeDefined();
    expect(result.find(s => s.segment_id === 'bob-draft')).toBeDefined();
    expect(result.find(s => s.segment_id === 'seg-1')).toBeDefined();
  });

  it('does not remove drafts when adding another draft', () => {
    const existing = seg('Alice', 0, 5, 'draft 1', { segment_id: 'draft-1', completed: false });
    const newDraft = seg('Alice', 3, 8, 'draft 2', { segment_id: 'draft-2', completed: false });
    const result = addSegment([existing], newDraft);
    expect(result).toHaveLength(2);
  });

  it('does not mutate the input array', () => {
    const segments = [seg('Alice', 0, 5, 'hello', { segment_id: 'seg-1' })];
    const result = addSegment(segments, seg('Bob', 5, 10, 'world', { segment_id: 'seg-2' }));
    expect(segments).toHaveLength(1); // original unchanged
    expect(result).toHaveLength(2);
  });
});

describe('bootstrapSegments', () => {
  it('filters out invalid segments', () => {
    const result = bootstrapSegments([
      seg('Alice', 0, 5, 'valid', { segment_id: 'seg-1' }),
      { text: '', speaker: 'Bob', absolute_start_time: '2026-03-21T12:00:06Z', absolute_end_time: '2026-03-21T12:00:10Z' },
      { text: 'no time', speaker: 'Carol', absolute_start_time: '', absolute_end_time: '' },
    ]);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('valid');
  });

  it('deduplicates by segment_id (last wins)', () => {
    const result = bootstrapSegments([
      seg('Alice', 0, 5, 'v1', { segment_id: 'seg-1' }),
      seg('Alice', 0, 5, 'v2', { segment_id: 'seg-1' }),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('v2');
  });

  it('deduplicates by absolute_start_time when no segment_id', () => {
    const s1 = seg('Alice', 0, 5, 'v1');
    const s2 = seg('Alice', 0, 5, 'v2');
    const result = bootstrapSegments([s1, s2]);
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('v2');
  });

  it('returns empty for empty input', () => {
    expect(bootstrapSegments([])).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Integration: realistic confirmed/pending lifecycle
// ---------------------------------------------------------------------------

describe('confirmed/pending lifecycle (integration)', () => {
  it('bootstrap → multiple WS ticks → no duplicates', () => {
    const state = createTranscriptState();

    // 1. Bootstrap from REST (3 confirmed segments from 2 speakers)
    const initial = bootstrapConfirmed(state, [
      seg('Alice', 0, 5, 'Hello everyone', { segment_id: 'a:0' }),
      seg('Bob', 5, 10, 'Hi Alice', { segment_id: 'b:0' }),
      seg('Alice', 10, 15, 'Let us begin', { segment_id: 'a:1' }),
    ]);
    expect(initial).toHaveLength(3);

    // 2. WS tick: Alice confirmed + Bob pending
    const tick1 = applyTranscriptTick(
      state,
      [seg('Alice', 15, 20, 'First topic is performance', { segment_id: 'a:2' })],
      [seg('Bob', 20, 23, 'I agree with', { segment_id: 'b:draft:1', completed: false })],
      'Bob',
    )!;
    expect(tick1).toHaveLength(5); // 3 initial + 1 alice confirmed + 1 bob pending

    // 3. WS tick: Bob's draft becomes confirmed, pending cleared
    const tick2 = applyTranscriptTick(
      state,
      [seg('Bob', 20, 25, 'I agree with that assessment', { segment_id: 'b:1' })],
      [], // empty pending → clears Bob's pending
      'Bob',
    )!;
    expect(tick2).toHaveLength(5); // 3 initial + 1 alice + 1 bob confirmed (pending filtered as stale)

    // Verify no duplicates — all unique segment_ids
    const ids = tick2.map(s => s.segment_id || s.absolute_start_time);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('multi-speaker concurrent ticks preserve all speakers', () => {
    const state = createTranscriptState();
    bootstrapConfirmed(state, []);

    // Simulate 5 speakers sending interleaved ticks
    const speakers = ['Alice', 'Bob', 'Carol', 'Dave', 'Eve'];
    for (let i = 0; i < 20; i++) {
      const sp = speakers[i % speakers.length];
      const start = i * 3;
      applyTranscriptTick(
        state,
        [seg(sp, start, start + 2.5, `Utterance ${i} from ${sp}`, { segment_id: `${sp}:${i}` })],
        [seg(sp, start + 3, start + 5, `${sp} is still speaking...`, { completed: false })],
        sp,
      );
    }

    const result = recomputeTranscripts(state);
    // 20 confirmed segments + up to 5 pending (one per speaker)
    // Pending may be filtered as stale if text overlaps with confirmed
    expect(result.length).toBeGreaterThanOrEqual(20);
    expect(result.length).toBeLessThanOrEqual(25);

    // All 5 speakers present
    const speakersInResult = new Set(result.map(s => s.speaker));
    expect(speakersInResult.size).toBe(5);
  });

  it('stale pending is filtered even when text is prefix of confirmed', () => {
    const state = createTranscriptState();
    bootstrapConfirmed(state, []);

    // Tick 1: Alice says something (pending draft)
    applyTranscriptTick(
      state,
      [],
      [seg('Alice', 0, 3, 'The project', { completed: false })],
      'Alice',
    );
    let result = recomputeTranscripts(state);
    expect(result).toHaveLength(1); // only the pending

    // Tick 2: Alice's confirmed arrives (expanded text), pending not yet cleared
    applyTranscriptTick(
      state,
      [seg('Alice', 0, 8, 'The project is on track', { segment_id: 'a:0' })],
      [seg('Alice', 0, 3, 'The project', { completed: false })], // stale pending
      'Alice',
    );
    result = recomputeTranscripts(state);
    // "The project" starts with prefix of "The project is on track" → stale
    expect(result).toHaveLength(1);
    expect(result[0].text).toBe('The project is on track');
  });
});
