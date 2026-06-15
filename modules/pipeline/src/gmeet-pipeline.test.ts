/**
 * Golden: the gmeet per-speaker pipeline routes capture.v1 audio by the BOUND
 * glow name (not the channel), emits named transcript.v1 directly, and never
 * drops or guesses. Run: npx tsx modules/pipeline/src/gmeet-pipeline.test.ts
 *
 * Proves touch #2:
 *  - per-name routing   — a name's audio → a transcript.v1 segment under that name.
 *  - channel-invariance — the channel index is not even an input; two feeds for one
 *                         name (i.e. a rotated channel) stay ONE stream.
 *  - provenance         — named ⇒ source 'glow-bound', confidence 1, per-participant.
 *  - UNKNOWN            — no bound name ⇒ 'Speaker', still transcribed, never guessed.
 *  - draft channel      — the forming tail rides transcript.v1 draft() under the name.
 */
import { createGmeetPipeline, streamKeyFor } from './gmeet-pipeline';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1';
import type { TranscriptionResult } from './transcription-client';

// Local assert (this module's isolation gate + CJS output disallow node:assert).
const assert = {
  ok: (c: unknown, m = 'expected truthy') => { if (!c) throw new Error(m); },
  equal: (a: unknown, b: unknown, m?: string) => { if (a !== b) throw new Error(m ?? `${JSON.stringify(a)} !== ${JSON.stringify(b)}`); },
  deepEqual: (a: unknown, b: unknown, m?: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(m ?? `${JSON.stringify(a)} !== ${JSON.stringify(b)}`); },
};

let pass = 0;
const ok = async (name: string, fn: () => void | Promise<void>) => { await fn(); pass++; console.log(`  ✅ ${name}`); };

const sec = (n = 16000) => new Float32Array(n).fill(0.12);            // 1s of (non-silent) audio
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const fakeTranscribe = (text: string) =>
  async (_pcm: Float32Array, _prompt?: string): Promise<TranscriptionResult> =>
    ({ text, language: 'en', duration: 1, segments: text ? [{ start: 0, end: 1, text }] : [] });

function makeSink() {
  const segments: TranscriptSegment[] = [];
  const drafts: TranscriptSegment[] = [];
  let finalized = false;
  const sink: TranscriptSink = {
    segment: (s) => segments.push(s),
    draft: (s) => drafts.push(s),
    finalize: () => { finalized = true; },
  };
  return { sink, segments, drafts, isFinal: () => finalized };
}

const newPipe = (sink: TranscriptSink, text = 'hello world') =>
  createGmeetPipeline({ transcribe: fakeTranscribe(text), sink, config: { submitInterval: 60 } });

async function main() {
  // ── pure routing ───────────────────────────────────────────────────────────
  await ok('streamKeyFor: name ⇒ name; empty/undefined ⇒ UNKNOWN', () => {
    assert.equal(streamKeyFor('Анна', 'Speaker'), 'Анна');
    assert.equal(streamKeyFor(undefined, 'Speaker'), 'Speaker');
    assert.equal(streamKeyFor('   ', 'Speaker'), 'Speaker');
  });

  // ── per-name routing + provenance + channel rotation invariance ────────────
  await ok('one name → one named glow-bound segment, regardless of channel', async () => {
    const s = makeSink();
    const p = newPipe(s.sink);
    p.feedAudio('Анна', sec(), 1000);        // "channel 0"
    p.feedAudio('Анна', sec(), 2000);        // "channel 2" — rotation; channel isn't even passed
    await p.flush();
    await p.dispose();
    assert.equal(s.segments.length, 1, 'exactly one segment for one name');
    const seg = s.segments[0];
    assert.equal(seg.speaker, 'Анна');
    assert.equal(seg.speakerKey, 'Анна');
    assert.equal(seg.source, 'glow-bound');
    assert.equal(seg.confidence, 1);
    assert.equal(seg.topology, 'per-participant');
    assert.equal(seg.text, 'hello world');
    assert.ok(s.isFinal(), 'finalize() called on dispose');
  });

  // ── independent streams per name ───────────────────────────────────────────
  await ok('two names → two independent named streams', async () => {
    const s = makeSink();
    const p = newPipe(s.sink);
    p.feedAudio('Анна', sec(), 1000);
    p.feedAudio('Борис', sec(), 1500);       // interleaved — different person, different stream
    p.feedAudio('Анна', sec(), 2000);
    await p.flush();
    await p.dispose();
    const speakers = [...new Set(s.segments.map((g) => g.speaker))].sort();
    assert.deepEqual(speakers, ['Анна', 'Борис']);
    assert.ok(s.segments.every((g) => g.source === 'glow-bound'), 'all glow-bound');
  });

  // ── UNKNOWN: no bound name still transcribed, never guessed ─────────────────
  await ok('no bound name ⇒ UNKNOWN stream, transcribed but provisional', async () => {
    const s = makeSink();
    const p = newPipe(s.sink);
    p.feedAudio(undefined, sec(), 1000);     // 0 or 2+ lit ⇒ no name
    await p.flush();
    await p.dispose();
    assert.equal(s.segments.length, 1);
    assert.equal(s.segments[0].speaker, 'Speaker');
    assert.equal(s.segments[0].source, 'provisional-cluster-id');
    assert.equal(s.segments[0].confidence, 0);
  });

  // ── draft channel carries the forming tail under the name ──────────────────
  await ok('forming tail rides transcript.v1 draft() under the name (real submit)', async () => {
    const s = makeSink();
    // A short submit interval lets the manager's trySubmit fire a NON-idle
    // submission — the path that emits the LocalAgreement pending tail (flush() is idle).
    const p = createGmeetPipeline({
      transcribe: fakeTranscribe('alpha beta gamma'),
      sink: s.sink,
      config: { submitInterval: 0.05, minAudioDuration: 0.1, confirmThreshold: 2 },
    });
    p.feedAudio('Зоя', sec(), 1000);
    await wait(140);                         // let trySubmit fire + the async transcribe settle
    await p.dispose();
    assert.ok(s.drafts.length >= 1, 'at least one draft emitted');
    const d = s.drafts[0];
    assert.equal(d.speaker, 'Зоя');
    assert.equal(d.speakerKey, 'Зоя');
    assert.equal(d.source, 'glow-bound');
    assert.equal(d.confidence, 0);
  });

  console.log(`\n✅ gmeet-pipeline golden: ${pass} checks passed`);
}

main().catch((e) => { console.error('❌', e?.message || e); process.exit(1); });
