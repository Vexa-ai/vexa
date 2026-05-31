/**
 * Offline diarizer eval harness (pack-msteams-diarization-cutover #394 RnD).
 *
 * Feeds a mono 16 kHz WAV through the EXACT shipping OnnxLocalDiarizer pipeline
 * (pyannote/segmentation-3.0 → wespeaker → online clustering) exactly as the
 * browser onaudioprocess path does — 4096-sample frames, monotonic timestamps —
 * and dumps every commit so a Python scorer can compare against ground truth.
 *
 * Pyannote inference is sample-count gated (not wall-clock), so fast-feeding
 * replays at correct audio-time cadence.
 *
 * Usage:
 *   DIAR_CACHE=/hfcache npx tsx eval-diarizer.ts \
 *       --wav /data/IS1009a.wav --out /data/IS1009a.commits.json \
 *       --config '{"newSpeakerThreshold":0.30,"veryFarThreshold":0.45}'
 */
import { env as transformersEnv } from '@huggingface/transformers';
import { OnnxLocalDiarizer, CommitEvent } from './src/services/diarization/onnx-local-diarizer';
import { metrics } from './src/services/diarization/metrics';
import * as fs from 'fs';

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def;
}

/** Minimal robust WAV reader: mono PCM16, scans for the 'data' chunk. */
function readWavMono16(path: string): { samples: Float32Array; sampleRate: number } {
  const buf = fs.readFileSync(path);
  if (buf.toString('ascii', 0, 4) !== 'RIFF' || buf.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('not a RIFF/WAVE file');
  }
  let off = 12;
  let sampleRate = 16000;
  let bitsPerSample = 16;
  let numChannels = 1;
  let dataOff = -1;
  let dataLen = 0;
  while (off + 8 <= buf.length) {
    const id = buf.toString('ascii', off, off + 4);
    const size = buf.readUInt32LE(off + 4);
    if (id === 'fmt ') {
      numChannels = buf.readUInt16LE(off + 10);
      sampleRate = buf.readUInt32LE(off + 12);
      bitsPerSample = buf.readUInt16LE(off + 22);
    } else if (id === 'data') {
      dataOff = off + 8;
      dataLen = size;
    }
    off += 8 + size + (size & 1);
  }
  if (dataOff < 0) throw new Error('no data chunk');
  if (bitsPerSample !== 16) throw new Error(`expected PCM16, got ${bitsPerSample}-bit`);
  const nSamples = Math.floor(dataLen / 2 / numChannels);
  const out = new Float32Array(nSamples);
  for (let i = 0; i < nSamples; i++) {
    // mono, or average channels
    let acc = 0;
    for (let c = 0; c < numChannels; c++) {
      acc += buf.readInt16LE(dataOff + (i * numChannels + c) * 2);
    }
    out[i] = acc / numChannels / 32768;
  }
  return { samples: out, sampleRate };
}

