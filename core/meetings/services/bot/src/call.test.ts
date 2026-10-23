/**
 * A Vexa call — the bot as a hidden listener (#1783). No browser: a LiveKit room reports each
 * participant's audio track, and the source feeds it into the per-channel lane, one channel per
 * participant, named by the participant. Offline: a scripted room stands in for LiveKit.
 *
 * Run: tsx src/call.test.ts
 */
import {
  LEAVE_GRACE_MS,
  createCallSource,
  createCallJoinDriver,
  createCallPipeline,
  callMaxActiveMs,
  toMono16k,
  SHARED_SCREEN_SUFFIX,
  type CallRoom,
  type CallRoomHandlers,
} from './call.js';
import { createBotPipeline } from './pipeline.js';
import type { Invocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
};

/** A room the test drives by hand; timers are manual too. */
function scriptedRoom(already: Array<{ identity: string; name?: string }> = []) {
  let h: CallRoomHandlers | null = null;
  let disconnected = 0;
  const room: CallRoom = {
    async connect(handlers) { h = handlers; for (const p of already) handlers.participantJoined(p); },
    async disconnect() { disconnected++; },
  };
  return { room, get h() { return h!; }, get disconnected() { return disconnected; } };
}
function manualTimers() {
  const pending: Array<{ fn: () => void; at: number; id: number }> = [];
  let t = 0, seq = 0;
  return {
    setTimer: (fn: () => void, ms: number) => { const id = ++seq; pending.push({ fn, at: t + ms, id }); return id; },
    clearTimer: (id: unknown) => { const i = pending.findIndex((p) => p.id === id); if (i >= 0) pending.splice(i, 1); },
    advance(ms: number) { t += ms; for (const p of pending.filter((p) => p.at <= t)) { pending.splice(pending.indexOf(p), 1); p.fn(); } },
    now: () => t,
  };
}
const tone = (n: number, rate = 48_000, v = 8000) => ({ data: new Int16Array(n).fill(v), sampleRate: rate, channels: 1 });

console.log('\n=== call: one channel per participant, named by the participant ===');
{
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }]);
  const tm = manualTimers();
  const fed: Array<{ ch: number; name: string; n: number }> = [];
  const src = createCallSource({ room: r.room, feed: (ch, name, pcm) => fed.push({ ch, name, n: pcm.length }),
    onEmpty: () => { /* not here */ }, emptyGraceMs: 5000, firstJoinTimeoutMs: 60_000, ...tm });
  await src.start();
  r.h.participantJoined({ identity: 'u:8', name: 'Grace' });
  r.h.audio({ identity: 'u:7', name: 'Ada' }, tone(480));
  r.h.audio({ identity: 'u:8', name: 'Grace' }, tone(480));
  r.h.audio({ identity: 'u:7', name: 'Ada' }, tone(480));
  check('Ada is channel 0 and Grace channel 1', fed[0].ch === 0 && fed[1].ch === 1 && fed[2].ch === 0,
    JSON.stringify(fed));
  check('frames carry the participant name', fed[0].name === 'Ada' && fed[1].name === 'Grace');
  check('48 kHz frames are resampled to 16 kHz', fed[0].n === 160, `n=${fed[0].n}`);
  const st = src.stats();
  check('per-speaker seconds are counted', Math.abs(st.speakers['u:7'].audioSeconds - 0.02) < 1e-9, JSON.stringify(st.speakers));
  r.h.participantLeft({ identity: 'u:7' });
  r.h.participantJoined({ identity: 'u:7', name: 'Ada' });
  r.h.audio({ identity: 'u:7', name: 'Ada' }, tone(480));
  check('a participant who rejoins keeps their channel', fed[3].ch === 0);
}

console.log('\n=== call: a shared screen with sound is its own channel, "<person> (shared screen)" ===');
{
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }]);
  const tm = manualTimers();
  const fed: Array<{ ch: number; name: string }> = [];
  const ends: string[] = [];
  const src = createCallSource({ room: r.room, feed: (ch, name) => fed.push({ ch, name }),
    onEmpty: (w) => ends.push(w), emptyGraceMs: 5000, firstJoinTimeoutMs: 60_000, ...tm });
  await src.start();
  r.h.audio({ identity: 'u:7', name: 'Ada' }, tone(480));
  r.h.audio({ identity: 'u:7', name: 'Ada', source: 'screen_share_audio' }, tone(480));
  r.h.audio({ identity: 'u:7', name: 'Ada' }, tone(480));
  r.h.audio({ identity: 'u:7', name: 'Ada', source: 'screen_share_audio' }, tone(480));
  check('the shared screen gets a channel of its own, apart from the voice',
    fed[0].ch === 0 && fed[1].ch === 1 && fed[2].ch === 0 && fed[3].ch === 1, JSON.stringify(fed));
  check(`it is named "Ada${SHARED_SCREEN_SUFFIX}"`, fed[1].name === 'Ada (shared screen)' && fed[0].name === 'Ada', JSON.stringify(fed));
  check('a shared screen is not a participant', src.stats().participants === 1);
  r.h.participantLeft({ identity: 'u:7', left: 'leave' });
  tm.advance(LEAVE_GRACE_MS);
  check('the call still ends with its people', ends.join() === 'everyone_left');
}

