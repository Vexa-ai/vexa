/**
 * QUALITY — does the transcript say what was actually said?
 *
 * Every other replay harness here answers a STRUCTURAL question (is the right speaker bound to
 * the turn, is the replay deterministic, how long until text appears) and answers it against a
 * MOCK transcribe that returns a canned string. That makes them blind by construction to the
 * failure a user actually reports: text that arrives in shreds, drops words, or invents them. A
 * pipeline that shredded a sentence into eleven fragments would pass every one of them.
 *
 * This harness closes that gap. It replays a `captured-signal.v1` session built out of REAL
 * speech whose words are KNOWN (`eval/src/speech_fixture.py`) through the REAL gmeet lane against
 * REAL STT, and scores the transcript against the truth sidecar:
 *
 *   recall        — known words the transcript kept, in order (longest common subsequence)
 *   precision     — transcript words that were really said (the inverse measures invention)
 *   fragmentation — confirmed segments per spoken turn, and their duration distribution
 *   attribution   — confirmed words landing under the speaker who said them
 *
 * It is NOT offline and NOT deterministic — real STT is neither — so it gates nothing by default
 * and is not in the default test chain. It is the instrument that tells you whether the product
 * works; run it against any STT endpoint and compare.
 *
 * The replay is PACED in real time because the lane's submit/confirm cadence is wall-clock
 * (`speaker-streams.ts` drives `setInterval(submitInterval*1000)`); feeding a session in a tight
 * loop would submit almost nothing and score a pipeline that never ran.
 *
 *   python3 core/meetings/eval/src/speech_fixture.py --speakers A,B --turns 4 --out /tmp/q
 *   QUALITY_FIXTURE=/tmp/q.captured-signal.jsonl TX_URL=http://localhost:18500 \
 *     npx tsx src/quality.test.ts
 *
 * Env: QUALITY_FIXTURE (session; truth sidecar is <base>.truth.json) · TX_URL · TX_TOKEN ·
 * TX_MODEL · RECALL_MIN / ATTRIB_MIN (assert instead of merely report).
 */
import { readFileSync } from 'node:fs';
import { createGmeetPipeline, type TranscriptSegment } from '@vexa/gmeet-pipeline';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';

const FIXTURE = process.env.QUALITY_FIXTURE;
if (!FIXTURE) throw new Error('QUALITY_FIXTURE is required (build one with eval/src/speech_fixture.py)');
const TRUTH = FIXTURE.replace(/\.captured-signal\.jsonl$/, '.truth.json');
const TX_URL = process.env.TX_URL ?? 'http://localhost:18500';
const TX_MODEL = process.env.TX_MODEL ?? 'Systran/faster-whisper-small';
const LANG = process.env.TX_LANG ?? 'en';
const EXTRA_MS = Number(process.env.TX_EXTRA_MS ?? 0);
const TAIL_WAIT_MS = Number(process.env.TAIL_WAIT_MS ?? 0);
const SAMPLE_RATE = 16000;

interface CapFrame { seq: number; ts: number; speakerIndex: number; speakerName?: string; pcm: string; pcm_len: number; }
interface TruthTurn { turn: number; speakerIndex: number; speaker: string; text: string; startMs: number; endMs: number; }

// The lane's PRODUCTION cadence. Feeding it anything faster measures a pipeline nobody runs.
const CONFIG = {
  minAudioDuration: Number(process.env.BOT_SPEAKER_MIN_AUDIO_SEC ?? 2),
  submitInterval: Number(process.env.BOT_SPEAKER_SUBMIT_INTERVAL_SEC ?? 2),
  confirmThreshold: Number(process.env.BOT_SPEAKER_CONFIRM_THRESHOLD ?? 2),
  maxBufferDuration: Number(process.env.BOT_SPEAKER_MAX_BUFFER_SEC ?? 30),
  idleTimeoutSec: 2,
  sampleRate: SAMPLE_RATE,
};

const words = (s: string): string[] =>
  s.toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').split(/\s+/).filter(Boolean);

