/**
 * hint-cut.smoke — on the pyannote spine, a start-hint for a NEW name cuts the open turn at the
 * hint's onset, so a speaker who takes over without a pause gets their own turn instead of the
 * tail of the previous speaker's (a Jitsi call: the takeover's first words closed a turn named for
 * the speaker who had just stopped). Off by default; same tape, no cut → one turn.
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function run(hintCutsTurns: boolean): Promise<{ turns: number; cuts: number; windows: Array<[number, number]> }> {
  let emit!: (ev: BoundaryEvent) => void;
  const windows: Array<[number, number]> = [];
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    hintCutsTurns,
    transcribe: async (pcm) => {
      const secs = pcm.length / 16000;
      const text = `w${windows.length}`;
      windows.push([Math.round(secs * 10) / 10, 0]);
      return { text, language: 'en', language_probability: 0.99, duration: secs,
        segments: [{ text, start: 0, end: secs, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any] };
    },
    publish: () => {}, publishPending: () => {}, clearPending: () => {}, rename: () => {},
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => { emit = onBoundary; return { appendFrame: async () => {}, reset: () => {} }; },
    log: () => {},
  });
  const frame = new Float32Array(1600).fill(0.05);
  const feed = (from: number, to: number) => { for (let t = from; t < to; t += 100) tc.feedAudio(frame, t); };

  // One continuous stretch of audio 1.0–9.0 s: the segmenter opens at 1.0 s and never cuts (same
  // voice, no pause). Anna is lit from 1.0 s; Boris's tile lights at 5.25 s (onset 5.0 s + lag).
  emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });
  feed(1000, 3000);
  tc.recordHint('Anna', 'dom-active', 1000);
  feed(3000, 5300);
  tc.recordHint('Anna', 'dom-active', 3000);         // heartbeat: same name, never a cut
  tc.recordHint('Anna', 'dom-active', 5200, true);
  tc.recordHint('Boris', 'dom-active', 5250);
  feed(5300, 9000);
  tc.recordHint('Boris', 'dom-active', 7250);        // heartbeat
  emit({ tMs: 9000, kind: 'speaker→silence', confidence: 0.9 });
  await sleep(300);
  await tc.dispose();
  const st = tc.stats();
  return { turns: st.turns, cuts: st.hintCuts, windows };
}

async function main() {
  const off = await run(false);
  const on = await run(true);
  console.log(`off: turns=${off.turns} cuts=${off.cuts}   on: turns=${on.turns} cuts=${on.cuts}`);
  const ok = off.cuts === 0 && off.turns === 1 && on.cuts === 1 && on.turns === 2;
  console.log(ok
    ? '✅ PASS hint-cut: a new name\'s start-hint cut the open turn at its onset; off by default it did not'
    : '❌ FAIL hint-cut: expected off → 1 turn/0 cuts, on → 2 turns/1 cut');
  process.exit(ok ? 0 : 1);
}
main().catch((e) => { console.error(e?.message || e); process.exit(1); });
