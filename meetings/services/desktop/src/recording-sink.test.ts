/**
 * L2 — RecordingSink unit (ARCHITECTURE.md §5). The recording.v1 ASSEMBLY core
 * with the transport mocked out: feed synthetic recording.v1 chunks straight to
 * the port (no WS, no disk — an in-memory `onMaster` fake captures the result)
 * and assert the `buildRecordingMaster` output. Proves the port accumulates,
 * orders by seq, drops the empty is_final chunk, and assembles a valid master —
 * the L2 seam (P5) that exists because assembly lives behind the port.
 * Run: npx tsx src/recording-sink.test.ts
 */
import { createRecordingSink, type RecordingMaster } from './recording-sink.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// A 44-byte canonical WAV header (16 kHz mono s16le) + `dataSize` payload bytes,
// mirroring what the recording-codec's parseWavHeader expects (RIFF/WAVE, 'data'
// at offset 36). Lets us assert buildRecordingMaster's RIFF header-merge exactly.
function wavChunk(payload: number[]): Uint8Array {
  const dataSize = payload.length;
  const buf = Buffer.alloc(44 + dataSize);
  buf.write('RIFF', 0, 'ascii');
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write('WAVE', 8, 'ascii');
  buf.write('fmt ', 12, 'ascii');
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);      // PCM
  buf.writeUInt16LE(1, 22);      // mono
  buf.writeUInt32LE(16000, 24);  // sample rate
  buf.writeUInt32LE(32000, 28);  // byte rate
  buf.writeUInt16LE(2, 32);      // block align
  buf.writeUInt16LE(16, 34);     // bits/sample
  buf.write('data', 36, 'ascii');
  buf.writeUInt32LE(dataSize, 40);
  Buffer.from(payload).copy(buf, 44);
  return new Uint8Array(buf);
}

// ── Case 1: WAV — two media chunks (out of seq order) + the empty final chunk. ──
{
  const got: RecordingMaster[] = [];
  const sink = createRecordingSink({ onMaster: (m) => got.push(m) });
  const key = 'google_meet/wav-sess';
  // Feed seq 1 BEFORE seq 0 to prove the sink orders by seq, not arrival.
  sink.chunk(key, 1, false, 'wav', wavChunk([0x05, 0x06, 0x07, 0x08]));
  sink.chunk(key, 0, false, 'wav', wavChunk([0x01, 0x02, 0x03, 0x04]));
  check('no master before is_final', got.length === 0, `got ${got.length}`);
  sink.chunk(key, 2, true, 'wav', new Uint8Array(0));   // empty COMPLETED signal

  check('one master emitted on is_final', got.length === 1, `got ${got.length}`);
  const m = got[0];
  check('master keyed to the session', m?.key === key, m?.key);
  check('master format is wav', m?.format === 'wav', m?.format);
  check('two media chunks fed the master', m?.chunks === 2, String(m?.chunks));
  // buildRecordingMaster (wav) = one 44B header + summed PCM payloads, in SEQ order.
  const wantData = Buffer.from([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]);
  check('master is a single 44B header + concatenated payloads', m.bytes.length === 44 + wantData.length, `${m.bytes.length}`);
  check('master RIFF/WAVE magic intact', m.bytes.toString('ascii', 0, 4) === 'RIFF' && m.bytes.toString('ascii', 8, 12) === 'WAVE');
  check("master 'data' size header corrected to the SUM", m.bytes.readUInt32LE(40) === wantData.length, `${m.bytes.readUInt32LE(40)}`);
  check('master PCM payload = seq-ordered concat of inputs', Buffer.compare(m.bytes.subarray(44), wantData) === 0, m.bytes.subarray(44).toString('hex'));
}

// ── Case 2: WebM — byte-concat in seq order; empty final concatenates as no-op. ──
{
  const got: RecordingMaster[] = [];
  const sink = createRecordingSink({ onMaster: (m) => got.push(m) });
  const key = 'youtube/webm-sess';
  sink.chunk(key, 0, false, 'webm', Uint8Array.from([0x1a, 0x45, 0xdf, 0xa3]));   // EBML header chunk
  sink.chunk(key, 1, false, 'webm', Uint8Array.from([0xaa, 0xbb]));               // a Cluster chunk
  sink.chunk(key, 2, true, 'webm', new Uint8Array(0));                            // empty final
  check('webm master emitted', got.length === 1, `got ${got.length}`);
  check('webm master = plain byte-concat in seq order', !!got[0] && Buffer.compare(got[0].bytes, Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0xaa, 0xbb])) === 0, got[0]?.bytes.toString('hex'));
}

// ── Case 3: two independent sessions don't bleed into each other. ──
{
  const got = new Map<string, RecordingMaster>();
  const sink = createRecordingSink({ onMaster: (m) => got.set(m.key, m) });
  sink.chunk('zoom/a', 0, false, 'webm', Uint8Array.from([1, 1]));
  sink.chunk('zoom/b', 0, false, 'webm', Uint8Array.from([2, 2]));
  sink.chunk('zoom/a', 1, true, 'webm', new Uint8Array(0));
  sink.chunk('zoom/b', 1, true, 'webm', new Uint8Array(0));
  check('session A master holds only A bytes', Buffer.compare(got.get('zoom/a')!.bytes, Buffer.from([1, 1])) === 0);
  check('session B master holds only B bytes', Buffer.compare(got.get('zoom/b')!.bytes, Buffer.from([2, 2])) === 0);
}

// ── Case 4: is_final with NO media chunks → no master (signal-only, never throws). ──
{
  const got: RecordingMaster[] = [];
  const sink = createRecordingSink({ onMaster: (m) => got.push(m) });
  sink.chunk('teams/empty', 0, true, 'webm', new Uint8Array(0));
  check('is_final with no media emits no master', got.length === 0, `got ${got.length}`);
}

// ── Case 5: close(key) finalizes accumulated chunks when NO is_final arrived — the
//    live Stop race (the WS drops before the trailing MediaRecorder chunk flushes).
//    The session-close is the ROBUST assembly trigger; is_final is the prompt path. ──
{
  const got: RecordingMaster[] = [];
  const sink = createRecordingSink({ onMaster: (m) => got.push(m) });
  const key = 'youtube/no-final';
  sink.chunk(key, 0, false, 'webm', Uint8Array.from([0x1a, 0x45]));
  sink.chunk(key, 1, false, 'webm', Uint8Array.from([0xcc, 0xdd]));
  check('no master before close (no is_final seen)', got.length === 0, `got ${got.length}`);
  sink.close(key);                                   // session-close finalizes the orphaned chunks
  check('close() assembles the accumulated chunks', got.length === 1, `got ${got.length}`);
  check('close() master = seq-ordered byte-concat', !!got[0] && Buffer.compare(got[0].bytes, Buffer.from([0x1a, 0x45, 0xcc, 0xdd])) === 0, got[0]?.bytes.toString('hex'));
  sink.close(key);                                   // idempotent — already assembled + cleared
  check('close() again is a no-op', got.length === 1, `got ${got.length}`);
}

// ── Case 6: close(key) on an unknown / empty session is a safe no-op. ──
{
  const got: RecordingMaster[] = [];
  const sink = createRecordingSink({ onMaster: (m) => got.push(m) });
  sink.close('youtube/never-started');
  check('close() on an unknown session emits no master', got.length === 0, `got ${got.length}`);
}

if (failed) { console.error(`\n❌ recording-sink: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ recording-sink (L2): the port accumulates, orders by seq, drops the empty final, and assembles a valid recording.v1 master — transport mocked.');