/** Longest common subsequence length — order-preserving overlap, so a reordered word is a miss. */
function lcs(a: string[], b: string[]): number {
  const prev = new Array<number>(b.length + 1).fill(0);
  const cur = new Array<number>(b.length + 1).fill(0);
  for (let i = 1; i <= a.length; i++) {
    for (let j = 1; j <= b.length; j++) {
      cur[j] = a[i - 1] === b[j - 1] ? prev[j - 1] + 1 : Math.max(prev[j], cur[j - 1]);
    }
    prev.splice(0, prev.length, ...cur);
  }
  return prev[b.length];
}

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  const lines = readFileSync(FIXTURE!, 'utf8').split('\n').filter(Boolean);
  const frames = lines.slice(1).map((l) => JSON.parse(l) as CapFrame).filter((f) => f.pcm);
  const truth = JSON.parse(readFileSync(TRUTH, 'utf8')) as { turns: TruthTurn[] };

  const client = new TranscriptionClient({ serviceUrl: TX_URL, apiToken: process.env.TX_TOKEN, model: TX_MODEL, sampleRate: SAMPLE_RATE });
  let sttCalls = 0, sttSubSegs = 0, sttFails = 0;
  const submitSecs: number[] = [];
  const transcribe = async (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult> => {
    sttCalls++;
    submitSecs.push(pcm.length / SAMPLE_RATE);
    try {
      // TX_EXTRA_MS emulates a SLOWER endpoint on a fast local one, so latency can be swept as a
      // variable without spending a metered credential per run.
      if (EXTRA_MS) await sleep(EXTRA_MS);
      const r = await client.transcribe(pcm, LANG, prompt);
      sttSubSegs += r.segments.length;
      return r;
    } catch (e) { sttFails++; throw e; }
  };

  const confirmed: TranscriptSegment[] = [];
  const pipe = createGmeetPipeline({
    transcribe,
    config: CONFIG,
    sink: { segment: (s) => { if (s.completed) confirmed.push(s); }, draft: () => { /* */ }, finalize: () => { /* */ } },
  });

  const audioSec = (frames[frames.length - 1].ts + 200) / 1000;
  console.log(`fixture: ${frames.length} frames · ${audioSec.toFixed(1)}s audio · ${truth.turns.length} turns · ${truth.turns.reduce((n, t) => n + words(t.text).length, 0)} known words`);
  console.log(`stt: ${TX_URL} (${TX_MODEL}) · lane config ${JSON.stringify(CONFIG)}`);
  console.log(`replaying at real time (~${audioSec.toFixed(0)}s)…\n`);

  // Paced feed — each frame is delivered when its audio would have finished being captured.
  const t0 = Date.now();
  for (const f of frames) {
    const due = t0 + f.ts + (f.pcm_len / SAMPLE_RATE) * 1000;
    const wait = due - Date.now();
    if (wait > 0) await sleep(wait);
    const b = Buffer.from(f.pcm, 'base64');
    pipe.feedAudio(f.speakerIndex, f.speakerName, new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4), f.ts);
  }
  // A real meeting does not end on the last syllable — the bot sits there while the lane's idle
  // flush (idleTimeoutSec) closes the final turn. Disposing the instant the last frame lands
  // measures the harness, not the product, so the tail is modelled explicitly.
  if (TAIL_WAIT_MS) await sleep(TAIL_WAIT_MS);
  await pipe.dispose();

  // ── Score ────────────────────────────────────────────────────────────────────────────────
  // Each confirmed segment belongs to the spoken turn it overlaps MOST in time. First-match with a
  // slop window mis-files a segment whose span crosses a turn boundary, which is exactly what a
  // long turn produces — the scorer would then invent drops that the pipeline did not commit.
  const perTurn = truth.turns.map((t) => ({ t, segs: [] as TranscriptSegment[] }));
  let unplaced = 0;
  for (const s of confirmed) {
    const a = s.start * 1000, b = s.end * 1000;
    let best: typeof perTurn[number] | undefined, bestOv = 0;
    for (const p of perTurn) {
      const ov = Math.min(b, p.t.endMs) - Math.max(a, p.t.startMs);
      if (ov > bestOv) { bestOv = ov; best = p; }
    }
    if (best) best.segs.push(s); else unplaced++;
  }

  console.log('turn  speaker  known  heard  recall  segs  attrib');
  console.log('-'.repeat(56));
  let kTot = 0, hTot = 0, mTot = 0, aOk = 0, aTot = 0;
  const segDurs: number[] = [];
  for (const { t, segs } of perTurn) {
    const known = words(t.text);
    const heard = segs.flatMap((s) => words(s.text));
    const m = lcs(known, heard);
    kTot += known.length; hTot += heard.length; mTot += m;
    for (const s of segs) { aTot++; if (s.speaker === t.speaker) aOk++; segDurs.push(s.end - s.start); }
    const attrib = segs.length ? segs.filter((s) => s.speaker === t.speaker).length / segs.length : 1;
    console.log(`${String(t.turn).padStart(4)}  ${t.speaker.padEnd(7)} ${String(known.length).padStart(6)} ${String(heard.length).padStart(6)}  ${(m / Math.max(1, known.length)).toFixed(3).padStart(6)} ${String(segs.length).padStart(5)}  ${attrib.toFixed(3).padStart(6)}`);
  }

  segDurs.sort((a, b) => a - b);
  const recall = mTot / Math.max(1, kTot);
  const precision = mTot / Math.max(1, hTot);
  const attribution = aTot ? aOk / aTot : 1;
  const segsPerTurn = confirmed.length / truth.turns.length;
  const p = (q: number): number => segDurs.length ? segDurs[Math.min(segDurs.length - 1, Math.floor(segDurs.length * q))] : 0;

  console.log('\n── quality ─────────────────────────────────────────────');
  console.log(`  recall        ${recall.toFixed(3)}   (${mTot}/${kTot} known words kept, in order)`);
  console.log(`  precision     ${precision.toFixed(3)}   (${hTot - mTot} of ${hTot} heard words were not said)`);
  console.log(`  attribution   ${attribution.toFixed(3)}   (${aOk}/${aTot} segments under the right speaker${unplaced ? `, ${unplaced} outside every turn` : ''})`);
  console.log('── fragmentation ───────────────────────────────────────');
  console.log(`  confirmed segments   ${confirmed.length}  (${segsPerTurn.toFixed(1)} per spoken turn)`);
  console.log(`  segment duration     p50 ${p(0.5).toFixed(2)}s · p90 ${p(0.9).toFixed(2)}s · min ${p(0).toFixed(2)}s · max ${p(1).toFixed(2)}s`);
  console.log(`  under 1s             ${segDurs.filter((d) => d < 1).length}/${segDurs.length}`);
  console.log('── stt ─────────────────────────────────────────────────');
  submitSecs.sort((a, b) => a - b);
  console.log(`  submissions          ${sttCalls} (${sttFails} failed) · ${sttSubSegs} sub-segments returned (${(sttSubSegs / Math.max(1, sttCalls - sttFails)).toFixed(1)} per response)`);
  console.log(`  submitted audio      p50 ${(submitSecs[Math.floor(submitSecs.length / 2)] ?? 0).toFixed(2)}s · min ${(submitSecs[0] ?? 0).toFixed(2)}s · under ${CONFIG.minAudioDuration}s: ${submitSecs.filter((d) => d < CONFIG.minAudioDuration).length}/${submitSecs.length}`);

  console.log('\n── transcript ──────────────────────────────────────────');
  for (const s of confirmed) console.log(`  ${(s.speaker ?? '?').padEnd(7)} [${s.start.toFixed(2)}–${s.end.toFixed(2)}]  ${s.text}`);

  // One machine-readable line, so a sweep over a variable (endpoint, TX_EXTRA_MS, config) can be
  // diffed without re-reading the whole report. lastTurn is called out because the final turn is
  // the one the close path can lose.
  const last = perTurn[perTurn.length - 1];
  const lastRecall = lcs(words(last.t.text), last.segs.flatMap((s) => words(s.text))) / Math.max(1, words(last.t.text).length);
  console.log(`\nSWEEP extra_ms=${EXTRA_MS} tail_ms=${TAIL_WAIT_MS} recall=${recall.toFixed(3)} precision=${precision.toFixed(3)} lastTurnRecall=${lastRecall.toFixed(3)} segs=${confirmed.length} segsPerTurn=${segsPerTurn.toFixed(1)} sttCalls=${sttCalls}`);

  let failed = 0;
  const assertMin = (name: string, got: number, envKey: string): void => {
    const min = process.env[envKey];
    if (!min) return;
    const ok = got >= Number(min);
    console.log(`  ${ok ? '✅' : '❌'} ${name} ${got.toFixed(3)} >= ${min}`);
    if (!ok) failed++;
  };
  console.log();
  assertMin('recall', recall, 'RECALL_MIN');
  assertMin('attribution', attribution, 'ATTRIB_MIN');
  if (failed) { console.error(`\n❌ quality: ${failed} threshold(s) missed.`); process.exit(1); }
}

void main();
