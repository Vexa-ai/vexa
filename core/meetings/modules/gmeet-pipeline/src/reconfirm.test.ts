/**
 * Show fast, then reconfirm (#1783): with reconfirmation on, every confirmed window goes out at once
 * as a DRAFT (completed:false); when the speaker pauses (or ~10 s accumulate) the group's audio is
 * transcribed again with context and ONE confirmed segment replaces the drafts — under the first
 * draft's id, the other draft ids withdrawn. Under STT back-pressure the reconfirmation waits; if it
 * cannot happen the drafts are confirmed as they are (never lost).
 *   npx tsx src/reconfirm.test.ts
 */
import { createGmeetPipeline } from './gmeet-pipeline.js';
import { setLogger } from './log.js';
import type { TranscriptSegment } from './contracts/transcript-v1.js';

setLogger(() => {});
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => { console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`); if (!cond) failed++; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const SR = 16000, FRAME = 1024;
const LOW = { pauseFinalMs: 500, idleTimeoutSec: 1, submitInterval: 1, maxBufferDuration: 10, confirmThreshold: 1, pipelineDrafts: true, minAudioDuration: 0.75 };
const FULL = 'AT&T Bell Labs and IBM Watson Labs.';
const FRAGS = ['AT&T Bell…', '…and IBM…', '…Watson…', 'Labs.'];

function speech(n: number): Float32Array { const a = new Float32Array(n); for (let i = 0; i < n; i++) a[i] = 0.1 * Math.sin(i * 0.07) * (1 + 0.3 * Math.sin(i * 0.003)); return a; }

async function run(opts: { pressure?: { backingOff(): boolean }; failReconfirm?: boolean }) {
  const state = new Map<string, { text: string; completed: boolean }>();
  let firstDraftAt: number | undefined; const t0 = Date.now();
  const sink = {
    segment(s: TranscriptSegment) { state.set(s.segment_id, { text: s.text, completed: true }); },
    draft(s: TranscriptSegment) {
      if (!s.text.trim()) { state.delete(s.segment_id); return; }
      if (firstDraftAt === undefined) firstDraftAt = Date.now() - t0;
      if (!state.get(s.segment_id)?.completed) state.set(s.segment_id, { text: s.text, completed: false });
    },
    finalize() {},
  };
  let calls = 0, longCalls = 0, frag = 0;
  const transcribe = async (pcm: Float32Array) => {
    calls++;
    await sleep(40);
    const sec = pcm.length / SR;
    if (sec >= 3) {                       // the reconfirmation: the whole group, with context
      longCalls++;
      if (opts.failReconfirm) throw new Error('stt refused');
      return { text: FULL, language: 'en', duration: sec, segments: [{ start: 0, end: sec, text: FULL }] } as never;
    }
    const t = FRAGS[Math.min(frag++, FRAGS.length - 1)];
    return { text: t, language: 'en', duration: sec, segments: [{ start: 0, end: sec, text: t }] } as never;
  };
  const lane = createGmeetPipeline({ transcribe, sink, config: LOW, reconfirmSec: 10, reconfirmIdleMs: 800, reconfirmHoldMs: 1500,
    sttPressure: opts.pressure as never, onsetGapMs: 5000 });
  // ~4 s of speech in 4 bursts with 0.6 s pauses, fed in real time.
  let ts = Date.now();
  for (let b = 0; b < 4; b++) {
    for (let f = 0; f < Math.round(0.9 * SR / FRAME); f++) { lane.feedAudio(0, 'Alice', speech(FRAME), ts); ts += FRAME / SR * 1000; await sleep(FRAME / SR * 1000); }
    for (let f = 0; f < Math.round(0.6 * SR / FRAME); f++) { lane.feedAudio(0, 'Alice', new Float32Array(FRAME), ts); ts += FRAME / SR * 1000; await sleep(FRAME / SR * 1000); }
  }
  await sleep(4000);                       // the pause: reconfirmation runs
  const before = new Map(state);
  await lane.dispose();
  return { state, before, firstDraftAt, calls, longCalls };
}

{
  const r = await run({});
  check('a draft is shown fast (≤ 2.5 s after speech starts)', r.firstDraftAt !== undefined && r.firstDraftAt <= 2500, String(r.firstDraftAt));
  const rows = [...r.before.values()];
  check('after the pause the drafts are replaced by ONE confirmed segment with the reconfirmed text',
    rows.length === 1 && rows[0].completed && rows[0].text === FULL, JSON.stringify(rows));
  check('no "…" draft fragment persists', ![...r.state.values()].some((s) => s.text.includes('…')), JSON.stringify([...r.state.values()]));
  check('the reconfirmation is one STT call over the group', r.longCalls === 1, `long=${r.longCalls} calls=${r.calls}`);
}
{
  let backing = true;
  const r = await run({ pressure: { backingOff: () => backing } });
  const rows = [...r.before.values()];
  check('under back-pressure the reconfirmation waits, then promotes the drafts (nothing lost, no extra request)',
    r.longCalls === 0 && rows.length >= 2 && rows.every((s) => s.completed) && rows.map((s) => s.text).join(' ').includes('Labs.'), JSON.stringify(rows));
  backing = false;
}
{
  const r = await run({ failReconfirm: true });
  const rows = [...r.state.values()];
  check('a failed reconfirmation confirms the drafts as they are (never dropped)',
    rows.length >= 2 && rows.every((s) => s.completed) && rows.some((s) => s.text === 'Labs.'), JSON.stringify(rows));
}
if (failed) { console.error(`\n❌ reconfirm: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('✅ reconfirm: drafts show fast, one reconfirmed segment replaces them, back-pressure and faults never lose words');
