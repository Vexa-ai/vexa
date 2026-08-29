/**
 * claim-contested.smoke — the late-box claim must follow the RECORD, not the race.
 *
 * `claim.smoke` pins the uncontested case: a turn ends before any tile lit, the box lights
 * afterwards, and the turn is claimed for that speaker. This file pins the case the first real
 * Teams tape exposed — a turn during which tiles WERE lit, but not decisively.
 *
 * The claim used to go to whichever name's hint happened to fire next, with no requirement that
 * the name was lit during the turn at all. On Teams the tile lights on NOISE, so a participant
 * TYPING emits hints continuously and won that race nearly every time: m24 labelled a balanced
 * two-speaker conversation Jacob 65 : Dmitry 1, and replaying the tape shows 42 of those labels
 * arriving through this path. Naming a turn by who happened to make a keystroke is fabrication.
 *
 * Now the claim reads the accumulated lit-time over the turn's own window and requires one name
 * to hold a clear plurality of it. When both tiles were lit through the turn — the genuinely
 * ambiguous case a single mixed Teams stream cannot resolve — the turn stays "Speaker".
 */
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** Run one turn over [1000, 3000] with the given hints, then fire `claimant`'s hint at 7000
 *  (past the window-match reach, inside the claim window). Returns the renames that resulted. */
async function runTurn(during: { name: string; tMs: number; isEnd?: boolean }[], claimant: string):
    Promise<{ renames: { speaker: string }[]; finalNames: string[] }> {
  const renames: { speaker: string }[] = [];
  const published: string[] = [];
  let emit!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async () => ({
      text: 'hello world this is a test', language: 'en', language_probability: 0.99, duration: 2,
      segments: [{ text: 'hello world this is a test', start: 0, end: 2, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any],
    }),
    publish: (speaker) => { published.push(speaker); }, publishPending: () => {}, clearPending: () => {},
    rename: (_o, newS) => { renames.push({ speaker: newS }); published.push(newS); },
    makeSegmenter: async (onBoundary): Promise<BoundarySource> => { emit = onBoundary; return { appendFrame: async () => {}, reset: () => {} }; },
    log: () => {},
  });
  const frame = new Float32Array(1600).fill(0.05);
  for (let t = 1000; t < 3000; t += 100) tc.feedAudio(frame, t);
  emit({ tMs: 1000, kind: 'silence→speaker', confidence: 0.9 });
  for (const h of during) tc.recordHint(h.name, 'dom-active', h.tMs, h.isEnd);
  await sleep(40);
  emit({ tMs: 3000, kind: 'speaker→silence', confidence: 0.9 });
  await sleep(60);
  tc.recordHint(claimant, 'dom-active', 7000);
  await sleep(40);
  await tc.dispose();
  return { renames, finalNames: [...new Set(published)] };
}

async function main(): Promise<void> {
  // ── BOTH tiles lit through the turn (Jacob typing while Dmitry speaks). Neither holds a
  // plurality, so no name may be claimed — the turn stays provisional.
  {
    const { renames } = await runTurn([
      { name: 'Dmitry', tMs: 1000 }, { name: 'Jacob', tMs: 1000 },
      { name: 'Dmitry', tMs: 2000 }, { name: 'Jacob', tMs: 2000 },
      { name: 'Dmitry', tMs: 3000, isEnd: true }, { name: 'Jacob', tMs: 3000, isEnd: true },
    ], 'Jacob');
    check('two tiles lit through the turn → NOT claimed by whoever fired next',
      renames.length === 0, JSON.stringify(renames));
  }

  // ── One tile dominant during the turn, and the OTHER name's hint fires afterwards. The claim
  // must follow the evidence (Dmitry), not the trigger (Jacob).
  {
    const { finalNames } = await runTurn([
      { name: 'Dmitry', tMs: 1000 }, { name: 'Dmitry', tMs: 2000 }, { name: 'Dmitry', tMs: 3000, isEnd: true },
    ], 'Jacob');
    check('a turn the record says was Dmitry never ends up under the firing hint (Jacob)',
      finalNames.includes('Dmitry') && !finalNames.includes('Jacob'), JSON.stringify(finalNames));
  }
}

void main().then(() => {
  if (failed) { console.error(`\n❌ claim-contested: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ claim-contested: the late-box claim follows the accumulated hint record; a contested turn stays unknown.');
}).catch((e) => { console.error('❌ claim-contested —', e?.message || e); process.exit(1); });
