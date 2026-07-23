/**
 * chunker.smoke — the MediaRecorder loop in isolation (no browser).
 *
 * record-chunker is the pure-logic recording brick: it wraps a MediaRecorder over
 * a combined audio stream and emits recording.v1 chunks (base64 + chunkSeq + isFinal
 * + mimeType). This test stubs the four browser globals it touches (MediaRecorder,
 * AudioContext-free path via direct MediaRecorderChunker, btoa, window/logBot) and
 * drives the REAL class to prove the contract the server reconciler depends on:
 *   1. chunkSeq increments monotonically from 0 across timeslice chunks;
 *   2. each chunk's base64 is the base64 of the blob body (round-trips);
 *   3. mimeType is the negotiated supported type;
 *   4. stop() emits exactly one extra chunk with isFinal=true (empty body OK)
 *      and resolves only AFTER that final onChunk completes.
 *
 * No assertion lib — same shape as mixed/pipeline's *.smoke.test.ts (tsx + exit code).
 */

// ── minimal browser-global stubs (installed before importing the brick) ─────────
const enc = (s: string): string => Buffer.from(s, 'binary').toString('base64');
(globalThis as any).btoa = (s: string) => enc(s);
// the brick reads isTypeSupported off window.MediaRecorder, so the stub must live there too.
(globalThis as any).window = { logBot: (_m: string) => {} };

/** A fake Blob whose arrayBuffer() yields the bytes we seeded. */
class FakeBlob {
  constructor(private bytes: Uint8Array) {}
  get size() { return this.bytes.length; }
  async arrayBuffer(): Promise<ArrayBuffer> {
    return this.bytes.buffer.slice(this.bytes.byteOffset, this.bytes.byteOffset + this.bytes.byteLength);
  }
}

class DelayedBlob {
  private releaseBuffer: (value: ArrayBuffer) => void = () => {};
  readonly buffer = new Promise<ArrayBuffer>((resolve) => { this.releaseBuffer = resolve; });
  constructor(private bytes: Uint8Array) {}
  get size() { return this.bytes.length; }
  async arrayBuffer(): Promise<ArrayBuffer> { return this.buffer; }
  release(): void {
    this.releaseBuffer(this.bytes.buffer.slice(this.bytes.byteOffset, this.bytes.byteOffset + this.bytes.byteLength));
  }
}

/** A fake MediaRecorder that lets the test fire ondataavailable / stop on demand. */
class FakeMediaRecorder {
  static isTypeSupported(mime: string) { return mime === 'audio/webm;codecs=opus'; }
  onstart: (() => void) | null = null;
  ondataavailable: ((e: any) => void) | null = null;
  onstop: (() => void) | null = null;
  state: 'inactive' | 'recording' = 'inactive';
  mimeType: string;
  constructor(_stream: any, opts?: { mimeType?: string }) { this.mimeType = opts?.mimeType ?? ''; }
  start(_timeslice?: number) { this.state = 'recording'; this.onstart?.(); }
  stop() { this.state = 'inactive'; this.onstop?.(); }
  /** test helper — deliver a timeslice blob */
  emit(bytes: Uint8Array) { this.ondataavailable?.({ data: new FakeBlob(bytes) }); }
}
(globalThis as any).MediaRecorder = FakeMediaRecorder;
(globalThis as any).window.MediaRecorder = FakeMediaRecorder; // brick reads window.MediaRecorder.isTypeSupported

// import AFTER globals exist (the module reads window/MediaRecorder lazily at runtime)
import { MediaRecorderChunker, type RecordingChunk } from './index';

const decode = (b64: string): Uint8Array => new Uint8Array(Buffer.from(b64, 'base64'));
const eqBytes = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((v, i) => v === b[i]);

