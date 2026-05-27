/**
 * Replay an arbitrary 16kHz mono wav through OnnxLocalDiarizer and print
 * the commit stream + the same per-cluster summary the suite uses (sans
 * ground truth alignment, since we don't have it for captured live audio).
 *
 * Usage:
 *   npx tsx eval/run-wav.ts path/to/captured-1748000000000.wav
 *   npx tsx eval/run-wav.ts path/to/some.wav --veryFar 0.70 --minSeed 2000
 *
 * Use this to iterate on threshold tuning against captured YouTube audio
 * without re-recording from the browser each time.
 */

import { promises as fs } from 'fs';
import path from 'path';

import { OnnxLocalDiarizer, type CommitEvent } from '../src/onnx-local-diarizer';

const SAMPLE_RATE = 16_000;
const FRAME_SAMPLES = 1024;

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

function parseArgs(argv: string[]) {
  const positional: string[] = [];
  const flags: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      flags[a.slice(2)] = argv[i + 1];
      i++;
    } else positional.push(a);
  }
  return { positional, flags };
}

async function main(): Promise<number> {
  const { positional, flags } = parseArgs(process.argv.slice(2));
  if (positional.length === 0) {
    console.error('usage: npx tsx eval/run-wav.ts <wav> [--veryFar N] [--minSeed MS] [--newThr N]');
    return 1;
  }
  const wavPath = path.resolve(positional[0]);
  const samples = await readWav16kMono(wavPath);
  console.log(`[wav] ${wavPath}  ${(samples.length / SAMPLE_RATE).toFixed(1)}s`);

  const commits: CommitEvent[] = [];
  const cfg: any = { onCommit: (ev: CommitEvent) => commits.push(ev) };
  if (flags.veryFar) cfg.veryFarThreshold = Number(flags.veryFar);
  if (flags.minSeed) cfg.minSeedUtteranceMs = Number(flags.minSeed);
  if (flags.newThr) cfg.newSpeakerThreshold = Number(flags.newThr);
  console.log(`[wav] config: ${JSON.stringify({ veryFar: cfg.veryFarThreshold, minSeed: cfg.minSeedUtteranceMs, newThr: cfg.newSpeakerThreshold })}`);

  const diarizer = await OnnxLocalDiarizer.create(cfg);
  for (let off = 0; off + FRAME_SAMPLES <= samples.length; off += FRAME_SAMPLES) {
    await diarizer.process(samples.subarray(off, off + FRAME_SAMPLES), Math.round((off / SAMPLE_RATE) * 1000));
  }
  if ((diarizer as any).utteranceSamples > 0) await (diarizer as any).commitUtterance();

  const rewrites = diarizer.getLabelRewrites();
  for (const c of commits) {
    let r = c.speakerId;
    while (rewrites.has(r)) r = rewrites.get(r)!;
    (c as any).resolvedSpeaker = r;
  }

  console.log();
  console.log('═══════════════════ COMMIT STREAM ═══════════════════');
  for (const c of commits as any[]) {
    const dur = ((c.tEndMs - c.tStartMs) / 1000).toFixed(2);
    console.log(
      `  ${(c.tStartMs / 1000).toFixed(2).padStart(7)}s  dur=${dur.padStart(5)}s  ` +
        `${c.resolvedSpeaker.padEnd(11)} (raw=${c.speakerId.padEnd(11)} cd=${c.centroidDist.toFixed(3)} td=${c.turnDist.toFixed(3)} new=${c.isNew ? 'Y' : '.'})`,
    );
  }
  console.log();
  // Cluster time-share summary
  const perCluster = new Map<string, number>();
  for (const c of commits as any[]) {
    perCluster.set(c.resolvedSpeaker, (perCluster.get(c.resolvedSpeaker) ?? 0) + (c.tEndMs - c.tStartMs));
  }
  const totalSpeechMs = [...perCluster.values()].reduce((a, b) => a + b, 0);
  console.log(`═══════════════════ CLUSTER TIME SHARE (${(totalSpeechMs / 1000).toFixed(1)}s speech) ═══════════════════`);
  for (const [id, ms] of [...perCluster.entries()].sort((a, b) => b[1] - a[1])) {
    console.log(`  ${id.padEnd(11)}  ${(ms / 1000).toFixed(1).padStart(6)}s  ${((ms / totalSpeechMs) * 100).toFixed(0).padStart(3)}%`);
  }
  return 0;
}

main().then((c) => process.exit(c)).catch((err) => {
  console.error('[wav] fatal:', err);
  process.exit(1);
});
