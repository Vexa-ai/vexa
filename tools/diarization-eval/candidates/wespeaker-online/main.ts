/** Standalone CPU candidate: 16 kHz mono PCM16 WAV in, single-file RTTM out. */
import { readFile, writeFile } from 'node:fs/promises';
import { basename, extname, join } from 'node:path';
import { tmpdir } from 'node:os';

function decodeWav(bytes: Buffer): Float32Array {
  if (bytes.length < 12 || bytes.toString('ascii', 0, 4) !== 'RIFF' ||
      bytes.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('Expected a RIFF/WAVE file');
  }
  const riffEnd = bytes.readUInt32LE(4) + 8;
  if (riffEnd > bytes.length) throw new Error('Truncated RIFF file');
  let format: Buffer | undefined;
  let pcm: Buffer | undefined;
  for (let offset = 12; offset + 8 <= riffEnd;) {
    const id = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (start + size > riffEnd) throw new Error(`Truncated WAV chunk: ${id}`);
    if (id === 'fmt ') format = bytes.subarray(start, start + size);
    if (id === 'data') {
      if (pcm) throw new Error('Multiple WAV data chunks are unsupported');
      pcm = bytes.subarray(start, start + size);
    }
    offset = start + size + (size % 2);
  }
  if (!format || format.length < 16 || !pcm || pcm.length % 2 ||
      format.readUInt16LE(0) !== 1 || format.readUInt16LE(2) !== 1 ||
      format.readUInt32LE(4) !== 16000 || format.readUInt16LE(12) !== 2 ||
      format.readUInt16LE(14) !== 16) {
    throw new Error('Expected 16 kHz mono PCM16 WAV with fmt and data chunks');
  }
  const audio = new Float32Array(pcm.length / 2);
  for (let i = 0; i < audio.length; i++) audio[i] = pcm.readInt16LE(2 * i) / 32768;
  return audio;
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (args.length === 1 && ['--help', '-h'].includes(args[0])) {
    console.log('Usage: run.sh <input.wav> <output.rttm> — 16 kHz mono PCM16, CPU only');
    return;
  }
  if (args.length !== 2) throw new Error('Usage: run.sh <input.wav> <output.rttm>');
  const [wav, output] = args;
  const name = basename(wav, extname(wav));
  if (/\s/.test(name)) throw new Error('RTTM fixture names cannot contain whitespace');
  const audio = decodeWav(await readFile(wav));
  // Import and load models only after the CLI and audio have been validated.
  const { env } = await import('@huggingface/transformers');
  env.cacheDir = join(tmpdir(), 'diarization-eval-models');
  const { OnnxLocalDiarizer } = await import('./onnx-local-diarizer');
  const turns: Array<{ tStartMs: number; tEndMs: number; speakerId: string }> = [];
  const diarizer = await OnnxLocalDiarizer.create({
    onCommit: ({ tStartMs, tEndMs, speakerId }) => turns.push({ tStartMs, tEndMs, speakerId }),
  });
  const chunkSamples = 320; // 20 ms, timestamps mark each frame's first sample.
  for (let offset = 0; offset < audio.length; offset += chunkSamples) {
    await diarizer.process(audio.subarray(offset, offset + chunkSamples), offset / 16);
  }
  await diarizer.finish();
  const rewrites = diarizer.getLabelRewrites();
  const commits = diarizer.getCommitRewrites();
  const duration = audio.length / 16000;
  const lines: string[] = [];
  for (const turn of turns) {
    let speaker = commits.get(`${turn.tStartMs}-${turn.tEndMs}`) ?? turn.speakerId;
    const visited = new Set<string>();
    while (rewrites.has(speaker)) {
      if (visited.has(speaker)) throw new Error('Cyclic speaker rewrite');
      visited.add(speaker);
      speaker = rewrites.get(speaker)!;
    }
    const startSec = Math.max(0, Math.min(duration, turn.tStartMs / 1000));
    const endSec = Math.max(startSec, Math.min(duration, turn.tEndMs / 1000));
    if (endSec > startSec) {
      lines.push(`SPEAKER ${name} 1 ${startSec.toFixed(6)} ${(endSec - startSec).toFixed(6)} <NA> <NA> ${speaker} <NA> <NA>`);
    }
  }
  await writeFile(output, lines.join('\n') + (lines.length ? '\n' : ''));
  process.exit(0);
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(2);
});
