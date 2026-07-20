/**
 * QUALITY (mixed lane) — how much of what was SAID survives to the transcript.
 *
 * The mixed lane's failure is not bad ASR, it is loss: a live jitsi session sharing a YouTube tab
 * (continuous single-speaker speech, 107s) produced a transcript covering 0.225 of the wall clock
 * with a 45.7s hole in it, while the STT tap showed 99.9s of that audio WAS submitted and 57 of 58
 * responses came back with text. So the audio arrived, STT answered, and the pipeline dropped
 * 64.9% of the words it was handed.
 *
 * Continuous speech is what makes this measurable without a reference transcript: if the source
 * never stops talking, every second not covered by a segment is a defect, and every word STT
 * returned but the transcript lacks is a drop. Both are computed here from the session itself.
 *
 * Drives the REAL @vexa/mixed-pipeline off a recorded captured-signal.v1 session — its audio, its
 * out-of-band speaker hints, and production's OWN segmenter cuts (recorded, so the replay chunks
 * the way the meeting actually chunked). Only STT is live (a real endpoint), because the question
 * is what the pipeline does with real responses.
 *
 *   QUALITY_MIXED_FIXTURE=<session.captured-signal.jsonl> TX_URL=http://localhost:18500 \
 *     npx tsx src/quality-mixed.test.ts
 *
 * Env: TX_URL · TX_TOKEN · TX_MODEL · TX_LANG · MIN_TURN_MS (see below) · REAL_SEGMENTER ·
 * METRICS_JSON (write the score block for a corpus baseline).
 *
 * WHAT THIS DOES NOT MEASURE: replay is unpaced — audio is fed as fast as it reads while the
 * lane's submission timers run on the wall clock, so the submitted-window sizes and every latency
 * derived from them are the harness's, not production's. Cadence is `replay-paced.test.ts`. What
 * survives that distortion is STRUCTURE — retention, store rows, duplicate identity, holes — and
 * structure is what this run is a baseline for.
 *
 * MIN_TURN_MS is the knob under test: the recorded session carries 183 cuts in 190s (p50 gap
 * 0.41s, 142/182 under 1s, every confidence 0.35-0.47), so the lane opens a turn per wobble and
 * LocalAgreement — which needs a turn to survive several submissions — never gets to confirm one.
 * Setting it coalesces boundaries that arrive sooner than a plausible speech unit.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { ChunkedTranscriber, PyannoteSegmenter, type BoundaryEvent, type BoundarySource } from '@vexa/mixed-pipeline';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';

const FIXTURE = process.env.QUALITY_MIXED_FIXTURE;
if (!FIXTURE) throw new Error('QUALITY_MIXED_FIXTURE is required (a recorded captured-signal.v1 session)');
const TX_URL = process.env.TX_URL ?? 'http://localhost:18500';
const TX_MODEL = process.env.TX_MODEL ?? 'Systran/faster-whisper-small';
const LANG = process.env.TX_LANG ?? 'en';
const MIN_TURN_MS = Number(process.env.MIN_TURN_MS ?? 0);
const MOCK = process.env.MOCK_STT === '1';
const CLOSE_ONLY = process.env.CLOSE_ONLY === '1';
const SR = 16000;

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
const words = (s: string): string[] => (s || '').toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').split(/\s+/).filter(Boolean);

interface Frame { seq: number; ts: number; pcm: string; pcm_len: number }
interface Hint { type: 'hint'; t: number; name: string; isEnd?: boolean }
interface Cut { type: 'boundary'; kind: string; tMs: number; confidence?: number }

function load(path: string): { frames: Frame[]; hints: Hint[]; cuts: Cut[] } {
  const raw = path.endsWith('.gz') ? gunzipSync(readFileSync(path)).toString('utf8') : readFileSync(path, 'utf8');
  const lines = raw.split('\n').filter(Boolean);
  if (JSON.parse(lines[0]).type !== 'captured_signal_header') throw new Error('not a captured-signal.v1 session');
  const recs = lines.slice(1).map((l) => JSON.parse(l));
  return {
    frames: recs.filter((r) => r.type !== 'hint' && r.type !== 'boundary'),
    hints: recs.filter((r) => r.type === 'hint'),
    cuts: recs.filter((r) => r.type === 'boundary'),
  };
}

const framePcm = (f: Frame): Float32Array => {
  const b = Buffer.from(f.pcm, 'base64');
  return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4);
};

async function main(): Promise<void> {
  const { frames, hints, cuts } = load(FIXTURE!);
  // No recorded cuts means the fixture came from upstream of the pipeline (a desktop tape), so the
  // segmenter has to be real or there is nothing to segment with. REAL_SEGMENTER forces it either
  // way, for comparing the model's cuts against production's on a fixture that has both.
  const REAL_SEG = process.env.REAL_SEGMENTER === '1' || cuts.length === 0;
  if (REAL_SEG) console.log('cuts: none recorded — running the REAL PyannoteSegmenter');
  const client = new TranscriptionClient({ serviceUrl: TX_URL, apiToken: process.env.TX_TOKEN, model: TX_MODEL, sampleRate: SR });

  let sttCalls = 0, sttWords = 0, sttFails = 0;
  const submitSecs: number[] = [];
  const published: Array<{ speaker: string; text: string; startMs: number; endMs: number; id: string; via: string }> = [];
  // The CONSUMER, modelled: the collector upserts on segment_id and keeps the last write, so what a
  // user reads is this map — not the publish call sequence. Publish-side metrics exonerated the
  // assembly stage once already while the store still held a doubled sentence; only a store model
  // shows that, and it is the framework's `integrity` axis (FRAMEWORK.md G6).
  const store = new Map<string, { speaker: string; text: string }>();

  // ── Attribution, scored WITHOUT ground truth (#849) ──────────────────────────────────────────
  // Nobody has to label this session. Four signals answer "is the namer working?" from the
  // pipeline's own outputs: a turn that never got a name, a hint that named nothing, a name that
  // could not make up its mind, and a speaker count that does not match the room.
  let hintMatched = 0, hintMissed = 0;
  const renames: Array<{ from: string; to: string; n: number }> = [];
  const names = new Map<string, string[]>();
  const nameHistory = (id: string): string[] => {
    let h = names.get(id);
    if (!h) { h = []; names.set(id, h); }
    return h;
  };
  /** `seg_<n>` is the segmenter's provisional cluster id — a turn published before any hint claimed it. */
  const isProvisional = (s: string): boolean => /^seg_\d+$/.test(s);

  let emitBoundary!: (ev: BoundaryEvent) => void;
  const tc = await ChunkedTranscriber.create({
    language: LANG,
    transcribe: async (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult> => {
      sttCalls++;
      const dur = pcm.length / SR;
      submitSecs.push(dur);
      // MOCK mode is legitimate for THIS defect and only this one. The question is structural —
      // how many of the words STT handed the pipeline reach the transcript — so a stand-in that
      // emits a known word count per second of audio measures retention EXACTLY, and does it
      // deterministically and without a 10-minute CPU-whisper round. It says nothing about ASR
      // quality, and must never be used to claim any.
      if (MOCK) {
        const n = Math.max(1, Math.round(dur * 2.5));   // ~150 wpm, ordinary speech
        const text = Array.from({ length: n }, (_, i) => `w${sttCalls}x${i}`).join(' ');
        sttWords += n;
        return { text, language: LANG, duration: dur, segments: [{ start: 0, end: dur, text }] } as TranscriptionResult;
      }
      try {
        const r = await client.transcribe(pcm, LANG, prompt);
        sttWords += words(r.text).length;
        return r;
      } catch (e) { sttFails++; throw e; }
    },
    // ONE atomic bundle — newly-confirmed AND the surviving pending tail — is what production ships
    // (services/bot/src/pipeline.ts). Dropping `pending` here is what a store model must not do:
    // the tail is a row like any other, and an identity defect lives entirely in its id.
    publish: (speaker, confirmed, pending) => {
      for (const c of confirmed) {
        published.push({ speaker, text: c.text, startMs: c.startMs, endMs: c.endMs, id: c.segmentId, via: 'publish' });
        store.set(c.segmentId, { speaker, text: c.text });
        nameHistory(c.segmentId).push(speaker);
      }
      for (const p of pending) store.set(p.segmentId, { speaker, text: p.text });
    },
    // A draft is not the CONTENT oracle, but it IS a row in the store. That is what makes an
    // identity defect visible: a draft that later confirms under a different id leaves its own row
    // behind forever, and the reader sees the sentence twice however correct `publish` was.
    publishPending: (speaker, segments) => {
      for (const s of segments) store.set(s.segmentId, { speaker, text: s.text });
    },
    clearPending: () => { /* a client-side clear is not a delete; the store keeps what it was told */ },
    rename: (oldS, newS, segments) => {
      renames.push({ from: oldS, to: newS, n: segments?.length ?? 0 });
      for (const p of published) if (p.speaker === oldS) p.speaker = newS;
      // The store repaints in place — same ids, new name — so the attribution a reader ends up
      // with is the LAST name each row was given, not the first.
      for (const s of segments ?? []) { const row = store.get(s.segmentId); if (row) row.speaker = newS; }
      for (const s of segments ?? []) nameHistory(s.segmentId).push(newS);
    },
    // The binder's own instantaneous verdict per hint. It has always been emitted and never read —
    // #797's "hints detected, silently discarded" is exactly this counter going unwatched.
    onHintOutcome: (o) => { if (o.outcome === 'matched') hintMatched++; else hintMissed++; },
    // Two cut sources, and which one is right depends on where the fixture came from. A BOT records
    // production's own boundary events, so replaying them chunks the session exactly as the meeting
    // chunked — the faithful choice, and the one that lets MIN_TURN_MS be swept against real cuts.
    // A DESKTOP TAPE is recorded upstream of the pipeline and carries none; stubbing the segmenter
    // there would score one 20-minute turn and call it the lane. So run the REAL PyannoteSegmenter
    // (the model loads locally) — strictly more faithful than pretending the cuts were the stub's.
    makeSegmenter: (onBoundary) => {
      emitBoundary = onBoundary;
      if (!REAL_SEG) return Promise.resolve<BoundarySource>({ appendFrame: async () => { /* */ }, reset() { /* */ } });
      return PyannoteSegmenter.create({ inferIntervalMs: 500, onBoundary }) as unknown as Promise<BoundarySource>;
    },
    log: () => { /* quiet */ },
  });

  type Ev = { t: number; frame?: Frame; hint?: Hint; cut?: Cut };
  const timeline: Ev[] = [
    ...frames.map((f) => ({ t: f.ts, frame: f })),
    ...hints.map((h) => ({ t: h.t, hint: h })),
    ...cuts.map((c) => ({ t: c.tMs, cut: c })),
  ].sort((a, b) => a.t - b.t);

  // MIN_TURN_MS coalescing, applied to the RECORDED cut stream so the knob can be swept against
  // the exact boundaries production emitted.
  let lastCutMs = -Infinity, cutsEmitted = 0, cutsSuppressed = 0;
  const t0 = timeline[0].t;
  // The synthetic opening cut exists so a recorded-cut replay has a turn to accumulate into. With
  // the real segmenter the first cut is the segmenter's own job, and injecting one ahead of it
  // would open a turn the model never opened.
  if (!REAL_SEG) { emitBoundary({ kind: 'silence→speaker', tMs: t0, confidence: 0.9 }); lastCutMs = t0; }

  for (const ev of timeline) {
    if (ev.cut) {
      // CLOSE_ONLY: coalesce only boundaries that would END a turn early. Suppressing an
      // OPENING boundary means the turn never opens and the audio arriving then has nowhere to
      // accumulate — which is why blanket suppression lost content.
      const closes = ev.cut.kind === 'speaker→silence' || ev.cut.kind === 'overlap-offset';
      const tooSoon = ev.cut.tMs - lastCutMs < MIN_TURN_MS;
      if (tooSoon && (!CLOSE_ONLY || closes)) { cutsSuppressed++; continue; }
      emitBoundary({ kind: ev.cut.kind as BoundaryEvent['kind'], tMs: ev.cut.tMs, confidence: ev.cut.confidence ?? 0.9 });
      lastCutMs = ev.cut.tMs; cutsEmitted++;
    } else if (ev.hint) {
      tc.recordHint(ev.hint.name, 'dom-active', ev.hint.t, ev.hint.isEnd);
    } else if (ev.frame) {
      tc.feedAudio(framePcm(ev.frame), ev.frame.ts);
      // Yield between frames. feedAudio is synchronous, so a tight loop feeds the WHOLE session
      // before the lane's async pump runs once — audio is evicted from the 120s ring before anything
      // reads it, and the run scores the leftovers rather than the session. Yielding lets the pump
      // interleave; the replay is still far faster than real time.
      await sleep(0);
    }
  }
  // Closing the last turn is the harness standing in for the meeting ending — true for either cut
  // source, since a segmenter only ever sees audio that arrived.
  emitBoundary({ kind: 'speaker→silence', tMs: timeline[timeline.length - 1].t, confidence: 0.9 });
  await sleep(2000);
  await tc.dispose();

  // ── Score ───────────────────────────────────────────────────────────────────────────────
  const wallSec = (timeline[timeline.length - 1].t - t0) / 1000;
  const audioSec = frames.reduce((n, f) => n + f.pcm_len / SR, 0);
  published.sort((a, b) => a.startMs - b.startMs);
  const covered = published.reduce((n, p) => n + Math.max(0, p.endMs - p.startMs) / 1000, 0);
  const txWords = published.reduce((n, p) => n + words(p.text).length, 0);
  const durs = published.map((p) => (p.endMs - p.startMs) / 1000).sort((a, b) => a - b);
  const holes: Array<[number, number]> = [];
  let prev = t0;
  for (const p of published) {
    if ((p.startMs - prev) / 1000 > 2) holes.push([(prev - t0) / 1000, (p.startMs - t0) / 1000]);
    prev = Math.max(prev, p.endMs);
  }
  const dupes = published.length - new Set(published.map((p) => p.text.trim())).size;
  // The store keys on segment_id, so THAT is the unit a consumer actually keeps.
  const byId = new Map<string, typeof published[number]>();
  for (const p of published) byId.set(p.id, p);
  const idWords = [...byId.values()].reduce((n, p) => n + words(p.text).length, 0);
  console.log(`  publish calls ${published.length} -> unique segment_ids ${byId.size} (repeat publishes: ${published.length - byId.size})`);
  console.log(`  words after upsert-by-id: ${idWords} of ${sttWords} STT words = ${(idWords / Math.max(1, sttWords)).toFixed(3)}`);
  const sub = submitSecs.slice().sort((a, b) => a - b);

  console.log(`fixture: ${frames.length} frames · ${audioSec.toFixed(1)}s audio over ${wallSec.toFixed(1)}s wall · ${hints.length} hints · ${cuts.length} recorded cuts`);
  console.log(`cuts: ${cutsEmitted} emitted, ${cutsSuppressed} suppressed (MIN_TURN_MS=${MIN_TURN_MS})`);
  console.log(`stt: ${sttCalls} calls (${sttFails} failed) · submitted p50 ${(sub[Math.floor(sub.length / 2)] ?? 0).toFixed(2)}s · under1s ${sub.filter((d) => d < 1).length}/${sub.length}`);
  console.log(`\n  coverage      ${(covered / wallSec).toFixed(3)}  (${covered.toFixed(1)}s of ${wallSec.toFixed(1)}s wall)`);
  console.log(`  word retention ${(txWords / Math.max(1, sttWords)).toFixed(3)}  (${txWords} of ${sttWords} words STT returned reached the transcript)`);
  console.log(`  segments      ${published.length} · p50 dur ${(durs[Math.floor(durs.length / 2)] ?? 0).toFixed(2)}s · under1s ${durs.filter((d) => d < 1).length}`);
  console.log(`  holes >2s     ${holes.length}${holes.length ? ' → ' + holes.map(([a, b]) => `${a.toFixed(1)}-${b.toFixed(1)}`).join(', ') : ''}`);
  console.log(`  duplicates    ${dupes}`);
  console.log(`\nSWEEP mock=${MOCK ? 1 : 0} closeOnly=${CLOSE_ONLY ? 1 : 0} min_turn_ms=${MIN_TURN_MS} coverage=${(covered / wallSec).toFixed(3)} retention=${(txWords / Math.max(1, sttWords)).toFixed(3)} segs=${published.length} words=${txWords} sttWords=${sttWords} holes=${holes.length} calls=${sttCalls}`);

  // The store as a reader sees it: rows, and how many of them are the same sentence twice.
  const storeRows = [...store.values()];
  const storeTexts = storeRows.map((r) => r.text.trim()).filter(Boolean);
  const storeDupes = storeTexts.length - new Set(storeTexts).size;
  console.log(`  store         ${storeRows.length} rows · ${storeDupes} duplicate texts (upsert-by-segment_id, drafts included)`);

  // ── Attribution (#849) — four signals, no ground truth required ─────────────────────────────
  const storeWords = (r: { text: string }): number => words(r.text).length;
  const totalWords = storeRows.reduce((n, r) => n + storeWords(r), 0);
  const provisionalWords = storeRows.filter((r) => isProvisional(r.speaker)).reduce((n, r) => n + storeWords(r), 0);
  // Churn counts turns whose name settled on something OTHER than what it was first given. A turn
  // renamed A→B→A is defective whichever name is right, so distinctness is the measure, not length.
  const churned = [...names.values()].filter((h) => new Set(h).size > 1).length;
  const publishedSpeakers = new Set(storeRows.map((r) => r.speaker).filter((s) => !isProvisional(s)));
  const hintNames = new Set(hints.map((h) => h.name));
  const attribution = {
    provisionalRate: Number((provisionalWords / Math.max(1, totalWords)).toFixed(3)),
    hintMatched, hintMissed,
    hintMissRate: Number((hintMissed / Math.max(1, hintMatched + hintMissed)).toFixed(3)),
    renames: renames.length,
    churnedTurns: churned,
    publishedSpeakers: publishedSpeakers.size,
    hintNames: hintNames.size,
  };
  console.log(`  attribution   provisional ${(attribution.provisionalRate * 100).toFixed(1)}% of words · ` +
    `hints ${hintMatched} matched / ${hintMissed} missed (${(attribution.hintMissRate * 100).toFixed(1)}% miss) · ` +
    `${renames.length} renames, ${churned} turns churned · speakers ${publishedSpeakers.size} published vs ${hintNames.size} hinted`);

  // A corpus baseline is this block, not the prose above it: a later run diffs against it, so a
  // regression is a failing comparison rather than something someone has to notice in a log.
  if (process.env.METRICS_JSON) {
    writeFileSync(process.env.METRICS_JSON, JSON.stringify({
      mock: MOCK, minTurnMs: MIN_TURN_MS, closeOnly: CLOSE_ONLY,
      sttCalls, sttWords, sttFails,
      publishCalls: published.length, uniqueSegmentIds: byId.size,
      storeRows: storeRows.length, storeDupes,
      publishedWords: txWords,
      // Retention is only a LOSS metric against real STT. The mock invents fresh tokens per call,
      // so every LocalAgreement resubmission of the same audio inflates the denominator with words
      // that were never new — retention then measures resubmission overlap and calls it loss. It is
      // recorded only when the words on both sides came from the same answer to the same audio.
      ...(MOCK ? {} : { retention: Number((txWords / Math.max(1, sttWords)).toFixed(3)) }),
      coverage: Number((covered / wallSec).toFixed(3)),
      segments: published.length,
      segP50Sec: Number((durs[Math.floor(durs.length / 2)] ?? 0).toFixed(2)),
      segUnder1s: durs.filter((d) => d < 1).length,
      holesOver2s: holes.length,
      ...attribution,
    }, null, 2) + '\n');
    console.log(`  metrics written: ${process.env.METRICS_JSON}`);
  }

  console.log('\n--- transcript ---');
  for (const p of published) console.log(`  [${((p.startMs - t0) / 1000).toFixed(2)}-${((p.endMs - t0) / 1000).toFixed(2)}] ${p.speaker}: ${p.text.slice(0, 80)}`);
}

void main();
