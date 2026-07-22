/**
 * A pass that segments into FEWER pieces than the draft it replaces must RETIRE the ids it no
 * longer writes.
 *
 * draft-identity.test.ts pins the other half: a draft and its confirmation share one segment_id,
 * so the confirmation repaints the draft in place. That holds only while the two passes agree on
 * how many pieces there are — and Whisper re-segments as its window grows, routinely answering
 * with one sentence where the previous pass gave two. The lower id repaints; the higher one is
 * never written again, and the consumer (upsert on segment_id, last write wins) keeps the orphan
 * half-sentence forever, beside the whole sentence that contains it:
 *
 *     turn:38:0 | who owns this after launch because that part is still unclear to me
 *     turn:38:1 | because that part is still unclear          ← nothing will ever overwrite this
 *
 * (Observed on the flat fixture: two such orphans, 13 duplicated words.) An empty-text row drops
 * the draft — transcript.v1's draft contract, and the same instrument the per-channel lane uses to
 * finalize its own drafts.
 *
 *   tsx src/draft-resegment.test.ts
 */
import { ChunkedTranscriber, type BoundarySource } from './chunked-transcriber.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const SR = 16000;
let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
const seg = (text: string, start: number, end: number): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0 });
const result = (segs: any[]): TranscriptionResult =>
  ({ text: segs.map((s) => s.text).join(' '), language: 'en', language_probability: 0.99, segments: segs } as any);

async function main(): Promise<void> {
  /** The consumer: segment_id → text, last write wins. An empty write drops the row. */
  const store = new Map<string, string>();
  const keep = (segs: any[]): void => {
    for (const s of segs ?? []) if (s?.segmentId) store.set(s.segmentId, (s.text || '').trim());
  };

  let emit!: (ev: BoundaryEvent) => void;
  let i = 0;
  // Two drafting passes split the utterance in two; the closing pass hears it as one sentence.
  const SCRIPT = [
    result([seg('who owns this after launch', 0, 2), seg('because that part is unclear', 2, 4)]),
    result([seg('who owns this after launch', 0, 2), seg('because that part is unclear', 2, 4)]),
    result([seg('who owns this after launch because that part is unclear', 0, 4)]),
  ];

  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => SCRIPT[Math.min(i++, SCRIPT.length - 1)],
    publish: (_s, confirmed, pending) => { keep(confirmed); keep(pending); },
    publishPending: (_s, pending) => { keep(pending); },
    clearPending: () => { /* a speaker-level clear removes nothing durable — the row stays */ },
    rename: () => { /* */ },
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => { /* */ }, reset() { /* */ } }); },
    log: () => { /* */ },
  });

  emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
  for (let k = 0; k < 2; k++) {
    const a = new Float32Array(SR * 2); a.fill(0.1);
    tc.feedAudio(a, k * 2000);
    await sleep(1200);
  }
  emit({ kind: 'speaker→silence', tMs: 4000, confidence: 0.9 });
  await tc.dispose();

  console.log('  stored rows (segment_id → text):');
  for (const [id, t] of store) console.log(`    ${id.padEnd(14)} ${JSON.stringify(t)}`);

  const rows = [...store.entries()].filter(([, t]) => t);
  ok(rows.length > 0, 'the turn stored at least one segment');
  const text = rows.map(([, t]) => t).join(' ');
  ok(!/because that part is unclear.*because that part is unclear/.test(text)
    && rows.filter(([, t]) => t === 'because that part is unclear').length === 0,
    `the orphan half-sentence is gone from the store (read: ${JSON.stringify(text)})`);
  ok(rows.some(([, t]) => t.includes('who owns this after launch because that part is unclear')),
    'the whole sentence is what the reader ends up with');

  console.log(`\n✅ draft-resegment: ${checks} checks passed — a re-segmenting confirmation retires the draft ids it no longer writes.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
