/**
 * Golden vectors for recording.v1 — the cross-language oracle, at TWO levels:
 *
 *  1. ASSEMBLY  (<name>.json)            — buildRecordingMaster(format, chunks[]) over
 *                                          ALREADY-ordered chunks. Pure byte assembly.
 *  2. LIFECYCLE (lifecycle/<name>.json)  — a SEQUENCE of recording.v1 frames
 *                                          {seq, isFinal, bytes} + a finalize trigger
 *                                          ('is_final' | 'close') → the master that
 *                                          createRecordingAssembler must produce.
 *
 * The expected master is computed HERE by the documented spec (webm = byte-concat in
 * seq order; wav = RIFF header-merge), independently of either implementation. BOTH
 * builders must reproduce these byte-for-byte:
 *   - meeting-api  recording_finalizer (Python)        ← cloud receiver
 *   - @vexa/recording  buildRecordingMaster + createRecordingAssembler (TS)  ← desktop
 *
 * The LIFECYCLE family pins the Stop-race invariant: a `close`-finalized vector and
 * its `is_final` twin share ONE master_sha256 — close-without-is_final MUST build the
 * same file a clean is_final would. (That invariant is the bug that reached live.)
 *
 * Deterministic (no Date/random) — re-running yields identical files.
 *   node modules/recording/src/contracts/golden/generate.mjs           # (re)write
 *   node modules/recording/src/contracts/golden/generate.mjs --check   # integrity guard
 */
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const LIFE = path.join(DIR, 'lifecycle');   // lifecycle vectors (frame-sequence + finalize trigger)
const b64 = (buf) => Buffer.from(buf).toString('base64');
const sha = (buf) => createHash('sha256').update(buf).digest('hex');
const bytes = (n, seed) => Buffer.from(Array.from({ length: n }, (_, i) => (seed * 31 + i * 7) & 0xff));

/** A canonical 44-byte-header WAV (s16le) wrapping `pcm`. */
function wav(pcm, sampleRate = 16000, channels = 1) {
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + pcm.length, 4); h.write('WAVE', 8);
  h.write('fmt ', 12); h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20);
  h.writeUInt16LE(channels, 22); h.writeUInt32LE(sampleRate, 24);
  h.writeUInt32LE(sampleRate * channels * 2, 28); h.writeUInt16LE(channels * 2, 32); h.writeUInt16LE(16, 34);
  h.write('data', 36); h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
}

// ── ORACLES (the spec, not the impls) ───────────────────────────────────────
const webmMaster = (chunks) => Buffer.concat(chunks); // byte-concat in order; empty chunks no-op

function wavMaster(chunks) {
  const real = chunks.filter((c) => c.length >= 44);
  const pcms = real.map((c) => c.subarray(44));
  const total = pcms.reduce((n, p) => n + p.length, 0);
  const fmtChunk = real[0].subarray(20, 36); // 16-byte fmt body copied from chunk 0
  const h = Buffer.alloc(44);
  h.write('RIFF', 0); h.writeUInt32LE(36 + total, 4); h.write('WAVE', 8);
  h.write('fmt ', 12); h.writeUInt32LE(16, 16); fmtChunk.copy(h, 20);
  h.write('data', 36); h.writeUInt32LE(total, 40);
  return Buffer.concat([h, ...pcms]);
}

// The LIFECYCLE oracle: order NON-EMPTY frames by seq, then apply the master oracle.
// This is EXACTLY what createRecordingAssembler must do (accumulate → order → drop the
// empty final → buildRecordingMaster) — regardless of whether finalize was is_final or close.
const lifecycleMaster = (format, frames) => {
  const chunks = frames.filter((f) => f.bytes.length).sort((a, b) => a.seq - b.seq).map((f) => f.bytes);
  return format === 'wav' ? wavMaster(chunks) : webmMaster(chunks);
};

/** The ASSEMBLY vectors — buildRecordingMaster over ordered chunks. */
function buildVectors() {
  const vectors = [];
  const add = (name, format, description, chunks, master) => vectors.push({
    name, format, description, chunks: chunks.map(b64),
    master_sha256: sha(master), master_len: master.length,
  });
  {
    const a = bytes(16, 1), b = bytes(12, 2), c = bytes(8, 3);
    add('webm-single', 'webm', 'one self-describing chunk → identity', [a], webmMaster([a]));
    add('webm-multi', 'webm', 'chunk0 + cluster-only chunks, concat in order', [a, b, c], webmMaster([a, b, c]));
    add('webm-empty-final', 'webm', 'empty is_final chunk concatenates as a no-op', [a, b, Buffer.alloc(0)], webmMaster([a, b, Buffer.alloc(0)]));
  }
  {
    const p1 = bytes(8, 4), p2 = bytes(6, 5), p3 = bytes(10, 6);
    add('wav-single', 'wav', 'one chunk → header rewritten, pcm preserved', [wav(p1)], wavMaster([wav(p1)]));
    add('wav-multi', 'wav', 'strip per-chunk headers, sum pcm, one master header', [wav(p1), wav(p2)], wavMaster([wav(p1), wav(p2)]));
    add('wav-44k-stereo', 'wav', 'fmt (44.1k/stereo) copied from chunk 0', [wav(p1, 44100, 2), wav(p3, 44100, 2)], wavMaster([wav(p1, 44100, 2), wav(p3, 44100, 2)]));
  }
  return vectors;
}

