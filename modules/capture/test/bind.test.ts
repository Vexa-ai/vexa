/**
 * Golden: the gmeet glow→audio binding survives BOTH capture.v1 serializations,
 * and the bind decision is honest. Run: npx tsx modules/capture/test/bind.test.ts
 *
 * Proves touch #1:
 *  - WS path   — encodeAudioFrame carries speakerName; decode round-trips it.
 *  - legacy    — a no-name frame still decodes byte-for-byte (additive v1).
 *  - in-proc   — a CaptureV1Sink receives speakerName directly (trivially carried).
 *  - decision  — exactly-one-lit ⇒ name; zero or 2+ lit ⇒ undefined.
 */
import assert from 'node:assert';
import { encodeAudioFrame, decodeAudioFrame, tee, type AudioChunk } from '../src/contract/capture-v1';
import { pickBoundName } from '../src/gmeet-capture-v1';

let pass = 0;
const ok = (name: string, fn: () => void) => { fn(); pass++; console.log(`  ✅ ${name}`); };

const pcm = Float32Array.from([0.1, -0.25, 0.5, -0.75, 1, -1, 0, 0.333]);
const samplesEqual = (a: Float32Array, b: Float32Array) => {
  assert.equal(a.length, b.length, 'sample count');
  for (let i = 0; i < a.length; i++) assert.ok(Math.abs(a[i] - b[i]) < 1e-6, `sample ${i}`);
};

// ── WS path: name rides the wire ─────────────────────────────────────────────
ok('named frame round-trips speakerName + ts + index + pcm', () => {
  const ts = 1_700_000_123_456;
  const f = decodeAudioFrame(encodeAudioFrame(2, ts, pcm, 'Анна'));   // non-ASCII on purpose
  assert.ok(f, 'decoded');
  assert.equal(f!.speakerIndex, 2);
  assert.equal(f!.ts, ts);
  assert.equal(f!.speakerName, 'Анна');
  samplesEqual(f!.samples, pcm);
});

ok('high bit is stripped — real channel id survives (999 mixed, 1000 mic, 0..N)', () => {
  for (const idx of [0, 1, 2, 7, 999, 1000]) {
    const f = decodeAudioFrame(encodeAudioFrame(idx, 1, pcm, 'Борис'));
    assert.equal(f!.speakerIndex, idx, `idx ${idx}`);
    assert.equal(f!.speakerName, 'Борис');
  }
});

ok('name padding keeps PCM intact across name lengths (alignment)', () => {
  for (const name of ['A', 'AB', 'ABC', 'ABCD', 'ABCDE', 'Дмитрий']) {
    const f = decodeAudioFrame(encodeAudioFrame(3, 42, pcm, name));
    assert.equal(f!.speakerName, name, `name "${name}"`);
    samplesEqual(f!.samples, pcm);
  }
});

ok('frame decodes correctly at a non-zero byteOffset (framed inside a buffer)', () => {
  const inner = encodeAudioFrame(1, 5, pcm, 'Зоя');
  const outer = new Uint8Array(8 + inner.byteLength);
  outer.set(new Uint8Array(inner), 8);                                 // 8-byte prefix
  const f = decodeAudioFrame(outer.buffer, 8, inner.byteLength);
  assert.equal(f!.speakerName, 'Зоя');
  samplesEqual(f!.samples, pcm);
});

// ── legacy/no-name: additive, nothing changes ────────────────────────────────
ok('no-name frame is byte-identical to the legacy format + decodes w/o speakerName', () => {
  const ts = 1_700_000_000_000;
  const buf = encodeAudioFrame(5, ts, pcm);                            // no 4th arg
  assert.equal(buf.byteLength, 12 + pcm.length * 4, 'legacy byte length unchanged');
  const f = decodeAudioFrame(buf);
  assert.equal(f!.speakerIndex, 5);
  assert.equal(f!.ts, ts);
  assert.equal(f!.speakerName, undefined, 'no name on a legacy frame');
  samplesEqual(f!.samples, pcm);
});

ok('empty-string speakerName is treated as no-name (legacy shape)', () => {
  const buf = encodeAudioFrame(5, 1, pcm, '');
  assert.equal(buf.byteLength, 12 + pcm.length * 4);
  assert.equal(decodeAudioFrame(buf)!.speakerName, undefined);
});

// ── in-process path: a sink receives the bound name directly ─────────────────
ok('in-process CaptureV1Sink carries speakerName through tee()', () => {
  const got: AudioChunk[] = [];
  const sink = tee({ audioChunk: (c) => got.push(c), event: () => {}, finalize: () => {} });
  sink.audioChunk({ speakerId: 'spk-2', speakerIndex: 2, samples: pcm, ts: 9, speakerName: 'Галина' });
  assert.equal(got.length, 1);
  assert.equal(got[0].speakerName, 'Галина');
});

// ── bind decision: honest, never a guess ─────────────────────────────────────
ok('pickBoundName: exactly-one-lit ⇒ name; zero or 2+ ⇒ undefined', () => {
  assert.equal(pickBoundName(['Егор']), 'Егор');
  assert.equal(pickBoundName([]), undefined);                          // silence/settling
  assert.equal(pickBoundName(['Анна', 'Борис']), undefined);           // overlap ⇒ ambiguous
});

console.log(`\n✅ capture bind golden: ${pass} checks passed`);
