/**
 * Transcript-correctness + boundary-correctness optimization runner.
 *
 * For every corpus WAV in eval/corpus/:
 *   1. Stream the audio through OnnxLocalDiarizer → collect commits.
 *   2. For each commit, slice the audio for [tStartMs, tEndMs] and send
 *      that slice directly to the transcription service (Whisper). This
 *      bypasses SpeakerStreamManager's word-prefix-confirmation streaming
 *      so we evaluate the diarizer's segmentation in isolation: "given
 *      this commit's audio chunk, what does Whisper transcribe?"
 *   3. For each commit, identify the GT speaker for the commit's time
 *      range (the dominant-overlap speaker, same as segmentPurity in
 *      run-suite.ts). Build the GT text for that range by concatenating
 *      every GT turn that overlaps the commit, weighted by overlap.
 *   4. Score:
 *        - text quality:  normalized similarity between Whisper text and
 *          GT text via word-error-rate (WER). 1.0 - WER is the "quality".
 *        - boundary quality: a commit straddling 2 GT speakers gets a
 *          penalty proportional to the minority speaker's share — that
 *          is the "Whisper sees mixed audio" failure mode.
 *        - composite = (1 - WER) × purity   (both in [0..1])
 *
 *   5. Aggregate across all corpora, weighted by audio duration. The
 *      result is a single number suitable for autonomous tuning.
 *
 * Usage:
 *   npx tsx eval/run-transcript-score.ts
 *   TRANSCRIPTION_URL=http://localhost:8085 npx tsx eval/run-transcript-score.ts
 */

import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { TranscriptionClient } from '../../../core/src/services/transcription-client';
import { OnnxLocalDiarizer, type CommitEvent } from '../src/onnx-local-diarizer';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CORPUS_DIR = path.join(__dirname, 'corpus');
const SAMPLE_RATE = 16_000;
const FRAME_SAMPLES = 1024;

const TRANSCRIPTION_URL = process.env.TRANSCRIPTION_URL ?? 'http://localhost:8085';
const TRANSCRIPTION_API_TOKEN = process.env.TRANSCRIPTION_API_TOKEN ?? '';

interface GroundTruth {
  id: string;
  turns: Array<{ speaker: string; text: string; start_ms: number; end_ms: number; duration_ms: number }>;
  total_duration_ms: number;
}

async function readWav16kMono(wavPath: string): Promise<Float32Array> {
  const buf = await fs.readFile(wavPath);
  let offset = 12;
  let dataOffset = -1;
  let dataLength = -1;
  let sampleRate = 0;
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === 'fmt ') sampleRate = buf.readUInt32LE(offset + 12);
    else if (id === 'data') {
      dataOffset = offset + 8;
      dataLength = size;
    }
    offset += 8 + size + (size % 2);
  }
  if (sampleRate !== SAMPLE_RATE) throw new Error(`${wavPath}: ${sampleRate} Hz, expected ${SAMPLE_RATE}`);
  const numSamples = dataLength / 2;
  const samples = new Float32Array(numSamples);
  for (let i = 0; i < numSamples; i++) samples[i] = buf.readInt16LE(dataOffset + i * 2) / 32768.0;
  return samples;
}

/** Normalize text for comparison: lowercase, collapse whitespace, strip
 *  punctuation that Whisper and the GT script disagree on (commas, dots,
 *  exclamation marks, hyphens that became spaces). */
function normalizeForWER(s: string): string[] {
  return s
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s']/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .split(' ')
    .filter(Boolean);
}

/** Word-error rate via Levenshtein on word arrays. Returns ratio in [0,1+).
 *  Capped at 1.0 for clamping to "quality = 1 - WER" being non-negative. */
function wordErrorRate(refText: string, hypText: string): number {
  const ref = normalizeForWER(refText);
  const hyp = normalizeForWER(hypText);
  if (ref.length === 0 && hyp.length === 0) return 0;
  if (ref.length === 0) return 1;
  const n = ref.length;
  const m = hyp.length;
  // DP table; rows = ref, cols = hyp
  let prev = new Int32Array(m + 1);
  let cur = new Int32Array(m + 1);
  for (let j = 0; j <= m; j++) prev[j] = j;
  for (let i = 1; i <= n; i++) {
    cur[0] = i;
    for (let j = 1; j <= m; j++) {
      if (ref[i - 1] === hyp[j - 1]) cur[j] = prev[j - 1];
      else cur[j] = 1 + Math.min(prev[j - 1], prev[j], cur[j - 1]);
    }
    [prev, cur] = [cur, prev];
  }
  return Math.min(1.0, prev[m] / n);
}