async function main() {
  const got: RecordingChunk[] = [];
  let started = 0;
  const chunker = new MediaRecorderChunker({
    stream: {} as any, // unused by the FakeMediaRecorder
    timesliceMs: 1000,
    onChunk: async (c) => { got.push(c); return true; },
    onStarted: () => { started++; },
  });

  await chunker.start();
  const mr = chunker.getMediaRecorder() as unknown as FakeMediaRecorder;
  if (!mr) { console.log('❌ FAIL — start() did not create a MediaRecorder'); process.exit(1); }

  // two timeslice chunks with distinct bodies
  const body0 = new Uint8Array([1, 2, 3, 4, 250, 0, 127]);
  const body1 = new Uint8Array([9, 8, 7]);
  mr.emit(body0);
  mr.emit(body1);
  // let the async ondataavailable handlers settle
  await new Promise((r) => setTimeout(r, 10));

  await chunker.stop(); // → onstop → final chunk

  // ── assertions ────────────────────────────────────────────────────────────────
  const fails: string[] = [];
  if (started !== 1) fails.push(`onStarted fired ${started}× (want 1)`);
  if (got.length !== 3) fails.push(`emitted ${got.length} chunks (want 3: 2 data + 1 final)`);

  const [c0, c1, cF] = got;
  if (c0) {
    if (c0.chunkSeq !== 0) fails.push(`chunk0 seq=${c0.chunkSeq} (want 0)`);
    if (c0.isFinal) fails.push('chunk0 isFinal=true (want false)');
    if (!eqBytes(decode(c0.base64), body0)) fails.push('chunk0 base64 did not round-trip body0');
    if (c0.mimeType !== 'audio/webm;codecs=opus') fails.push(`chunk0 mimeType=${c0.mimeType} (want negotiated opus)`);
  }
  if (c1) {
    if (c1.chunkSeq !== 1) fails.push(`chunk1 seq=${c1.chunkSeq} (want 1)`);
    if (!eqBytes(decode(c1.base64), body1)) fails.push('chunk1 base64 did not round-trip body1');
  }
  if (cF) {
    if (cF.chunkSeq !== 2) fails.push(`final seq=${cF.chunkSeq} (want 2)`);
    if (!cF.isFinal) fails.push('final chunk isFinal=false (want true)');
    if (cF.base64 !== '') fails.push(`final chunk body=${JSON.stringify(cF.base64)} (want empty)`);
  }

  // The browser does not await an async EventTarget listener. A dataavailable encode still pending
  // when onstop fires must finish before the final marker is emitted.
  const overtaking: RecordingChunk[] = [];
  const ordered = new MediaRecorderChunker({
    stream: {} as any,
    onChunk: async (c) => { overtaking.push(c); return true; },
  });
  await ordered.start();
  const orderedRecorder = ordered.getMediaRecorder() as unknown as FakeMediaRecorder;
  const delayed = new DelayedBlob(new Uint8Array([4, 5, 6]));
  orderedRecorder.ondataavailable?.({ data: delayed });
  let orderedStopped = false;
  const orderedStopP = ordered.stop().then(() => { orderedStopped = true; });
  await new Promise((r) => setTimeout(r, 5));
  if (orderedStopped || overtaking.some((c) => c.isFinal)) {
    fails.push('stop emitted final before the pending dataavailable encode settled');
  }
  delayed.release();
  await orderedStopP;
  if (overtaking.map((c) => `${c.chunkSeq}:${c.isFinal}`).join(',') !== '0:false,1:true') {
    fails.push(`pending data/final order=${JSON.stringify(overtaking)} (want data seq0 then final seq1)`);
  }

  console.log(`chunks: ${JSON.stringify(got.map((c) => ({ seq: c.chunkSeq, final: c.isFinal, bytes: decode(c.base64).length })))}`);
  if (fails.length) {
    console.log('❌ FAIL — ' + fails.join('; '));
    process.exit(1);
  }
  console.log('✅ PASS — seq increments 0,1, bodies round-trip base64, final chunk isFinal=true(empty), mimeType negotiated');
  process.exit(0);
}

main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
