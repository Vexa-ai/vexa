/**
 * publish-stitch — the rules that make a merge safe, each with its own case.
 *
 * The dangerous failures of a stitcher are not "it merged too little". They are: it fused two
 * speakers; it swallowed a draft so a retraction became unrepresentable; it duplicated speech by
 * publishing an absorbed piece under its own id as well as inside its block; and it lost a
 * repaint because the rename named ids that no longer exist as rows. There is a case for each.
 */
import { stitchPublishBoundary } from './publish-stitch.js';
import type { ChunkSegment, ChunkedTranscriberCallbacks } from './chunked-transcriber.js';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  ok  ${name}`);
  else { failures++; console.error(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`); }
}
function eq(name: string, actual: unknown, expected: unknown): void {
  const a = JSON.stringify(actual), e = JSON.stringify(expected);
  check(name, a === e, `got ${a}, want ${e}`);
}

type Call =
  | { k: 'publish'; speaker: string; confirmed: ChunkSegment[]; pending: ChunkSegment[] }
  | { k: 'publishPending'; speaker: string; segments: ChunkSegment[] }
  | { k: 'clearPending'; speaker: string }
  | { k: 'rename'; from: string; to: string; segments: ChunkSegment[] };

function rig(): { cb: ChunkedTranscriberCallbacks; calls: Call[]; rows: () => Record<string, { speaker: string; text: string; start: number; end: number }> } {
  const calls: Call[] = [];
  const store = new Map<string, { speaker: string; text: string; start: number; end: number }>();
  const put = (speaker: string, segs: ChunkSegment[]): void => {
    for (const s of segs) store.set(s.segmentId, { speaker, text: s.text, start: s.startMs, end: s.endMs });
  };
  const base: ChunkedTranscriberCallbacks = {
    transcribe: async () => ({ text: '', language: 'en', duration: 0, segments: [] }) as never,
    publish: (speaker, confirmed, pending) => { calls.push({ k: 'publish', speaker, confirmed, pending }); put(speaker, confirmed); },
    publishPending: (speaker, segments) => { calls.push({ k: 'publishPending', speaker, segments }); },
    clearPending: (speaker) => { calls.push({ k: 'clearPending', speaker }); },
    rename: (from, to, segments) => { calls.push({ k: 'rename', from, to, segments }); put(to, segments); },
  };
  return { cb: stitchPublishBoundary(base, { enabled: true }), calls, rows: () => Object.fromEntries(store) };
}

const seg = (id: string, text: string, startMs: number, endMs: number, language = 'en'): ChunkSegment =>
  ({ segmentId: id, text, startMs, endMs, language });

console.log('publish-stitch');

// ── 1. The base case: one speaker, two consecutive finals, one row that GROWS. ──
{
  const { cb, calls, rows } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'It\'s a really good solid', 1000, 3000)], []);
  cb.publish('Dmitry', [seg('turn:2:0', 'base and it obviously will improve', 3200, 6000)], []);
  const r = rows();
  eq('one row survives', Object.keys(r), ['turn:1:0']);
  eq('the row holds the whole sentence', r['turn:1:0'].text,
     'It\'s a really good solid base and it obviously will improve');
  eq('the row\'s span covers both pieces', [r['turn:1:0'].start, r['turn:1:0'].end], [1000, 6000]);
  check('the absorbed piece was never published under its own id',
        !calls.some((c) => c.k === 'publish' && c.confirmed.some((s) => s.segmentId === 'turn:2:0')));
}

// ── 2. Latency is not traded away: the row exists after the FIRST piece. ──
{
  const { cb, calls } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'It\'s a really good solid', 1000, 3000)], []);
  check('the first piece publishes immediately', calls.length === 1 && calls[0].k === 'publish');
}

// ── 3. Two speakers are never fused. ──
{
  const { cb, rows } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'so what I mean is', 1000, 3000)], []);
  cb.publish('Daniel', [seg('turn:2:0', 'that it should match', 3100, 5000)], []);
  eq('two rows, one per speaker', Object.keys(rows()), ['turn:1:0', 'turn:2:0']);
}

// ── 4. "Speaker" is a refusal, not an identity — two unattributed spans stay apart. ──
{
  const { cb, rows } = rig();
  cb.publish('Speaker', [seg('turn:1:0', 'one', 1000, 3000)], []);
  cb.publish('Speaker', [seg('turn:2:0', 'two', 3100, 5000)], []);
  eq('unattributed spans are never merged', Object.keys(rows()).length, 2);
}

// ── 5. A stable transport track IS an identity, and merges. ──
{
  const { cb, rows } = rig();
  cb.publish('Speaker A', [seg('turn:1:0', 'one', 1000, 3000)], []);
  cb.publish('Speaker A', [seg('turn:2:0', 'two', 3100, 5000)], []);
  eq('one row for one track', Object.keys(rows()), ['turn:1:0']);
}

// ── 6. A real pause ends the block. ──
{
  const { cb, rows } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'first thought', 1000, 3000)], []);
  cb.publish('Dmitry', [seg('turn:2:0', 'unrelated second thought', 9000, 11000)], []);
  eq('a 6 s pause is a break', Object.keys(rows()).length, 2);
}

