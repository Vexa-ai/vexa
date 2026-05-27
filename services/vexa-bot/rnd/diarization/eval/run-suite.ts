/**
 * Run the entire eval suite (every conversation that has both
 * <id>.wav and <id>.ground-truth.json in eval/corpus/) through the
 * diarizer, then print a compact pass/fail table:
 *
 *   per-corpus:
 *     per ground-truth speaker:
 *       set of predicted cluster ids
 *       ✓ if size = 1, ✗ if split
 *
 * Iteration loop: tweak threshold/seed-gate/EMA → `npm run eval:suite`
 * → read the table. Stop when every row is ✓.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { OnnxLocalDiarizer, type CommitEvent } from '../src/onnx-local-diarizer';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CORPUS_DIR = path.join(__dirname, 'corpus');
const SAMPLE_RATE = 16_000;
const FRAME_SAMPLES = 1024;

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
    if (id === 'fmt ') {
      sampleRate = buf.readUInt32LE(offset + 12);
    } else if (id === 'data') {
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

async function runOne(diarizer: OnnxLocalDiarizer, id: string): Promise<CommitEvent[]> {
  const wavPath = path.join(CORPUS_DIR, `${id}.wav`);
  const samples = await readWav16kMono(wavPath);
  const commits: CommitEvent[] = [];
  // Reattach onCommit per-corpus by reconstructing — but we want the same
  // diarizer state for re-runs. Cleaner: reset + capture via a per-run
  // ref. We re-create the diarizer per corpus to keep clusters independent.
  diarizer.reset();
  // attach by patching the listener — quick hack since onCommit is set at
  // construction. Workaround: we recreate the diarizer per corpus in main().
  for (let off = 0; off + FRAME_SAMPLES <= samples.length; off += FRAME_SAMPLES) {
    const frame = samples.subarray(off, off + FRAME_SAMPLES);
    const ts = Math.round((off / SAMPLE_RATE) * 1000);
    await diarizer.process(frame, ts);
  }
  if ((diarizer as any).utteranceSamples > 0) {
    await (diarizer as any).commitUtterance();
  }
  return commits;
}

interface CorpusResult {
  id: string;
  gtSpeakers: string[];
  predictedClusters: number;
  perSpeaker: Map<string, Set<string>>;
  /** Per GT speaker: primary cluster (covers most of their speaking time)
   *  + coverage fraction of that primary [0..1]. */
  primary: Map<string, { cluster: string; coverage: number }>;
  passCount: number;
  splitCount: number;
  /** "Useful" outcome: all GT speakers have a unique primary cluster AND
   *  every primary coverage ≥ 0.60. Kept for back-compat. */
  useful: boolean;
  /** Raw commits (post-label-rewrite). Used by boundary metrics. */
  commits: CommitEvent[];
  /** GT turn list (start/end/speaker). Used by boundary metrics. */
  gtTurns: GroundTruth['turns'];
  /** Per-commit segment purity. Each commit's audio time may overlap
   *  multiple GT speakers (a "straddle" = missed split). Pure fraction =
   *  dominant_speaker_overlap / total_commit_overlap. 1.0 = pure. */
  segmentPurity: Array<{
    tStartMs: number;
    tEndMs: number;
    dominantSpeaker: string;
    purity: number;
    overlapMs: number;
  }>;
  /** Collaborative attribution: per commit, the speaker assigned by
   *  blue-box majority vote inside the commit's window (shifted by lag).
   *  Then per GT speaker: total correctly-attributed audio time. */
  collabAccuracy: number;
  collabPerCommit: Array<{
    tStartMs: number;
    tEndMs: number;
    bluebox: string | null;
    truth: string;
    correct: boolean;
  }>;
}

