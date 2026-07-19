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
 * WHAT IT MEASURES: turn start → confirmed text ("time to text") — the delay a user experiences
 * between starting to speak and seeing their words. Measured against the segment's `start`,
 * deliberately NOT its `end`: speaker-streams computes end as
 * `windowStartMs + totalSamples/sampleRate`, the buffer's FULL extent at confirm time, so it
 * tracks the newest audio fed rather than the span the text covers. Latency measured against
 * `end` collapses to a constant (~55ms) no matter how slow STT is — the first version of this
 * harness did exactly that, which is why a coherence check now fails the run if time-to-text ever
 * drops below the STT round-trip it must pay.
 *
 * VALIDATED SENSITIVITY (golden, this harness): mock STT 100ms → p50 503ms · 250ms → 856ms ·
 * 600ms → 1509ms. That is ≈ 2×STT + ~300ms, because LocalAgreement `confirmThreshold: 2` requires
 * TWO submissions — and two STT round-trips — before a prefix confirms. So the confirm threshold,
 * not buffering, is the dominant latency lever: halving STT time saves ~2× that much end to end,
 * and dropping the threshold to 1 would roughly halve time-to-text at a cost in stability.
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
import { ChunkedTranscriber, type BoundaryEvent, type BoundarySource } from '@vexa/mixed-pipeline';
import { gunzipSync } from 'node:zlib';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURE = process.env.REPLAY_FIXTURE
  ?? join(HERE, '..', '..', '..', 'eval', 'replay-fixture', 'session.captured-signal.jsonl');
const SPEED = Math.max(1, Number(process.env.SPEED ?? 1));
// Default budget for the golden at the default mock-STT cost: a REGRESSION guard with headroom
// (observed p95 856ms), not an aspiration. Set explicitly when changing MOCK_STT_MS or SPEED,
// since the budget only means anything against the run's own STT cost.
// Per-lane REGRESSION guards, each with headroom over what that lane measures today — not
// targets, and explicitly not equal: the two lanes chunk differently and the gap is a product
// question (see below), not something a shared budget should paper over.
//   gmeet: per-speaker streams, 0.1s submit interval        → observed p95 856ms
//   mixed: chunked on pyannote cuts, which need a PAUSE     → observed p95 2780ms, max 4406ms
// Both measured at MOCK_STT_MS=250; a different STT cost moves both (~2xSTT, see above).
const DEFAULT_BUDGET_GMEET_MS = 1500;
const DEFAULT_BUDGET_MIXED_MS = 6000;
const BUDGET_OVERRIDE = Number(process.env.LATENCY_BUDGET_P95_MS ?? 0);

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface CapFrame { seq: number; ts: number; speakerIndex: number; speakerName?: string; pcm: string; pcm_len: number; type?: string; }

interface Hint { type: 'hint'; t: number; name: string; isEnd?: boolean }

function loadAll(path: string): { header: any; frames: CapFrame[]; hints: Hint[]; cuts: Cut[] } {
  const raw = path.endsWith('.gz') ? gunzipSync(readFileSync(path)).toString('utf8') : readFileSync(path, 'utf8');
  const lines = raw.split('\n').filter(Boolean);
  const recs = lines.slice(1).map((l) => JSON.parse(l));
  return {
    header: JSON.parse(lines[0]),
    frames: recs.filter((r: any) => r.type !== 'hint' && r.type !== 'boundary') as CapFrame[],
    hints: recs.filter((r: any) => r.type === 'hint') as Hint[],
    cuts: recs.filter((r: any) => r.type === 'boundary') as Cut[],
  };
}
function load(path: string): CapFrame[] { return loadAll(path).frames; }
const framePcm = (f: CapFrame): Float32Array => {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4);
};

/** The mixed lane's paced run: one stream named from recorded hints, its own real pipeline. */
interface Cut { type: 'boundary'; tMs: number; kind: string; confidence?: number }

