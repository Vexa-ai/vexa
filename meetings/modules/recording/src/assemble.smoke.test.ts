/**
 * assemble.smoke — the Node master-builder in isolation (no browser, no I/O).
 *
 * buildRecordingMaster is the pure recording.v1 master codec (the Node twin of
 * meeting-api's recording_codec.py). It is Buffer-in → Buffer-out, so it is
 * fully isolated-testable. We pin the two strategies + the guards:
 *   WAV  — strip each chunk's 44-byte RIFF header, concat the PCM payloads,
 *          prepend ONE corrected master header (RIFF size = 36+data, data size,
 *          fmt copied from chunk 0); the empty final chunk is skipped; a fmt
 *          mismatch between chunks throws.
 *   WEBM — byte-concat in seq order (the empty final chunk is a no-op concat).
 *
 * No assert lib — same shape as the mixed/pipeline *.smoke.test.ts (tsx + exit).
 */
import { buildRecordingMaster } from './index';

const fails: string[] = [];
const check = (cond: boolean, msg: string) => { if (!cond) fails.push(msg); };

const FMT = Buffer.from([
  0x01, 0x00,             // PCM
  0x01, 0x00,             // 1 channel
  0x80, 0x3e, 0x00, 0x00, // 16000 Hz
  0x00, 0x7d, 0x00, 0x00, // byte rate
  0x02, 0x00,             // block align
  0x10, 0x00,             // 16 bps
]); // exactly 16 bytes (offsets 20..36 of a canonical WAV header)

/** Build a canonical 44-byte-header WAV chunk wrapping `data` with `fmt`. */
function wavChunk(data: Buffer, fmt: Buffer = FMT): Buffer {
  const h = Buffer.alloc(44);
  h.write('RIFF', 0, 'ascii');
  h.writeUInt32LE(36 + data.length, 4);
  h.write('WAVE', 8, 'ascii');
  h.write('fmt ', 12, 'ascii');
  h.writeUInt32LE(16, 16);
  fmt.copy(h, 20);
  h.write('data', 36, 'ascii');
  h.writeUInt32LE(data.length, 40);
  return Buffer.concat([h, data]);
}

// ── WAV: two real chunks + an empty final chunk ───────────────────────────────
{
  const a = Buffer.from([0x11, 0x22, 0x33, 0x44]);
  const b = Buffer.from([0x55, 0x66]);
  const chunks = [wavChunk(a), wavChunk(b), Buffer.alloc(0) /* empty final */];
  const master = buildRecordingMaster('wav', chunks);

  const totalData = a.length + b.length;
  check(master.length === 44 + totalData, `wav master length ${master.length} (want ${44 + totalData})`);
  check(master.toString('ascii', 0, 4) === 'RIFF' && master.toString('ascii', 8, 12) === 'WAVE',
    'wav master missing RIFF/WAVE');
  check(master.readUInt32LE(4) === 36 + totalData, `wav RIFF size ${master.readUInt32LE(4)} (want ${36 + totalData})`);
  check(master.readUInt32LE(40) === totalData, `wav data size ${master.readUInt32LE(40)} (want ${totalData})`);
  check(master.subarray(20, 36).equals(FMT), 'wav fmt block not copied from chunk 0');
  check(master.subarray(44).equals(Buffer.concat([a, b])), 'wav payload != concat(a,b) — header strip/order wrong');
}

// ── WAV: fmt mismatch between chunks must throw ───────────────────────────────
{
  const otherFmt = Buffer.from(FMT); otherFmt.writeUInt16LE(2, 2); // 2 channels ≠ chunk0
  let threw = false;
  try { buildRecordingMaster('wav', [wavChunk(Buffer.from([1, 2])), wavChunk(Buffer.from([3, 4]), otherFmt)]); }
  catch { threw = true; }
  check(threw, 'wav fmt mismatch did NOT throw');
}

// ── WEBM: byte-concat in seq order, empty final is a no-op ────────────────────
{
  const c0 = Buffer.from([0x1a, 0x45, 0xdf, 0xa3]); // EBML magic-ish (self-describing chunk 0)
  const c1 = Buffer.from([0xa3, 0x01, 0x02]);       // cluster-only
  const master = buildRecordingMaster('webm', [c0, c1, Buffer.alloc(0)]);
  check(master.equals(Buffer.concat([c0, c1])), 'webm master != byte-concat of chunks');
}

if (fails.length) {
  console.log('❌ FAIL —\n  ' + fails.join('\n  '));
  process.exit(1);
}
console.log('✅ PASS — wav master strips headers + sums payloads + corrects sizes (fmt mismatch throws, empty final skipped); webm master is byte-concat.');
process.exit(0);
