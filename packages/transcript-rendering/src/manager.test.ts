import { describe, it, expect } from 'vitest';
import type { TranscriptSegment } from './types';
import type { TranscriptMessage } from './manager';
import { createTranscriptManager } from './manager';

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

describe('createTranscriptManager', () => {
  it('bootstrap returns deduplicated sorted segments', () => {
    const manager = createTranscriptManager();
    const result = manager.bootstrap([
      seg('Bob', 5, 10, 'second', { segment_id: 'seg-2' }),
      seg('Alice', 0, 5, 'first', { segment_id: 'seg-1' }),
    ]);
    expect(result).toHaveLength(2);
    expect(result[0].text).toBe('first');
    expect(result[1].text).toBe('second');
  });

  it('handleMessage processes "transcript" format', () => {
    const manager = createTranscriptManager();
    manager.bootstrap([]);

    const msg: TranscriptMessage = {
      type: 'transcript',
      speaker: 'Alice',
      confirmed: [seg('Alice', 0, 5, 'hello', { segment_id: 'a:0' })],
      pending: [seg('Alice', 5, 8, 'world draft', { completed: false })],
    };

    const result = manager.handleMessage(msg);
    expect(result).not.toBeNull();
    expect(result!.length).toBeGreaterThanOrEqual(1);
    expect(result!.find(s => s.text === 'hello')).toBeTruthy();
  });

  it('handleMessage returns null for non-transcript messages', () => {
    const manager = createTranscriptManager();
    expect(manager.handleMessage({ type: 'transcript' })).toBeNull();
  });

  it('handleMessage returns null when nothing changed', () => {
    const manager = createTranscriptManager();
    manager.bootstrap([]);
    const msg: TranscriptMessage = {
      type: 'transcript',
      confirmed: [],
    };
    // No speaker provided, no confirmed → nothing changed
    expect(manager.handleMessage(msg)).toBeNull();
  });

  it('multi-tick lifecycle: bootstrap → confirmed → pending → confirmed', () => {
    const manager = createTranscriptManager();

    // Bootstrap from REST
    manager.bootstrap([
      seg('Alice', 0, 5, 'existing', { segment_id: 'a:0' }),
    ]);

    // Tick 1: Bob pending
    const r1 = manager.handleMessage({
      type: 'transcript',
      speaker: 'Bob',
      confirmed: [],
      pending: [seg('Bob', 5, 8, 'bob typing...', { completed: false })],
    });
    expect(r1).not.toBeNull();
    expect(r1!).toHaveLength(2); // existing + bob pending

    // Tick 2: Bob confirmed
    const r2 = manager.handleMessage({
      type: 'transcript',
      speaker: 'Bob',
      confirmed: [seg('Bob', 5, 10, 'bob said this', { segment_id: 'b:0' })],
      pending: [],
    });
    expect(r2).not.toBeNull();
    // bob pending ("bob typing...") should be filtered as stale
    expect(r2!).toHaveLength(2); // existing + bob confirmed

    // Verify no duplicates
    const texts = r2!.map(s => s.text);
    expect(texts).toContain('existing');
    expect(texts).toContain('bob said this');
  });

  it('getSegments returns current state without a new message', () => {
    const manager = createTranscriptManager();
    manager.bootstrap([
      seg('Alice', 0, 5, 'hello', { segment_id: 'a:0' }),
    ]);
    const segs = manager.getSegments();
    expect(segs).toHaveLength(1);
    expect(segs[0].text).toBe('hello');
  });

  it('clear resets all state', () => {
    const manager = createTranscriptManager();
    manager.bootstrap([seg('Alice', 0, 5, 'hello', { segment_id: 'a:0' })]);
    expect(manager.getSegments()).toHaveLength(1);

    manager.clear();
    expect(manager.getSegments()).toHaveLength(0);
  });
});
