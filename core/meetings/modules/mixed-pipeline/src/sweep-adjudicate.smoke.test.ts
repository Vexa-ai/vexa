/**
 * sweep-adjudicate.smoke — a held short-UI-switch turn is adjudicated from the
 * ALREADY-recorded hint log by the sweep (audio-clock + once at dispose), never
 * left provisional forever. Pins the pass-six fix (#868): under hint-leads-turn
 * interleaving a hold could never see a FUTURE re-assertion, so a real speaker held
 * on a sub-second alternation stayed seg_N for good. The sweep re-judges each held
 * turn against recorded testimony: ≥2000ms lit around the turn ⇒ a speaker (claim);
 * one short slice ⇒ a flip (hold stands).
 *
 * Two arms, each its own transcriber:
 *  A. RICH: Boris is held after Alice, but Boris's DOM lit ~2.5s around the turn →
 *     the sweep at dispose repaints it to Boris.  Pre-fix (23f6841b): no sweep → the
 *     turn stays provisional, Boris never published.
 *  B. LONE FLIP: Carol is held after Alice with a single ~0.5s blip → the sweep
 *     leaves the hold standing (the steal class the hold exists for). Same on both.
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function runArm(
  richArm: boolean,
): Promise<{ published: string[]; renames: { from: string; to: string }[] }> {
  let emit!: (ev: BoundaryEvent) => void;
  let call = 0;
  const published: string[] = [];
  const renames: { from: string; to: string }[] = [];
  const second = richArm ? 'Boris' : 'Carol';

  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => {
      call++;
      const text = call === 1 ? 'alice opens the meeting here' : 'the very short second turn';
      return {
        text, language: 'en', language_probability: 0.99,
        segments: [{ text, start: 0, end: call === 1 ? 4.0 : 1.6, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
      };
    },
    publish: (speaker) => { published.push(speaker); },
    publishPending: () => {},
    clearPending: () => {},
    rename: (from, to) => { renames.push({ from, to }); },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => { emit = onBoundary; return { appendFrame: async () => {}, reset: () => {} }; },
    log: () => {},
  });

  const frame = new Float32Array(1600).fill(0.05);
  const feed = (from: number, to: number) => { for (let t = from; t < to; t += 100) tc.feedAudio(frame, t); };

  // Turn 1 — Alice, a normal 4s turn: publishes as Alice (no prior speaker → no defer).
  feed(1000, 5000);
  tc.recordHint('Alice', 'dom-active', 1000);
  emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });
  emit({ tMs: 5000, kind: 'speaker→silence', confidence: 0.9 });
  await sleep(200);

  // Turn 2 — a SHORT turn right after Alice, window-matching the second speaker →
  // held provisional (short-UI-switch). Its DOM testimony differs by arm.
  if (richArm) {
    // Boris lit continuously ~2.5s around the turn — a real speaker's heartbeats.
    tc.recordHint('Boris', 'dom-active', 5050);
    feed(5200, 7000);
    emit({ tMs: 5200, kind: 'silence→speaker', confidence: 0.9 });
    emit({ tMs: 7000, kind: 'speaker→silence', confidence: 0.9 });
    tc.recordHint('Boris', 'dom-active', 7550, true);   // close the ~2.5s Boris slice
  } else {
    // Carol lit a single ~0.5s blip — a transient tile flip.
    tc.recordHint('Carol', 'dom-active', 5150);
    feed(5200, 6000);
    emit({ tMs: 5200, kind: 'silence→speaker', confidence: 0.9 });
    emit({ tMs: 6000, kind: 'speaker→silence', confidence: 0.9 });
    tc.recordHint('Carol', 'dom-active', 5650, true);   // close the 0.5s blip
  }
  await sleep(200);
  // more audio so the audio-clock advances well past the re-assertion window
  feed(7200, 16000);
  await sleep(200);

  await tc.dispose();   // sweep(∞) runs here
  return { published, renames };
}

async function main() {
  const rich = await runArm(true);
  const flip = await runArm(false);
  console.log('RICH  published=', JSON.stringify(rich.published), 'renames=', JSON.stringify(rich.renames));
  console.log('FLIP  published=', JSON.stringify(flip.published), 'renames=', JSON.stringify(flip.renames));

  const richRepainted = rich.renames.some((r) => r.to === 'Boris') || rich.published.includes('Boris');
  const flipStayedHeld = !flip.renames.some((r) => r.to === 'Carol') && !flip.published.includes('Carol');
  const ok = richRepainted && flipStayedHeld;
  console.log(ok
    ? '✅ PASS — the sweep repainted the rich-testimony hold to Boris; the lone flip stayed held'
    : `❌ FAIL — richRepainted=${richRepainted} (want true), flipStayedHeld=${flipStayedHeld} (want true)`);
  process.exit(ok ? 0 : 1);
}
main().catch((e) => { console.error('❌ FAIL —', e?.message || e); process.exit(1); });
