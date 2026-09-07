/**
 * Human witness for the Teams CSRC candidate.
 *
 * Unlike the static eval UI, this process replays captured CSRC, named Teams hints, and mixed PCM
 * into TeamsCsrcGmeetPipeline on wall clock. Browser rows come only from that pipeline's live
 * onSegment callback. Whisper responses may be read from the exact content-addressed fixture cache;
 * a cache miss fails closed so the witness cannot silently exercise another decoder or model.
 */
import { createHash } from 'node:crypto';
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { performance } from 'node:perf_hooks';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';
import { TeamsCsrcGmeetPipeline, type TeamsCsrcTranscriptSegment } from './teams-csrc-gmeet-pipeline.js';

const SAMPLE_RATE = 16_000;
const argument = (name: string, fallback?: string): string | undefined => {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : fallback;
};
const required = (name: string): string => {
  const value = argument(name);
  if (!value) throw new Error(`--${name} is required`);
  return value;
};
const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

interface CsrcSignal { kind: 'csrc'; tMs: number; csrc: number; active: boolean }
interface HintSignal { kind: 'hint'; tMs: number; name: string; isEnd: boolean }
interface AudioSignal { kind: 'audio'; tMs: number; pcm: string }
interface CadenceSignal { kind: 'cadence'; tMs: number }
type Signal = CsrcSignal | HintSignal | AudioSignal | CadenceSignal;
type PreCutSignal = CsrcSignal | HintSignal;

const capturedPath = required('captured');
const csrcPath = required('csrc');
const cacheDir = required('cache-dir');
const wavPath = argument('wav', join(dirname(capturedPath), 'meeting.wav'))!;
const startSec = Number(argument('start-sec', '60'));
const durationSec = Number(argument('duration-sec', '1200'));
const cadenceMs = Number(argument('cadence-ms', '2000'));
const port = Number(argument('port', '8771'));
const sttUrl = argument('stt-url');
if (![startSec, durationSec, cadenceMs, port].every(Number.isFinite)) throw new Error('numeric argument is invalid');
const liveClient = sttUrl ? new TranscriptionClient({
  serviceUrl: sttUrl,
  apiToken: process.env.VEXA_STT_TOKEN,
  model: argument('stt-model', 'large-v3-turbo'),
  requestTimeoutMs: 120_000,
  maxRetries: 3,
}) : null;
mkdirSync(cacheDir, { recursive: true });

const capturedRows = readFileSync(capturedPath, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line));
const csrcRows = readFileSync(csrcPath, 'utf8').split('\n').filter(Boolean).map((line) => JSON.parse(line));
const t0Ms = Number(JSON.parse(readFileSync(join(dirname(capturedPath), 'render.json'), 'utf8')).t0_epoch_ms);
const cutStartMs = t0Ms + startSec * 1000;
const cutEndMs = cutStartMs + durationSec * 1000;
const preCutSignals: PreCutSignal[] = [];
const signals: Signal[] = [];
for (const row of csrcRows) {
  if (row.type !== 'csrc') continue;
  const signal: CsrcSignal = { kind: 'csrc', tMs: Number(row.t), csrc: Number(row.csrc), active: row.active === true };
  if (signal.tMs < cutStartMs) preCutSignals.push(signal);
  else if (signal.tMs <= cutEndMs) signals.push(signal);
}
for (const row of capturedRows) {
  const at = Number(row.t ?? row.ts);
  if (!Number.isFinite(at)) continue;
  if (row.type === 'hint' && row.name) {
    const signal: HintSignal = { kind: 'hint', tMs: at, name: String(row.name), isEnd: row.isEnd === true };
    if (at < cutStartMs) preCutSignals.push(signal);
    else if (at <= cutEndMs) signals.push(signal);
  } else if (at >= cutStartMs && at <= cutEndMs && typeof row.pcm === 'string') {
    signals.push({ kind: 'audio', tMs: at, pcm: row.pcm });
  }
}
for (let tMs = cutStartMs + cadenceMs; tMs <= cutEndMs; tMs += cadenceMs) {
  signals.push({ kind: 'cadence', tMs });
}
// A frame stamped exactly at the timer boundary is available to that request. The cadence event is
// otherwise independent of PCM arrival, matching the real GMeet-compatible timer instead of
// bunching overdue requests after a captured-source gap.
const order: Record<Signal['kind'], number> = { csrc: 0, hint: 1, audio: 2, cadence: 3 };
preCutSignals.sort((left, right) => left.tMs - right.tMs || order[left.kind] - order[right.kind]);
signals.sort((left, right) => left.tMs - right.tMs || order[left.kind] - order[right.kind]);

