/**
 * Golden — the LocalAgreement-3 confirm loop (characterization).
 *
 * Pins the confirm/pending/prompt/id behavior of ChunkedTranscriber's inner loop
 * — the logic that gets extracted into @vexa/transcribe-buffer. Fully model-free:
 * a SCRIPTED stub stands in for Whisper (we control exactly what each submission
 * "transcribes" and record the prompt it was called with), and an injected
 * segmenter owns the turn lifecycle. This same golden must pass before AND after
 * the buffer extraction → proof the behavior is unchanged.
 *
 *   tsx src/confirm-loop.golden.test.ts
 */
import { ChunkedTranscriber, type BoundarySource } from './chunked-transcriber.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SR = 16000;
let checks = 0;
function ok(cond: boolean, msg: string): void { if (!cond) throw new Error(`assertion failed: ${msg}`); console.log(`  ✅ ${msg}`); checks++; }
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const seg = (text: string, start: number, end: number, extra: any = {}): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0, ...extra });
const result = (segs: any[]): TranscriptionResult =>
  ({ text: segs.map((s) => s.text).join(' '), language: 'en', language_probability: 0.99, segments: segs } as any);

interface Harness {
  tc: ChunkedTranscriber;
  emit: (kind: BoundaryEvent['kind'], tMs: number) => void;
  feed: (fromSec: number, durSec: number) => void;
  prompts: (string | undefined)[];
  publishes: { speaker: string; confirmed: { id: string; text: string }[]; pending: string[] }[];
}

async function harness(script: TranscriptionResult[]): Promise<Harness> {
  const prompts: (string | undefined)[] = [];
  const publishes: Harness['publishes'] = [];
  let emit!: (ev: BoundaryEvent) => void;
  let i = 0;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (_pcm, prompt) => { prompts.push(prompt); const r = script[Math.min(i, script.length - 1)]; i++; return r; },
    publish: (speaker, confirmed, pending) =>
      publishes.push({ speaker, confirmed: confirmed.map((c) => ({ id: c.segmentId, text: c.text })), pending: pending.map((p) => p.text) }),
    publishPending: (speaker, pending) =>
      publishes.push({ speaker, confirmed: [], pending: pending.map((p) => p.text) }),
    clearPending: (speaker) => publishes.push({ speaker, confirmed: [], pending: [] }),
    rename: () => {},
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
    log: () => {},
  });
  return {
    tc,
    emit: (kind, tMs) => emit({ kind, tMs, confidence: 0.9 }),
    feed: (fromSec, durSec) => { const a = new Float32Array(Math.round(SR * durSec)); a.fill(0.1); tc.feedAudio(a, fromSec * 1000); },
    prompts, publishes,
  };
}

async function main() {
  // ── Scenario A: LocalAgreement-3 across three open-turn submissions + close ──
  // "This is Anna" must be stable across THREE consecutive submissions before it
  // confirms (2 passes is no longer enough).
  const h = await harness([
    result([seg('This is Anna', 0, 1.0)]),                                   // submit 1
    result([seg('This is Anna', 0, 1.0), seg('speaking', 1.0, 1.6)]),        // submit 2 (prefix agrees, 2 passes)
    result([seg('This is Anna', 0, 1.0), seg('speaking now', 1.0, 2.0)]),    // submit 3 → 3 passes → confirm
    result([seg('speaking now about the plan', 0, 2.2)]),                    // close
  ]);
  h.emit('silence→speaker', 0);
  h.feed(0, 3); await sleep(1200);   // tick → submit 1
  h.feed(3, 3); await sleep(1200);   // tick → submit 2
  h.feed(6, 3); await sleep(1200);   // tick → submit 3 (LocalAgreement-3 confirms "This is Anna")
  h.emit('speaker→silence', 9000);
  await h.tc.dispose();

  console.log('PROMPTS:', JSON.stringify(h.prompts));
  console.log('PUBLISHES:', JSON.stringify(h.publishes, null, 0));

  const confirmedTexts = h.publishes.flatMap((p) => p.confirmed.map((c) => c.text));
  const confirmedIds = h.publishes.flatMap((p) => p.confirmed.map((c) => c.id));
  const firstConfirmAt = h.publishes.findIndex((p) => p.confirmed.length > 0);

  ok(h.publishes.length > 0, 'the loop published something');
  ok(firstConfirmAt > 0 && h.publishes.slice(0, firstConfirmAt).every((p) => p.confirmed.length === 0),
    'first submission confirms nothing (no prior pass to agree with)');
  ok(confirmedTexts.includes('This is Anna'),
    'the stable word-prefix confirms ("This is Anna")');
  ok(confirmedIds.every((id) => /^turn:\d+:\d+$/.test(id)) && new Set(confirmedIds).size === confirmedIds.length,
    'confirmed segment ids are stable turn:N:seq and unique');
  ok(h.prompts.some((p) => (p || '').includes('This is Anna')),
    'prompt chaining: a later transcribe call carries the confirmed tail');
  ok((h.publishes[h.publishes.length - 1].pending.length === 0),
    'on close, pending is cleared');

  // ── Scenario B: a low-confidence segment is dropped by the gates ──
  // The mixed engine gates on faster-whisper confidence (no_speech / avg_logprob /
  // compression), NOT a phrase list — so a high-no_speech, low-logprob segment is junk.
  const lowConf = { no_speech_prob: 0.9, avg_logprob: -1.5 };
  const hb = await harness([
    result([seg('ghost over silence', 0, 1.0, lowConf)]),
    result([seg('ghost over silence', 0, 1.0, lowConf)]),
    result([seg('ghost over silence', 0, 1.0, lowConf)]),
  ]);
  hb.emit('silence→speaker', 0);
  hb.feed(0, 3); await sleep(1200);
  hb.feed(3, 3); await sleep(1200);
  hb.emit('speaker→silence', 6000);
  await hb.tc.dispose();
  const hbConfirmed = hb.publishes.flatMap((p) => p.confirmed.map((c) => c.text));
  console.log('B confirmed:', JSON.stringify(hbConfirmed));
  ok(!hbConfirmed.includes('ghost over silence'),
    'a low-confidence segment is dropped by the gates (never confirmed)');

  console.log(`\n✅ confirm-loop golden: ${checks} checks passed`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