async function pacedMixed(frames: CapFrame[], hints: Hint[], sttMs: number, cuts: Cut[] = [])
  : Promise<{ confirmed: Array<{ speaker: string; startMs: number; endMs: number; atMs: number }>;
              firstDraft: Map<string, number> }> {
  const out: Array<{ speaker: string; startMs: number; endMs: number; atMs: number }> = [];
  const firstDraft = new Map<string, number>();
  let emitBoundary!: (ev: BoundaryEvent) => void;
  const t0 = Date.now();
  const tc = await ChunkedTranscriber.create({
    language: 'en',
    transcribe: async (pcm: Float32Array) => {
      await sleep(sttMs / SPEED);
      const d = pcm.length / 16000;
      return { text: `speech(${d.toFixed(1)}s)`, language: 'en', duration: d, segments: [{ start: 0, end: d, text: `speech(${d.toFixed(1)}s)` }] } as TranscriptionResult;
    },
    publish: (speaker, confirmed) => {
      if (confirmed.length && !firstDraft.has(speaker)) firstDraft.set(speaker, Date.now() - t0);
      for (const c of confirmed) out.push({ speaker, startMs: c.startMs, endMs: c.endMs, atMs: Date.now() - t0 });
    },
    publishPending: (speaker, segs) => {
      // The mixed lane confirms a turn only when the speaker YIELDS, so confirmed-text timing
      // measures turn LENGTH, not latency. What the user actually sees first is the draft — that
      // is this lane's comparable time-to-text.
      if (segs.length && !firstDraft.has(speaker)) firstDraft.set(speaker, Date.now() - t0);
    },
    clearPending: () => { /* */ },
    rename: (oldS, newS) => { for (const o of out) if (o.speaker === oldS) o.speaker = newS; },
    makeSegmenter: (onB) => { emitBoundary = onB; return Promise.resolve<BoundarySource>({ appendFrame: async () => { /* */ }, reset() { /* */ } }); },
    log: () => { /* quiet */ },
  });

  const base = frames[0].ts;
  const useRecordedCuts = cuts.length > 0;
  const timeline = [
    ...frames.map((f) => ({ t: f.ts + (f.pcm_len / 16000) * 1000, frame: f as CapFrame | undefined, hint: undefined as Hint | undefined, cut: undefined as Cut | undefined })),
    ...hints.map((h) => ({ t: h.t, frame: undefined, hint: h as Hint | undefined, cut: undefined as Cut | undefined })),
    ...cuts.map((c) => ({ t: c.tMs, frame: undefined, hint: undefined, cut: c as Cut | undefined })),
  ].sort((a, b) => a.t - b.t);

  let current = '';
  emitBoundary({ kind: 'silence→speaker', tMs: timeline[0].t, confidence: 0.9 });
  for (const ev of timeline) {
    const behind = (ev.t - base) / SPEED - (Date.now() - t0);
    if (behind > 0) await sleep(behind);
    if (ev.cut) {
      emitBoundary({ kind: ev.cut.kind as BoundaryEvent['kind'], tMs: ev.cut.tMs, confidence: ev.cut.confidence ?? 0.9 });
    } else if (ev.hint) {
      const h = ev.hint;
      if (!useRecordedCuts && !h.isEnd && h.name !== current) {
        if (current) emitBoundary({ kind: 'speaker→speaker', tMs: h.t, confidence: 0.9 });
        current = h.name;
      }
      tc.recordHint(h.name, 'dom-active', h.t, h.isEnd);
    } else if (ev.frame) {
      tc.feedAudio(framePcm(ev.frame), ev.frame.ts);
    }
  }
  emitBoundary({ kind: 'speaker→silence', tMs: timeline[timeline.length - 1].t, confidence: 0.9 });
  await sleep(1500 / SPEED);
  await tc.dispose();
  return { confirmed: out, firstDraft };
}

