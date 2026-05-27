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
   *  every primary coverage ≥ 0.60. Real-world good enough. */
  useful: boolean;
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

  return {
    id,
    gtSpeakers: [...perSpeaker.keys()],
    predictedClusters: allLabels.size,
    perSpeaker,
    primary,
    passCount,
    splitCount,
    useful,
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
  console.log();
  console.log(`OVERALL  useful=${totalUseful}/${totalCorpora}  ${totalUseful === totalCorpora ? '✓ ALL USEFUL' : 'still has broken corpora'}`);
  return totalUseful === totalCorpora ? 0 : 1;
}

main().then((c) => process.exit(c)).catch((err) => { console.error('[suite] fatal:', err); process.exit(1); });