/**
 * Synthetic blue-box stream.
 *
 * Models the MS Teams "who's-talking-now" UI indicator the bot would
 * otherwise scrape. Realistic properties:
 *
 *   - LAG: when a speaker starts at T, the blue box lights at T + lagMs.
 *     We use a fixed lag for the ground-truth turn boundaries; the END of
 *     the lit interval also shifts by lagMs (the box stays lit a beat after
 *     speech ends).
 *
 *   - FLICKER: occasionally an unrelated speaker's mic activates the wrong
 *     box for a short window (200–500 ms). Models open-mic / cough /
 *     room-noise capture that briefly hijacks the DOM signal.
 *
 *   - GAP: blue boxes can be momentarily UNLIT (no one), e.g. during a
 *     silence or a too-quiet utterance.
 *
 * The stream is a sorted list of `(ts, speaker|null)` switch events.
 * Lookups are: "what speaker was lit at time T?" via binary search.
 */
interface BlueBoxEvent {
  ts: number;
  speaker: string | null;
}

interface BlueBoxConfig {
  lagMs: number;
  /** Average flickers per minute of speech. */
  flickersPerMin: number;
  flickerMinMs: number;
  flickerMaxMs: number;
  /** Deterministic seed for reproducibility. */
  seed: number;
}

const DEFAULT_BLUEBOX: BlueBoxConfig = {
  lagMs: 1000,
  flickersPerMin: 6,
  flickerMinMs: 200,
  flickerMaxMs: 500,
  seed: 42,
};

function makeSeededRng(seed: number): () => number {
  let state = (seed >>> 0) || 1;
  return () => {
    // xorshift32 — adequate for noise sim
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return ((state >>> 0) % 1_000_000_007) / 1_000_000_007;
  };
}

function generateBlueBoxStream(gt: GroundTruth, cfg: BlueBoxConfig): BlueBoxEvent[] {
  // Build the base stream: each GT turn becomes a lit interval shifted by lag.
  // Adjacent turns from the same speaker merge into one lit interval (the box
  // doesn't blink between consecutive same-speaker turns).
  const speakers = Array.from(new Set(gt.turns.map((t) => t.speaker)));
  const intervals: Array<{ start: number; end: number; speaker: string }> = [];
  for (const t of gt.turns) {
    const start = t.start_ms + cfg.lagMs;
    const end = t.end_ms + cfg.lagMs;
    const prev = intervals[intervals.length - 1];
    if (prev && prev.speaker === t.speaker && start - prev.end < 250) {
      prev.end = Math.max(prev.end, end);
    } else {
      intervals.push({ start, end, speaker: t.speaker });
    }
  }

  // Insert flickers. Total flickers = flickersPerMin * total_speech_minutes.
  const rng = makeSeededRng(cfg.seed);
  const totalSpeechMs = intervals.reduce((s, i) => s + (i.end - i.start), 0);
  const nFlickers = Math.max(0, Math.floor((cfg.flickersPerMin * totalSpeechMs) / 60_000));
  const totalDuration = intervals.length > 0 ? intervals[intervals.length - 1].end : 0;
  const flickers: Array<{ start: number; end: number; speaker: string }> = [];
  for (let i = 0; i < nFlickers; i++) {
    const t = Math.floor(rng() * totalDuration);
    const dur = cfg.flickerMinMs + Math.floor(rng() * (cfg.flickerMaxMs - cfg.flickerMinMs));
    // Pick a speaker DIFFERENT from whoever is currently lit at t.
    const currentSpeaker = (() => {
      for (const iv of intervals) if (t >= iv.start && t < iv.end) return iv.speaker;
      return null;
    })();
    const candidates = speakers.filter((s) => s !== currentSpeaker);
    if (candidates.length === 0) continue;
    const flickerSpk = candidates[Math.floor(rng() * candidates.length)];
    flickers.push({ start: t, end: t + dur, speaker: flickerSpk });
  }

  // Emit events. At every ms boundary where the "currently lit" speaker
  // changes, emit one event. We don't actually iterate ms — we sweep
  // over the sorted set of (start, end) edges and recompute the topmost
  // lit speaker at each edge. Flickers take precedence over the base
  // interval (mic-hijack overrides).
  type Edge = { ts: number };
  const edges = new Set<number>();
  for (const iv of intervals) {
    edges.add(iv.start);
    edges.add(iv.end);
  }
  for (const iv of flickers) {
    edges.add(iv.start);
    edges.add(iv.end);
  }
  const sortedEdges = [...edges].sort((a, b) => a - b);
  const events: BlueBoxEvent[] = [];
  let lastSpk: string | null | undefined = undefined;
  const litAt = (t: number): string | null => {
    // Flickers take precedence.
    for (const iv of flickers) if (t >= iv.start && t < iv.end) return iv.speaker;
    for (const iv of intervals) if (t >= iv.start && t < iv.end) return iv.speaker;
    return null;
  };
  for (const ts of sortedEdges) {
    const spk = litAt(ts);
    if (spk !== lastSpk) {
      events.push({ ts, speaker: spk });
      lastSpk = spk;
    }
  }
  return events;
}