console.log('\n=== call: the call ends when the room empties, after the grace window ===');
{
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }, { identity: 'u:8', name: 'Grace' }]);
  const tm = manualTimers();
  const ends: string[] = [];
  const src = createCallSource({ room: r.room, feed: () => {}, onEmpty: (w) => ends.push(w),
    emptyGraceMs: 5000, firstJoinTimeoutMs: 60_000, ...tm });
  await src.start();
  r.h.participantLeft({ identity: 'u:7' });
  tm.advance(10_000);
  check('one left, one stays: the call goes on', ends.length === 0);
  r.h.participantLeft({ identity: 'u:8' });
  tm.advance(3000);
  r.h.participantJoined({ identity: 'u:8', name: 'Grace' });
  tm.advance(10_000);
  check('back within the grace window: the call goes on', ends.length === 0);
  r.h.participantLeft({ identity: 'u:8' });
  tm.advance(5000);
  check('empty past the grace window: the call ends once, everyone_left', ends.join() === 'everyone_left');
  r.h.disconnected('room deleted');
  check('a later disconnect does not end it twice', ends.length === 1);
}

console.log('\n=== call: the last person pressing Leave ends the call within seconds ===');
{
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }, { identity: 'u:8', name: 'Grace' }]);
  const tm = manualTimers();
  const ends: string[] = [];
  const src = createCallSource({ room: r.room, feed: () => {}, onEmpty: (w) => ends.push(w),
    emptyGraceMs: 30_000, firstJoinTimeoutMs: 60_000, ...tm });
  await src.start();
  r.h.participantLeft({ identity: 'u:7', left: 'leave' });
  tm.advance(LEAVE_GRACE_MS);
  check('one pressed Leave, one stays: the call goes on', ends.length === 0);
  r.h.participantLeft({ identity: 'u:8', left: 'leave' });
  tm.advance(LEAVE_GRACE_MS - 1);
  check('the last one pressed Leave: a moment for a misclick', ends.length === 0);
  tm.advance(1);
  check('then the call ends — no 30 s wait, no manual stop', ends.join() === 'everyone_left');
  check('the leave grace is a few seconds', LEAVE_GRACE_MS <= 5000);
}
{
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }]);
  const tm = manualTimers();
  const ends: string[] = [];
  const src = createCallSource({ room: r.room, feed: () => {}, onEmpty: (w) => ends.push(w),
    emptyGraceMs: 30_000, firstJoinTimeoutMs: 60_000, ...tm });
  await src.start();
  r.h.participantLeft({ identity: 'u:7', left: 'lost' });
  tm.advance(LEAVE_GRACE_MS);
  check('a dropped connection keeps the full grace window to come back', ends.length === 0);
  r.h.participantJoined({ identity: 'u:7', name: 'Ada' });
  tm.advance(30_000);
  check('back within it: the same call goes on', ends.length === 0);
}

console.log('\n=== call: nobody came ===');
{
  const r = scriptedRoom();
  const tm = manualTimers();
  const ends: string[] = [];
  const src = createCallSource({ room: r.room, feed: () => {}, onEmpty: (w) => ends.push(w),
    emptyGraceMs: 5000, firstJoinTimeoutMs: 30_000, ...tm });
  await src.start();
  tm.advance(29_000);
  check('before the first-join window: waiting', ends.length === 0);
  tm.advance(1000);
  check('nobody joined in the window: the call ends', ends.join() === 'nobody_joined');
}

