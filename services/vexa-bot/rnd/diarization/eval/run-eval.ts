/**
 * Feed a corpus WAV through OnnxLocalDiarizer (no HTTP/WS, no harness) and
 * dump the diarizer's per-utterance commit decisions to a JSON file.
 *
 * Usage:
 *     npm run eval -- intro-2speakers
 *     → reads  eval/corpus/intro-2speakers.wav
 *     → writes eval/corpus/intro-2speakers.harness-output.json
 *
 * Pure standalone — no server, no transcription. Just the diarizer's
 * decisions, byte-identical to what the live harness would emit at each
 * commit.
 */

import { promises as fs } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

import { OnnxLocalDiarizer, type CommitEvent } from '../src/onnx-local-diarizer';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const CORPUS_DIR = path.join(__dirname, 'corpus');
const SAMPLE_RATE = 16_000;
// Match what the browser AudioWorklet emits: ~64 ms frames.
const FRAME_SAMPLES = 1024;

interface WavData {
  sampleRate: number;
  numChannels: number;
  bitsPerSample: number;
  samples: Float32Array;
}

async function readWav16kMono(wavPath: string): Promise<WavData> {
  const buf = await fs.readFile(wavPath);
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`not a RIFF/WAVE file: ${wavPath}`);
  }
  // Walk the chunk list to find 'fmt ' and 'data'
  let offset = 12;
  let fmt = { sampleRate: 0, numChannels: 0, bitsPerSample: 0 };
  let dataOffset = -1;
  let dataLength = -1;
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === 'fmt ') {
      fmt = {
        numChannels: buf.readUInt16LE(offset + 10),
        sampleRate: buf.readUInt32LE(offset + 12),
        bitsPerSample: buf.readUInt16LE(offset + 22),
      };
    } else if (id === 'data') {
      dataOffset = offset + 8;
      dataLength = size;
    }
    offset += 8 + size + (size % 2);
  }
  if (dataOffset < 0) throw new Error(`no 'data' chunk in ${wavPath}`);
  if (fmt.numChannels !== 1) throw new Error(`expected mono, got ${fmt.numChannels} channels`);
  if (fmt.bitsPerSample !== 16) throw new Error(`expected 16-bit, got ${fmt.bitsPerSample}-bit`);
  if (fmt.sampleRate !== SAMPLE_RATE) throw new Error(`expected ${SAMPLE_RATE} Hz, got ${fmt.sampleRate}`);

  const numSamples = dataLength / 2;
  const samples = new Float32Array(numSamples);
  for (let i = 0; i < numSamples; i++) {
    const int16 = buf.readInt16LE(dataOffset + i * 2);
    samples[i] = int16 / 32768.0;
  }
  return { sampleRate: fmt.sampleRate, numChannels: fmt.numChannels, bitsPerSample: fmt.bitsPerSample, samples };
}

async function main(): Promise<number> {
  const idArg = process.argv[2];
  if (!idArg) {
    console.error('usage: tsx eval/run-eval.ts <conversation-id>');
    return 2;
  }
  const wavPath = path.join(CORPUS_DIR, `${idArg}.wav`);
  const outPath = path.join(CORPUS_DIR, `${idArg}.harness-output.json`);

  console.log(`[eval] loading ${wavPath} ...`);
  const wav = await readWav16kMono(wavPath);
  console.log(
    `[eval]   ${(wav.samples.length / wav.sampleRate).toFixed(2)} s @ ${wav.sampleRate} Hz mono`,
  );

  console.log(`[eval] loading diarizer ...`);
  const commits: CommitEvent[] = [];
  const diarizer = await OnnxLocalDiarizer.create({
    onCommit: (ev) => commits.push(ev),
  });
  console.log(`[eval] diarizer ready: ${diarizer.name}`);

  console.log(`[eval] feeding ${wav.samples.length} samples in ${FRAME_SAMPLES}-sample chunks ...`);
  const t0 = Date.now();
  let frameCount = 0;
  for (let off = 0; off + FRAME_SAMPLES <= wav.samples.length; off += FRAME_SAMPLES) {
    const frame = wav.samples.subarray(off, off + FRAME_SAMPLES);
    const ts = Math.round((off / SAMPLE_RATE) * 1000);
    await diarizer.process(frame, ts);
    frameCount++;
  }
  // Force-flush whatever utterance is in flight at end-of-stream so its
  // final commit lands in the report.
  if ((diarizer as any).utteranceSamples > 0) {
    await (diarizer as any).commitUtterance();
  }
  const elapsed = Date.now() - t0;
  console.log(
    `[eval] fed ${frameCount} frames in ${elapsed} ms ` +
      `(${(elapsed / (wav.samples.length / wav.sampleRate)).toFixed(2)}x audio-time)`,
  );
  console.log(`[eval] diarizer emitted ${commits.length} commit(s)`);

  const out = {
    conversation_id: idArg,
    sample_rate: SAMPLE_RATE,
    total_duration_ms: Math.round((wav.samples.length / SAMPLE_RATE) * 1000),
    diarizer_name: diarizer.name,
    commits,
  };
  await fs.writeFile(outPath, JSON.stringify(out, null, 2), 'utf-8');
  console.log(`[eval] wrote ${outPath}`);
  return 0;
}

main().then((code) => process.exit(code)).catch((err) => {
  console.error('[eval] fatal:', err);
  process.exit(1);
});
