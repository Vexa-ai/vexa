/**
 * Voice note — the bot as a buffer (#1783). No meeting, no browser: capture.v1 frames arrive on a
 * Redis stream meeting-api fills from the person's browser, and this source feeds them, in order,
 * into the same lane a meeting's audio takes. Offline: an in-memory stream stands in for Redis and a
 * recording engine stands in for the lane.
 *
 * Run: tsx src/voice-note.test.ts
 */
import { encodeAudioFrame } from '@vexa/capture-codec';
import {
  createVoiceNoteSource,
  createVoiceNoteJoinDriver,
  createVoiceNotePipeline,
  voiceNoteMaxActiveMs,
  type AudioStreamReader,
  type StreamEntry,
} from './voice-note.js';
import { createBotPipeline } from './pipeline.js';
import type { Invocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** An in-memory stream: entries are appended by the test; read() returns what follows `afterId`,
 *  waiting up to `blockMs` for something to arrive, like XREAD BLOCK. */
function memoryStream() {
  const entries: StreamEntry[] = [];
  let seq = 0;
  const waiters: Array<() => void> = [];
  const reads: string[] = [];
  const reader: AudioStreamReader = {
    async read(afterId, blockMs) {
      reads.push(afterId);
      const after = () => entries.filter((e) => cmp(e.id, afterId) > 0);
      if (after().length === 0 && blockMs > 0) {
        await new Promise<void>((res) => { const t = setTimeout(res, blockMs); waiters.push(() => { clearTimeout(t); res(); }); });
      }
      return after();
    },
  };
  const cmp = (a: string, b: string) => (b === '0-0' ? 1 : Number(a.split('-')[0]) - Number(b.split('-')[0]));
  return {
    reader,
    reads,
    frame(pcm: Float32Array, ts: number) { entries.push({ id: `${++seq}-0`, frame: new Uint8Array(encodeAudioFrame(0, ts, pcm)) }); waiters.splice(0).forEach((w) => w()); },
    raw(bytes: Uint8Array) { entries.push({ id: `${++seq}-0`, frame: bytes }); waiters.splice(0).forEach((w) => w()); },
    end() { entries.push({ id: `${++seq}-0`, end: true }); waiters.splice(0).forEach((w) => w()); },
    alive() { entries.push({ id: `${++seq}-0`, alive: true }); waiters.splice(0).forEach((w) => w()); },
  };
}

console.log('\n=== voice note: the stream feeds the lane, in order, then ends ===');
{
  const s = memoryStream();
  const fed: Array<{ n: number; ts: number; first: number }> = [];
  let ends = 0, idles = 0;
  const src = createVoiceNoteSource({
    reader: s.reader,
    feed: (pcm, ts) => fed.push({ n: pcm.length, ts, first: pcm[0] }),
    onEnd: () => { ends++; },
    onIdle: () => { idles++; },
    idleMs: 10_000,
    blockMs: 20,
  });
  src.start();
  s.frame(new Float32Array(1600).fill(0.25), 1_700_000_000_000);
  s.frame(new Float32Array(1600).fill(0.5), 1_700_000_000_100);
  await sleep(60);
  check('two frames fed', fed.length === 2, JSON.stringify(fed));
  check('frames keep their capture time (never restamped)', fed[0]?.ts === 1_700_000_000_000 && fed[1]?.ts === 1_700_000_000_100);
  check('PCM survives the wire', fed[0]?.n === 1600 && fed[1]?.first === 0.5);
  s.end();
  await sleep(60);
  check('the end of the note is reported once', ends === 1);
  s.frame(new Float32Array(10), 1_700_000_000_200);
  await sleep(60);
  check('nothing is read after the end', fed.length === 2);
  check('no idle verdict on a note that ended', idles === 0);
  await src.stop(100);
  check('stats count the seconds of audio', Math.abs(src.stats().audioSeconds - 0.2) < 1e-9, String(src.stats().audioSeconds));
}

console.log('\n=== voice note: a malformed entry is dropped, never thrown ===');
{
  const s = memoryStream();
  const fed: number[] = [];
  const src = createVoiceNoteSource({ reader: s.reader, feed: (pcm) => fed.push(pcm.length), onEnd: () => {}, onIdle: () => {}, idleMs: 10_000, blockMs: 20 });
  src.start();
  s.raw(new Uint8Array([1, 2, 3]));
  s.frame(new Float32Array(160), 1);
  await sleep(60);
  check('the short entry is dropped, the next frame still fed', fed.length === 1 && fed[0] === 160);
  check('the drop is counted', src.stats().dropped === 1);
  await src.stop(50);
}

console.log('\n=== voice note: a browser that went away ends the note (idle) ===');
{
  const s = memoryStream();
  let idles = 0;
  let t = 0;
  const src = createVoiceNoteSource({ reader: s.reader, feed: () => {}, onEnd: () => {}, onIdle: () => { idles++; }, idleMs: 1000, blockMs: 10, now: () => t });
  src.start();
  s.frame(new Float32Array(16), 1);
  await sleep(30);
  t = 500; await sleep(30);
  check('no idle verdict inside the window', idles === 0);
  t = 1600; await sleep(30);
  check('idle reported once past the window', idles === 1);
  t = 5000; await sleep(30);
  check('and only once', idles === 1);
  await src.stop(10);
}

console.log('\n=== voice note: a keepalive holds a paused note open ===');
{
  const s = memoryStream();
  let idles = 0, fed = 0;
  let t = 0;
  const src = createVoiceNoteSource({ reader: s.reader, feed: () => { fed++; }, onEnd: () => {}, onIdle: () => { idles++; }, idleMs: 1000, blockMs: 10, now: () => t });
  src.start();
  s.frame(new Float32Array(16), 1);
  await sleep(30);
  t = 900; s.alive(); await sleep(30);
  t = 1600; await sleep(30);
  check('a keepalive restarts the idle window', idles === 0);
  check('a keepalive is not audio', fed === 1 && src.stats().dropped === 0);
  t = 2000; await sleep(30);
  check('the window still closes after the last keepalive', idles === 1);
  await src.stop(10);
}

console.log('\n=== voice note: stop drains what is buffered, up to the end ===');
{
  const s = memoryStream();
  const fed: number[] = [];
  const src = createVoiceNoteSource({ reader: s.reader, feed: (pcm) => fed.push(pcm.length), onEnd: () => {}, onIdle: () => {}, idleMs: 10_000, blockMs: 20 });
  for (let i = 0; i < 5; i++) s.frame(new Float32Array(10), i);
  s.end();
  src.start();
  await src.stop(1000);
  check('every buffered frame reached the lane before stop returned', fed.length === 5);
}

console.log('\n=== voice note: the join is immediate, there is nobody to admit us ===');
{
  const driver = createVoiceNoteJoinDriver();
  const reported: string[] = [];
  const outcome = await driver.join((s) => { reported.push(s); });
  check('admitted at once', outcome === 'admitted');
  let removed = false;
  const off = driver.onRemoval(() => { removed = true; });
  off();
  await driver.leave('stopped');
  await driver.withdraw('stopped');
  check('leave and withdraw are no-ops', removed === false);
}

console.log('\n=== voice note: the pipeline starts the source and the lane, stops both in order ===');
{
  const order: string[] = [];
  const pipeline = createVoiceNotePipeline({
    source: { start: () => { order.push('source.start'); }, stop: async () => { order.push('source.stop'); } },
    engine: { start: async () => { order.push('engine.start'); }, stop: async () => { order.push('engine.stop'); } },
  });
  await pipeline.start();
  await pipeline.stop();
  check('lane starts before audio flows; audio drains before the lane flushes',
    order.join(',') === 'engine.start,source.start,source.stop,engine.stop', order.join(','));
}

console.log('\n=== voice note: the duration cap and the lane ===');
{
  const inv = {
    platform: 'voice_note', meetingUrl: null, botName: 'Vexa', redisUrl: 'redis://x',
    voiceNote: { audioStream: 'voice_note:meeting:1:audio', maxDurationSec: 600 },
  } as unknown as Invocation;
  check('the bot outlives the cap by a minute, no more', voiceNoteMaxActiveMs(inv) === 660_000);
  const published: TranscriptSegment[] = [];
  const bp = createBotPipeline(inv, { publish: async (s) => { published.push(s); } }, {
    transcribe: async () => ({ text: 'hello there', language: 'en', duration: 1, segments: [] }),
  });
  check('a voice note rides the per-channel lane (feedAudio is live)', typeof bp.feedAudio === 'function' && bp.hintCounters === undefined);
}

console.log(`\nvoice-note: ${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
