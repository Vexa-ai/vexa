/**
 * FIRST-SUBMIT FAST PATH — a fresh turn's first window must not wait a full SUBMIT_TICK_MS.
 *
 * The mixed lane opens a new turn on every handover/overlap boundary. Before this fix, a turn that
 * had never submitted waited for the 2s tick condition (`latestAudio − confirmedUpTo ≥ SUBMIT_TICK_MS`)
 * before its FIRST window went to Whisper — up to 2s of dead air before any draft, paid on EVERY turn.
 * With handover churn that per-turn wait dominates draft latency. The fix (port of the gmeet lane's
 * #851 fix b) releases the first window the moment MIN_SUBMIT_MS (0.8s) has accrued.
 *
 * The RED→GREEN discriminator is the FIRST submitted window's SPAN, which is timing-independent:
 *   • OLD: first submit waits for ~2.0s to accrue  → first window ≈ SUBMIT_TICK_MS/1000 ≈ 2.0s
 *   • NEW: first submit fires at MIN_SUBMIT_MS      → first window ≈ 0.8–1.0s
 * We feed exactly one MIN_SUBMIT-sized window of audio and assert the first real Whisper call asked
 * about ≈ that, not a full tick's worth.
 *
 *   tsx src/first-submit-fastpath.test.ts
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
const seg = (text: string, start: number, end: number): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0 });
const result = (segs: any[]): TranscriptionResult =>
  ({ text: segs.map((s) => s.text).join(' '), language: 'en', language_probability: 0.99, segments: segs } as any);

async function main(): Promise<void> {
  const submittedSpansSec: number[] = [];
  let emit!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm: Float32Array): Promise<TranscriptionResult> => {
      submittedSpansSec.push(pcm.length / SR);
      return result([seg('one two three', 0, pcm.length / SR)]);
    },
    publish: () => {},
    publishPending: () => {},
    clearPending: () => {},
    rename: () => {},
    makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
    log: () => {},
  });

  // This is NOT the first turn (the first turn back-extends to session start, which would inflate
  // the opening window and mask the tick wait). A real speaker handover closes the warm-up turn and
  // opens the turn under test at t=1s, exactly as a mid-meeting handover does.
  const frame = (fromSec: number, durSec: number): void => {
    const a = new Float32Array(Math.round(SR * durSec)); a.fill(0.12);
    tc.feedAudio(a, fromSec * 1000);
  };
  emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
  frame(0, 1.0);
  await sleep(1300);
  emit({ kind: 'speaker→speaker', tMs: 1000, confidence: 0.9 });
  await sleep(300);

  // THE TURN UNDER TEST is already open at t=1.0s. Feed exactly one MIN_SUBMIT-sized window
  // (0.9s) and stop. Record how large the FIRST window submitted for THIS turn is.
  const before = submittedSpansSec.length;
  // Let the open's own (once-per-drain) submit run against an EMPTY window first — exactly as a live
  // handover behaves, where the new turn's audio arrives over the following seconds, not all at the
  // boundary instant. Without this settle the open's submit grabs the frame through a microtask gap
  // and both code paths look identical; with it, the first real window is decided by the heartbeat.
  await sleep(50);
  frame(1.0, 0.9);             // 0.9s ≥ MIN_SUBMIT_MS(0.8s), well below SUBMIT_TICK_MS(2.0s)
  // Wait past two 1s heartbeats. No more audio is fed, so <SUBMIT_TICK_MS of NEW audio ever exists:
  // the tick-gated path submits NOTHING here; only the fast path releases this window.
  await sleep(1600);

  const firstSpan = submittedSpansSec[before] ?? Infinity;
  console.log(`  first window for the turn under test: ${firstSpan.toFixed(2)}s (MIN_SUBMIT=0.80s, SUBMIT_TICK=2.00s)`);

  ok(submittedSpansSec.length > before, 'the turn under test submitted its first window without more audio arriving');
  ok(firstSpan < 1.5, `first window ≈ MIN_SUBMIT, not a full tick (${firstSpan.toFixed(2)}s < 1.5s) — RED on tick-gated code (~2.0s)`);

  await tc.dispose();
  console.log(`\n✅ first-submit fast path: ${checks} checks passed`);
}

main().catch((e) => { console.error('❌', e); process.exit(1); });