interface CommitScore {
  tStartMs: number;
  tEndMs: number;
  durationMs: number;
  speakerCluster: string;
  /** GT text covered by the commit's time range, built by concatenating
   *  every GT turn (weighted by audio overlap) within [tStart, tEnd]. */
  gtText: string;
  /** Dominant GT speaker (by audio time) within the commit window. */
  dominantGtSpeaker: string;
  /** Fraction of the commit's audio time covered by the dominant GT
   *  speaker. 1.0 = pure (one speaker), <1.0 = straddled. */
  purity: number;
  /** Whisper's transcription of the commit's audio slice. */
  predictedText: string;
  /** WER of predictedText vs gtText. Lower = better. */
  wer: number;
  /** Composite per-commit score: (1 - wer) * purity. Higher = better. */
  composite: number;
}

interface CorpusScore {
  id: string;
  durationMs: number;
  commitScores: CommitScore[];
  /** Audio-time-weighted mean of composite per-commit scores. */
  meanComposite: number;
  /** Same but mean of (1 - WER). */
  meanTranscriptQuality: number;
  /** Same but mean of purity. */
  meanPurity: number;
  /** Boundary recall @ ±500ms (also reported in run-suite.ts). */
  boundaryRecall: number;
}

async function scoreCorpus(id: string, samples: Float32Array, gt: GroundTruth, transcription: TranscriptionClient): Promise<CorpusScore> {
  // Pass 1: stream through diarizer, collect commits.
  const commits: CommitEvent[] = [];
  const diarizer = await OnnxLocalDiarizer.create({ onCommit: (ev) => commits.push(ev) });
  for (let off = 0; off + FRAME_SAMPLES <= samples.length; off += FRAME_SAMPLES) {
    await diarizer.process(samples.subarray(off, off + FRAME_SAMPLES), Math.round((off / SAMPLE_RATE) * 1000));
  }
  if ((diarizer as any).utteranceSamples > 0) await (diarizer as any).commitUtterance();

  // Apply post-hoc rewrites just like run-suite does — this only affects
  // the cluster label, not the commit time range.
  const labelRewrites = diarizer.getLabelRewrites();
  const commitRewrites = (diarizer as any).getCommitRewrites?.() as Map<string, string> | undefined;
  for (const c of commits) {
    let target = c.speakerId;
    while (labelRewrites.has(target)) target = labelRewrites.get(target)!;
    c.speakerId = target;
    if (commitRewrites) {
      const key = `${c.tStartMs}-${c.tEndMs}`;
      const cr = commitRewrites.get(key);
      if (cr) c.speakerId = cr;
    }
  }

  // Pass 2: score each commit.
  const commitScores: CommitScore[] = [];
  for (const c of commits) {
    // Give Whisper ±150ms of context audio outside the commit window so
    // words straddling the boundary still get fully heard. This is a
    // transcription-only padding; the boundary timing itself is unchanged.
    const CONTEXT_MS = 300;
    const startSample = Math.max(0, Math.floor(((c.tStartMs - CONTEXT_MS) / 1000) * SAMPLE_RATE));
    const endSample = Math.min(samples.length, Math.floor(((c.tEndMs + CONTEXT_MS) / 1000) * SAMPLE_RATE));
    if (endSample - startSample < SAMPLE_RATE * 0.3) {
      // <300ms — skip; too short to score reliably.
      continue;
    }
    const rawSlice = samples.subarray(startSample, endSample);
    // Trim leading/trailing silence AND collapse long internal silence
    // gaps (>500ms of contiguous sub-threshold audio). Hysteresis padding
    // and mid-monologue pauses both confuse Whisper into hallucinating
    // filler — but mid-word breaths (~50-200ms) are useful context, so
    // we collapse only sufficiently-long quiet stretches.
    const WIN = 512;             // ~32ms at 16kHz
    const SILENCE_RMS = 0.006;
    const PAD_WINS = 4;          // ~128ms pad around speech
    const MIN_GAP_WINS = 16;     // ~512ms → collapse only if gap exceeds this
    const nWins = Math.floor(rawSlice.length / WIN);
    const winIsSpeech: boolean[] = new Array(nWins);
    let firstSpeechWin = -1;
    let lastSpeechWin = -1;
    for (let w = 0; w < nWins; w++) {
      let sumSq = 0;
      for (let j = 0; j < WIN; j++) sumSq += rawSlice[w * WIN + j] * rawSlice[w * WIN + j];
      const rms = Math.sqrt(sumSq / WIN);
      winIsSpeech[w] = rms >= SILENCE_RMS;
      if (winIsSpeech[w]) {
        if (firstSpeechWin < 0) firstSpeechWin = w;
        lastSpeechWin = w;
      }
    }
    if (firstSpeechWin < 0) continue;
    // Build keep-mask: start with trimmed range (firstSpeech..lastSpeech),
    // then within it scan for runs of consecutive non-speech windows of
    // length >= MIN_GAP_WINS; drop those runs (with PAD_WINS preserved on
    // each side to keep breath context).
    const trimStart = Math.max(0, firstSpeechWin - PAD_WINS);
    const trimEnd = Math.min(nWins - 1, lastSpeechWin + PAD_WINS);
    const keepWin: boolean[] = new Array(nWins).fill(false);
    for (let w = trimStart; w <= trimEnd; w++) keepWin[w] = true;
    let runStart = -1;
    for (let w = trimStart; w <= trimEnd + 1; w++) {
      const silent = w <= trimEnd ? !winIsSpeech[w] : false;
      if (silent && runStart < 0) runStart = w;
      else if (!silent && runStart >= 0) {
        const runLen = w - runStart;
        if (runLen >= MIN_GAP_WINS) {
          // Drop middle of the run, keep PAD_WINS at each end.
          for (let k = runStart + PAD_WINS; k < w - PAD_WINS; k++) keepWin[k] = false;
        }
        runStart = -1;
      }
    }
    const keptChunks: Float32Array[] = [];
    for (let w = 0; w < nWins; w++) {
      if (keepWin[w]) keptChunks.push(rawSlice.subarray(w * WIN, (w + 1) * WIN));
    }
    if (keptChunks.length === 0) continue;
    const slice = new Float32Array(keptChunks.reduce((a, c) => a + c.length, 0));
    let pos = 0;
    for (const k of keptChunks) { slice.set(k, pos); pos += k.length; }
    if (slice.length < SAMPLE_RATE * 0.3) continue;

    // Build GT text + dominant-speaker for this commit's range.
    const perSpeakerMs = new Map<string, number>();
    const turnsHit: Array<{ overlap: number; text: string; speaker: string }> = [];
    for (const t of gt.turns) {
      const overlap = Math.max(0, Math.min(t.end_ms, c.tEndMs) - Math.max(t.start_ms, c.tStartMs));
      if (overlap <= 0) continue;
      perSpeakerMs.set(t.speaker, (perSpeakerMs.get(t.speaker) ?? 0) + overlap);
      turnsHit.push({ overlap, text: t.text, speaker: t.speaker });
    }
    const totalOverlap = [...perSpeakerMs.values()].reduce((a, b) => a + b, 0);
    if (totalOverlap === 0) {
      // Commit didn't overlap any GT turn (probably silence). Skip.
      continue;
    }
    let dominantSpeaker = '';
    let dominantMs = 0;
    for (const [spk, ms] of perSpeakerMs) {
      if (ms > dominantMs) {
        dominantMs = ms;
        dominantSpeaker = spk;
      }
    }
    const purity = dominantMs / totalOverlap;

    // GT text for the commit's window. We can't take a turn's FULL text —
    // a commit might cover only the first 3s of a 30s turn, comparing
    // against the whole turn's text would give a bogus WER. Instead, for
    // each dominant-speaker turn that overlaps the commit, slice the
    // turn's text proportionally by the time coverage:
    //
    //   coverage_start_frac = (commit.tStart - turn.start_ms) / turn.duration
    //   coverage_end_frac   = (commit.tEnd   - turn.start_ms) / turn.duration
    //   slice = turn.text.words[ start_frac*n : end_frac*n ]
    //
    // Clamp fractions to [0,1]. This is approximate (Piper's speech rate
    // is roughly constant per turn) but much better than the whole-turn
    // comparison.
    const gtPieces: string[] = [];
    for (const t of gt.turns) {
      if (t.speaker !== dominantSpeaker) continue;
      const overlap = Math.max(0, Math.min(t.end_ms, c.tEndMs) - Math.max(t.start_ms, c.tStartMs));
      if (overlap <= 0) continue;
      const turnDur = Math.max(1, t.end_ms - t.start_ms);
      const startFrac = Math.max(0, Math.min(1, (c.tStartMs - t.start_ms) / turnDur));
      const endFrac = Math.max(0, Math.min(1, (c.tEndMs - t.start_ms) / turnDur));
      const words = t.text.split(/\s+/).filter(Boolean);
      const a = Math.floor(startFrac * words.length);
      const b = Math.ceil(endFrac * words.length);
      const slice = words.slice(a, b).join(' ').trim();
      if (slice) gtPieces.push(slice);
    }
    const gtText = gtPieces.join(' ').trim();

    // Transcribe the slice.
    let predictedText = '';
    try {
      const result = await transcription.transcribe(slice);
      predictedText = (result.text || '').trim();
    } catch (err: any) {
      console.warn(`[score]   transcription error for ${id} commit ${c.tStartMs}-${c.tEndMs}: ${err.message}`);
      predictedText = '';
    }

    const wer = wordErrorRate(gtText, predictedText);
    const transcriptQuality = 1 - wer;
    const composite = transcriptQuality * purity;

    commitScores.push({
      tStartMs: c.tStartMs,
      tEndMs: c.tEndMs,
      durationMs: c.tEndMs - c.tStartMs,
      speakerCluster: c.speakerId,
      gtText,
      dominantGtSpeaker: dominantSpeaker,
      purity,
      predictedText,
      wer,
      composite,
    });
  }

  // Audio-time-weighted means.
  const totalMs = commitScores.reduce((a, c) => a + c.durationMs, 0) || 1;
  const meanComposite = commitScores.reduce((a, c) => a + c.composite * c.durationMs, 0) / totalMs;
  const meanTranscriptQuality = commitScores.reduce((a, c) => a + (1 - c.wer) * c.durationMs, 0) / totalMs;
  const meanPurity = commitScores.reduce((a, c) => a + c.purity * c.durationMs, 0) / totalMs;

  // Boundary recall @ ±500ms.
  const TOLERANCE_MS = 500;
  const commitBoundaries = commits.map((c) => c.tStartMs).sort((a, b) => a - b);
  const changes: number[] = [];
  for (let i = 1; i < gt.turns.length; i++) {
    if (gt.turns[i].speaker !== gt.turns[i - 1].speaker) changes.push(gt.turns[i].start_ms);
  }
  let hits = 0;
  for (const change of changes) {
    let lo = 0, hi = commitBoundaries.length;
    while (lo < hi) {
      const mid = (lo + hi) >>> 1;
      if (commitBoundaries[mid] < change) lo = mid + 1; else hi = mid;
    }
    const left = lo > 0 ? Math.abs(commitBoundaries[lo - 1] - change) : Infinity;
    const right = lo < commitBoundaries.length ? Math.abs(commitBoundaries[lo] - change) : Infinity;
    if (Math.min(left, right) <= TOLERANCE_MS) hits++;
  }
  const boundaryRecall = changes.length > 0 ? hits / changes.length : 1;

  return {
    id,
    durationMs: Math.round((samples.length / SAMPLE_RATE) * 1000),
    commitScores,
    meanComposite,
    meanTranscriptQuality,
    meanPurity,
    boundaryRecall,
  };
}