async function main() {
  const wavPath = arg('wav');
  const outPath = arg('out');
  const configStr = arg('config', '{}')!;
  if (!wavPath || !outPath) {
    console.error('need --wav and --out');
    process.exit(2);
  }
  if (process.env.DIAR_CACHE) {
    (transformersEnv as any).cacheDir = process.env.DIAR_CACHE;
  }
  const config = JSON.parse(configStr);

  const SAMPLE_RATE = 16000;
  const FRAME = 4096; // mirrors browser ScriptProcessor frame size
  console.log(`[eval] reading ${wavPath} ...`);
  const { samples, sampleRate } = readWavMono16(wavPath);
  if (sampleRate !== SAMPLE_RATE) throw new Error(`expected 16k, got ${sampleRate}`);
  const durS = samples.length / SAMPLE_RATE;
  console.log(`[eval] ${durS.toFixed(0)}s, ${samples.length} samples; config=${JSON.stringify(config)}`);

  const commits: CommitEvent[] = [];
  const embRecs: any[] = [];
  const dumpEmb = !!process.env.DIAR_DUMP_EMB;
  const t0 = Date.now();
  const diarizer = await OnnxLocalDiarizer.create({
    ...config,
    onCommit: (ev: CommitEvent) => commits.push({ ...ev }),
    ...(dumpEmb
      ? {
          onUtteranceEmbed: (rec: any) =>
            embRecs.push({
              tStartMs: rec.tStartMs,
              tEndMs: rec.tEndMs,
              durSamples: rec.durSamples,
              canSeedNew: rec.canSeedNew,
              emb: rec.emb,
            }),
        }
      : {}),
  });
  const rssLoadedMiB = process.memoryUsage().rss / 1048576;
  console.log(`[eval] diarizer ready in ${((Date.now() - t0) / 1000).toFixed(1)}s; RSS_after_modelload=${rssLoadedMiB.toFixed(0)}MiB; feeding frames...`);

  const feedStart = Date.now();
  let fed = 0;
  let rssPeakMiB = rssLoadedMiB;
  for (let i = 0; i < samples.length; i += FRAME) {
    const frame = samples.subarray(i, Math.min(i + FRAME, samples.length));
    const tsMs = (i / SAMPLE_RATE) * 1000;
    await diarizer.process(frame, tsMs);
    const rss = process.memoryUsage().rss / 1048576;
    if (rss > rssPeakMiB) rssPeakMiB = rss;
    fed += frame.length;
    if (fed % (SAMPLE_RATE * 120) < FRAME) {
      const audioMin = fed / SAMPLE_RATE / 60;
      const wallS = (Date.now() - feedStart) / 1000;
      console.log(`[eval] fed ${audioMin.toFixed(1)} min audio in ${wallS.toFixed(0)}s wall (${commits.length} commits)`);
    }
  }
  const wallS = (Date.now() - feedStart) / 1000;
  console.log(`[eval] DONE: ${commits.length} commits over ${durS.toFixed(0)}s audio in ${wallS.toFixed(0)}s wall (${(durS / wallS).toFixed(1)}x realtime)`);

  // Compute overhead breakdown. pyannote forward passes are analytic:
  // one inference per inferIntervalMs of audio once past the warm-up window.
  const inferIntervalMs = config.pyannoteInferIntervalMs ?? 500;
  const pyannoteInfers = Math.max(0, Math.floor((durS * 1000) / inferIntervalMs));
  const snap: any = metrics.snapshot();
  const embed = snap?.diarization?.embedLatency ?? snap?.embedLatency ?? {};
  const embedCount = embed.count ?? embed.n ?? null;
  const embedMeanMs = embed.mean ?? embed.avg ?? embed.p50 ?? null;
  const cpuSecPerAudioSec = wallS / durS; // at THREADS threads
  console.log(
    `[eval] OVERHEAD: ${cpuSecPerAudioSec.toFixed(3)} wall-sec/audio-sec @ ${process.env.OMP_NUM_THREADS || 'default'} threads | ` +
    `pyannote_infers≈${pyannoteInfers} (every ${inferIntervalMs}ms) | ` +
    `embeds=${embedCount} mean=${embedMeanMs != null ? Number(embedMeanMs).toFixed(0) + 'ms' : 'n/a'}`,
  );

  const clusters = new Set(commits.map((c) => c.speakerId));
  fs.writeFileSync(
    outPath,
    JSON.stringify(
      {
        wav: wavPath,
        config,
        durationS: durS,
        wallS,
        nCommits: commits.length,
        nClusters: clusters.size,
        clusters: [...clusters],
        commits: commits.map((c) => ({
          speakerId: c.speakerId,
          startS: +(c.tStartMs / 1000).toFixed(3),
          endS: +(c.tEndMs / 1000).toFixed(3),
          isNew: c.isNew,
          centroidDist: Number.isFinite(c.centroidDist) ? +c.centroidDist.toFixed(3) : null,
          turnDist: Number.isFinite(c.turnDist) ? +c.turnDist.toFixed(3) : null,
          dbSize: c.dbSize,
        })),
      },
      null,
      2,
    ),
  );
  console.log(`[eval] wrote ${outPath} (${clusters.size} clusters)`);

  if (dumpEmb) {
    const embOut = outPath.replace(/\.json$/, '') + '.emb.json';
    fs.writeFileSync(embOut, JSON.stringify({ durationS: durS, config, records: embRecs }));
    console.log(`[eval] wrote ${embOut} (${embRecs.length} utterance embeddings for clustering replay)`);
  }
  process.exit(0);
}

main().catch((e) => {
  console.error('[eval] FATAL', e);
  process.exit(1);
});
