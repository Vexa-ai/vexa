#!/usr/bin/env node
// corpus — the speech fixtures: a pool of TTS clips per speaker, generated ONCE
// from Deepgram Aura voices and cached to cache/<key>.json (never committed —
// fixtures stay out of the repo). Each clip = { text, b64 (16kHz mono WAV), durSec }.
// The clip TEXT is the ground truth (we know exactly what was said), and every
// clip leads with a self-ID ("Boris here, …") so the scorer can detect leakage.
//
//   regenerate:  FORCE_REGEN=1 [CLIPS_PER=16] node src/corpus.mjs   (needs DG_KEY)
//   length dist: LEN_MED (s, 11) · LEN_SD (lognormal σ, 0.65) · LEN_MIN/LEN_MAX (2/30)
//
// Imported by drive.mjs (loadOrBuildCache + prepare). 0 Deepgram calls per turn at
// run time — clips are reused, so runs are instant, free, and apples-to-apples.
import fs from 'fs';
import path from 'path';
import { SPEAKERS } from './speakers.mjs';

const HERE = path.dirname(new URL(import.meta.url).pathname);
const RATE = 16000;
const DG_KEY = process.env.DG_KEY;
export const CACHE_DIR = process.env.EVAL_CACHE || path.join(HERE, '..', 'cache');
const CLIPS_PER = Number(process.env.CLIPS_PER || 16);

const LEN_MED = Number(process.env.LEN_MED ?? 11);
const LEN_SD = Number(process.env.LEN_SD ?? 0.65);
const LEN_MIN = Number(process.env.LEN_MIN ?? 2);
const LEN_MAX = Number(process.env.LEN_MAX ?? 30);

const rnd = (a) => a[Math.floor(Math.random() * a.length)];
const gauss = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

const FILLER = [
  'I think we should split this into two phases instead of one big push.',
  'The scope keeps creeping, and that worries me more than the timeline.',
  'Can we agree on what done actually means before we start building?',
  'If we cut the reporting piece, the rest ships a full week earlier.',
  'My worry is the migration step, everything else feels low risk.',
  'Let us write down the assumptions, because half of them are guesses.',
  'From a budget standpoint, the second vendor is almost double the first.',
  'We are already over on cloud spend, so a new service is a hard sell.',
  'I would rather hire one strong contractor than three junior ones.',
  'Honestly the licensing cost is what kills this idea for me.',
  'The database is the bottleneck, not the API, I am fairly certain of that.',
  'Caching would buy us time, but it hides the real performance problem.',
  'We saw the exact same race condition in the old payments service.',
  'If the queue backs up, retries make it worse, not better.',
  'I pushed a small prototype last night and the numbers look promising.',
  'Can we move the review to Thursday so people have time to read it?',
  'Standups are running long again, maybe we trim the status updates.',
  'I will be out Friday, so let us not schedule the cutover then.',
  'The deadline is the end of the quarter, and that is genuinely fixed.',
  'I broadly agree, but I want to challenge one assumption underneath it.',
  'That is a fair point, and it changes how I would prioritize this.',
  'I am not convinced, the upside feels smaller than the effort involved.',
  'Strong yes from me, this unblocks two other teams immediately.',
  'I hear the concern, but I think we are overthinking a simple change.',
  'Let us not let perfect be the enemy of shipping something useful.',
  'What does the data actually say, do we have numbers from last quarter?',
  'Has anyone talked to the customers who asked for this originally?',
  'Maybe we should hear from the people who have been quiet so far.',
  'What is the smallest version of this we could ship and learn from?',
  'Who owns this after launch, because that part is still unclear to me.',
  'Last time we skipped the design review, we paid for it for months.',
  'This reminds me of the onboarding rewrite that spiralled out of control.',
  'In my experience the boring solution usually wins in the long run.',
  'We tried a similar approach two years ago and it did not stick.',
  'Let us lock this in so we are not relitigating it next week.',
  'I think we have enough to make a call, let us not gather more data.',
  'Can someone summarize the decision so we all leave with the same notes?',
  'Action items first, then we can argue about the details offline.',
];

function buildText(s) {
  const sec = Math.min(LEN_MAX, Math.max(LEN_MIN, LEN_MED * Math.exp(LEN_SD * gauss())));
  const target = sec * 15; // ~15 chars/s English
  const leads = [`This is ${s.en}.`, `${s.en} here.`, `${s.en} again.`, `${s.en} jumping in,`,
    `${s.en} would add,`, `From ${s.en}'s side,`, `${s.en} thinks that`, `${s.en} speaking,`,
    `Quick one from ${s.en},`, `Yeah, ${s.en} here,`, `${s.en} again, honestly`, `Picking up from there, ${s.en} here,`];
  let t = rnd(leads);
  const recent = [];
  while (t.length < target) {
    let f = rnd(FILLER);
    for (let k = 0; k < 4 && recent.includes(f); k++) f = rnd(FILLER);
    recent.push(f); if (recent.length > 4) recent.shift();
    t += ' ' + f;
  }
  return t;
}

async function dgTTS(text, voice) {
  const url = `https://api.deepgram.com/v1/speak?model=${voice}&encoding=linear16&sample_rate=${RATE}&container=wav`;
  const r = await fetch(url, { method: 'POST', headers: { Authorization: `Token ${DG_KEY}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
  if (!r.ok) throw new Error(`deepgram ${r.status}: ${(await r.text()).slice(0, 80)}`);
  const buf = Buffer.from(await r.arrayBuffer());
  return { b64: buf.toString('base64'), durSec: (buf.length - 44) / (RATE * 2) };
}

const CACHE = {};
export async function loadOrBuildCache() {
  fs.mkdirSync(CACHE_DIR, { recursive: true });
  for (const s of SPEAKERS) {
    const f = path.join(CACHE_DIR, `${s.key}.json`);
    if (fs.existsSync(f) && !process.env.FORCE_REGEN) { CACHE[s.key] = JSON.parse(fs.readFileSync(f, 'utf8')); continue; }
    if (!DG_KEY) { console.error(`[corpus] ${s.en}: no clip pool and no DG_KEY to regenerate (${f})`); process.exit(1); }
    const clips = [];
    process.stdout.write(`[corpus] ${s.en}: `);
    for (let k = 0; k < CLIPS_PER; k++) {
      const text = buildText(s);
      const { b64, durSec } = await dgTTS(text, s.voice);
      clips.push({ text, b64, durSec }); process.stdout.write('.');
    }
    fs.writeFileSync(f, JSON.stringify(clips)); CACHE[s.key] = clips;
    console.log(` ${clips.length} clips (${clips.map((c) => c.durSec.toFixed(0)).sort((a, b) => a - b).join('/')}s)`);
  }
  const total = Object.values(CACHE).reduce((n, c) => n + c.length, 0);
  console.log(`[corpus] ready: ${total} clips, ${SPEAKERS.length} speakers (${CACHE_DIR})`);
  return CACHE;
}

/** A turn = a random cached clip for the speaker (no API call → instant + free). */
export function prepare(s) {
  const clip = rnd(CACHE[s.key]);
  return { s, text: clip.text, b64: clip.b64, durSec: clip.durSec };
}

// CLI: regenerate / verify the corpus.
if (import.meta.url === `file://${process.argv[1]}`) {
  loadOrBuildCache().catch((e) => { console.error(e); process.exit(1); });
}