async function main(): Promise<number> {
  const transcription = new TranscriptionClient({
    serviceUrl: TRANSCRIPTION_URL,
    apiToken: TRANSCRIPTION_API_TOKEN || undefined,
    sampleRate: SAMPLE_RATE,
  });

  // Probe the transcription service first.
  try {
    const probeUrl = TRANSCRIPTION_URL.replace(/\/+$/, '').replace(/\/v1\/audio\/transcriptions$/, '') + '/health';
    const r = await fetch(probeUrl, { signal: AbortSignal.timeout(3000) });
    if (!r.ok) throw new Error(`probe HTTP ${r.status}`);
    console.log(`[score] transcription service reachable: ${TRANSCRIPTION_URL}`);
  } catch (err: any) {
    console.error(`[score] transcription service NOT reachable at ${TRANSCRIPTION_URL}: ${err.message}`);
    console.error('[score] aborting — this eval needs a live Whisper backend');
    return 1;
  }

  const entries = await fs.readdir(CORPUS_DIR);
  const wavs = entries.filter((e) => e.endsWith('.wav')).sort();
  console.log(`[score] ${wavs.length} corpora`);

  const results: CorpusScore[] = [];
  for (const wav of wavs) {
    const id = wav.replace(/\.wav$/, '');
    const gtPath = path.join(CORPUS_DIR, `${id}.ground-truth.json`);
    try { await fs.access(gtPath); } catch { console.log(`[score]   ${id}: SKIP (no ground-truth.json)`); continue; }
    const gt = JSON.parse(await fs.readFile(gtPath, 'utf-8')) as GroundTruth;
    const samples = await readWav16kMono(path.join(CORPUS_DIR, wav));
    console.log(`[score]   ${id}: ${(samples.length / SAMPLE_RATE).toFixed(1)}s, ${gt.turns.length} GT turns`);
    const r = await scoreCorpus(id, samples, gt, transcription);
    results.push(r);
    console.log(
      `[score]   → ${r.commitScores.length} commits scored ` +
        `· transcript=${(r.meanTranscriptQuality * 100).toFixed(1)}% ` +
        `· purity=${(r.meanPurity * 100).toFixed(1)}% ` +
        `· composite=${(r.meanComposite * 100).toFixed(1)}% ` +
        `· boundary recall=${(r.boundaryRecall * 100).toFixed(1)}%`,
    );
    // Persist per-corpus details for offline inspection.
    await fs.writeFile(
      path.join(CORPUS_DIR, `${id}.transcript-score.json`),
      JSON.stringify(r, null, 2),
      'utf-8',
    );
  }

  // Aggregate, audio-weighted across corpora.
  const totalMs = results.reduce((a, r) => a + r.durationMs, 0) || 1;
  const aggTranscript = results.reduce((a, r) => a + r.meanTranscriptQuality * r.durationMs, 0) / totalMs;
  const aggPurity = results.reduce((a, r) => a + r.meanPurity * r.durationMs, 0) / totalMs;
  const aggComposite = results.reduce((a, r) => a + r.meanComposite * r.durationMs, 0) / totalMs;
  const aggRecall = results.reduce((a, r) => a + r.boundaryRecall * r.durationMs, 0) / totalMs;

  console.log();
  console.log('═══════════════════ TRANSCRIPT + BOUNDARY SCORE ═══════════════════');
  for (const r of results) {
    console.log(
      `  ${r.id.padEnd(32)}  transcript=${(r.meanTranscriptQuality * 100).toFixed(1).padStart(5)}%  ` +
        `purity=${(r.meanPurity * 100).toFixed(1).padStart(5)}%  ` +
        `composite=${(r.meanComposite * 100).toFixed(1).padStart(5)}%  ` +
        `recall=${(r.boundaryRecall * 100).toFixed(1).padStart(5)}%`,
    );
  }
  console.log();
  // BALANCED metric: transcript × purity × recall. Rewards systems that
  // get all three right rather than optimizing one at the expense of the
  // others. This is the right single number for the user-asked-for
  // optimization: "correct transcript AND correct speech boundaries".
  const aggBalanced = aggTranscript * aggPurity * aggRecall;
  console.log(
    `OVERALL  transcript=${(aggTranscript * 100).toFixed(1)}%  purity=${(aggPurity * 100).toFixed(1)}%  ` +
      `recall=${(aggRecall * 100).toFixed(1)}%  composite=${(aggComposite * 100).toFixed(1)}%  ` +
      `BALANCED=${(aggBalanced * 100).toFixed(1)}%`,
  );
  console.log(`SCORE  balanced=${(aggBalanced * 100).toFixed(1)}  transcript=${(aggTranscript * 100).toFixed(1)}  purity=${(aggPurity * 100).toFixed(1)}  recall=${(aggRecall * 100).toFixed(1)}  composite=${(aggComposite * 100).toFixed(1)}`);
  return 0;
}

main().then((c) => process.exit(c)).catch((err) => { console.error('[score] fatal:', err); process.exit(1); });
