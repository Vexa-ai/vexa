/**
 * FLAT QUALITY — does the MIXED lane turn a flat room mix into WHOLE, CORRECT text?
 *
 * The input contract is deliberately the smallest one a transcription claim can rest on:
 *
 *     a flat WAV  +  a golden text file   →   a scorecard
 *
 * Nothing else. A flat mix has no speaker dimension — one mono track, one stream of words — so
 * this harness feeds NO hints and ignores every name the lane assigns. The only question it
 * answers is whether the words come out whole and correct. Attribution is a different question
 * measured by a different instrument (quality-mixed.test.ts, with its truth oracle).
 *
 * FAITHFUL BY CONSTRUCTION, in the three places that decide the answer:
 *   • the CUT is the real PyannoteSegmenter (the model loads from the HF cache) — the mixed lane's
 *     chunking IS the segmenter, so substituting it would score the substitute;
 *   • STT is a REAL endpoint. A recorded/mocked STT cannot test a change to the submit WINDOW at
 *     all: a different window is different audio and needs a real re-transcription to answer;
 *   • the FEED is real time, in production's 4096-sample (256 ms) frames. The lane's submit/confirm
 *     cadence is wall-clock (`setInterval(1000)`, `Date.now()` TTL), so a batch feed races the
 *     audio ahead of the pump and measures window sizes no deployment ever produces.
 *
 * WHAT IS SCORED (all against the golden, all reported as one block a later run can diff):
 *   WER · CER                              — is the text CORRECT
 *   segments / golden turn · segment dur mean·median · % under 1s   — is the text WHOLE
 *   submit-span mean·median·p10 · % under 1s                        — what STT was actually asked
 *
 * The transcript scored is the CONSUMER's view: rows upserted by segment_id, last write wins
 * (drafts self-replace by id — services/bot/src/pipeline.ts), read back in start order.
 *
 * WHAT THIS DOES NOT MEASURE: speaker attribution (out of scope by construction — no hints are
 * fed), latency, or anything about a live meeting's capture path upstream of the WAV.
 *
 *   TX_URL=http://localhost:18500 TX_TOKEN=… \
 *   FLAT_WAV=…/flat.wav FLAT_GOLDEN=…/flat-golden.txt \
 *     npx tsx src/flat-quality.test.ts
 *
 * Env: FLAT_WAV · FLAT_GOLDEN (a sibling .jsonl, if present, supplies the golden TURN count) ·
 *      TX_URL · TX_TOKEN · TX_MODEL · TX_LANG · METRICS_JSON · TRANSCRIPT_OUT · SUBMIT_LOG ·
 *      SPEED (>1 compresses the audio clock while the lane's timers stay in real seconds — the
 *      windows are then the harness's, not production's; compare only runs at the same SPEED).
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { ChunkedTranscriber, PyannoteSegmenter, type BoundaryEvent, type BoundarySource } from '@vexa/mixed-pipeline';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';

const WAV = process.env.FLAT_WAV;
const GOLDEN = process.env.FLAT_GOLDEN;
if (!WAV || !GOLDEN) throw new Error('FLAT_WAV and FLAT_GOLDEN are required (a flat mono WAV + its golden text)');
const TX_URL = process.env.TX_URL ?? 'http://localhost:18500';
const TX_MODEL = process.env.TX_MODEL ?? 'whisper-1';
const LANG = process.env.TX_LANG ?? 'en';
const SPEED = Math.max(1, Number(process.env.SPEED ?? 1));
const SR = 16000;
/** Production's capture frame: ScriptProcessor 4096 @ 16 kHz = 256 ms (mixed-audio.ts). */
const FRAME_SAMPLES = 4096;

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

