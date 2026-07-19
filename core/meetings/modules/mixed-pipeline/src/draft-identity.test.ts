/**
 * A draft must CONFIRM UNDER ITS OWN ID — otherwise every confirmation leaves its draft behind as
 * a second, permanent row.
 *
 * The store upserts on `(meeting_id, segment_id)` (`ix_transcription_meeting_segment`), so a
 * segment_id IS the identity of a line of transcript: re-publishing under the same id repaints it,
 * publishing under a new id appends. The mixed lane published its forming tail under
 * `turn:N:p<i>` — indexed off the UNCONFIRMED slice, so it also renumbered as the turn advanced —
 * and the confirmation under `turn:N:<seq>`. Two different identities for the same words, so the
 * draft row was never replaced and the reader saw every sentence twice:
 *
 *     turn:54:p0 | происходящего.        turn:54:0 | происходящего.
 *     turn:54:p1 | И, мало того, …       turn:54:1 | И, мало того, …
 *
 * (Observed in a real Jitsi meeting: 6 stored rows for 3 spoken sentences.) The gmeet lane already
 * keeps one identity across draft→confirm; this pins the same invariant for the mixed lane.
 *
 * The check models the CONSUMER rather than the wire: every published segment is folded into a
 * map keyed by segment_id, last-write-wins — exactly `upsert_segments` — and the resulting store
 * must not hold the same words twice.
 *
 *   tsx src/draft-identity.test.ts
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
  // The store, modelled: segment_id → text, last write wins.
  const store = new Map<string, string>();
  const keep = (segs: any[]): void => {
    for (const s of segs ?? []) if (s?.segmentId) store.set(s.segmentId, (s.text || '').trim());
  };

  let emit!: (ev: BoundaryEvent) => void;
  let i = 0;
  // Whisper re-returns the whole window, one segment per finished sentence — the real shape. The
  // leading sentences stay stable, so LocalAgreement confirms them while the tail still forms.
  const SCRIPT = [
    result([seg('one two', 0, 1)]),
    result([seg('one two', 0, 1), seg('three four', 1, 2)]),
    result([seg('one two', 0, 1), seg('three four', 1, 2), seg('five six', 2, 3)]),
    result([seg('one two', 0, 1), seg('three four', 1, 2), seg('five six', 2, 3), seg('seven eight', 3, 4)]),
    result([seg('one two', 0, 1), seg('three four', 1, 2), seg('five six', 2, 3), seg('seven eight', 3, 4)]),
  ];

  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => SCRIPT[Math.min(i++, SCRIPT.length - 1)],
    publish: (_s, confirmed, pending) => { keep(confirmed); keep(pending); },
    publishPending: (_s, pending) => { keep(pending); },
    clearPending: () => { /* a clear removes nothing durable — the row stays */ },
    rename: () => { /* */ },
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => { /* */ }, reset() { /* */ } }); },
    log: () => { /* */ },
  });

  emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
  for (let k = 0; k < 5; k++) {
    const a = new Float32Array(SR * 3); a.fill(0.1);
    tc.feedAudio(a, k * 3000);
    await sleep(1200);
  }
  emit({ kind: 'speaker→silence', tMs: 15000, confidence: 0.9 });
  await tc.dispose();

  console.log('  stored rows (segment_id → text):');
  for (const [id, t] of store) console.log(`    ${id.padEnd(16)} ${JSON.stringify(t)}`);

  ok(store.size > 0, 'the turn stored at least one segment');

  // The defect, stated as the reader sees it: the same words present under two identities.
  const byText = new Map<string, string[]>();
  for (const [id, t] of store) {
    if (!t) continue;
    if (!byText.has(t)) byText.set(t, []);
    byText.get(t)!.push(id);
  }
  const doubled = [...byText.entries()].filter(([, ids]) => ids.length > 1);
  ok(doubled.length === 0,
    `no sentence is stored twice under two segment_ids (doubled: ${JSON.stringify(doubled)})`);

  // …and the mechanism behind it: a draft id must be an id the turn actually confirms under.
  const draftish = [...store.keys()].filter((id) => /:p\d+$/.test(id));
  ok(draftish.length === 0,
    `no draft-only identity survives in the store (found: ${JSON.stringify(draftish)})`);

  console.log(`\n✅ draft-identity: ${checks} checks passed — a mixed-lane draft confirms under its own segment_id, so the store holds one row per sentence.`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
