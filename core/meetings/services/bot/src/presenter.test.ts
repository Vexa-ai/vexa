/**
 * The demo presenter (invocation.v1 `call.presenter`): the bundled script loads and matches its
 * tokens, every speaker joins with their own token and publishes a microphone track, the turns play
 * in order as 10 ms frames, everyone leaves at the end, and the presenter starts only once the
 * listener is in the room. Offline: a recording SDK stands in for @livekit/rtc-node.
 *
 * Run: tsx src/presenter.test.ts
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadScript, runPresenter, wavPcm, withPresenter, type Presenter, type PresenterSdk } from './presenter.js';
import type { CallRoom } from './call.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
};

const ASSETS = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'demo', 'demo-v1');
const read = (rel: string) => fs.readFileSync(path.join(ASSETS, rel));
const presenter: Presenter = { script: 'demo-v1', speakers: [{ name: 'Maya', token: 't-maya' }, { name: 'Leo', token: 't-leo' }] };

function wav(samples: number[], rate = 16000, channels = 1, dataSize?: number): Uint8Array {
  const data = Buffer.alloc(samples.length * 2);
  samples.forEach((v, i) => data.writeInt16LE(v, i * 2));
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + data.length, 4); h.write('WAVE', 8);
  h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(channels, 22);
  h.writeUInt32LE(rate, 24); h.writeUInt32LE(rate * 2 * channels, 28); h.writeUInt16LE(2 * channels, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(dataSize ?? data.length, 40);
  return new Uint8Array(Buffer.concat([h, data]));
}

function recordingSdk() {
  const events: string[] = [];
  const frames: Record<string, number> = {};
  let sourceSeq = 0;
  const sdk: PresenterSdk = {
    Room: class {
      token = '';
      async connect(_url: string, token: string) { this.token = token; events.push(`connect ${token}`); }
      async disconnect() { events.push(`leave ${this.token}`); }
      get localParticipant() { const token = this.token; return { async publishTrack() { events.push(`publish ${token}`); } }; }
    },
    AudioSource: class {
      id = `s${sourceSeq++}`;
      async captureFrame(f: unknown) { const n = (f as { n: number }).n; frames[this.id] = (frames[this.id] ?? 0) + n; }
    },
    AudioFrame: class { n: number; constructor(_d: Int16Array, _r: number, _c: number, n: number) { this.n = n; } },
    LocalAudioTrack: { createAudioTrack: () => ({}) },
    TrackPublishOptions: class { source?: unknown },
    TrackSource: { SOURCE_MICROPHONE: 2 },
  };
  return { sdk, events, frames };
}

// ── WAV reading ─────────────────────────────────────────────────────────────
{
  const { pcm, sampleRate } = wavPcm(wav([1, -2, 3]));
  check('a 16-bit mono WAV reads as its samples', sampleRate === 16000 && Array.from(pcm).join() === '1,-2,3');
  const streamed = wavPcm(wav([5, 6], 16000, 1, 0xffffffff));
  check('a streamed WAV (unknown data size) reads to the end', Array.from(streamed.pcm).join() === '5,6');
  let threw = false;
  try { wavPcm(wav([1, 2], 16000, 2)); } catch { threw = true; }
  check('a stereo WAV is refused', threw);
}

// ── the bundled script ──────────────────────────────────────────────────────
{
  const { script, turns } = loadScript(presenter, read);
  check('demo-v1 loads: two voices, alternating turns', script.speakers.join() === 'Maya,Leo' && turns.length >= 4
    && turns.every((t, i) => t.speaker === (i % 2 ? 'Leo' : 'Maya')), JSON.stringify(turns.map((t) => t.speaker)));
  const seconds = turns.reduce((s, t) => s + t.pcm.length, 0) / script.sampleRate;
  check('demo-v1 speaks for 40–70 s (a demo, not a meeting)', seconds >= 40 && seconds <= 70, `${seconds.toFixed(1)}s`);
  let threw = '';
  try { loadScript({ script: 'demo-v1', speakers: [{ name: 'Maya', token: 't' }] }, read); } catch (e) { threw = (e as Error).message; }
  check('a script voice without a token is refused', /no token for the script's speaker 'Leo'/.test(threw), threw);
  threw = '';
  try { loadScript({ ...presenter, script: 'other' }, read); } catch (e) { threw = (e as Error).message; }
  check('a script that is not the one asked for is refused', /names 'demo-v1'/.test(threw), threw);
}

// ── a run ───────────────────────────────────────────────────────────────────
await (async () => {
  const { sdk, events, frames } = recordingSdk();
  const script = { script: 'demo-v1', sampleRate: 16000, leadInMs: 0, gapMs: 0, speakers: ['Maya', 'Leo'], turns: [] };
  const turns = [
    { speaker: 'Maya', pcm: new Int16Array(1600) },
    { speaker: 'Leo', pcm: new Int16Array(3200) },
    { speaker: 'Maya', pcm: new Int16Array(800) },
  ];
  const run = runPresenter({ sdk, url: 'ws://livekit:7880', presenter, script, turns, sleep: async () => {} });
  const r = await run.done;
  check('every speaker joins with their own token and publishes a microphone', events.slice(0, 4).join('|') === 'connect t-maya|publish t-maya|connect t-leo|publish t-leo', events.join('|'));
  check('every turn plays, each through its speaker\'s source', r.turns === 3 && frames.s0 === 2400 && frames.s1 === 3200, JSON.stringify(frames));
  check('the seconds published are the script\'s', r.secondsPublished === 0.35, String(r.secondsPublished));
  check('everyone leaves when the script ends', events.filter((e) => e.startsWith('leave')).length === 2, events.join('|'));
})();

await (async () => {
  const { sdk, events } = recordingSdk();
  sdk.Room = class {
    async connect(_u: string, token: string) { if (token === 't-leo') throw new Error('token refused'); events.push(`connect ${token}`); }
    async disconnect() { events.push('leave'); }
    get localParticipant() { return { async publishTrack() {} }; }
  };
  const logs: string[] = [];
  const run = runPresenter({ sdk, url: 'ws://x', presenter, script: { script: 'demo-v1', sampleRate: 16000, speakers: [], turns: [] },
    turns: [{ speaker: 'Maya', pcm: new Int16Array(160) }], sleep: async () => {}, log: (m) => logs.push(m) });
  const r = await run.done;
  check('a refused token ends the run without throwing, and whoever joined leaves', r.turns === 0 && events.includes('leave')
    && logs.some((l) => /token refused/.test(l)), JSON.stringify({ r, events, logs }));
})();

// ── started only once the listener is in the room ───────────────────────────
await (async () => {
  const order: string[] = [];
  const room: CallRoom = {
    async connect() { order.push('listener in'); },
    async disconnect() { order.push('listener out'); },
  };
  const wrapped = withPresenter(room, () => {
    order.push('presenter starts');
    return { done: Promise.resolve({ turns: 0, secondsPublished: 0 }), async stop() { order.push('presenter stops'); } };
  });
  await wrapped.connect({} as never);
  await wrapped.disconnect();
  check('the presenter starts after the listener connects and stops before it leaves',
    order.join('|') === 'listener in|presenter starts|presenter stops|listener out', order.join('|'));
})();

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