// ── Scoring primitives ────────────────────────────────────────────────────────────────────────
/** Normalise for scoring: case, punctuation and whitespace are not what this measures. */
const normalise = (s: string): string =>
  (s || '').toLowerCase().replace(/['’]/g, '').replace(/[^\p{L}\p{N}\s]/gu, ' ').replace(/\s+/g, ' ').trim();
const wordsOf = (s: string): string[] => (normalise(s) ? normalise(s).split(' ') : []);

/** Levenshtein distance over any token sequence (rolling row — the char case is ~3k × 3k). */
function editDistance(a: ArrayLike<unknown>, b: ArrayLike<unknown>): number {
  if (a.length === 0) return b.length;
  if (b.length === 0) return a.length;
  let prev = new Int32Array(b.length + 1);
  let cur = new Int32Array(b.length + 1);
  for (let j = 0; j <= b.length; j++) prev[j] = j;
  for (let i = 1; i <= a.length; i++) {
    cur[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    const t = prev; prev = cur; cur = t;
  }
  return prev[b.length];
}

const mean = (xs: number[]): number => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
const quantile = (sorted: number[], p: number): number =>
  (sorted.length ? sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor((sorted.length - 1) * p)))] : 0);

// ── WAV ───────────────────────────────────────────────────────────────────────────────────────
/** Minimal RIFF reader: 16-bit PCM mono is the only shape a flat tap produces. */
function readWav(path: string): { pcm: Float32Array; sampleRate: number } {
  const buf = readFileSync(path);
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${path} is not a RIFF/WAVE file`);
  }
  let off = 12, channels = 0, sampleRate = 0, bits = 0, dataAt = -1, dataLen = 0;
  while (off + 8 <= buf.length) {
    const id = buf.toString('ascii', off, off + 4);
    const size = buf.readUInt32LE(off + 4);
    if (id === 'fmt ') {
      channels = buf.readUInt16LE(off + 10);
      sampleRate = buf.readUInt32LE(off + 12);
      bits = buf.readUInt16LE(off + 22);
    } else if (id === 'data') { dataAt = off + 8; dataLen = size; }
    off += 8 + size + (size % 2);
  }
  if (dataAt < 0) throw new Error(`${path} has no data chunk`);
  if (bits !== 16) throw new Error(`${path}: only 16-bit PCM is supported (got ${bits})`);
  const total = Math.floor(dataLen / 2);
  const out = new Float32Array(Math.floor(total / channels));
  // Mixing down here would be inventing a flat track; a flat fixture is mono by definition, and a
  // multi-channel file means the caller handed the wrong artefact.
  if (channels !== 1) throw new Error(`${path}: a flat fixture must be mono (got ${channels} channels)`);
  for (let i = 0; i < out.length; i++) out[i] = buf.readInt16LE(dataAt + i * 2) / 32768;
  return { pcm: out, sampleRate };
}

interface GoldenTurn { start: number; end: number; text: string }

function loadGolden(path: string): { text: string; turns: GoldenTurn[] | null } {
  const text = readFileSync(path, 'utf8').replace(/\s+/g, ' ').trim();
  const jsonl = path.replace(/\.[^.]+$/, '') + '.jsonl';
  let turns: GoldenTurn[] | null = null;
  try {
    turns = readFileSync(jsonl, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as GoldenTurn);
  } catch { /* the turn count is optional — the text is the contract */ }
  return { text, turns };
}

// ── Run ───────────────────────────────────────────────────────────────────────────────────────
async function main(): Promise<void> {
  const { pcm: audio, sampleRate } = readWav(WAV!);
  if (sampleRate !== SR) throw new Error(`${WAV}: expected ${SR} Hz (got ${sampleRate})`);
  const golden = loadGolden(GOLDEN!);
  const goldenWords = wordsOf(golden.text);
  const audioSec = audio.length / SR;
  console.log(`fixture: ${WAV}`);
  console.log(`  ${audioSec.toFixed(1)}s mono @ ${SR}Hz · golden ${goldenWords.length} words`
    + (golden.turns ? ` in ${golden.turns.length} turns` : ' (no turn file — fragmentation ratio unavailable)'));
  console.log(`  STT: ${TX_URL} model=${TX_MODEL} lang=${LANG} · feed: REAL-TIME${SPEED > 1 ? ` ÷${SPEED} (NOT production cadence)` : ''}`);

  const client = new TranscriptionClient({ serviceUrl: TX_URL, apiToken: process.env.TX_TOKEN, model: TX_MODEL, sampleRate: SR });

  let sttCalls = 0, sttFails = 0, sttWords = 0;
  let fatalSttError: Error | null = null;
  /** Every submission's span, in seconds of audio handed to STT — the knob under test. */
  const submitSpans: number[] = [];
  const submitLog: Array<{ n: number; sec: number; text: string }> = [];
  const cuts: Array<{ kind: string; tMs: number }> = [];
  const laneLog: string[] = [];
  /** The consumer's view: upsert by segment_id, last write wins. */
  const store = new Map<string, { text: string; startMs: number; endMs: number; completed: boolean }>();

  // ── LATENCY INSTRUMENT ────────────────────────────────────────────────────────────────────────
  // The feed is anchored to wall-clock (base = Date.now() at t0), and a segment's audio-time endMs is
  // `base + audio_offset_ms` — so endMs IS the wall instant that end-of-speech audio was captured.
  // time-to-draft   = firstPublishWall − endMs   (a reader first sees these words)
  // time-to-confirm = confirmWall      − endMs   (the words stop moving)
  // Both are only meaningful at SPEED=1 (below the timers run in real seconds while the audio clock is
  // compressed — the two diverge and a latency in audio-seconds is not a latency the user feels).
  interface SegLatency { endMs: number; firstDraftWall: number | null; confirmWall: number | null }
  const seg = new Map<string, SegLatency>();
  const noteDraft = (id: string, endMs: number, text: string): void => {
    if (!text.trim()) return;                       // empty-text rows are stale-draft drops, not a draft
    const e = seg.get(id) ?? { endMs, firstDraftWall: null, confirmWall: null };
    e.endMs = endMs;
    if (e.firstDraftWall === null) e.firstDraftWall = Date.now();
    seg.set(id, e);
  };
  const noteConfirm = (id: string, endMs: number, text: string): void => {
    if (!text.trim()) return;
    const e = seg.get(id) ?? { endMs, firstDraftWall: null, confirmWall: null };
    e.endMs = endMs;
    if (e.firstDraftWall === null) e.firstDraftWall = Date.now();   // confirm-only (short turn) is also its first paint
    if (e.confirmWall === null) e.confirmWall = Date.now();
    seg.set(id, e);
  };
  /** Every STT round-trip's wall duration (the (b) bucket of the draft budget). */
  const sttRttMs: number[] = [];

  let emitBoundary!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: LANG,
    transcribe: async (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult> => {
      sttCalls++;
      const sec = pcm.length / SR;
      submitSpans.push(sec);
      const sent = Date.now();
      try {
        const r = await client.transcribe(pcm, LANG, prompt);
        sttRttMs.push(Date.now() - sent);
        sttWords += wordsOf(r.text).length;
        submitLog.push({ n: sttCalls, sec: Number(sec.toFixed(2)), text: (r.text || '').trim().slice(0, 120) });
        return r;
      } catch (e) {
        sttFails++;
        fatalSttError ??= e as Error;
        sttRttMs.push(Date.now() - sent);
        submitLog.push({ n: sttCalls, sec: Number(sec.toFixed(2)), text: '<FAILED>' });
        throw e;
      }
    },
    // Names are ignored on purpose: a flat mix has no speaker dimension. Both callbacks write the
    // same rows a reader ends up with — a draft and its confirmation share an id and self-replace.
    publish: (_speaker, confirmed, pending) => {
      for (const c of confirmed) { store.set(c.segmentId, { text: c.text, startMs: c.startMs, endMs: c.endMs, completed: true }); noteConfirm(c.segmentId, c.endMs, c.text); }
      for (const p of pending) { store.set(p.segmentId, { text: p.text, startMs: p.startMs, endMs: p.endMs, completed: false }); noteDraft(p.segmentId, p.endMs, p.text); }
    },
    publishPending: (_speaker, segments) => {
      for (const s of segments) { store.set(s.segmentId, { text: s.text, startMs: s.startMs, endMs: s.endMs, completed: false }); noteDraft(s.segmentId, s.endMs, s.text); }
    },
    clearPending: () => { /* the bot's transcript.v1 egress is append-only; drafts self-replace by id */ },
    rename: () => { /* attribution is not this instrument's question */ },
    makeSegmenter: (onBoundary) => {
      emitBoundary = (ev) => {
        cuts.push({ kind: ev.kind, tMs: ev.tMs });
        laneLog.push(`[cut] ${ev.kind} @${Math.round(ev.tMs)}`);   // in the lane's own order
        onBoundary(ev);
      };
      return PyannoteSegmenter.create({ inferIntervalMs: 500, onBoundary: emitBoundary }) as unknown as Promise<BoundarySource>;
    },
    // The lane narrates every submit window and every reason one produces no text ([submit] …).
    // A hole in the transcript is only readable against that: audio never submitted and audio
    // submitted-then-dropped look identical from the outside.
    log: (msg: string) => { laneLog.push(msg); },
  });

  // Feed at the rate it was spoken, in production's frame size. The audio clock is anchored to
  // wall-clock now, the way a live tap's is, so the lane's Date.now() gates see the same world.
  const t0Wall = Date.now();
  const base = t0Wall;
  for (let off = 0; off < audio.length; off += FRAME_SAMPLES) {
    // ChunkedTranscriber deliberately survives an individual backend error in production. This
    // instrument cannot: once the real STT leg is red, every later score is invalid and waiting
    // through the rest of a long fixture only produces a convincing green-on-empty report.
    if (fatalSttError) {
      await tc.dispose();
      throw new Error(`flat quality invalid: STT failed after ${sttCalls} call(s) — ${fatalSttError.message}`);
    }
    const frame = audio.subarray(off, Math.min(audio.length, off + FRAME_SAMPLES));
    // A capture frame is delivered when its audio has been CAPTURED — at the END of the span it
    // covers. Pacing to its start hands the lane audio that has not been spoken yet.
    const dueMs = ((off + frame.length) / SR) * 1000 / SPEED;
    const behind = dueMs - (Date.now() - t0Wall);
    if (behind > 0) await sleep(behind);
    tc.feedAudio(new Float32Array(frame), base + (off / SR) * 1000);
  }
  // The meeting ending: close whatever the segmenter left open.
  emitBoundary({ kind: 'speaker→silence', tMs: base + audioSec * 1000, confidence: 0.9 });
  await sleep(3000 / SPEED);
  await tc.dispose();

  // ── Score ───────────────────────────────────────────────────────────────────────────────────
  const rows = [...store.values()].filter((r) => r.text.trim()).sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs);
  const hyp = rows.map((r) => r.text.trim()).join(' ');
  const hypWords = wordsOf(hyp);
  const wer = goldenWords.length ? editDistance(hypWords, goldenWords) / goldenWords.length : 0;
  const goldChars = normalise(golden.text);
  const hypChars = normalise(hyp);
  const cer = goldChars.length ? editDistance(hypChars, goldChars) / goldChars.length : 0;

  const durs = rows.map((r) => Math.max(0, r.endMs - r.startMs) / 1000).sort((a, b) => a - b);
  const spans = submitSpans.slice().sort((a, b) => a - b);
  const fragmentation = golden.turns ? rows.length / golden.turns.length : null;

  // ── Latency distributions (seconds), only meaningful at SPEED=1 ────────────────────────────────
  const draftLat = [...seg.values()].filter((s) => s.firstDraftWall !== null)
    .map((s) => (s.firstDraftWall! - s.endMs) / 1000).sort((a, b) => a - b);
  const confirmLat = [...seg.values()].filter((s) => s.confirmWall !== null)
    .map((s) => (s.confirmWall! - s.endMs) / 1000).sort((a, b) => a - b);
  const rtt = sttRttMs.slice().sort((a, b) => a - b).map((m) => m / 1000);
  // Per-stage draft budget (medians). draft = (audio-end → covering submit sent) + RTT + plumbing.
  // RTT is measured directly; the residual is the tick-wait/accumulation bucket the nominal terms
  // don't name. confirm−draft is the extra LocalAgreement passes (each ≈ one SUBMIT_TICK cadence).
  const draftMed = quantile(draftLat, 0.5), confirmMed = quantile(confirmLat, 0.5), rttMed = quantile(rtt, 0.5);
  const residualMed = draftMed - rttMed;             // audio-end → submit-sent (tick wait) + plumbing
  const agreeMed = confirmMed - draftMed;            // extra stability passes after first draft

  const metrics = {
    fixture: WAV, golden: GOLDEN, sttUrl: TX_URL, sttModel: TX_MODEL, speed: SPEED,
    audioSec: Number(audioSec.toFixed(1)),
    goldenWords: goldenWords.length,
    goldenTurns: golden.turns?.length ?? null,

    wer: Number(wer.toFixed(4)),
    cer: Number(cer.toFixed(4)),
    transcriptWords: hypWords.length,
    wordYield: Number((hypWords.length / Math.max(1, goldenWords.length)).toFixed(3)),

    segments: rows.length,
    segmentsPerGoldenTurn: fragmentation === null ? null : Number(fragmentation.toFixed(2)),
    segDurMeanSec: Number(mean(durs).toFixed(2)),
    segDurMedianSec: Number(quantile(durs, 0.5).toFixed(2)),
    segUnder1sPct: Number((durs.filter((d) => d < 1).length / Math.max(1, durs.length)).toFixed(3)),

    sttCalls, sttFails, sttWords,
    submitMeanSec: Number(mean(spans).toFixed(2)),
    submitMedianSec: Number(quantile(spans, 0.5).toFixed(2)),
    submitP10Sec: Number(quantile(spans, 0.1).toFixed(2)),
    submitUnder1sPct: Number((spans.filter((d) => d < 1).length / Math.max(1, spans.length)).toFixed(3)),

    cutsEmitted: cuts.length,

    // Latency (s) — SPEED=1 only. Segments carrying a draft / a confirm.
    latencySpeed1: SPEED === 1,
    draftSegs: draftLat.length,
    confirmSegs: confirmLat.length,
    toDraftMedianSec: Number(draftMed.toFixed(2)),
    toDraftP90Sec: Number(quantile(draftLat, 0.9).toFixed(2)),
    toDraftMaxSec: Number((draftLat[draftLat.length - 1] ?? 0).toFixed(2)),
    toConfirmMedianSec: Number(confirmMed.toFixed(2)),
    toConfirmP90Sec: Number(quantile(confirmLat, 0.9).toFixed(2)),
    toConfirmMaxSec: Number((confirmLat[confirmLat.length - 1] ?? 0).toFixed(2)),
    // Per-stage draft budget (medians, s)
    sttRttMedianSec: Number(rttMed.toFixed(2)),
    sttRttP90Sec: Number(quantile(rtt, 0.9).toFixed(2)),
    budgetSubmitWaitSec: Number(residualMed.toFixed(2)),   // audio-end → covering submit sent (+plumbing)
    budgetSttRttSec: Number(rttMed.toFixed(2)),            // submit → STT response
    budgetAgreePassesSec: Number(agreeMed.toFixed(2)),     // first draft → confirm (extra passes)
  };

  const invalidReasons = [
    sttFails > 0 ? `${sttFails}/${sttCalls} STT calls failed` : '',
    sttCalls === 0 ? 'the pipeline made no STT calls' : '',
    goldenWords.length > 0 && hypWords.length === 0 ? 'the golden has speech but the hypothesis is empty' : '',
  ].filter(Boolean);

  console.log(`\n── SCORECARD ────────────────────────────────────────────────`);
  console.log(`  WER                      ${(metrics.wer * 100).toFixed(1)}%`);
  console.log(`  CER                      ${(metrics.cer * 100).toFixed(1)}%`);
  console.log(`  words produced           ${metrics.transcriptWords} / ${metrics.goldenWords} golden = ${(metrics.wordYield * 100).toFixed(0)}%`);
  console.log(`  segments / golden turn   ${metrics.segments} / ${metrics.goldenTurns ?? '?'} = ${metrics.segmentsPerGoldenTurn ?? '?'}×`);
  console.log(`  segment duration         mean ${metrics.segDurMeanSec}s · median ${metrics.segDurMedianSec}s`);
  console.log(`  segments under 1s        ${(metrics.segUnder1sPct * 100).toFixed(0)}%`);
  console.log(`  STT submit span          mean ${metrics.submitMeanSec}s · median ${metrics.submitMedianSec}s · p10 ${metrics.submitP10Sec}s`);
  console.log(`  submit spans under 1s    ${(metrics.submitUnder1sPct * 100).toFixed(0)}%`);
  console.log(`  stt calls                ${sttCalls} (${sttFails} failed) · segmenter cuts ${cuts.length}`);
  console.log(`── LATENCY ${SPEED === 1 ? '' : '(SPEED>1 — NOT comparable to production)'} ────────────────────────────────`);
  console.log(`  time-to-draft            median ${metrics.toDraftMedianSec}s · p90 ${metrics.toDraftP90Sec}s · max ${metrics.toDraftMaxSec}s  (${metrics.draftSegs} segs)`);
  console.log(`  time-to-confirm          median ${metrics.toConfirmMedianSec}s · p90 ${metrics.toConfirmP90Sec}s · max ${metrics.toConfirmMaxSec}s  (${metrics.confirmSegs} segs)`);
  console.log(`  STT round-trip           median ${metrics.sttRttMedianSec}s · p90 ${metrics.sttRttP90Sec}s`);
  console.log(`  draft budget (median)    submit-wait ${metrics.budgetSubmitWaitSec}s + STT-RTT ${metrics.budgetSttRttSec}s ≈ draft ${metrics.toDraftMedianSec}s`);
  console.log(`  confirm budget (median)  draft ${metrics.toDraftMedianSec}s + agreement-passes ${metrics.budgetAgreePassesSec}s ≈ confirm ${metrics.toConfirmMedianSec}s`);
  console.log(`─────────────────────────────────────────────────────────────`);

  if (process.env.METRICS_JSON) {
    writeFileSync(process.env.METRICS_JSON, JSON.stringify(metrics, null, 2) + '\n');
    console.log(`  metrics written: ${process.env.METRICS_JSON}`);
  }
  if (process.env.TRANSCRIPT_OUT) {
    writeFileSync(process.env.TRANSCRIPT_OUT, JSON.stringify({ text: hyp, segments: rows }, null, 2) + '\n');
    console.log(`  transcript written: ${process.env.TRANSCRIPT_OUT}`);
  }
  if (process.env.SUBMIT_LOG) {
    // The lane's own narration, rebased to seconds-into-the-fixture so a line can be read against
    // the transcript above it, with the cut stream interleaved (the cut is what opens/closes turns).
    const rel = (ms: number): string => ((ms - base) / 1000).toFixed(2);
    const lines = laneLog.map((m) => m.replace(/(\d{10,})/g, (d) => rel(Number(d))));
    writeFileSync(process.env.SUBMIT_LOG,
      JSON.stringify(submitLog, null, 2) + '\n\n' + lines.join('\n') + '\n');
    console.log(`  submit log written: ${process.env.SUBMIT_LOG} (${submitLog.length} submissions, ${laneLog.length} lane lines)`);
  }

  console.log('\n--- transcript ---');
  for (const r of rows) console.log(`  [${((r.startMs - base) / 1000).toFixed(2)}-${((r.endMs - base) / 1000).toFixed(2)}] ${r.completed ? ' ' : '~'} ${r.text}`);
  if (invalidReasons.length) {
    console.error(`\n❌ flat quality INVALID — ${invalidReasons.join('; ')}`);
    process.exitCode = 1;
  }
}

void main();