/** Given the sorted event stream, return the dominant speaker (most lit
 *  time) during the window [windowStart, windowEnd]. Returns null if the
 *  window only sees gaps. */
function dominantBlueBoxIn(events: BlueBoxEvent[], windowStart: number, windowEnd: number): string | null {
  if (events.length === 0 || windowEnd <= windowStart) return null;
  const tally = new Map<string, number>();
  for (let i = 0; i < events.length; i++) {
    const ts = events[i].ts;
    const next = i + 1 < events.length ? events[i + 1].ts : Number.POSITIVE_INFINITY;
    const a = Math.max(ts, windowStart);
    const b = Math.min(next, windowEnd);
    if (b <= a) continue;
    const spk = events[i].speaker;
    if (!spk) continue;
    tally.set(spk, (tally.get(spk) ?? 0) + (b - a));
  }
  let best: string | null = null;
  let bestMs = 0;
  for (const [spk, ms] of tally) {
    if (ms > bestMs) {
      bestMs = ms;
      best = spk;
    }
  }
  return best;
}

async function analyze(id: string, gt: GroundTruth, commits: CommitEvent[]): Promise<CorpusResult> {
  const perSpeaker = new Map<string, Set<string>>();
  // Per (gt_speaker, predicted_cluster) → milliseconds of GT-time overlap
  const timeMatrix = new Map<string, Map<string, number>>();
  const speakerTotalTime = new Map<string, number>();

  for (const t of gt.turns) {
    if (!perSpeaker.has(t.speaker)) {
      perSpeaker.set(t.speaker, new Set());
      timeMatrix.set(t.speaker, new Map());
    }
    speakerTotalTime.set(t.speaker, (speakerTotalTime.get(t.speaker) ?? 0) + (t.end_ms - t.start_ms));

    // Distribute this GT turn's time across all commits that overlap it
    for (const c of commits) {
      const overlap = Math.max(0, Math.min(t.end_ms, c.tEndMs) - Math.max(t.start_ms, c.tStartMs));
      if (overlap <= 0) continue;
      perSpeaker.get(t.speaker)!.add(c.speakerId);
      const row = timeMatrix.get(t.speaker)!;
      row.set(c.speakerId, (row.get(c.speakerId) ?? 0) + overlap);
    }
  }

  // Primary cluster per GT speaker = cluster with max time-overlap
  const primary = new Map<string, { cluster: string; coverage: number }>();
  for (const [spk, row] of timeMatrix) {
    let bestCluster = '';
    let bestTime = -1;
    for (const [cluster, ms] of row) {
      if (ms > bestTime) {
        bestTime = ms;
        bestCluster = cluster;
      }
    }
    const total = speakerTotalTime.get(spk) ?? 1;
    primary.set(spk, { cluster: bestCluster, coverage: bestTime / total });
  }

  let passCount = 0;
  let splitCount = 0;
  for (const labels of perSpeaker.values()) {
    if (labels.size === 1) passCount++; else splitCount++;
  }
  const allLabels = new Set<string>();
  for (const labels of perSpeaker.values()) labels.forEach((l) => allLabels.add(l));

  // "Useful" = unique primary clusters across all GT speakers AND every coverage ≥ 0.60
  const primaryClusters = new Set<string>();
  let minCoverage = 1.0;
  for (const p of primary.values()) {
    primaryClusters.add(p.cluster);
    if (p.coverage < minCoverage) minCoverage = p.coverage;
  }
  const useful = primaryClusters.size === primary.size && minCoverage >= 0.60;

  // Segment purity per commit: for each commit, distribute its audio time
  // across every overlapping GT turn (and so its speaker). Purity is the
  // dominant-speaker fraction. A purity < 1 means the commit straddles a
  // speaker change — that's a MISSED SPLIT, the failure mode we care most
  // about: Whisper would receive two voices in one buffer.
  const segmentPurity: CorpusResult['segmentPurity'] = [];
  for (const c of commits) {
    const perSpeakerOverlap = new Map<string, number>();
    let totalOverlap = 0;
    for (const t of gt.turns) {
      const overlap = Math.max(0, Math.min(t.end_ms, c.tEndMs) - Math.max(t.start_ms, c.tStartMs));
      if (overlap <= 0) continue;
      perSpeakerOverlap.set(t.speaker, (perSpeakerOverlap.get(t.speaker) ?? 0) + overlap);
      totalOverlap += overlap;
    }
    if (totalOverlap <= 0) continue;
    let bestSpk = '';
    let bestMs = 0;
    for (const [spk, ms] of perSpeakerOverlap) {
      if (ms > bestMs) {
        bestMs = ms;
        bestSpk = spk;
      }
    }
    segmentPurity.push({
      tStartMs: c.tStartMs,
      tEndMs: c.tEndMs,
      dominantSpeaker: bestSpk,
      purity: bestMs / totalOverlap,
      overlapMs: totalOverlap,
    });
  }

  // Collaborative attribution: simulate the blue-box stream the bot would
  // see in production (1s lag, periodic flicker), then for each commit
  // assign the speaker that the blue box says was talking at the same
  // wall-clock time. The commit's [tStartMs, tEndMs] is in audio time;
  // blue-box ts is also audio-anchored (we generate it from GT turn ts),
  // so the lag is built INTO the stream. We look at the stream within
  // [tStartMs + lag, tEndMs + lag] — the blue box at time T reflects
  // what was happening at audio time (T - lag).
  const blueBoxEvents = generateBlueBoxStream(gt, DEFAULT_BLUEBOX);
  const collabPerCommit: CorpusResult['collabPerCommit'] = [];
  let collabCorrectMs = 0;
  let collabTotalMs = 0;
  for (let i = 0; i < commits.length; i++) {
    const c = commits[i];
    const purity = segmentPurity[i];
    if (!purity) continue;
    const windowStart = c.tStartMs + DEFAULT_BLUEBOX.lagMs;
    const windowEnd = c.tEndMs + DEFAULT_BLUEBOX.lagMs;
    const bluebox = dominantBlueBoxIn(blueBoxEvents, windowStart, windowEnd);
    const truth = purity.dominantSpeaker;
    const correct = bluebox === truth;
    collabPerCommit.push({
      tStartMs: c.tStartMs,
      tEndMs: c.tEndMs,
      bluebox,
      truth,
      correct,
    });
    // Score by audio overlap ms — long commits count more.
    if (correct) collabCorrectMs += purity.overlapMs;
    collabTotalMs += purity.overlapMs;
  }
  const collabAccuracy = collabTotalMs > 0 ? collabCorrectMs / collabTotalMs : 0;

  return {
    id,
    gtSpeakers: [...perSpeaker.keys()],
    predictedClusters: allLabels.size,
    perSpeaker,
    primary,
    passCount,
    splitCount,
    useful,
    commits,
    gtTurns: gt.turns,
    segmentPurity,
    collabAccuracy,
    collabPerCommit,
  };
}

