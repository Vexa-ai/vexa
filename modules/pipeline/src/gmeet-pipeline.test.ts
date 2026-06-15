/**
 * Golden: the gmeet CHANNEL-routed pipeline transcribes overlapping speakers
 * INDEPENDENTLY (separate channels) and names each channel-turn from the glow,
 * bound at onset and held through overlap.
 * Run: npx tsx modules/pipeline/src/gmeet-pipeline.test.ts
 *
 * Proves:
 *  - channel routing  — a channel's audio → a named transcript.v1 segment.
 *  - OVERLAP-safe     — two channels at the same time → two independent named streams.
 *  - onset hold       — the name bound at onset survives a mid-turn glow ambiguity.
 *  - rotation re-bind — a silence gap re-binds the channel to a new speaker.
 *  - UNKNOWN + upgrade — no single glow at onset ⇒ UNKNOWN, upgraded by a confident glow.
 */
import { createGmeetPipeline } from './gmeet-pipeline';
import type { TranscriptSegment, TranscriptSink } from './contracts/transcript-v1';
import type { TranscriptionResult } from './transcription-client';

const assert = {
  ok: (c: unknown, m = 'expected truthy') => { if (!c) throw new Error(m); },
  equal: (a: unknown, b: unknown, m?: string) => { if (a !== b) throw new Error(m ?? `${JSON.stringify(a)} !== ${JSON.stringify(b)}`); },
  deepEqual: (a: unknown, b: unknown, m?: string) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(m ?? `${JSON.stringify(a)} !== ${JSON.stringify(b)}`); },
};
let pass = 0;
const ok = async (name: string, fn: () => Promise<void>) => { await fn(); pass++; console.log(`  ✅ ${name}`); };

const sec = (n = 16000) => new Float32Array(n).fill(0.12);
const fakeTranscribe = (text: string) =>
  async (_pcm: Float32Array, _prompt?: string): Promise<TranscriptionResult> =>
    ({ text, language: 'en', duration: 1, segments: text ? [{ start: 0, end: 1, text }] : [] });

function makeSink() {
  const segments: TranscriptSegment[] = [];
  const drafts: TranscriptSegment[] = [];
  const sink: TranscriptSink = { segment: (s) => segments.push(s), draft: (s) => drafts.push(s), finalize: () => {} };
  return { sink, segments, drafts };
}
const newPipe = (sink: TranscriptSink, gapMs = 800) =>
  createGmeetPipeline({ transcribe: fakeTranscribe('hello world'), sink, config: { submitInterval: 60 }, onsetGapMs: gapMs });

async function main() {
  // ── channel routing + provenance ────────────────────────────────────────────
  await ok('one channel + glow → named glow-bound segment on ch-0', async () => {
    const s = makeSink(); const p = newPipe(s.sink);
    p.feedAudio(0, 'Анна', sec(), 1000);
    p.feedAudio(0, 'Анна', sec(), 1200);
    await p.flush(); await p.dispose();
    assert.equal(s.segments.length, 1);
    assert.equal(s.segments[0].speaker, 'Анна');
    assert.ok(s.segments[0].speakerKey.startsWith('ch-0:'), 'keyed by channel');
    assert.equal(s.segments[0].source, 'glow-bound');
    assert.equal(s.segments[0].topology, 'per-participant');
  });

  // ── the headline: OVERLAP transcribed independently ─────────────────────────
  await ok('OVERLAP: two channels at the SAME time → two independent named streams', async () => {
    const s = makeSink(); const p = newPipe(s.sink);
    p.feedAudio(0, 'Анна', sec(), 1000);     // Anna on ch0 …
    p.feedAudio(1, 'Борис', sec(), 1000);    // … Boris on ch1 AT THE SAME ts (overlap)
    p.feedAudio(0, 'Анна', sec(), 1200);
    p.feedAudio(1, 'Борис', sec(), 1200);
    await p.flush(); await p.dispose();
    assert.deepEqual([...new Set(s.segments.map((g) => g.speaker))].sort(), ['Анна', 'Борис']);
    assert.ok(s.segments.every((g) => g.source === 'glow-bound'), 'both glow-bound');
    // distinct channels, not merged
    assert.ok(s.segments.some((g) => g.speakerKey.startsWith('ch-0:')) && s.segments.some((g) => g.speakerKey.startsWith('ch-1:')));
  });

  // ── onset name HELD through a mid-turn glow ambiguity (the overlap-naming fix) ─
  await ok('name bound at onset is HELD when the glow goes ambiguous mid-turn', async () => {
    const s = makeSink(); const p = newPipe(s.sink);
    p.feedAudio(0, 'Анна', sec(), 1000);     // onset: one glow → Анна
    p.feedAudio(0, undefined, sec(), 1300);  // mid-turn: 2 glows → undefined, but HOLD Анна
    p.feedAudio(0, undefined, sec(), 1600);
    await p.flush(); await p.dispose();
    assert.equal(s.segments.length, 1);
    assert.equal(s.segments[0].speaker, 'Анна', 'held, not flipped to UNKNOWN');
  });

  // ── a silence gap re-binds the channel (rotation) ───────────────────────────
  await ok('a silence gap on a channel re-binds it to a new speaker', async () => {
    const s = makeSink(); const p = newPipe(s.sink);
    p.feedAudio(0, 'Анна', sec(), 1000);
    p.feedAudio(0, 'Анна', sec(), 1200);
    p.feedAudio(0, 'Борис', sec(), 3000);    // 1800ms gap > 800 → new turn → Борис
    p.feedAudio(0, 'Борис', sec(), 3200);
    await p.flush(); await p.dispose();
    assert.deepEqual([...new Set(s.segments.map((g) => g.speaker))].sort(), ['Анна', 'Борис'], 'two turns, two names, one channel');
  });

  // ── a confident glow-name CHANGE re-binds even WITH NO gap (the overlap rotation) ─
  await ok('a glow-name change re-binds a channel with NO silence gap (overlap rotation)', async () => {
    const s = makeSink(); const p = newPipe(s.sink);
    p.feedAudio(0, 'Анна', sec(), 1000);
    p.feedAudio(0, 'Анна', sec(), 1100);
    p.feedAudio(0, 'Борис', sec(), 1200);    // 100ms gap < 800, but glow CHANGED Анна→Борис
    p.feedAudio(0, 'Борис', sec(), 1300);
    await p.flush(); await p.dispose();
    assert.deepEqual([...new Set(s.segments.map((g) => g.speaker))].sort(), ['Анна', 'Борис'], 'gap-less rotation split by the glow change');
  });

  // ── UNKNOWN onset, upgraded by a confident glow ─────────────────────────────
  await ok('no single glow at onset ⇒ UNKNOWN, upgraded when a confident glow appears', async () => {
    const s = makeSink(); const p = newPipe(s.sink);
    p.feedAudio(0, undefined, sec(), 1000);  // onset during overlap → UNKNOWN
    p.feedAudio(0, 'Зоя', sec(), 1200);      // confident single glow early → upgrade
    await p.flush(); await p.dispose();
    assert.equal(s.segments.length, 1);
    assert.equal(s.segments[0].speaker, 'Зоя');
  });

  console.log(`\n✅ gmeet-pipeline golden: ${pass} checks passed`);
}
main().catch((e) => { console.error('❌', e?.message || e); process.exit(1); });
