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

class FakeAudioContext {
  sampleRate: number;
  destination = { _id: 'dest' };
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

// ── liveness is reported apart from the silence gate (#1673) ────────────────────
// A silent room and a dead capture both produce no PCM; only the liveness report tells them apart.
{
  const pcms: Float32Array[] = [];
  const beats: number[] = [];
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, (pcm) => pcms.push(pcm),
    { sampleRate: 16000, silenceThreshold: 0.005, replay: false, onLiveness: (n) => beats.push(n) });
  fire(new Float32Array(4096));            // digital silence
  check('a silent buffer emits a liveness report', beats.length === 1 && beats[0] === 4096);
  check('…and no PCM (the gate still keeps silence away from STT)', pcms.length === 0);
  for (let i = 0; i < 7; i++) fire(new Float32Array(4096));   // 8 x 4096 = 2.048 s of audio in all
  check('liveness is rate-limited to ~1/s of processed audio, not one per callback',
    beats.length === 2 && beats[1] === 5 * 4096);
  fire(new Float32Array(4096).fill(0.5));
  check('an audible buffer still crosses as PCM alongside liveness', pcms.length === 1);
  cap.stop();
}
{
  const pcms: Float32Array[] = [];
  const cap = await createMixedAudioCapture(new FakeMediaStream() as any, (pcm) => pcms.push(pcm),
    { replay: false, onLiveness: () => { throw new Error('consumer bug'); } });
  fire(new Float32Array(8).fill(0.5));
  check('a throwing liveness consumer cannot break PCM delivery', pcms.length === 1);
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
