/**
 * mixed-capture-core L2 — the PURE signal-gating logic, no browser. Shims a
 * minimal AudioContext/MediaStream so we can drive the real createMixedAudioCapture
 * ScriptProcessor callback with synthetic buffers and assert: near-silent frames
 * are dropped (silenceThreshold), audible frames are forwarded as a COPY (the
 * source buffer is reused by the engine), and stop() tears down. The actual
 * device capture + re-play is live-validated. installRemoteAudioHook is a no-op
 * without RTCPeerConnection — assert that contract too.
 * Run: npm test  or  npx tsx src/mixed-capture-core.test.ts
 */
import { createMixedAudioCapture, installRemoteAudioHook } from './index.js';

let failed = 0;
const check = (name: string, cond: boolean) => { console.log(`  ${cond ? '✅' : '❌'} ${name}`); if (!cond) failed++; };

// ── Minimal Web Audio / MediaStream shim ──────────────────────────────────────
const g = globalThis as any;
let lastProc: any = null;            // the most-recently-created ScriptProcessor
const closed: any[] = [];            // contexts that got .close()

// The context clock, driven by the test. In a browser it advances with rendering; here advancing
// it independently of `fire()` is exactly how a starved ScriptProcessor is modelled.
let ctxClock = 0;

class FakeAudioContext {
  sampleRate: number;
  destination = { _id: 'dest' };
  get currentTime() { return ctxClock; }
  constructor(opts?: { sampleRate?: number }) { this.sampleRate = opts?.sampleRate ?? 48000; }
  createMediaStreamSource() { return { connect() {} }; }
  createScriptProcessor() {
    const proc: any = { onaudioprocess: null, connect() {}, disconnect() {} };
    lastProc = proc;
    return proc;
  }
  resume() { return Promise.resolve(); }
  close() { closed.push(this); return Promise.resolve(); }
}
class FakeTrack { stopped = false; clone() { return new FakeTrack(); } stop() { this.stopped = true; } }
class FakeMediaStream {
  private tracks: FakeTrack[];
  constructor(tracks?: FakeTrack[]) { this.tracks = tracks ?? [new FakeTrack()]; }
  getAudioTracks() { return this.tracks; }
}
g.AudioContext = FakeAudioContext;
g.MediaStream = FakeMediaStream;

// Fire one audio buffer through the live ScriptProcessor callback.
const fire = (samples: Float32Array) => {
  lastProc.onaudioprocess({ inputBuffer: { getChannelData: () => samples } });
};

// ── silence gate ──────────────────────────────────────────────────────────────
{
  const pcms: Float32Array[] = [];
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, (pcm) => pcms.push(pcm), { sampleRate: 16000, silenceThreshold: 0.01 });
  fire(new Float32Array(8).fill(0.001));   // below threshold → dropped
  check('near-silent frame is dropped', pcms.length === 0);
  fire(new Float32Array(8).fill(0.5));     // above threshold → forwarded
  check('audible frame is forwarded', pcms.length === 1);
  check('forwarded PCM has the right length', pcms[0]?.length === 8);
  cap.stop();
}
{
  // The forwarded PCM must be a COPY — the engine reuses the input buffer, so a
  // forwarded reference would alias and corrupt downstream.
  const pcms: Float32Array[] = [];
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, (pcm) => pcms.push(pcm), { silenceThreshold: 0.01 });
  const shared = new Float32Array(4).fill(0.5);
  fire(shared);
  shared.fill(0.0);                        // mutate the engine's buffer after the callback
  check('forwarded PCM is a copy (not aliased to the engine buffer)', pcms[0]?.every((v) => v === 0.5) === true);
  cap.stop();
}
{
  // replay:false → no second (native-rate) context; stop() closes the capture ctx.
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, () => {}, { replay: false });
  const before = closed.length;
  cap.stop();
  check('stop() closes the capture context', closed.length === before + 1);
}

// ── the default lets EVERYTHING through, digital silence included ─────────────
// Dropping a frame drops TIME, and a codec's silence suppression emits exactly-zero buffers — so
// the case a disabled gate most needs to pass is the one a naive `maxVal > 0` would still refuse.
{
  const pcms: Float32Array[] = [];
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, (pcm) => pcms.push(pcm), { sampleRate: 16000, replay: false });
  ctxClock = 0;
  fire(new Float32Array(8));               // digital silence — what DTX sends
  fire(new Float32Array(8).fill(0.0005));  // near-silence, under the old 0.005 gate
  fire(new Float32Array(8).fill(0.5));     // speech
  check('by default nothing is gated — silence keeps the timeline intact', pcms.length === 3);
  check('and the gate reports nothing refused', cap.stats().gatedSec === 0);
  cap.stop();
}

// ── delivery accounting: the two losses must be told apart ────────────────────
// A frame missing downstream was either refused by the gate or never handed over by the
// ScriptProcessor, and the fixes are unrelated (drop the gate vs move to an AudioWorklet). The
// context clock is the only local witness to how much audio existed, so stats() measures both
// against it — which is what makes a capture deficit attributable at all.
{
  const SR = 16000, BUF = 4096, FRAME = BUF / SR;
  ctxClock = 0;
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, () => { /* */ },
    { sampleRate: SR, silenceThreshold: 0.005, replay: false });

  // Ten buffers rendered, ten delivered, three of them silent: the gate is the whole deficit.
  for (let i = 0; i < 10; i++) { ctxClock += FRAME; fire(new Float32Array(BUF).fill(i < 3 ? 0.001 : 0.5)); }
  let s = cap.stats();
  check('seen counts every buffer the processor delivered', s.seen === 10);
  check('emitted counts only what passed the gate', s.emitted === 7);
  check('gated time is the silent buffers', Math.abs(s.gatedSec - 3 * FRAME) < 1e-6);
  check('no processor deficit when every rendered buffer arrives', s.processorDeficitSec < 1e-6);

  // Now the context renders five buffers' worth of time while one buffer arrives — the signature
  // of a ScriptProcessor starved on the main thread. The gate must not be blamed for it.
  ctxClock += 5 * FRAME;
  fire(new Float32Array(BUF).fill(0.5));
  s = cap.stats();
  check('processor deficit appears when rendered time outruns delivered buffers',
    Math.abs(s.processorDeficitSec - 4 * FRAME) < 1e-6);
  check('gated time is unchanged by a processor deficit', Math.abs(s.gatedSec - 3 * FRAME) < 1e-6);
  check('delivered time counts real samples, not an assumed buffer size',
    Math.abs(s.deliveredSec - 11 * FRAME) < 1e-6);
  cap.stop();
}

// ── installRemoteAudioHook contract: no RTCPeerConnection → no-op false ─────────
{
  g.window = {};
  delete g.RTCPeerConnection;
  check('hook returns false when RTCPeerConnection is unavailable', installRemoteAudioHook({ log: () => {} }) === false);
}

if (failed) { console.error(`\n❌ mixed-capture-core: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ mixed-capture-core: silence-gate + copy-on-forward + teardown pass. (device capture/re-play is live-validated.)`);