// ── 7. A finished sentence + a real pause ends the block; a finished sentence run
//      straight on does not. ──
{
  const { cb, rows } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'That is done.', 1000, 3000)], []);
  cb.publish('Dmitry', [seg('turn:2:0', 'Now the next thing.', 3900, 5000)], []);
  eq('sentence + pause breaks', Object.keys(rows()).length, 2);
}
{
  const { cb, rows } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'That is done.', 1000, 3000)], []);
  cb.publish('Dmitry', [seg('turn:2:0', 'Now the next thing.', 3100, 5000)], []);
  eq('sentence run straight on stays one block', Object.keys(rows()).length, 1);
}

// ── 8. A language change is never hidden inside a block. ──
{
  const { cb, rows } = rig();
  cb.publish('Dmitry', [seg('turn:1:0', 'привет как дела', 1000, 3000, 'ru')], []);
  cb.publish('Dmitry', [seg('turn:2:0', 'and then this', 3100, 5000, 'en')], []);
  eq('a language flip stays its own row', Object.keys(rows()).length, 2);
}

// ── 9. Drafts pass through untouched, and the confirmed+pending bundle stays atomic. ──
{
  const { cb, calls } = rig();
  const tail = [seg('turn:1:p0', 'and then', 3000, 3500)];
  cb.publish('Dmitry', [seg('turn:1:0', 'hello there', 1000, 3000)], tail);
  const p = calls.find((c) => c.k === 'publish') as Extract<Call, { k: 'publish' }>;
  eq('the pending tail rides with the confirmed row', p.pending, tail);
  cb.publishPending('Dmitry', tail);
  cb.clearPending('Dmitry');
  check('draft verbs are passed straight through',
        calls.some((c) => c.k === 'publishPending') && calls.some((c) => c.k === 'clearPending'));
}

// ── 10. A repaint of absorbed ids repaints the ROW, once, with the whole text. ──
{
  const { cb, calls, rows } = rig();
  const a = seg('turn:1:0', 'It\'s a really good solid', 1000, 3000);
  const b = seg('turn:2:0', 'base and it obviously will improve', 3200, 6000);
  cb.publish('seg_1', [a], []);
  cb.publish('seg_1', [b], []);
  cb.rename!('seg_1', 'Dmitry Grankin', [a, b]);
  const r = rows();
  eq('still one row', Object.keys(r), ['turn:1:0']);
  eq('renamed in place, whole text intact', [r['turn:1:0'].speaker, r['turn:1:0'].text],
     ['Dmitry Grankin', 'It\'s a really good solid base and it obviously will improve']);
  eq('exactly one rename reached the host',
     calls.filter((c) => c.k === 'rename').length, 1);
}

// ── 11. A repaint that claims only PART of a block splits it — and the part that stays
//        loses exactly the words that moved. ──
{
  const { cb, rows } = rig();
  const a = seg('turn:1:0', 'first piece', 1000, 3000);
  const b = seg('turn:2:0', 'second piece', 3100, 5000);
  cb.publish('seg_1', [a], []);
  cb.publish('seg_1', [b], []);
  cb.rename!('seg_1', 'Daniel', [b]);
  const r = rows();
  eq('the block split into two rows', Object.keys(r).sort(), ['turn:1:0', 'turn:2:0']);
  eq('the retained row shrank to its own words', r['turn:1:0'].text, 'first piece');
  eq('the claimed row carries the new name', [r['turn:2:0'].speaker, r['turn:2:0'].text],
     ['Daniel', 'second piece']);
}

// ── 12. The kill switch restores the raw boundary exactly. ──
{
  const calls: Call[] = [];
  const base: ChunkedTranscriberCallbacks = {
    transcribe: async () => ({ text: '', language: 'en', duration: 0, segments: [] }) as never,
    publish: (speaker, confirmed, pending) => calls.push({ k: 'publish', speaker, confirmed, pending }),
    publishPending: () => { /* unused */ },
    clearPending: () => { /* unused */ },
    rename: () => { /* unused */ },
  };
  const off = stitchPublishBoundary(base, { enabled: false });
  check('disabled returns the original callback object', off === base);
  off.publish('Dmitry', [seg('turn:1:0', 'a', 1000, 2000)], []);
  off.publish('Dmitry', [seg('turn:2:0', 'b', 2100, 3000)], []);
  eq('two publishes, two rows, nothing merged', calls.length, 2);
}

// ── 13. Caps hold: a monologue does not become one unreadable row. ──
{
  const { cb, rows } = rig();
  let t = 1000;
  for (let i = 0; i < 60; i++) {
    cb.publish('Dmitry', [seg(`turn:${i}:0`, `piece number ${i} of a long continuous monologue`, t, t + 1000)], []);
    t += 1100;
  }
  const n = Object.keys(rows()).length;
  check('the monologue is split by the caps, not left as one row', n > 1 && n < 60, `rows=${n}`);
  for (const [, v] of Object.entries(rows())) {
    check(`block within the time cap (${v.start}..${v.end})`, v.end - v.start <= 45_000);
    check('block within the char cap', v.text.length <= 700);
  }
}

if (failures) { console.error(`\n${failures} check(s) failed`); process.exit(1); }
console.log('\npublish-stitch: all checks passed');
