/**
 * late-stream.smoke — the recording tap must not give up when the meeting's first audio arrives late.
 *
 * Zoom web mirrors a participant's remote track into an <audio> element only once that participant's
 * audio actually reaches the bot. On app.dev meeting 144 (zoom, 2026-10-16) the bot was admitted at
 * 10:57:38, the tap's bounded first-stream wait expired at 10:57:48 ("no audio streams — cannot
 * record"), and the first remote track appeared at 10:57:59 — so 28 minutes of meeting produced no
 * recording at all while transcription worked. The tap must keep watching and start the recorder
 * when the first stream appears, without blocking start() (the caller's pipeline.start() awaits it).
 */

const enc = (s: string): string => Buffer.from(s, 'binary').toString('base64');
(globalThis as any).btoa = (s: string) => enc(s);

class FakeTrack { readyState: 'live' | 'ended' = 'live'; }
class FakeMediaStream {
  private static n = 0;
  id = `s${FakeMediaStream.n++}`;
  tracks: FakeTrack[];
  constructor(tracks: FakeTrack[] = []) { this.tracks = tracks; }
  getAudioTracks() { return this.tracks; }
}
(globalThis as any).MediaStream = FakeMediaStream;

class FakeAudioContext {
  resume() { return Promise.resolve(); }
  close() { return Promise.resolve(); }
  createMediaStreamDestination() { return { stream: new FakeMediaStream() }; }
  createMediaStreamSource(_s: any) { return { connect() {}, disconnect() {} }; }
}
(globalThis as any).AudioContext = FakeAudioContext;

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = [];
  static isTypeSupported(mime: string) { return mime === 'audio/webm;codecs=opus'; }
  onstart: (() => void) | null = null;
  ondataavailable: ((e: any) => void) | null = null;
  onstop: (() => void) | null = null;
  state: 'inactive' | 'recording' = 'inactive';
  mimeType: string;
  constructor(_stream: any, opts?: { mimeType?: string }) { this.mimeType = opts?.mimeType ?? ''; FakeMediaRecorder.instances.push(this); }
  start(_t?: number) { this.state = 'recording'; this.onstart?.(); }
  stop() { this.state = 'inactive'; this.onstop?.(); }
  requestData() {}
}
(globalThis as any).MediaRecorder = FakeMediaRecorder;

const logs: string[] = [];
const elements: any[] = [];
(globalThis as any).window = { logBot: (m: string) => { logs.push(m); }, MediaRecorder: FakeMediaRecorder };
(globalThis as any).document = { querySelectorAll: (_sel: string) => elements };

import { createRecordingTap } from './index';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main() {
  let started = 0;
  const tap = createRecordingTap({ timesliceMs: 1000, scanIntervalMs: 20, onChunk: async () => true, onStarted: () => { started++; } });

  // No audio in the page yet (Zoom before anyone's audio reaches the bot). start() must RESOLVE —
  // the bot's pipeline.start() awaits it — and must not have abandoned the recording.
  const t0 = Date.now();
  await tap.start();
  const startMs = Date.now() - t0;

  // The first participant's audio arrives well after the initial wait window.
  await sleep(400);
  elements.push({ srcObject: new FakeMediaStream([new FakeTrack()]) });
  await sleep(200);

  const fails: string[] = [];
  if (startMs > 5000) fails.push(`start() took ${startMs} ms (must not block the pipeline)`);
  if (FakeMediaRecorder.instances.length !== 1) fails.push(`MediaRecorder created ${FakeMediaRecorder.instances.length}× after a late first stream (want 1)`);
  if (started !== 1) fails.push(`onStarted fired ${started}× (want 1 — t=0 when audio first arrives)`);

  await tap.stop();
  const mr = FakeMediaRecorder.instances[0];
  if (mr && mr.state !== 'inactive') fails.push('recorder still running after stop()');

  // stop() before any audio ever arrives must not start a recorder afterwards.
  FakeMediaRecorder.instances.length = 0;
  elements.length = 0;
  const tap2 = createRecordingTap({ timesliceMs: 1000, scanIntervalMs: 20, onChunk: async () => true });
  await tap2.start();
  await tap2.stop();
  elements.push({ srcObject: new FakeMediaStream([new FakeTrack()]) });
  await sleep(200);
  if (FakeMediaRecorder.instances.length !== 0) fails.push('a stopped tap started a recorder when audio appeared later');

  if (fails.length) { console.log('❌ FAIL — ' + fails.join('; ') + `\nlogs: ${JSON.stringify(logs)}`); process.exit(1); }
  console.log('✅ PASS — a late first audio stream still starts the recording; start() does not block; stop() before audio is final');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