console.log('\n=== call: the room going away ends it; stop leaves the room quietly ===');
{
  const r = scriptedRoom([{ identity: 'u:7' }]);
  const ends: string[] = [];
  const src = createCallSource({ room: r.room, feed: () => {}, onEmpty: (w) => ends.push(w),
    emptyGraceMs: 5000, firstJoinTimeoutMs: 30_000, ...manualTimers() });
  await src.start();
  r.h.disconnected('ROOM_DELETED');
  check('room_closed', ends.join() === 'room_closed');
  const r2 = scriptedRoom([{ identity: 'u:7' }]);
  const ends2: string[] = [];
  const src2 = createCallSource({ room: r2.room, feed: () => {}, onEmpty: (w) => ends2.push(w),
    emptyGraceMs: 5000, firstJoinTimeoutMs: 30_000, ...manualTimers() });
  await src2.start();
  await src2.stop();
  r2.h.disconnected('client');
  check('stop disconnects the room and does not report an end', r2.disconnected === 1 && ends2.length === 0);
}

console.log('\n=== call: digital silence is not fed ===');
{
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }]);
  const fed: number[] = [];
  const src = createCallSource({ room: r.room, feed: (_c, _n, pcm) => fed.push(pcm.length), onEmpty: () => {},
    emptyGraceMs: 5000, firstJoinTimeoutMs: 30_000, ...manualTimers() });
  await src.start();
  r.h.audio({ identity: 'u:7', name: 'Ada' }, { data: new Int16Array(480), sampleRate: 48_000, channels: 1 });
  r.h.audio({ identity: 'u:7', name: 'Ada' }, { data: new Int16Array(480).fill(1), sampleRate: 48_000, channels: 1 });
  r.h.audio({ identity: 'u:7', name: 'Ada' }, tone(480));
  const st = src.stats().speakers['u:7'];
  check('zeros (a muted or DTX-silent track) are skipped, speech is fed', fed.length === 1 && st.silentFrames === 2,
    JSON.stringify({ fed, st }));
}

console.log('\n=== toMono16k ===');
{
  const stereo = { data: new Int16Array([16384, 0, 16384, 0]), sampleRate: 16_000, channels: 2 };
  const m = toMono16k(stereo);
  check('stereo is averaged to mono', m.length === 2 && Math.abs(m[0] - 0.25) < 1e-6);
}

console.log('\n=== call: through the real lane, one transcript per speaker ===');
{
  const inv = { platform: 'vexa_call', meeting_id: 1, botName: 'Vexa', call: { url: 'ws://x', room: 'r', token: 't', maxDurationSec: 60 } } as unknown as Invocation;
  const segs: TranscriptSegment[] = [];
  const sink = { publish: async (s: TranscriptSegment) => { segs.push(s); } };
  const engine = createBotPipeline(inv, sink, {
    transcribe: async (pcm: Float32Array) => ({ text: `we should move the budget review to Thursday, about ${Math.round(pcm.length / 16000)} seconds`, language: 'en', segments: [] }) as never,
  });
  const r = scriptedRoom([{ identity: 'u:7', name: 'Ada' }, { identity: 'u:8', name: 'Grace' }]);
  let t = 1_760_000_000_000;
  const src = createCallSource({ room: r.room, feed: (ch, name, pcm, ts) => engine.feedAudio(ch, name, pcm, ts),
    onEmpty: () => {}, emptyGraceMs: 5000, firstJoinTimeoutMs: 30_000, now: () => t });
  const pipe = createCallPipeline({ source: src, engine });
  await pipe.start();
  const speech = (who: { identity: string; name: string; source?: 'microphone' | 'screen_share_audio' }, sec: number) => {
    for (let i = 0; i < sec * 50; i++) {
      const data = new Int16Array(960);
      for (let k = 0; k < data.length; k++) data[k] = Math.round(8000 * Math.sin((k + i * 960) / 7));
      r.h.audio(who, { data, sampleRate: 48_000, channels: 1 });
      t += 20;
    }
  };
  const silence = (sec: number) => { t += sec * 1000; };
  speech({ identity: 'u:7', name: 'Ada' }, 3);
  silence(3);
  speech({ identity: 'u:8', name: 'Grace' }, 3);
  silence(3);
  speech({ identity: 'u:8', name: 'Grace', source: 'screen_share_audio' }, 3);
  silence(3);
  await pipe.stop();
  await new Promise((res) => setTimeout(res, 50));
  const speakers = new Set(segs.map((s) => s.speaker));
  check('the lane emitted segments for both speakers', speakers.has('Ada') && speakers.has('Grace'), JSON.stringify([...speakers]));
  check('a shared screen\'s sound becomes segments of "Grace (shared screen)"', speakers.has('Grace (shared screen)'), JSON.stringify([...speakers]));
  check('max active time is the call cap', callMaxActiveMs(inv) === 60_000);
  const jd = createCallJoinDriver();
  check('the join driver admits at once', (await jd.join()) === 'admitted');
}

console.log(`\n${passed} passed, ${failed} failed`);
if (failed) process.exit(1);