async function main(): Promise<void> {
  const session = loadAll(FIXTURE);
  const frames = session.frames;
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

  // MIXED lane (zoom/teams/jitsi): its own pipeline, hints and all — same paced clock.
  if (session.header.lane === 'mixed') {
    const base = frames[0].ts;
    console.log(session.cuts.length
      ? `  chunking: production's OWN ${session.cuts.length} recorded cuts`
      : '  chunking: SUBSTITUTE cut source — numbers are an UPPER BOUND, not this lane\'s latency');
    const { confirmed: out, firstDraft } = await pacedMixed(frames, session.hints, STT_MS, session.cuts);
    const s2 = out.map((c) => ({ ...c, latencyMs: Math.round(c.atMs - (c.startMs - base) / SPEED) }));
    // Time-to-text for THIS lane = turn start → first visible draft.
    const turnStart = new Map<string, number>();
    for (const c of out) if (!turnStart.has(c.speaker)) turnStart.set(c.speaker, (c.startMs - base) / SPEED);
    const draftLat = [...firstDraft.entries()]
      .filter(([sp]) => turnStart.has(sp))
      .map(([sp, at]) => ({ speaker: sp, latencyMs: Math.round(at - (turnStart.get(sp) as number)) }));
    const dl = draftLat.map((d) => d.latencyMs).sort((a, b) => a - b);
    const dq = (p: number) => (dl.length ? dl[Math.min(dl.length - 1, Math.floor((dl.length - 1) * p))] : 0);
    const l2 = s2.map((x) => x.latencyMs).sort((a, b) => a - b);
    const q = (p: number) => (l2.length ? l2[Math.min(l2.length - 1, Math.floor((l2.length - 1) * p))] : 0);
    for (const x of s2) console.log(`    ${x.speaker}: turn began @${((x.startMs - base) / 1000).toFixed(1)}s → text confirmed +${x.latencyMs}ms`);
    for (const d of draftLat) console.log(`    ${d.speaker}: first visible text +${d.latencyMs}ms after their turn began`);
    console.log(`  TIME-TO-TEXT (mixed, turn start → FIRST DRAFT): n=${dl.length} p50=${dq(0.5)}ms p95=${dq(0.95)}ms max=${dl[dl.length - 1] ?? 0}ms`);
    console.log(`  turn-start → CONFIRMED (bounded by turn LENGTH, not latency): p50=${q(0.5)}ms max=${l2[l2.length - 1] ?? 0}ms`);
    check('mixed: every turn produced confirmed text', s2.length > 0, String(s2.length));
    check('mixed: no text confirmed before its turn began (causality)',
      s2.every((x) => x.latencyMs >= 0), JSON.stringify(s2.filter((x) => x.latencyMs < 0)));
    check('mixed: time-to-text exceeds the STT round-trip it must pay (coherence)',
      dl.length > 0 && dq(0.5) >= STT_MS, `p50=${dq(0.5)}ms < mock STT ${STT_MS}ms`);
    // NOT GATED, and the reason matters. Production cuts this lane with PyannoteSegmenter, which
    // emits boundaries continuously on speech/silence; this harness injects a deterministic cut
    // source that fires only on a SPEAKER CHANGE (a model download cannot live in a unit test), so
    // the pipeline is starved of the cuts that trigger early drafts. The numbers above are
    // therefore an UPPER BOUND on this lane's latency, not a measurement of it — Anna's first
    // turn shows the signature: no draft at all until the turn ended. Gating on an upper bound
    // produced by an unrepresentative double would be exactly the kind of green that proves
    // nothing. A real mixed-lane budget needs a boundary source with production's cut density
    // (recorded pyannote boundaries would do it, and captured-signal.v1 could carry them).
    if (session.cuts.length > 0) {
      // The session carries production's OWN cuts, so the chunking is faithful and the number is
      // this lane's latency rather than an artifact of a substitute — gate it.
      const budget = BUDGET_OVERRIDE || DEFAULT_BUDGET_MIXED_MS;
      check(`mixed: p95 time-to-first-text within budget (${budget}ms)`, dq(0.95) <= budget, `p95=${dq(0.95)}ms`);
      console.log(`  NOTE: this lane is ~3x slower to first text than gmeet (${dq(0.5)}ms vs ~856ms).`
        + ' It waits for a pyannote cut, which needs a PAUSE, where gmeet submits per speaker every'
        + ' 100ms. That gap is a product decision, not a test threshold — the budget here only'
        + ' guards against getting WORSE.');
    } else {
      console.log('  (mixed budget NOT gated — no recorded cuts in this session, so the substitute'
        + ' cut source makes these an upper bound; re-record with a cut-capturing bot to gate)');
    }
    if (failed) { console.error(`\n❌ replay-paced (mixed): ${failed} check(s) FAILED.`); process.exit(1); }
    console.log('\n✅ replay-paced (mixed): a recorded Zoom/Teams/Jitsi session replayed at speaking rate yields a real time-to-text profile.');
    return;
  }

  const t0 = Date.now();
  const confirmedAt: Array<{ speaker: string; startMs: number; endMs: number; atMs: number }> = [];
  const pipe = createGmeetPipeline({
    transcribe,
    config: { minAudioDuration: 0.15, submitInterval: 0.1, confirmThreshold: 2, maxBufferDuration: 5, idleTimeoutSec: 2, sampleRate: 16000 },
    sink: {
      segment: (s) => {
        if (!s.completed) return;
        confirmedAt.push({
          speaker: s.speaker ?? '?',
          startMs: Math.round(s.start * 1000),
          endMs: Math.round(s.end * 1000),
          atMs: Date.now() - t0,
        });
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

  // TIME-TO-TEXT: from the moment a speaker STARTS a turn to the moment their words are
  // confirmed. Measured against `start`, deliberately NOT against `end`: speaker-streams computes
  // end as windowStartMs + totalSamples/sampleRate — the buffer's FULL extent at confirm time,
  // which includes audio that arrived after the text being confirmed. So `end` tracks the newest
  // audio fed rather than the span the text covers, and any latency measured against it collapses
  // to a constant (~55ms here) no matter how slow STT is. `start` is stable and is the instant a
  // user began speaking, so this is the delay a user actually experiences before seeing their words.
  const samples = confirmedAt.map((c) => {
    const spokeStartAt = (c.startMs - base) / SPEED;     // when that turn began, on our clock
    return { ...c, latencyMs: Math.round(c.atMs - spokeStartAt), spanMs: c.endMs - c.startMs };
  });
  const lat = samples.map((s) => s.latencyMs).sort((a, b) => a - b);
  const pct = (p: number) => (lat.length ? lat[Math.min(lat.length - 1, Math.floor((lat.length - 1) * p))] : 0);
  const p50 = pct(0.5), p95 = pct(0.95), max = lat.length ? lat[lat.length - 1] : 0;

  console.log(`  fed ${frames.length} frames over ${(fedAt / 1000).toFixed(1)}s wall (mock STT ${STT_MS}ms/call)`);
  for (const s of samples) console.log(`    ${s.speaker}: turn began @${((s.startMs - base) / 1000).toFixed(1)}s (span ${(s.spanMs / 1000).toFixed(1)}s) → text confirmed +${s.latencyMs}ms`);
  console.log(`  TIME-TO-TEXT (turn start → confirmed): n=${lat.length} p50=${p50}ms p95=${p95}ms max=${max}ms`);

  check('every segment was confirmed', samples.length > 0, String(samples.length));
  check('no text confirmed before its turn began (causality)',
    samples.every((s) => s.latencyMs >= 0), JSON.stringify(samples.filter((s) => s.latencyMs < 0)));
  check('time-to-text exceeds the STT round-trip it must pay (coherence)',
    lat.length > 0 && p50 >= STT_MS,
    `p50=${p50}ms < mock STT ${STT_MS}ms — the metric is measuring a moving target, not latency`);
  const gmeetBudget = BUDGET_OVERRIDE || DEFAULT_BUDGET_GMEET_MS;
  check(`p95 time-to-text within budget (${gmeetBudget}ms)`, p95 <= gmeetBudget, `p95=${p95}ms`);

  if (failed) { console.error(`\n❌ replay-paced: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ replay-paced: a recorded session replayed at speaking rate yields a real speech→transcript latency profile.');
}

void main();