async function main(): Promise<number> {
  const entries = await fs.readdir(CORPUS_DIR);
  const wavs = entries.filter((e) => e.endsWith('.wav')).sort();
  if (wavs.length === 0) {
    console.error(`no corpora in ${CORPUS_DIR}`);
    return 1;
  }
  console.log(`[suite] ${wavs.length} corpora`);
  const results: CorpusResult[] = [];
  for (const wav of wavs) {
    const id = wav.replace(/\.wav$/, '');
    const gtPath = path.join(CORPUS_DIR, `${id}.ground-truth.json`);
    try {
      await fs.access(gtPath);
    } catch {
      console.log(`[suite]   ${id}: SKIP (no ground-truth.json)`);
      continue;
    }
    const gt = JSON.parse(await fs.readFile(gtPath, 'utf-8')) as GroundTruth;
    const expectedSpeakers = new Set(gt.turns.map((t) => t.speaker)).size;
    console.log(`[suite]   ${id}: ${expectedSpeakers} GT speakers, ${gt.turns.length} turns, ${(gt.total_duration_ms / 1000).toFixed(1)}s`);

    // Fresh diarizer per corpus so clusters don't bleed across runs.
    // No maxSpeakers hint — production usually doesn't have a reliable
    // count up front. Let online clustering allocate freely.
    const commits: CommitEvent[] = [];
    const diarizer = await OnnxLocalDiarizer.create({
      onCommit: (ev) => commits.push(ev),
    });
    const samples = await readWav16kMono(path.join(CORPUS_DIR, wav));
    for (let off = 0; off + FRAME_SAMPLES <= samples.length; off += FRAME_SAMPLES) {
      await diarizer.process(samples.subarray(off, off + FRAME_SAMPLES), Math.round((off / SAMPLE_RATE) * 1000));
    }
    if ((diarizer as any).utteranceSamples > 0) {
      await (diarizer as any).commitUtterance();
    }
    // Apply post-hoc merges to all collected commits. A noisy short
    // utterance early in the stream may have allocated a spurious cluster
    // that later evidence merged into a real cluster — rewrite those
    // past commits' speaker IDs so the alignment metric sees the true picture.
    const rewrites = diarizer.getLabelRewrites();
    if (rewrites.size > 0) {
      for (const c of commits) {
        let target = c.speakerId;
        while (rewrites.has(target)) target = rewrites.get(target)!;
        c.speakerId = target;
      }
      console.log(`[suite]   applied ${rewrites.size} merge rewrite(s)`);
    }
    // Per-commit refinement rewrites (post-hoc re-evaluation of each
    // commit against the stabilized centroid set). Distinct from the
    // cluster-level rewrites above — these target individual commits.
    const commitRewrites = (diarizer as any).getCommitRewrites?.() as Map<string, string> | undefined;
    if (commitRewrites && commitRewrites.size > 0) {
      let n = 0;
      for (const c of commits) {
        const key = `${c.tStartMs}-${c.tEndMs}`;
        const target = commitRewrites.get(key);
        if (target && target !== c.speakerId) {
          c.speakerId = target;
          n++;
        }
      }
      console.log(`[suite]   applied ${n} per-commit refinement(s)`);
    }
    // Persist harness output so /corpus browser shows it
    await fs.writeFile(
      path.join(CORPUS_DIR, `${id}.harness-output.json`),
      JSON.stringify(
        {
          conversation_id: id,
          sample_rate: SAMPLE_RATE,
          total_duration_ms: Math.round((samples.length / SAMPLE_RATE) * 1000),
          diarizer_name: diarizer.name,
          commits,
        },
        null,
        2,
      ),
      'utf-8',
    );

    const r = await analyze(id, gt, commits);
    results.push(r);
  }

  // Summary table — uses the "useful" metric (unique primary cluster per speaker + ≥60% coverage)
  console.log();
  console.log('═══════════════════════════ SUITE SUMMARY ═══════════════════════════');
  console.log('Symbols:  ✓✓ strict (no labels leaked)   ✓ useful (unique primary + ≥60% coverage)   ✗ broken');
  console.log();
  let totalUseful = 0;
  let totalCorpora = 0;
  for (const r of results) {
    const expectedSpeakers = r.gtSpeakers.length;
    const dPred = r.predictedClusters - expectedSpeakers;
    const pred = dPred === 0 ? `${r.predictedClusters}` : `${r.predictedClusters} (${dPred > 0 ? '+' : ''}${dPred})`;
    const strict = r.splitCount === 0;
    const mark = strict ? '✓✓' : (r.useful ? '✓ ' : '✗ ');
    console.log(
      `  ${mark}  ${r.id.padEnd(28)}  GT=${expectedSpeakers}  pred=${pred.padEnd(8)}  ` +
        `strict=${strict ? 'Y' : 'N'}  useful=${r.useful ? 'Y' : 'N'}`,
    );
    for (const [spk, labels] of r.perSpeaker) {
      const p = r.primary.get(spk)!;
      const consistent = labels.size === 1 ? '  ✓✓' : (r.primary.get(spk)!.coverage >= 0.60 ? '  ✓ ' : '  ✗ ');
      const labelsStr = labels.size > 4 ? `{${[...labels].slice(0, 4).join(', ')}, +${labels.size - 4}}` : `{${[...labels].join(', ')}}`;
      console.log(
        `    ${consistent}  ${spk.padEnd(10)} → primary=${p.cluster.padEnd(11)} ` +
          `cov=${(p.coverage * 100).toFixed(0).padStart(3)}%   all=${labelsStr}`,
      );
    }
    totalCorpora++;
    if (r.useful) totalUseful++;
  }
  // PRIORITY METRICS for the "diarizer-as-segmenter + blue-box-as-identity"
  // pipeline:
  //
  //   1. BOUNDARY RECALL: how often does the diarizer split AT a real GT
  //      speaker change? Missed splits leave two voices in one commit →
  //      Whisper hallucinates. (False splits — extra boundaries in the
  //      middle of one speaker — are harmless because blue-box labels
  //      both halves the same.)
  //
  //   2. SEGMENT PURITY: among commits the diarizer DID produce, what
  //      fraction of audio time is a single voice? <100% means a commit
  //      straddled a boundary.
  //
  //   3. COLLABORATIVE ATTRIBUTION ACCURACY: simulate the blue-box stream
  //      with realistic noise (1s lag + flicker), label each commit by
  //      blue-box majority vote, measure final per-commit accuracy vs GT.
  //      This is the END-TO-END routing quality — what Whisper actually sees.
  //
  // We still report the legacy useful/strict/coverage numbers for context.
  const TOLERANCE_MS = 500;
  const STRICT_TOLERANCE_MS = 200;

  let totalChanges = 0;
  let hitsLoose = 0;
  let hitsStrict = 0;
  let totalBoundaries = 0;
  let boundaryMatchesLoose = 0;
  let strictCorpora = 0;
  let speakerCount = 0;
  let coverageSum = 0;
  let purityWeightedMs = 0;
  let purityTotalMs = 0;
  let collabCorrectMs = 0;
  let collabTotalMs = 0;
  for (const r of results) {
    if (r.splitCount === 0) strictCorpora++;
    for (const p of r.primary.values()) {
      speakerCount++;
      coverageSum += Math.min(1, p.coverage);
    }
    if (!r.commits || !r.gtTurns) continue;
    const commitBoundaries = r.commits.map((c) => c.tStartMs).sort((a, b) => a - b);
    const changes: number[] = [];
    for (let i = 1; i < r.gtTurns.length; i++) {
      if (r.gtTurns[i].speaker !== r.gtTurns[i - 1].speaker) changes.push(r.gtTurns[i].start_ms);
    }
    totalChanges += changes.length;
    totalBoundaries += commitBoundaries.length;
    for (const change of changes) {
      const nearest = nearestDistance(commitBoundaries, change);
      if (nearest <= STRICT_TOLERANCE_MS) hitsStrict++;
      if (nearest <= TOLERANCE_MS) hitsLoose++;
    }
    for (const b of commitBoundaries) {
      if (changes.length === 0) continue;
      if (nearestDistance(changes, b) <= TOLERANCE_MS) boundaryMatchesLoose++;
    }
    // Segment purity (per commit, weighted by commit overlap ms).
    for (const sp of r.segmentPurity) {
      purityWeightedMs += sp.purity * sp.overlapMs;
      purityTotalMs += sp.overlapMs;
    }
    // Collab accuracy (already computed per-corpus).
    for (const cc of r.collabPerCommit) {
      // Time-weight by commit duration.
      const sp = r.segmentPurity.find((p) => p.tStartMs === cc.tStartMs && p.tEndMs === cc.tEndMs);
      const w = sp?.overlapMs ?? 0;
      if (cc.correct) collabCorrectMs += w;
      collabTotalMs += w;
    }
  }
  const meanCoverage = speakerCount > 0 ? coverageSum / speakerCount : 0;
  const recallLoose = totalChanges > 0 ? hitsLoose / totalChanges : 0;
  const recallStrict = totalChanges > 0 ? hitsStrict / totalChanges : 0;
  const precision = totalBoundaries > 0 ? boundaryMatchesLoose / totalBoundaries : 0;
  const purity = purityTotalMs > 0 ? purityWeightedMs / purityTotalMs : 0;
  const collab = collabTotalMs > 0 ? collabCorrectMs / collabTotalMs : 0;

  console.log();
  console.log(
    `OVERALL  boundary recall=${(recallLoose * 100).toFixed(1)}%  ` +
      `(strict @±${STRICT_TOLERANCE_MS}ms=${(recallStrict * 100).toFixed(1)}%)   ` +
      `boundary precision=${(precision * 100).toFixed(1)}%   ` +
      `(${totalChanges} GT changes, ${totalBoundaries} commit boundaries)`,
  );
  console.log(
    `         segment purity=${(purity * 100).toFixed(1)}%   ` +
      `collab acc=${(collab * 100).toFixed(1)}%   ` +
      `(blue-box lag=${DEFAULT_BLUEBOX.lagMs}ms flicker=${DEFAULT_BLUEBOX.flickersPerMin}/min)`,
  );
  console.log(
    `         legacy: useful=${totalUseful}/${totalCorpora}  strict=${strictCorpora}/${totalCorpora}  meanCoverage=${(meanCoverage * 100).toFixed(1)}%`,
  );
  console.log(
    `SCORE  recall@500=${(recallLoose * 100).toFixed(1)}  purity=${(purity * 100).toFixed(1)}  collab=${(collab * 100).toFixed(1)}  precision=${(precision * 100).toFixed(1)}`,
  );
  return totalUseful === totalCorpora ? 0 : 1;
}

/** Find the absolute distance to the nearest value in a SORTED array. */
function nearestDistance(sortedArr: number[], target: number): number {
  if (sortedArr.length === 0) return Infinity;
  // Binary search for insertion point.
  let lo = 0;
  let hi = sortedArr.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (sortedArr[mid] < target) lo = mid + 1;
    else hi = mid;
  }
  const left = lo > 0 ? Math.abs(sortedArr[lo - 1] - target) : Infinity;
  const right = lo < sortedArr.length ? Math.abs(sortedArr[lo] - target) : Infinity;
  return Math.min(left, right);
}

main().then((c) => process.exit(c)).catch((err) => { console.error('[suite] fatal:', err); process.exit(1); });
