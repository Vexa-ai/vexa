/**
 * hallucination-gate — pins WHAT the mixed lane suppresses and, more importantly, what it
 * must never touch.
 *
 * The negative control is not invented: every string in REAL below is a row the lane actually
 * published on the m24 Teams tape (2 speakers, real STT), and the gmeet lane's phrase list
 * would have deleted two of them ("Yeah.", "I don't know."). A gate that eats a meeting's
 * backchannel is worse than the hallucination it removes, because the mixed lane is one
 * stream with nothing to re-derive the words from.
 *
 * The positives are rows the lane published on the 270s clean YouTube fixture that the same
 * audio, transcribed straight through, never produced.
 *
 *   tsx src/hallucination-gate.test.ts
 */
import { hallucinationRule, teamsWindowHallucinationRule } from './hallucination-gate.js';
import { ChunkedTranscriber, type BoundarySource } from './chunked-transcriber.js';
import type { BoundaryEvent } from './pyannote-segmenter.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

/** Observed hallucinations — clean-audio fixture, real STT. */
const HALLUC = [
  'Субтитры сделал DimaTorzok',
  'Субтитры создавал DimaTorzok',
  'Добавил субтитры DimaTorzok',
  'Редактор субтитров А.Семкин Корректор А.Егорова',
  'Продолжение следует...',
  'Продолжение следует',
  'Thanks for watching!',
  'ご視聴ありがとうございました',
  'Abone olmayı unutmayın',
];
/** Real rows the lane published on the m24 Teams tape. None may be suppressed. */
const REAL = [
  'Yeah.', 'Yep.', 'Okay.', 'No.', 'Right.', 'Mm-hmm.', 'um', 'On noise.',
  "I don't know.", 'Thank you.', "I'm not sure", 'So that\'s so cool.',
  'We might just have to deal with that on Teams. Teams is just going to be a hard platform to work with.',
  'is it using the method that I set up for Zoom and Meet?',
  "The latency isn't great though.",
];

for (const t of HALLUC) check(`suppressed: ${JSON.stringify(t)}`, hallucinationRule(t) !== null);
for (const t of REAL) check(`kept: ${JSON.stringify(t)}`, hallucinationRule(t) === null, String(hallucinationRule(t)));
check('repetition loop suppressed',
  hallucinationRule('thank you so much thank you so much thank you so much thank you so much') === 'repetition');
check('pure repetition is still suppressed for a Teams growing window',
  teamsWindowHallucinationRule('thank you so much thank you so much thank you so much thank you so much') === 'repetition');
check('a repeated prefix cannot delete the genuine tail of a Teams growing window',
  teamsWindowHallucinationRule('good that sounds good that sounds good that sounds good that sounds good well sorry we have been chaotic recently because it has been too busy here with bits and pieces but we are back now') === null);
check('the gate is switchable off', (() => {
  process.env.VEXA_MIXED_HALLUCINATION_GATE = 'off';
  const r = hallucinationRule('Субтитры сделал DimaTorzok');
  delete process.env.VEXA_MIXED_HALLUCINATION_GATE;
  return r === null;
})());

// ── The wiring: a suppressed segment must reach the host as an observation, and must not
//    take the real speech in the same submission down with it. ──
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const published: string[] = [];
const suppressed: { text: string; rule: string }[] = [];
let emit!: (ev: BoundaryEvent) => void;
const seg = (text: string, start: number, end: number): any =>
  ({ text, start, end, no_speech_prob: 0, avg_logprob: -0.1, compression_ratio: 1.0 });
const tc = await ChunkedTranscriber.create({
  language: 'ru',
  transcribe: async (): Promise<TranscriptionResult> =>
    ({ text: 'real words here Субтитры сделал DimaTorzok', language: 'ru', language_probability: 0.99,
       segments: [seg('real words here', 0, 1.5), seg('Субтитры сделал DimaTorzok', 1.5, 2.5)] } as any),
  publish: (_s, confirmed) => { for (const c of confirmed) published.push(c.text); },
  publishPending: (_s, segs) => { for (const p of segs) published.push(p.text); },
  clearPending: () => {}, rename: () => {},
  onSuppressed: (s) => suppressed.push({ text: s.text, rule: s.rule }),
  makeSegmenter: (onBoundary) => { emit = onBoundary; return Promise.resolve<BoundarySource>({ appendFrame: async () => {}, reset() {} }); },
  log: () => {},
});
const a = new Float32Array(16000 * 3); a.fill(0.1);
emit({ kind: 'silence→speaker', tMs: 0, confidence: 0.9 });
tc.feedAudio(a, 0);
await sleep(200);
emit({ kind: 'speaker→silence', tMs: 3000, confidence: 0.9 });
await sleep(200);
await tc.dispose();

check('the invented segment never publishes', !published.some((t) => t.includes('DimaTorzok')), JSON.stringify(published));
check('the real segment in the SAME submission still publishes', published.some((t) => t.includes('real words')), JSON.stringify(published));
check('the suppression is reported to the host, not silent',
  suppressed.length > 0 && suppressed.every((s) => s.rule === 'pattern' || s.rule === 'phrase'), JSON.stringify(suppressed));
check('the lane counts it in stats()', tc.stats().suppressed > 0, JSON.stringify(tc.stats()));

if (failed) { console.error(`\n❌ hallucination-gate: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ hallucination-gate: invented media-artifact text is suppressed and reported; a real meeting\'s short answers are untouched.');
