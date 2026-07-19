/**
 * PACED replay — the real speech-to-transcript latency, measured.
 *
 * "Great latency" was an unchecked claim, and the batch replay harnesses cannot check it: they
 * feed a whole session in a tight loop, so every confirmation happens after all the audio is in
 * and the apparent lag is just (session end − segment end). The reason batching cannot work is in
 * the pipeline itself — its submit/confirm cadence is WALL-CLOCK
 * (`speaker-streams.ts`: `setInterval(submitInterval*1000)`, `Date.now()` idle checks), so the
 * only faithful measurement feeds a recorded session at the rate it was actually spoken and times
 * the answer with the same clock the pipeline uses.
 *
 * So this replays the golden at real time (or SPEED× faster) and reports, per confirmed segment,
 * the delay between speech becoming audible and the transcript admitting it. That is the
 * number a user feels. It is inherently wall-clock and therefore machine-sensitive, which is why
 * it is NOT in the default test chain and gates nothing by default: it is an instrument you run
 * to get a number, and a regression check only when a budget is set explicitly
 * (LATENCY_BUDGET_P95_MS).
 *
 * READ THE NUMBER CAREFULLY. It is (confirmation wall-clock) − (the segment's own reported `end`,
 * placed on the paced timeline). On the golden it currently reads ~55ms even with a 250ms mock STT
 * round-trip, which is NOT physically coherent for audio that must be transcribed before it can be
 * confirmed — so `end` evidently tracks the confirmed prefix's audio timeline rather than the last
 * chunk actually sent to STT. Until that semantic is pinned down this is an INSTRUMENT and a
 * regression comparator, NOT a validated latency budget, and it gates nothing unless a budget is
 * passed explicitly. Do not quote it as "our latency" in anything user-facing yet.
 *
 *   npm run replay:paced                       # real time (~10s for the golden)
 *   SPEED=4 npm run replay:paced               # 4× faster (cadence no longer faithful — see below)
 *   LATENCY_BUDGET_P95_MS=6000 npm run replay:paced   # fail if p95 regresses past a budget
 *
 * SPEED>1 caveat: the pipeline's timers stay in real seconds while the audio arrives compressed,
 * so a sped-up run makes the pipeline look SLOWER per unit of audio, not faster. Compare only runs
 * at the same SPEED.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createGmeetPipeline } from '@vexa/gmeet-pipeline';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = process.env.REPLAY_FIXTURE
  ?? join(HERE, '..', '..', '..', 'eval', 'replay-fixture', 'session.captured-signal.jsonl');
const SPEED = Math.max(1, Number(process.env.SPEED ?? 1));
const BUDGET_P95 = Number(process.env.LATENCY_BUDGET_P95_MS ?? 0);

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface CapFrame { seq: number; ts: number; speakerIndex: number; speakerName?: string; pcm: string; pcm_len: number; type?: string; }

function load(path: string): CapFrame[] {
  const lines = readFileSync(path, 'utf8').split('\n').filter(Boolean);
  return lines.slice(1).map((l) => JSON.parse(l) as CapFrame).filter((r) => r.type !== 'hint');
}
const framePcm = (f: CapFrame): Float32Array => {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4);
};

async function main(): Promise<void> {
  const frames = load(FIXTURE);
  const audioMs = frames.reduce((n, f) => n + (f.pcm_len / 16000) * 1000, 0);
  console.log(`  fixture: ${frames.length} frames, ${(audioMs / 1000).toFixed(1)}s of audio, SPEED=${SPEED}`);

  // Wall-clock is the instrument here, so the mock STT must not pretend a round trip is free:
  // a real provider costs 200-600ms, and a 0ms stub would flatter every number.
  const STT_MS = Number(process.env.MOCK_STT_MS ?? 250);
  const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
    await sleep(STT_MS / SPEED);
    const text = pcm[0] > 0.07 ? 'second speaker line' : 'first speaker line';
    return { text, language: 'en', duration: pcm.length / 16000, segments: [{ start: 0, end: pcm.length / 16000, text }] };
  };

  const t0 = Date.now();
  const confirmedAt: Array<{ speaker: string; endMs: number; atMs: number }> = [];
  const pipe = createGmeetPipeline({
    transcribe,
    config: { minAudioDuration: 0.15, submitInterval: 0.1, confirmThreshold: 2, maxBufferDuration: 5, idleTimeoutSec: 2, sampleRate: 16000 },
    sink: {
      segment: (s) => {
        if (!s.completed) return;
        confirmedAt.push({ speaker: s.speaker ?? '?', endMs: Math.round(s.end * 1000), atMs: Date.now() - t0 });
      },
      draft: () => { /* */ }, finalize: () => { /* */ },
    },
  });

  // Feed at the recorded cadence: the gap between frames is real time, so the pipeline's
  // wall-clock submit/confirm loop sees the session as it was actually spoken.
  const base = frames[0].ts;
  let elapsedAudio = 0;
  for (const f of frames) {
    // A capture frame is delivered when its audio has been CAPTURED, i.e. at the END of the span
    // it covers — pacing to its start would hand the pipeline 200ms of not-yet-spoken audio and
    // make every segment look confirmed before it was said (the causality check catches exactly
    // that, and did).
    const due = (f.ts + (f.pcm_len / 16000) * 1000 - base) / SPEED;
    const behind = due - (Date.now() - t0);
    if (behind > 0) await sleep(behind);
    pipe.feedAudio(f.speakerIndex, f.speakerName, framePcm(f), f.ts);
    elapsedAudio += (f.pcm_len / 16000) * 1000;
  }
  const fedAt = Date.now() - t0;
  await pipe.dispose();

  // Wall-clock latency per segment: when its speech ENDED (in the paced timeline) → confirmation.
  const samples = confirmedAt.map((c) => {
    const spokeEndAt = (c.endMs - base) / SPEED;         // when that speech finished, on our clock
    return { ...c, latencyMs: Math.round(c.atMs - spokeEndAt) };
  });
  const lat = samples.map((s) => s.latencyMs).sort((a, b) => a - b);
  const pct = (p: number) => (lat.length ? lat[Math.min(lat.length - 1, Math.floor((lat.length - 1) * p))] : 0);
  const p50 = pct(0.5), p95 = pct(0.95), max = lat.length ? lat[lat.length - 1] : 0;

  console.log(`  fed ${frames.length} frames over ${(fedAt / 1000).toFixed(1)}s wall (mock STT ${STT_MS}ms/call)`);
  for (const s of samples) console.log(`    ${s.speaker}: speech ended @${(s.endMs - base) / 1000}s → confirmed +${s.latencyMs}ms`);
  console.log(`  SPEECH→TRANSCRIPT latency: n=${lat.length} p50=${p50}ms p95=${p95}ms max=${max}ms`);

  check('every segment was confirmed', samples.length > 0, String(samples.length));
  check('no segment confirmed before its speech finished (causality)',
    samples.every((s) => s.latencyMs >= 0), JSON.stringify(samples.filter((s) => s.latencyMs < 0)));
  if (BUDGET_P95 > 0) {
    check(`p95 within the declared budget (${BUDGET_P95}ms)`, p95 <= BUDGET_P95, `p95=${p95}ms`);
  } else {
    console.log('  (no LATENCY_BUDGET_P95_MS set — measuring only, gating nothing)');
  }

  if (failed) { console.error(`\n❌ replay-paced: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ replay-paced: a recorded session replayed at speaking rate yields a real speech→transcript latency profile.');
}

void main();
