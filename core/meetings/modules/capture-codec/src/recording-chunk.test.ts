/**
 * recording.v1 wire-frame conformance — encode/decodeRecordingChunk must round-trip,
 * and must NOT mis-decode a transcription audio frame. This is the capture-codec
 * brick's DELTA: the REC1-magic framing that lets one ingest WS carry both audio
 * frames and recording chunks (desktop transport). The master-build codec is tested
 * elsewhere (modules/recording golden.test.ts); here we only prove the framing.
 * Run: npm test  (or npx tsx src/recording-chunk.test.ts)
 */
import { encodeRecordingChunk, decodeRecordingChunk, encodeAudioFrame, type RecordingFormat } from "./index.js";

let failed = 0;
const check = (name: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✅" : "❌"} ${name}${cond ? "" : "  — " + detail}`);
  if (!cond) failed++;
};
const eqBytes = (a: Uint8Array, b: Uint8Array) =>
  a.length === b.length && Buffer.compare(Buffer.from(a), Buffer.from(b)) === 0;

// Round-trip across formats / flags / sizes — including the two edge cases that
// matter on the wire: a large seq and the empty is_final chunk (the COMPLETED signal).
const cases: Array<{ seq: number; isFinal: boolean; format: RecordingFormat; bytes: Uint8Array }> = [
  { seq: 0, isFinal: false, format: "webm", bytes: Uint8Array.from([0x1a, 0x45, 0xdf, 0xa3, 1, 2, 3]) },
  { seq: 1, isFinal: false, format: "wav", bytes: Uint8Array.from([0x52, 0x49, 0x46, 0x46, 9, 9]) },
  { seq: 123456, isFinal: true, format: "webm", bytes: Uint8Array.from([7, 7, 7, 7, 7]) }, // large seq
  { seq: 42, isFinal: true, format: "wav", bytes: new Uint8Array(0) },                      // empty final chunk
];
for (const c of cases) {
  const dec = decodeRecordingChunk(encodeRecordingChunk(c.seq, c.isFinal, c.format, c.bytes));
  const ok = !!dec && dec.seq === c.seq && dec.isFinal === c.isFinal && dec.format === c.format && eqBytes(dec.bytes, c.bytes);
  check(`round-trip seq=${c.seq} ${c.format} final=${c.isFinal} ${c.bytes.length}B`, ok, JSON.stringify(dec));
}

// A frame embedded in a larger buffer: decode must honor byteOffset/byteLength.
{
  const bytes = Uint8Array.from([5, 6, 7, 8, 9, 10]);
  const frame = new Uint8Array(encodeRecordingChunk(99, false, "webm", bytes));
  const big = new Uint8Array(8 + frame.length + 4);
  big.set(frame, 8);
  const dec = decodeRecordingChunk(big.buffer, 8, frame.length);
  check("decode honors byteOffset/byteLength slice", !!dec && dec.seq === 99 && eqBytes(dec.bytes, bytes), JSON.stringify(dec));
}

// Disambiguation: an audio frame must decode to null (so the receiver falls through
// to decodeAudioFrame). longEnough guards that null is the magic check, not a length short-circuit.
{
  const audio = encodeAudioFrame(3, 1000, new Float32Array(16).fill(0.25), "alice");
  const longEnough = audio.byteLength > 16;
  const dec = decodeRecordingChunk(audio);
  check("returns null on an audio frame (not a REC1 frame)", longEnough && dec === null, `len=${audio.byteLength} dec=${JSON.stringify(dec)}`);
}

// Forward-compat: an unknown format code decodes as 'webm' (never throws).
{
  const buf = new ArrayBuffer(16 + 2);
  const v = new DataView(buf);
  v.setInt32(0, 0x52454331, true); v.setInt32(4, 1, true); v.setInt32(8, 0, true); v.setInt32(12, 99, true);
  new Uint8Array(buf, 16).set([1, 2]);
  const dec = decodeRecordingChunk(buf);
  check("unknown format code falls back to webm", !!dec && dec.format === "webm", JSON.stringify(dec));
}

if (failed) { console.error(`\n❌ recording-chunk: ${failed} checks FAILED.`); process.exit(1); }
console.log(`\n✅ recording-chunk: all checks pass — REC1 framing round-trips and is disambiguated from audio.`);
