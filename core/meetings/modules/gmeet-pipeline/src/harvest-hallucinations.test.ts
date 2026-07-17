/**
 * #617 harvester — offline proof of the pure core (language set, non-speech corpus, harvest sweep,
 * file rendering) with a FAKE transcribe. The live sweep against the real STT is the operator step
 * (VEXA_TX_KEY); this pins that the harness collects, dedups, and renders correctly.
 * Run: npx tsx src/harvest-hallucinations.test.ts  (or via `npm test`, chained).
 */
import {
  WHISPER_LANGUAGES,
  nonSpeechCorpus,
  harvest,
  renderHarvestFile,
  type Transcribe,
} from './harvest-hallucinations.js';

let failed = 0;
const check = (name: string, cond: boolean) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}`);
  if (!cond) failed++;
};

function rms(a: Float32Array): number {
  if (!a.length) return 0;
  let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * a[i];
  return Math.sqrt(s / a.length);
}

// Language set — every language Whisper serves, incl. the ones #613 exposed.
check('WHISPER_LANGUAGES covers ~99 langs', WHISPER_LANGUAGES.length >= 95);
check('includes en/es/pt/ru + the #613 ja/tr', ['en', 'es', 'pt', 'ru', 'ja', 'tr'].every((l) => WHISPER_LANGUAGES.includes(l)));
check('no duplicate language codes', new Set(WHISPER_LANGUAGES).size === WHISPER_LANGUAGES.length);

// Corpus is guaranteed NON-SPEECH: pure silence (RMS 0) + noise above the production gate.
const corpus = nonSpeechCorpus(16000, 1);
check('corpus has silence + noise samples', corpus.length >= 3 && corpus.some((s) => s.name === 'silence'));
check('silence sample is truly silent (RMS 0)', rms(corpus.find((s) => s.name === 'silence')!.pcm) === 0);
check('a noise sample sits above the 0.0025 gate (reaches Whisper in the field)',
  corpus.some((s) => rms(s.pcm) > 0.0025));
check('corpus is reproducible (seeded)', rms(nonSpeechCorpus(16000, 1)[1].pcm) === rms(corpus[1].pcm));

// Harvest sweep — fake STT hallucinates a ja outro on silence, nothing for en; empties are dropped.
const fake: Transcribe = async (pcm, lang) => {
  if (rms(pcm) === 0 && lang === 'ja') return '  ご視聴ありがとうございました  '; // trimmed on collect
  if (lang === 'tr') return 'Abone olmayı unutmayın';
  return ''; // no hallucination
};
const got = await harvest(fake, ['en', 'ja', 'tr'], corpus);
check('en produced nothing → not in the map', !got.has('en'));
check('ja hallucination collected + trimmed', got.get('ja')?.has('ご視聴ありがとうございました') === true);
check('tr hallucination collected', got.get('tr')?.has('Abone olmayı unutmayın') === true);
check('ja deduped across samples (1 unique phrase)', got.get('ja')?.size === 1);

// Per-call failures are isolated, never abort the sweep.
const flaky: Transcribe = async (_pcm, lang) => { if (lang === 'ja') throw new Error('STT 500'); return 'boom'; };
let errs = 0;
const got2 = await harvest(flaky, ['ja', 'en'], corpus.slice(0, 1), { onError: () => { errs++; } });
check('a failing language is isolated (en still harvested)', got2.get('en')?.has('boom') === true);
check('the failure was reported, not swallowed', errs === 1);

// Rendered file: provenance header + sorted, deduped phrases.
const body = renderHarvestFile('ja', ['b', 'a', 'a', ' a '], { model: 'large-v3', date: '2026-07-14', url: 'https://x' });
check('rendered file has a GENERATED provenance header', body.includes('GENERATED') && body.includes('large-v3'));
check('rendered phrases are sorted + deduped', body.trimEnd().endsWith('a\nb'));

if (failed) { console.error(`\n❌ harvest-hallucinations: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ harvest-hallucinations: language set, non-speech corpus, sweep, dedup, and rendering all correct.');
