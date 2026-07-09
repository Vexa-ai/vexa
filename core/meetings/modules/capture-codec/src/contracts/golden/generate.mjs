/**
 * Golden vectors for capture.v1 — the extension/bot → desktop capture wire.
 *
 * Unlike a hand-computed oracle, these are produced FROM THE CODEC itself
 * (`../../index.ts`): generate.mjs encodes each input with the real
 * `encodeAudioFrame` / `encodeEvent`, then pins the exact bytes (base64 + sha256
 * + len) AND the input/decoded struct. The golden test then asserts both
 * directions: `encode(input)` ≡ the pinned bytes, and `decode(bytes)` ≡ input.
 *
 * Deterministic (no Date/random) — re-running yields identical files. Because it
 * imports the TS codec it is run with tsx (the package already depends on it):
 *   npx tsx src/contracts/golden/generate.mjs           # (re)write the vectors
 *   npx tsx src/contracts/golden/generate.mjs --check    # integrity guard (CI)
 *
 * Vector families:
 *   AUDIO (audio-*.json)  — { kind:'audio', speakerIndex, ts, samples,
 *                             speakerName?, bytes_b64, sha256, len }
 *   EVENT (event-*.json)  — { kind:'event', event, bytes_b64, sha256, len }
 *                           (bytes here are the UTF-8 of JSON.stringify(event))
 */
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { fileURLToPath } from 'node:url';
import { encodeAudioFrame, encodeEvent } from '../../index.ts';

const DIR = path.dirname(fileURLToPath(import.meta.url));
const b64 = (u8) => Buffer.from(u8).toString('base64');
const sha = (u8) => createHash('sha256').update(Buffer.from(u8)).digest('hex');

// A deterministic Float32-exact PCM ramp. Each value is representable in Float32
// (n/256 with small n), so the input array doubles as the post-round-trip oracle.
const pcm = (n, seed) => Float32Array.from({ length: n }, (_, i) => ((((seed * 5 + i * 3) % 256) - 128) / 256));

/** AUDIO vector — encode the frame, pin its bytes + the input struct. */
function audioVector(name, description, speakerIndex, ts, samples, speakerName) {
  const buf = new Uint8Array(encodeAudioFrame(speakerIndex, ts, samples, speakerName));
  const v = {
    name, kind: 'audio', description,
    speakerIndex, ts,
    samples: Array.from(samples),          // exact Float32 values (decode oracle)
    ...(speakerName !== undefined ? { speakerName } : {}),
    len: buf.length, sha256: sha(buf), bytes_b64: b64(buf),
  };
  return v;
}

/** EVENT vector — JSON.stringify(event) → UTF-8 bytes, pin them + the event. */
function eventVector(name, description, event) {
  const json = encodeEvent(event);
  const buf = new TextEncoder().encode(json);
  return {
    name, kind: 'event', description,
    event,
    len: buf.length, sha256: sha(buf), bytes_b64: b64(buf),
  };
}

function buildVectors() {
  return [
    // (1) unnamed audio frame (mixed lane) — high bit clear, 12B header.
    audioVector('audio-unnamed', 'mixed-lane frame: no speakerName, 12-byte header, Int32 track + Float64 ts + Float32 PCM',
      3, 1718000000123, pcm(8, 1)),
    // (2) named audio frame (gmeet lane) — high-bit flag, name zero-padded to 4B.
    //     'Alice' is 5 bytes → padded to 8, exercising the padding rule.
    audioVector('audio-named', 'gmeet-lane frame: speakerName present (high-bit flag), 16-byte header + zero-padded UTF-8 name + Float32 PCM',
      7, 1718000000456, pcm(6, 2), 'Alice'),
    // edge: named frame whose UTF-8 name is already a 4-byte multiple (4 bytes) — no padding.
    audioVector('audio-named-aligned', 'named frame with a 4-byte (already-aligned) name → zero padding bytes added',
      0, 1718000000789, pcm(4, 3), 'Bob!'),
    // edge: empty-PCM frame — header only, n=0 samples (a valid wire frame).
    audioVector('audio-empty-pcm', 'unnamed frame with zero PCM samples (header only)',
      999, 1718000001000, pcm(0, 4)),
    // (3) event frame — active-speaker hint (the mixed lane's attribution event).
    eventVector('event-active-speaker', 'active-speaker hint event with a detail payload',
      { kind: 'active-speaker', ts: 1718000002000, speaker: 'Alice', detail: { hint: 'dom-active', isEnd: false, index: 7 } }),
    // event frame — a minimal lifecycle event (only required fields).
    eventVector('event-lifecycle', 'minimal lifecycle event: only the required kind + ts',
      { kind: 'lifecycle', ts: 1718000003000 }),
    // event frame — a chat message (speaker + text).
    eventVector('event-chat', 'chat event carrying a sender + text',
      { kind: 'chat', ts: 1718000004000, speaker: 'Bob', text: 'hello world' }),
  ];
}

const vectors = buildVectors();
const serialize = (v) => JSON.stringify(v, null, 2) + '\n';

function write() {
  fs.mkdirSync(DIR, { recursive: true });
  for (const v of vectors) {
    fs.writeFileSync(path.join(DIR, `${v.name}.json`), serialize(v));
    console.log(`  ${v.name}`.padEnd(28) + `${v.kind.padEnd(5)} → ${String(v.len).padStart(4)}B  ${v.sha256.slice(0, 12)}…`);
  }
  console.log(`\n  ${vectors.length} capture.v1 golden vectors written.`);
}

function check() {
  let drift = 0;
  for (const v of vectors) {
    const p = path.join(DIR, `${v.name}.json`);
    const onDisk = fs.existsSync(p) ? fs.readFileSync(p, 'utf8') : null;
    const ok = onDisk === serialize(v);
    console.log(`  ${ok ? '✅' : '❌'} ${v.name}`);
    if (!ok) { drift++; if (onDisk === null) console.log('     missing on disk'); }
  }
  if (drift) {
    console.error(`\n❌ golden integrity: ${drift}/${vectors.length} vector(s) drifted from the codec.`);
    console.error('   Re-run without --check to regenerate, then confirm the new sha256/len/bytes are intended.');
    process.exit(1);
  }
  console.log(`\n✅ golden integrity: all ${vectors.length} capture.v1 vectors match the codec.`);
}

if (process.argv.includes('--check')) check();
else write();