function pcm16WavSlice(path: string, sliceStartSec: number, sliceDurationSec: number): Buffer {
  const bytes = readFileSync(path);
  if (bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${path}: expected RIFF/WAVE audio`);
  }
  let offset = 12;
  let channels = 0;
  let rate = 0;
  let bits = 0;
  let audioFormat = 0;
  let data: Buffer | null = null;
  while (offset + 8 <= bytes.length) {
    const id = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (id === 'fmt ' && size >= 16) {
      audioFormat = bytes.readUInt16LE(start);
      channels = bytes.readUInt16LE(start + 2);
      rate = bytes.readUInt32LE(start + 4);
      bits = bytes.readUInt16LE(start + 14);
    } else if (id === 'data') {
      data = bytes.subarray(start, Math.min(bytes.length, start + size));
    }
    offset = start + size + (size % 2);
  }
  if (audioFormat !== 1 || channels !== 1 || rate !== SAMPLE_RATE || bits !== 16 || !data) {
    throw new Error(`${path}: expected mono PCM16 ${SAMPLE_RATE}Hz audio`);
  }
  const firstByte = Math.max(0, Math.round(sliceStartSec * SAMPLE_RATE) * 2);
  const lastByte = Math.min(data.length, firstByte + Math.max(0, Math.round(sliceDurationSec * SAMPLE_RATE)) * 2);
  const pcm = data.subarray(firstByte, lastByte);
  const output = Buffer.allocUnsafe(44 + pcm.length);
  output.write('RIFF', 0); output.writeUInt32LE(36 + pcm.length, 4); output.write('WAVE', 8);
  output.write('fmt ', 12); output.writeUInt32LE(16, 16); output.writeUInt16LE(1, 20);
  output.writeUInt16LE(1, 22); output.writeUInt32LE(SAMPLE_RATE, 24);
  output.writeUInt32LE(SAMPLE_RATE * 2, 28); output.writeUInt16LE(2, 32); output.writeUInt16LE(16, 34);
  output.write('data', 36); output.writeUInt32LE(pcm.length, 40); pcm.copy(output, 44);
  return output;
}

const audioWav = pcm16WavSlice(wavPath, startSec, durationSec);

function sendAudio(req: IncomingMessage, res: ServerResponse): void {
  const baseHeaders = {
    'content-type': 'audio/wav',
    'accept-ranges': 'bytes',
    'cache-control': 'no-store',
  };
  const range = req.headers.range;
  if (!range) {
    res.writeHead(200, { ...baseHeaders, 'content-length': audioWav.length });
    res.end(audioWav);
    return;
  }
  const match = /^bytes=(\d*)-(\d*)$/.exec(range);
  if (!match) {
    res.writeHead(416, { ...baseHeaders, 'content-range': `bytes */${audioWav.length}` });
    res.end();
    return;
  }
  const requestedStart = match[1] ? Number(match[1]) : 0;
  const requestedEnd = match[2] ? Number(match[2]) : audioWav.length - 1;
  const start = Math.max(0, Math.min(audioWav.length - 1, requestedStart));
  const end = Math.max(start, Math.min(audioWav.length - 1, requestedEnd));
  const body = audioWav.subarray(start, end + 1);
  res.writeHead(206, {
    ...baseHeaders,
    'content-length': body.length,
    'content-range': `bytes ${start}-${end}/${audioWav.length}`,
  });
  res.end(body);
}

const clients = new Set<ServerResponse>();
const send = (body: Record<string, unknown>): void => {
  const line = `data: ${JSON.stringify(body)}\n\n`;
  for (const client of [...clients]) {
    if (client.destroyed) clients.delete(client);
    else client.write(line);
  }
};

let state: 'ready' | 'running' | 'complete' | 'failed' = 'ready';
let sequence = 0;
let fixtureNow = cutStartMs;
let startedAtWallMs: number | null = null;
let calls = 0;
let cachedCalls = 0;
let pipelineErrors = 0;
let lastPipelineError: Error | null = null;
const latestSegments = new Map<string, TeamsCsrcTranscriptSegment>();
const lastOrdinaryWhisperStartBySource = new Map<string, {
  monotonicMs: number;
  fixtureMs: number;
}>();
let ordinaryStarts = 0;
let forcedStarts = 0;
let bufferStarts = 0;
let ordinaryGapSamples = 0;
let ordinaryWallGapMinMs: number | null = null;
let ordinaryWallGapMaxMs: number | null = null;
let ordinaryFixtureGapMinMs: number | null = null;
let ordinaryFixtureGapMaxMs: number | null = null;
let ordinaryWallGapViolations = 0;

const cadenceReceipt = (): Record<string, number | null> => ({
  ordinaryStarts,
  forcedStarts,
  bufferStarts,
  ordinaryGapSamples,
  ordinaryWallGapMinMs,
  ordinaryWallGapMaxMs,
  ordinaryFixtureGapMinMs,
  ordinaryFixtureGapMaxMs,
  ordinaryWallGapViolations,
});

async function waitForWall(targetMs: number): Promise<void> {
  while (Date.now() < targetMs) await sleep(Math.min(250, Math.max(1, targetMs - Date.now())));
}

async function runReplay(): Promise<void> {
  state = 'running';
  startedAtWallMs = Date.now() + 600;
  fixtureNow = cutStartMs;
  sequence = 0;
  calls = 0;
  cachedCalls = 0;
  pipelineErrors = 0;
  lastPipelineError = null;
  latestSegments.clear();
  lastOrdinaryWhisperStartBySource.clear();
  ordinaryStarts = 0;
  forcedStarts = 0;
  bufferStarts = 0;
  ordinaryGapSamples = 0;
  ordinaryWallGapMinMs = null;
  ordinaryWallGapMaxMs = null;
  ordinaryFixtureGapMinMs = null;
  ordinaryFixtureGapMaxMs = null;
  ordinaryWallGapViolations = 0;
  send({ type: 'started', startedAtWallMs, cutStartMs, cutEndMs, durationSec });

  const pipeline = new TeamsCsrcGmeetPipeline({
    lookbackMs: Number(argument('lookback-ms', '600')),
    ownershipLookbackMs: Number(argument('ownership-lookback-ms', '1200')),
    flickerHoldMs: Number(argument('flicker-hold-ms', '1500')),
    onsetGapMs: Number(argument('onset-gap-ms', '1000')),
    selfName: argument('self-name', ''),
    buffer: {
      minAudioDuration: 2,
      submitInterval: 2,
      confirmThreshold: 2,
      maxBufferDuration: 30,
      idleTimeoutSec: 15,
      sampleRate: SAMPLE_RATE,
      silenceRmsThreshold: 0.0025,
      // Exercise the production/default GMeet timer itself. Fixture time remains only the captured
      // audio/timestamp clock; Whisper eligibility and in-flight skipping are real wall-clock work.
      // Keeping `now` on the captured timeline is essential because the copied GMeet window uses it
      // when advancing/resetting segment timestamps after confirmation.
      scheduleSubmissions: true,
      now: () => fixtureNow,
    },
    onSegment: (segment) => {
      if (!segment.completed && !segment.text.trim()) latestSegments.delete(segment.segmentId);
      else latestSegments.set(segment.segmentId, segment);
      send({
        type: 'segment',
        segment,
        sequence: sequence++,
        emittedAtFixtureMs: fixtureNow,
        emittedAtWallMs: Date.now(),
        callPositionMs: fixtureNow - cutStartMs,
      });
    },
    onRejectedOwnership: (segment, intervals) => send({ type: 'rejected-ownership', segment, intervals }),
    onError: (error) => {
      pipelineErrors++;
      lastPipelineError = error instanceof Error ? error : new Error(String(error));
      send({ type: 'pipeline-error', message: lastPipelineError.message });
    },
    transcribe: async (pcm, prompt, context) => {
      const hash = createHash('sha256')
        .update(Buffer.from(pcm.buffer, pcm.byteOffset, pcm.byteLength))
        .update('\0')
        .update(prompt ?? '')
        .digest('hex');
      calls++;
      const path = join(cacheDir, `${hash}.json`);
      let result: TranscriptionResult;
      let cached = true;
      const startedAt = Date.now();
      const startedAtMonotonicMs = performance.now();
      const csrc = context?.csrc;
      const sourceKey = context?.sourceKey;
      const forced = context?.forced === true;
      const trigger = context?.trigger ?? (forced ? 'forced' : 'buffer');
      const previousOrdinaryStart = sourceKey === undefined || trigger !== 'scheduled'
        ? undefined
        : lastOrdinaryWhisperStartBySource.get(sourceKey);
      const ordinaryCallGapMs = previousOrdinaryStart === undefined
        ? null
        : startedAtMonotonicMs - previousOrdinaryStart.monotonicMs;
      const ordinaryFixtureGapMs = previousOrdinaryStart === undefined
        ? null
        : fixtureNow - previousOrdinaryStart.fixtureMs;
      if (trigger === 'forced') {
        forcedStarts++;
      } else if (trigger === 'scheduled' && sourceKey !== undefined) {
        ordinaryStarts++;
        lastOrdinaryWhisperStartBySource.set(sourceKey, {
          monotonicMs: startedAtMonotonicMs,
          fixtureMs: fixtureNow,
        });
        if (ordinaryCallGapMs !== null) {
          ordinaryGapSamples++;
          ordinaryWallGapMinMs = ordinaryWallGapMinMs === null
            ? ordinaryCallGapMs
            : Math.min(ordinaryWallGapMinMs, ordinaryCallGapMs);
          ordinaryWallGapMaxMs = ordinaryWallGapMaxMs === null
            ? ordinaryCallGapMs
            : Math.max(ordinaryWallGapMaxMs, ordinaryCallGapMs);
          // The timer and this receipt both use monotonic elapsed time. Keep a small observation
          // tolerance for callback/serialization granularity while failing any material early start.
          if (ordinaryCallGapMs < cadenceMs - 20) ordinaryWallGapViolations++;
        }
        if (ordinaryFixtureGapMs !== null) {
          ordinaryFixtureGapMinMs = ordinaryFixtureGapMinMs === null
            ? ordinaryFixtureGapMs
            : Math.min(ordinaryFixtureGapMinMs, ordinaryFixtureGapMs);
          ordinaryFixtureGapMaxMs = ordinaryFixtureGapMaxMs === null
            ? ordinaryFixtureGapMs
            : Math.max(ordinaryFixtureGapMaxMs, ordinaryFixtureGapMs);
        }
      } else {
        bufferStarts++;
      }
      send({
        type: 'whisper-start',
        csrc,
        sourceKey: context?.sourceKey,
        prompt: prompt ?? null,
        hash,
        forced,
        trigger,
        startedAtWallMs: startedAt,
        startedAtFixtureMs: fixtureNow,
        callPositionMs: fixtureNow - cutStartMs,
        ordinaryCallGapMs,
        ordinaryFixtureGapMs,
      });
      try {
        result = JSON.parse(readFileSync(path, 'utf8')) as TranscriptionResult;
      } catch {
        if (!liveClient) throw new Error(`fixture cache miss ${hash} for ${context?.sourceKey ?? 'unknown source'}`);
        cached = false;
        result = await liveClient.transcribe(pcm, argument('language'), prompt);
        writeFileSync(path, `${JSON.stringify(result)}\n`);
      }
      if (cached) cachedCalls++;
      send({
        type: 'whisper',
        csrc: context?.csrc,
        sourceKey: context?.sourceKey,
        prompt: prompt ?? null,
        hash,
        cached,
        decoderMs: Date.now() - startedAt,
      });
      return result;
    },
  });

  try {
    // A cut is a view into a live meeting, not a new meeting. Restore both sides of the naming
    // correlation before audio begins; restoring transport without the matching Teams hints makes
    // the fixture artificially anonymous even though the capture contains the evidence.
    for (const signal of preCutSignals) {
      if (signal.kind === 'csrc') {
        pipeline.recordTransportEvent({ csrc: signal.csrc, active: signal.active, tMs: signal.tMs });
      } else {
        pipeline.recordHint(signal.name, signal.tMs, signal.isEnd);
      }
    }
    await waitForWall(startedAtWallMs);
    for (const signal of signals) {
      await waitForWall(startedAtWallMs + signal.tMs - cutStartMs);
      if (lastPipelineError) throw lastPipelineError;
      fixtureNow = signal.tMs;
      if (signal.kind === 'csrc') {
        pipeline.recordTransportEvent({ csrc: signal.csrc, active: signal.active, tMs: signal.tMs });
      } else if (signal.kind === 'hint') {
        pipeline.recordHint(signal.name, signal.tMs, signal.isEnd);
      } else if (signal.kind === 'audio') {
        const bytes = Buffer.from(signal.pcm, 'base64');
        pipeline.feedMixedAudio(new Float32Array(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)), signal.tMs);
      } else if (signal.kind === 'cadence') {
        const knownCsrcs = pipeline.activeCsrcs();
        send({
          type: 'scheduler',
          fixtureMs: signal.tMs,
          callPositionMs: signal.tMs - cutStartMs,
          emittedAtWallMs: Date.now(),
          knownCsrcs,
        });
      }
      if (lastPipelineError) throw lastPipelineError;
    }
    fixtureNow = cutEndMs;
    await pipeline.dispose();
    state = 'complete';
    send({ type: 'complete', calls, cachedCalls, pipelineErrors, cadence: cadenceReceipt(), health: pipeline.health(), atWallMs: Date.now() });
  } catch (error) {
    state = 'failed';
    send({ type: 'failed', message: error instanceof Error ? error.stack : String(error), calls, cachedCalls, pipelineErrors });
    try { await pipeline.dispose(); } catch { /* failure already surfaced */ }
  }
}

const json = (res: ServerResponse, status: number, body: Record<string, unknown>): void => {
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8' });
  res.end(`${JSON.stringify(body)}\n`);
};

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://127.0.0.1');
  if (url.pathname === '/events') {
    res.writeHead(200, {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-store',
      connection: 'keep-alive',
    });
    clients.add(res);
    res.write(`data: ${JSON.stringify({ type: 'ready', state, cutStartMs, cutEndMs, durationSec, startedAtWallMs })}\n\n`);
    res.write(`data: ${JSON.stringify({
      type: 'snapshot',
      state,
      fixtureNow,
      startedAtWallMs,
      segments: [...latestSegments.values()],
      cadence: cadenceReceipt(),
    })}\n\n`);
    req.on('close', () => clients.delete(res));
    return;
  }
  if (url.pathname === '/start') {
    if (state === 'running') return json(res, 409, { state, startedAtWallMs });
    if (state === 'complete') return json(res, 409, { state, message: 'restart the witness process for a fresh run' });
    void runReplay();
    return json(res, 202, { state: 'starting', startedAtWallMs, cutStartMs, cutEndMs, durationSec });
  }
  if (url.pathname === '/status') return json(res, 200, {
    state,
    startedAtWallMs,
    fixtureNow,
    calls,
    cachedCalls,
    pipelineErrors,
    lastPipelineError: lastPipelineError?.message ?? null,
    clients: clients.size,
    cadence: cadenceReceipt(),
  });
  if (url.pathname === '/audio.wav') return sendAudio(req, res);
  return json(res, 404, { error: 'not found' });
});

server.listen(port, '127.0.0.1', () => {
  console.log(`[teams-csrc-live-replay] events http://127.0.0.1:${port}/events`);
  console.log(`[teams-csrc-live-replay] start  http://127.0.0.1:${port}/start`);
  console.log(`[teams-csrc-live-replay] audio  http://127.0.0.1:${port}/audio.wav`);
  console.log(`[teams-csrc-live-replay] ${capturedPath} · ${startSec}s..${startSec + durationSec}s · ${signals.length} wall-clock inputs`);
});