/** The LIFECYCLE vectors — frame sequence + finalize trigger → master (via the assembler). */
function buildLifecycleVectors() {
  const vectors = [];
  const add = (name, format, description, frames, finalize, master) => vectors.push({
    name, format, description, finalize,
    frames: frames.map((f) => ({ seq: f.seq, isFinal: f.isFinal, bytes: b64(f.bytes) })),
    master_sha256: sha(master), master_len: master.length,
  });
  // webm — the is_final-terminated vector and its close-without-is_final TWIN share one master.
  const a = bytes(16, 1), b = bytes(12, 2), c = bytes(8, 3);
  const wf = [{ seq: 0, isFinal: false, bytes: a }, { seq: 1, isFinal: false, bytes: b }];
  add('webm-isfinal', 'webm', 'in-order chunks + empty is_final → assembled on is_final', [...wf, { seq: 2, isFinal: true, bytes: Buffer.alloc(0) }], 'is_final', lifecycleMaster('webm', wf));
  add('webm-close-no-final', 'webm', 'NO is_final — finalized by session-close; MUST equal the is_final twin (the Stop-race bug guard)', wf, 'close', lifecycleMaster('webm', wf));
  // out-of-order arrival, sorted by seq at finalize, close-triggered.
  const oo = [{ seq: 2, isFinal: false, bytes: c }, { seq: 0, isFinal: false, bytes: a }, { seq: 1, isFinal: false, bytes: b }];
  add('webm-outoforder-close', 'webm', 'out-of-order arrival, sorted by seq at finalize, close-triggered', oo, 'close', lifecycleMaster('webm', oo));
  // wav — close vs is_final twin: SAME master (RIFF header-merge).
  const p1 = bytes(8, 4), p2 = bytes(6, 5);
  const wv = [{ seq: 0, isFinal: false, bytes: wav(p1) }, { seq: 1, isFinal: false, bytes: wav(p2) }];
  add('wav-isfinal', 'wav', 'wav RIFF-merge on is_final', [...wv, { seq: 2, isFinal: true, bytes: Buffer.alloc(0) }], 'is_final', lifecycleMaster('wav', wv));
  add('wav-close-no-final', 'wav', 'wav RIFF-merge finalized by close (no is_final); equals the is_final twin', wv, 'close', lifecycleMaster('wav', wv));
  return vectors;
}

const vectors = buildVectors();
const lifecycle = buildLifecycleVectors();
const serialize = (v) => JSON.stringify(v, null, 2) + '\n';

function writeFamily(dir, vs) {
  fs.mkdirSync(dir, { recursive: true });
  for (const v of vs) {
    fs.writeFileSync(path.join(dir, `${v.name}.json`), serialize(v));
    console.log(`  ${(path.basename(dir) === 'golden' ? '' : path.basename(dir) + '/') + v.name}`.padEnd(34) + `${v.format.padEnd(4)} → ${String(v.master_len).padStart(4)}B  ${v.master_sha256.slice(0, 12)}…`);
  }
}
function checkFamily(dir, vs) {
  let drift = 0;
  for (const v of vs) {
    const p = path.join(dir, `${v.name}.json`);
    const onDisk = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
    const ok = onDisk === serialize(v);
    console.log(`  ${ok ? '✅' : '❌'} ${path.basename(dir) === 'golden' ? '' : path.basename(dir) + '/'}${v.name}`);
    if (!ok) { drift++; if (onDisk === null) console.log('     missing on disk'); }
  }
  return drift;
}

if (process.argv.includes('--check')) {
  // INTEGRITY GUARD: committed vectors must equal what the oracle re-derives from the spec.
  const drift = checkFamily(DIR, vectors) + checkFamily(LIFE, lifecycle);
  if (drift) {
    console.error(`\n❌ golden integrity: ${drift} vector(s) drifted from the spec.`);
    console.error('   Re-run without --check to regenerate, then confirm the new master_sha256 values are intended.');
    process.exit(1);
  }
  console.log(`\n✅ golden integrity: all ${vectors.length} assembly + ${lifecycle.length} lifecycle vectors match the oracle.`);
} else {
  writeFamily(DIR, vectors);
  writeFamily(LIFE, lifecycle);
  console.log(`\n  ${vectors.length} assembly + ${lifecycle.length} lifecycle golden vectors written.`);
}
