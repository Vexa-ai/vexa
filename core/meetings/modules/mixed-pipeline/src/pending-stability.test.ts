/**
 * Sibling audit — the mixed (Zoom/Teams) lane's PENDING-draft stability.
 *
 * The per-channel gmeet lane had a flicker: it published two conflicting pending drafts (a tail
 * fragment AND the whole window) under the SAME segment_id per submission, so a consumer that
 * upserts by id rendered "output → lost → reappear". This audits the SIBLING engine for the same
 * class of bug: drive ChunkedTranscriber with a forming (counting) sequence and assert that, per
 * pending segmentId, the text only GROWS — never shrinks to a fragment then regrows.
 *
 *   tsx src/pending-stability.test.ts
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
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const seg = (text: string, start: number, end: number, extra: any = {}): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0, ...extra });
const result = (segs: any[]): TranscriptionResult =>
  ({ text: segs.map((s) => s.text).join(' '), language: 'en', language_probability: 0.99, segments: segs } as any);

interface Pub { speaker: string; pending: { id: string; text: string }[] }

async function harness(script: TranscriptionResult[]) {
  const publishes: Pub[] = [];
  let emit!: (ev: BoundaryEvent) => void;
  let i = 0;
  const record = (_speaker: string, pending: any[]) =>
    publishes.push({ speaker: _speaker, pending: pending.map((p) => ({ id: p.segmentId, text: (p.text || '').trim() })) });
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (_pcm, _prompt) => { const r = script[Math.min(i, script.length - 1)]; i++; return r; },
    publish: (speaker, _confirmed, pending) => record(speaker, pending),
    publishPending: (speaker, pending) => record(speaker, pending),
    clearPending: () => { /* a clear is an intentional empty — not a shrink */ },
    rename: () => {},
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
    log: () => {},
  });
  return {
    tc, publishes,
    emit: (kind: BoundaryEvent['kind'], tMs: number) => emit({ kind, tMs, confidence: 0.9 }),
    feed: (fromSec: number, durSec: number) => { const a = new Float32Array(Math.round(SR * durSec)); a.fill(0.1); tc.feedAudio(a, fromSec * 1000); },
  };
}

async function main() {
  // A single forming utterance (counting), Whisper re-returning the whole growing window each pass —
  // the exact shape that flickered in the gmeet lane.
  const h = await harness([
    result([seg('39, 40, 41, 42', 0, 1.0)]),
    result([seg('39, 40, 41, 42, 43, 44', 0, 1.5)]),
    result([seg('39, 40, 41, 42, 43, 44, 45, 46', 0, 2.0)]),
    result([seg('39, 40, 41, 42, 43, 44, 45, 46, 47, 48', 0, 2.5)]),
  ]);
  h.emit('silence→speaker', 0);
  h.feed(0, 3); await sleep(1200);
  h.feed(3, 3); await sleep(1200);
  h.feed(6, 3); await sleep(1200);
  h.feed(9, 3); await sleep(1200);
  h.emit('speaker→silence', 12000);
  await h.tc.dispose();

  const byId = new Map<string, string[]>();
  for (const p of h.publishes) {
    for (const s of p.pending) {
      if (!s.text) continue;
      if (!byId.has(s.id)) byId.set(s.id, []);
      const arr = byId.get(s.id)!;
      if (arr[arr.length - 1] !== s.text) arr.push(s.text);
    }
  }
  console.log('  pending per segmentId:');
  for (const [id, ts] of byId) console.log('    ', id, '→', JSON.stringify(ts));

  ok(byId.size > 0, 'the forming utterance produced at least one pending segment');
  for (const [id, texts] of byId) {
    for (let k = 1; k < texts.length; k++) {
      const prev = texts[k - 1], cur = texts[k];
      ok(
        cur === prev || cur.startsWith(prev),
        `[${id}] pending grows monotonically (no full↔fragment flicker): ${JSON.stringify(prev)} → ${JSON.stringify(cur)}`,
      );
    }
  }

  console.log(`\n✅ mixed pending-stability: ${checks} checks passed`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
