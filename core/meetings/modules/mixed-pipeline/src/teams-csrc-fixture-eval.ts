/**
 * Replay a captured Teams mixed-PCM tape through raw CSRC virtual lanes and the shared faithful
 * GMeet transcription window. This is the candidate proof: no Pyannote and no identity inference.
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';
import { TeamsCsrcGmeetPipeline, type TeamsCsrcTranscriptSegment } from './teams-csrc-gmeet-pipeline.js';

const SAMPLE_RATE = 16_000;
const arg = (name: string): string | undefined => {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : undefined;
};
const required = (name: string): string => {
  const value = arg(name);
  if (!value) throw new Error(`--${name} is required`);
  return value;
};

interface CsrcRow { type: 'csrc'; t: number; csrc: number; active: boolean }
interface AudioRow { seq: number; ts: number; pcm: string; pcm_len: number }
interface HintRow { type: 'hint'; t: number; name: string; isEnd?: boolean }
interface Span { csrc: number; startMs: number; endMs: number }
type ReplaySignal =
  | { kind: 'csrc'; tMs: number; row: CsrcRow }
  | { kind: 'hint'; tMs: number; row: HintRow }
  | { kind: 'audio'; tMs: number; row: AudioRow }
  | { kind: 'cadence'; tMs: number };

function buildAcceptedSpans(frames: Array<{ csrc: number; tsMs: number; samples: number }>): Span[] {
  const byCsrc = new Map<number, Array<{ startMs: number; endMs: number }>>();
  for (const frame of frames) {
    const rows = byCsrc.get(frame.csrc) ?? [];
    rows.push({ startMs: frame.tsMs, endMs: frame.tsMs + frame.samples / SAMPLE_RATE * 1000 });
    byCsrc.set(frame.csrc, rows);
  }
  const spans: Span[] = [];
  for (const [csrc, rows] of byCsrc) {
    rows.sort((a, b) => a.startMs - b.startMs);
    for (const row of rows) {
      const previous = spans.length && spans[spans.length - 1].csrc === csrc ? spans[spans.length - 1] : undefined;
      if (previous && row.startMs <= previous.endMs + 20) previous.endMs = Math.max(previous.endMs, row.endMs);
      else spans.push({ csrc, ...row });
    }
  }
  return spans.sort((a, b) => a.startMs - b.startMs || a.csrc - b.csrc);
}

function writePcm16Wav(path: string, pcm: Float32Array): void {
  const dataBytes = pcm.length * 2;
  const bytes = Buffer.allocUnsafe(44 + dataBytes);
  bytes.write('RIFF', 0); bytes.writeUInt32LE(36 + dataBytes, 4); bytes.write('WAVE', 8);
  bytes.write('fmt ', 12); bytes.writeUInt32LE(16, 16); bytes.writeUInt16LE(1, 20);
  bytes.writeUInt16LE(1, 22); bytes.writeUInt32LE(SAMPLE_RATE, 24);
  bytes.writeUInt32LE(SAMPLE_RATE * 2, 28); bytes.writeUInt16LE(2, 32); bytes.writeUInt16LE(16, 34);
  bytes.write('data', 36); bytes.writeUInt32LE(dataBytes, 40);
  for (let index = 0; index < pcm.length; index++) {
    const sample = Math.max(-1, Math.min(1, pcm[index]));
    bytes.writeInt16LE(sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767), 44 + index * 2);
  }
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, bytes);
}

function readJsonLines(path: string): any[] {
  return readFileSync(path, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line));
}

function loadWavSlice(path: string, startSec: number, durationSec: number): Float32Array {
  const bytes = readFileSync(path);
  if (bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${path}: expected RIFF/WAVE`);
  }
  let offset = 12;
  let format: { audio: number; channels: number; rate: number; bits: number } | null = null;
  let data: Buffer | null = null;
  while (offset + 8 <= bytes.length) {
    const id = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (id === 'fmt ' && size >= 16) {
      format = {
        audio: bytes.readUInt16LE(start),
        channels: bytes.readUInt16LE(start + 2),
        rate: bytes.readUInt32LE(start + 4),
        bits: bytes.readUInt16LE(start + 14),
      };
    } else if (id === 'data') {
      data = bytes.subarray(start, start + size);
    }
    offset = start + size + (size % 2);
  }
  if (!format || format.audio !== 1 || format.channels !== 1 || format.rate !== SAMPLE_RATE
      || format.bits !== 16 || !data) {
    throw new Error(`${path}: expected mono PCM16 ${SAMPLE_RATE}Hz`);
  }
  const first = Math.max(0, Math.round(startSec * SAMPLE_RATE));
  const count = Math.max(0, Math.round(durationSec * SAMPLE_RATE));
  const last = Math.min(Math.floor(data.length / 2), first + count);
  const out = new Float32Array(Math.max(0, last - first));
  for (let index = first; index < last; index++) out[index - first] = data.readInt16LE(index * 2) / 32768;
  return out;
}

function buildSpans(events: CsrcRow[], startMs: number, endMs: number): Span[] {
  const active = new Map<number, number>();
  const spans: Span[] = [];
  for (const event of events) {
    if (event.t < startMs) {
      if (event.active) active.set(event.csrc, startMs);
      else active.delete(event.csrc);
      continue;
    }
    if (event.t > endMs) break;
    if (event.active) {
      if (!active.has(event.csrc)) active.set(event.csrc, event.t);
    } else {
      const from = active.get(event.csrc);
      if (from !== undefined) spans.push({ csrc: event.csrc, startMs: from, endMs: event.t });
      active.delete(event.csrc);
    }
  }
  for (const [csrc, from] of active) spans.push({ csrc, startMs: from, endMs });
  return spans.sort((a, b) => a.startMs - b.startMs || a.csrc - b.csrc);
}

async function main(): Promise<void> {
  const capturedPath = required('captured');
  const csrcPath = required('csrc');
  const wavPath = required('wav');
  const outPath = required('out');
  const cacheDir = required('cache-dir');
  const sttUrl = required('stt-url');
  const token = process.env.VEXA_STT_TOKEN;
  if (!token) throw new Error('VEXA_STT_TOKEN is required');
  const cacheOnly = process.env.VEXA_FIXTURE_CACHE_ONLY === '1';
  const startSec = Number(arg('start-sec') ?? 60);
  const durationSec = Number(arg('duration-sec') ?? 120);
  const cadenceMs = Number(arg('cadence-sec') ?? 2) * 1000;
  const languageArg = arg('language');
  const language = languageArg && languageArg !== 'auto' ? languageArg : undefined;

  const captureHeader = JSON.parse(readFileSync(capturedPath, 'utf8').split('\n', 1)[0]);
  const t0Ms = Number(JSON.parse(readFileSync(join(dirname(capturedPath), 'render.json'), 'utf8')).t0_epoch_ms);
  const cutStartMs = t0Ms + startSec * 1000;
  const cutEndMs = cutStartMs + durationSec * 1000;
  const csrcEvents = readJsonLines(csrcPath).filter((row): row is CsrcRow => row.type === 'csrc')
    .sort((a, b) => a.t - b.t);
  const spans = buildSpans(csrcEvents, cutStartMs, cutEndMs);
  mkdirSync(cacheDir, { recursive: true });
  mkdirSync(dirname(outPath), { recursive: true });

  const client = new TranscriptionClient({
    serviceUrl: sttUrl,
    apiToken: token,
    model: arg('stt-model') ?? 'large-v3-turbo',
    // Fixture replay must survive an isolated remote STT timeout. This is evaluator-only;
    // it does not change the Teams production pipeline or its visible update cadence.
    maxRetries: 3,
    requestTimeoutMs: 120_000,
  });
  let virtualNow = cutStartMs;
  let decoderMaxMs = 0;
  let calls = 0;
  let cachedCalls = 0;
  const submissionReceipts: Array<Record<string, unknown>> = [];
  const confirmed = new Map<string, TeamsCsrcTranscriptSegment>();
  const pending = new Map<string, TeamsCsrcTranscriptSegment>();
  const events: Array<TeamsCsrcTranscriptSegment & { emittedAtMs: number; sequence: number }> = [];
  const rejectedOwnership: Array<Record<string, unknown>> = [];
  const routedFrames: Array<{ csrc: number; tsMs: number; samples: number }> = [];

  const pipeline = new TeamsCsrcGmeetPipeline({
    lookbackMs: Number(arg('lookback-ms') ?? 600),
    ownershipLookbackMs: Number(arg('ownership-lookback-ms') ?? 1200),
    flickerHoldMs: Number(arg('flicker-hold-ms') ?? 1500),
    onsetGapMs: Number(arg('onset-gap-ms') ?? 1000),
    buffer: {
      minAudioDuration: 2,
      submitInterval: 2,
      confirmThreshold: 2,
      maxBufferDuration: 30,
      idleTimeoutSec: 15,
      sampleRate: SAMPLE_RATE,
      silenceRmsThreshold: 0.0025,
      scheduleSubmissions: false,
      now: () => virtualNow,
      logger: (message) => process.argv.includes('--verbose') && console.log(message),
    },
    onSegment: (segment) => {
      events.push({ ...segment, emittedAtMs: virtualNow, sequence: events.length });
      if (segment.completed) {
        confirmed.set(segment.segmentId, segment);
        pending.delete(segment.segmentId);
      } else if (segment.text.trim()) {
        pending.set(segment.segmentId, segment);
      } else {
        pending.delete(segment.segmentId);
      }
    },
    onRejectedOwnership: (segment, intervals) => rejectedOwnership.push({ segment, intervals }),
    onRoutedFrame: (frame) => routedFrames.push({ csrc: frame.csrc, tsMs: frame.tsMs, samples: frame.pcm.length }),
    onError: (error) => { throw error; },
    transcribe: async (pcm, prompt, context) => {
      const hash = createHash('sha256')
        .update(Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength))
        .update('\0')
        .update(prompt ?? '')
        .digest('hex');
      const cachePath = join(cacheDir, `${hash}.json`);
      const cached = existsSync(cachePath);
      if (!cached && cacheOnly) {
        throw new Error(`fixture cache miss ${hash} for ${context?.sourceKey ?? 'unknown-source'}`);
      }
      const started = Date.now();
      const result = cached
        ? JSON.parse(readFileSync(cachePath, 'utf8')) as TranscriptionResult
        : await client.transcribe(pcm, language, prompt);
      if (!cached) writeFileSync(cachePath, `${JSON.stringify(result)}\n`);
      const elapsedMs = Date.now() - started;
      calls++;
      if (cached) cachedCalls++;
      decoderMaxMs = Math.max(decoderMaxMs, elapsedMs);
      submissionReceipts.push({
        hash,
        csrc: context?.csrc ?? null,
        sourceKey: context?.sourceKey ?? null,
        samples: pcm.length,
        prompt: prompt ?? null,
        resultText: result.text.trim(),
        segmentCount: result.segments?.length ?? 0,
        // Evaluation-only trace for the post-confirm contested-word detector. The shared GMeet
        // window remains unchanged; it still owns confirmation semantics and publishes no word
        // timestamps. This receipt lets the offline UI test proximity without re-transcribing.
        segments: result.segments ?? [],
        elapsedMs,
        cached,
      });
      return result;
    },
  });

  let csrcIndex = 0;
  while (csrcIndex < csrcEvents.length && csrcEvents[csrcIndex].t < cutStartMs) {
    pipeline.recordTransportEvent({
      csrc: csrcEvents[csrcIndex].csrc,
      active: csrcEvents[csrcIndex].active,
      tMs: csrcEvents[csrcIndex].t,
    });
    csrcIndex++;
  }
  let frames = 0;
  let samples = 0;
  const replay: ReplaySignal[] = [];
  for (; csrcIndex < csrcEvents.length && csrcEvents[csrcIndex].t <= cutEndMs; csrcIndex++) {
    const row = csrcEvents[csrcIndex];
    replay.push({ kind: 'csrc', tMs: row.t, row });
  }
  for (const parsed of readJsonLines(capturedPath) as Array<AudioRow | HintRow | Record<string, unknown>>) {
    if ('type' in parsed && parsed.type === 'hint') {
      const row = parsed as HintRow;
      if (row.t >= cutStartMs && row.t <= cutEndMs) replay.push({ kind: 'hint', tMs: row.t, row });
    } else if ('pcm' in parsed) {
      const row = parsed as AudioRow;
      if (Number.isFinite(row.ts) && row.ts >= cutStartMs && row.ts <= cutEndMs) {
        replay.push({ kind: 'audio', tMs: row.ts, row });
      }
    }
  }
  for (let tMs = cutStartMs + cadenceMs; tMs <= cutEndMs; tMs += cadenceMs) {
    replay.push({ kind: 'cadence', tMs });
  }
  const order: Record<ReplaySignal['kind'], number> = { csrc: 0, hint: 1, audio: 2, cadence: 3 };
  replay.sort((left, right) => left.tMs - right.tMs || order[left.kind] - order[right.kind]);

  for (const signal of replay) {
    virtualNow = signal.tMs;
    if (signal.kind === 'csrc') {
      pipeline.recordTransportEvent({
        csrc: signal.row.csrc,
        active: signal.row.active,
        tMs: signal.row.t,
      });
    } else if (signal.kind === 'hint') {
      pipeline.recordHint(signal.row.name, signal.row.t, signal.row.isEnd === true);
    } else if (signal.kind === 'audio') {
      const bytes = Buffer.from(signal.row.pcm, 'base64');
      const pcm = new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
      pipeline.feedMixedAudio(pcm, signal.row.ts);
      frames++;
      samples += pcm.length;
    } else {
      await Promise.all(pipeline.activeCsrcs().map((csrc) => pipeline.requestTranscription(csrc)));
    }
  }
  virtualNow = cutEndMs;
  await pipeline.dispose();

  const flatPcm = loadWavSlice(wavPath, startSec, durationSec);
  const sliceWavOut = arg('slice-wav-out');
  if (sliceWavOut) writePcm16Wav(sliceWavOut, flatPcm);
  const flatHash = createHash('sha256').update(Buffer.from(flatPcm.buffer)).digest('hex');
  const flatCachePath = join(cacheDir, `single-pass-${flatHash}.json`);
  const flatCached = existsSync(flatCachePath);
  if (!flatCached && cacheOnly) {
    throw new Error(`fixture cache miss single-pass-${flatHash}`);
  }
  const flatStarted = Date.now();
  const singlePass = flatCached
    ? JSON.parse(readFileSync(flatCachePath, 'utf8')) as TranscriptionResult
    : await client.transcribe(flatPcm, language);
  if (!flatCached) writeFileSync(flatCachePath, `${JSON.stringify(singlePass)}\n`);
  const flatElapsedMs = Date.now() - flatStarted;

  const confirmedRows = [...confirmed.values()].sort((a, b) => a.startMs - b.startMs || a.csrc - b.csrc);
  const pendingRows = [...pending.values()].sort((a, b) => a.startMs - b.startMs || a.csrc - b.csrc);
  writeFileSync(outPath, `${JSON.stringify({
    kind: 'teams-csrc-gmeet-window-fixture-eval',
    fixture: dirname(capturedPath),
    source: { captureHeader, capturedPath, csrcPath, wavPath, t0Ms },
    slice: { startSec, durationSec, cutStartMs, cutEndMs },
    candidate: {
      config: {
        lookbackMs: Number(arg('lookback-ms') ?? 600),
        flickerHoldMs: Number(arg('flicker-hold-ms') ?? 1500),
        onsetGapMs: Number(arg('onset-gap-ms') ?? 1000),
        cadenceMs,
      },
      health: pipeline.health(),
      frames,
      samples,
      calls,
      cachedCalls,
      refreshLatency: { cadenceMs, decoderMaxMs, cadencePlusDecoderMaxMs: cadenceMs + decoderMaxMs },
      spans,
      acceptedSpans: buildAcceptedSpans(routedFrames),
      confirmed: confirmedRows,
      pending: pendingRows,
      events,
      rejectedOwnership,
      text: confirmedRows.map((row) => row.text).filter(Boolean).join(' ').trim(),
      submissions: submissionReceipts,
    },
    singlePass: {
      text: singlePass.text.trim(),
      segments: singlePass.segments ?? [],
      language: singlePass.language,
      elapsedMs: flatElapsedMs,
      cached: flatCached,
    },
  }, null, 2)}\n`);
  console.log(`wrote ${confirmedRows.length} confirmed + ${pendingRows.length} pending rows, ${calls} buffered calls → ${outPath}`);
}

void main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
