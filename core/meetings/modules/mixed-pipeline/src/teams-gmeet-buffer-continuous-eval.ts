/**
 * Isolated buffer→Whisper evaluation for MS Teams continuous mixed audio.
 *
 * There is deliberately no CSRC, speaker mapping, diarization, word-timestamp ownership, or
 * runtime publication here. One uninterrupted PCM source drives the faithful shared Google Meet
 * window manager and is compared with one Whisper pass over the identical PCM slice.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { GmeetCompatibleBuffer } from '@vexa/transcribe-buffer';
import { TranscriptionClient, type TranscriptionResult } from '@vexa/transcribe-whisper';
import { hallucinationRule } from './hallucination-gate.js';

const SAMPLE_RATE = 16_000;
const SOURCE = 'teams:continuous';

const arg = (name: string): string | undefined => {
  const index = process.argv.indexOf(`--${name}`);
  return index >= 0 ? process.argv[index + 1] : undefined;
};
const required = (name: string): string => {
  const value = arg(name);
  if (!value) throw new Error(`--${name} is required`);
  return value;
};

function loadWav(path: string): Float32Array {
  const bytes = readFileSync(path);
  if (bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error(`${path}: expected RIFF/WAVE`);
  }
  let offset = 12;
  let audioFormat = 0;
  let channels = 0;
  let sampleRate = 0;
  let bitsPerSample = 0;
  let data: Buffer | null = null;
  while (offset + 8 <= bytes.length) {
    const id = bytes.toString('ascii', offset, offset + 4);
    const size = bytes.readUInt32LE(offset + 4);
    const start = offset + 8;
    const end = Math.min(bytes.length, start + size);
    if (id === 'fmt ' && size >= 16) {
      audioFormat = bytes.readUInt16LE(start);
      channels = bytes.readUInt16LE(start + 2);
      sampleRate = bytes.readUInt32LE(start + 4);
      bitsPerSample = bytes.readUInt16LE(start + 14);
    } else if (id === 'data') {
      data = bytes.subarray(start, end);
    }
    offset = start + size + (size % 2);
  }
  if (audioFormat !== 1 || channels !== 1 || sampleRate !== SAMPLE_RATE || bitsPerSample !== 16 || !data) {
    throw new Error(`${path}: expected mono PCM16 ${SAMPLE_RATE}Hz`);
  }
  const samples = new Float32Array(Math.floor(data.length / 2));
  for (let index = 0; index < samples.length; index++) {
    samples[index] = data.readInt16LE(index * 2) / 32768;
  }
  return samples;
}

async function main(): Promise<void> {
  const wavPath = required('wav');
  const outPath = required('out');
  const cacheDir = required('cache-dir');
  const serviceUrl = required('stt-url');
  const token = process.env.VEXA_STT_TOKEN;
  if (!token) throw new Error('VEXA_STT_TOKEN is required');

  const allSamples = loadWav(wavPath);
  const startSec = Number(arg('start-sec') ?? 0);
  const requestedDurationSec = arg('duration-sec') === undefined ? undefined : Number(arg('duration-sec'));
  const from = Math.max(0, Math.round(startSec * SAMPLE_RATE));
  const to = requestedDurationSec === undefined
    ? allSamples.length
    : Math.min(allSamples.length, from + Math.max(0, Math.round(requestedDurationSec * SAMPLE_RATE)));
  if (from >= to) throw new Error(`empty slice: start=${startSec}, duration=${requestedDurationSec ?? 'rest'}`);
  const samples = allSamples.slice(from, to);
  const durationMs = samples.length / SAMPLE_RATE * 1000;
  const cadenceMs = Number(arg('cadence-sec') ?? 2) * 1000;
  const frameMs = Number(arg('frame-ms') ?? 500);
  const languageArg = arg('language');
  const language = languageArg && languageArg !== 'auto' ? languageArg : undefined;

  mkdirSync(cacheDir, { recursive: true });
  mkdirSync(dirname(outPath), { recursive: true });
  const client = new TranscriptionClient({
    serviceUrl,
    apiToken: token,
    model: arg('stt-model') ?? 'large-v3-turbo',
    maxRetries: 1,
  });

  let virtualNow = 0;
  let currentSubmission: Promise<void> | null = null;
  const windowOccurrences = new Map<string, number>();
  const submissions: Array<Record<string, unknown>> = [];
  const confirmed: Array<Record<string, unknown>> = [];
  const pending = new Map<string, Record<string, unknown>>();
  const firstVisible = new Map<string, Record<string, unknown>>();

  const manager = new GmeetCompatibleBuffer({
    // These are the actual Google Meet defaults, repeated explicitly so the receipt is reviewable.
    minAudioDuration: 2,
    submitInterval: 2,
    confirmThreshold: 2,
    maxBufferDuration: 30,
    idleTimeoutSec: 15,
    sampleRate: SAMPLE_RATE,
    silenceRmsThreshold: 0.0025,
    scheduleSubmissions: false,
    now: () => virtualNow,
    logger: (message) => console.log(message),
    isHallucination: (text) => hallucinationRule(text) !== null,
  });
  manager.addSpeaker(SOURCE, 'Teams continuous source');

  manager.onSegmentPending = (source, _name, text, startMs, detectedLanguage) => {
    const id = `${source}:${Math.round(startMs)}`;
    if (!text.trim()) {
      pending.delete(id);
      return;
    }
    const row = {
      segmentId: id,
      text: text.trim(),
      startMs,
      visibleAtMs: virtualNow,
      language: detectedLanguage ?? null,
    };
    pending.set(id, row);
    if (!firstVisible.has(id)) firstVisible.set(id, row);
  };
  manager.onSegmentConfirmed = (source, _name, text, startMs, endMs, segmentId, detectedLanguage) => {
    pending.delete(`${source}:${Math.round(startMs)}`);
    confirmed.push({
      segmentId,
      text: text.trim(),
      startMs,
      endMs,
      confirmedAtMs: virtualNow,
      language: detectedLanguage ?? null,
    });
    const visibleId = `${source}:${Math.round(startMs)}`;
    if (!firstVisible.has(visibleId)) {
      firstVisible.set(visibleId, {
        segmentId: visibleId,
        text: text.trim(),
        startMs,
        visibleAtMs: virtualNow,
        language: detectedLanguage ?? null,
      });
    }
  };

  manager.onSegmentReady = (_source, _name, audio) => {
    const startMs = manager.getBufferStartMs(SOURCE);
    const endMs = startMs + audio.length / SAMPLE_RATE * 1000;
    const windowKey = `${Math.round(startMs)}:${Math.round(endMs)}`;
    const occurrence = windowOccurrences.get(windowKey) ?? 0;
    windowOccurrences.set(windowKey, occurrence + 1);
    const cachePath = join(cacheDir, `${windowKey}:${occurrence}.json`);
    const prompt = manager.getLastConfirmedText(SOURCE) || undefined;
    currentSubmission = (async () => {
      const cached = existsSync(cachePath);
      const started = Date.now();
      const result = cached
        ? JSON.parse(readFileSync(cachePath, 'utf8')) as TranscriptionResult
        : await client.transcribe(audio, language, prompt);
      if (!cached) writeFileSync(cachePath, `${JSON.stringify(result)}\n`);
      const elapsedMs = Date.now() - started;
      const segmentEnd = result.segments?.length ? result.segments[result.segments.length - 1].end : undefined;
      manager.handleTranscriptionResult(SOURCE, result.text, segmentEnd, result.segments, result.language);
      submissions.push({
        windowStartMs: startMs,
        windowEndMs: endMs,
        durationMs: endMs - startMs,
        prompt: prompt ?? null,
        resultText: result.text.trim(),
        segmentCount: result.segments?.length ?? 0,
        // Evaluation-only evidence: production ownership does not consume these
        // yet. Keeping the raw Whisper segments lets the contested-speech harness
        // measure whether the faithful mixed GMeet window supplies a stable phrase
        // time before that concern is wired into Teams.
        segments: result.segments ?? [],
        elapsedMs,
        cached,
        occurrence,
      });
      console.log(`[buffer ${submissions.length}] ${Math.round(startMs)}:${Math.round(endMs)} prompt=${prompt ? 'yes' : 'no'} ${elapsedMs}ms${cached ? ' cached' : ''}`);
    })();
  };

  const frameSamples = Math.max(1, Math.round(frameMs / 1000 * SAMPLE_RATE));
  let nextSubmitMs = cadenceMs;
  for (let frameStart = 0; frameStart < samples.length; frameStart += frameSamples) {
    const frameEnd = Math.min(samples.length, frameStart + frameSamples);
    virtualNow = frameStart / SAMPLE_RATE * 1000;
    manager.feedAudio(SOURCE, samples.subarray(frameStart, frameEnd), virtualNow);
    const frameEndMs = frameEnd / SAMPLE_RATE * 1000;
    while (frameEndMs >= nextSubmitMs) {
      virtualNow = nextSubmitMs;
      currentSubmission = null;
      await manager.requestTranscription(SOURCE);
      if (currentSubmission) await currentSubmission;
      nextSubmitMs += cadenceMs;
    }
  }

  virtualNow = durationMs;
  currentSubmission = null;
  await manager.flushSpeaker(SOURCE, true);
  if (currentSubmission) await currentSubmission;
  manager.removeAll();

  const singlePassPath = join(cacheDir, 'single-pass.json');
  const singlePassCached = existsSync(singlePassPath);
  const singleStarted = Date.now();
  const singlePass = singlePassCached
    ? JSON.parse(readFileSync(singlePassPath, 'utf8')) as TranscriptionResult
    : await client.transcribe(samples, language);
  if (!singlePassCached) writeFileSync(singlePassPath, `${JSON.stringify(singlePass)}\n`);
  const singlePassElapsedMs = Date.now() - singleStarted;
  const decoderElapsed = submissions.map((row) => Number(row.elapsedMs)).filter(Number.isFinite);
  const maxDecoderElapsedMs = decoderElapsed.length ? Math.max(...decoderElapsed) : 0;

  writeFileSync(outPath, `${JSON.stringify({
    kind: 'teams-gmeet-buffer-continuous-eval',
    wav: wavPath,
    slice: { startSec, durationSec: durationMs / 1000 },
    language: language ?? null,
    gmeetDefaults: {
      minAudioDuration: 2,
      submitInterval: 2,
      confirmThreshold: 2,
      maxBufferDuration: 30,
      idleTimeoutSec: 15,
      sampleRate: SAMPLE_RATE,
      silenceRmsThreshold: 0.0025,
    },
    cadenceMs,
    buffered: {
      text: confirmed.map((row) => String(row.text)).join(' ').trim(),
      confirmed,
      pending: [...pending.values()],
      firstVisible: [...firstVisible.values()],
      submissions,
      latencyReceipt: {
        cadenceMs,
        maxDecoderElapsedMs,
        cadencePlusMaxDecoderMs: cadenceMs + maxDecoderElapsedMs,
      },
    },
    singlePass: {
      text: singlePass.text.trim(),
      segments: singlePass.segments,
      language: singlePass.language,
      elapsedMs: singlePassElapsedMs,
      cached: singlePassCached,
    },
  }, null, 2)}\n`);
  console.log(`wrote ${confirmed.length} confirmed rows and ${pending.size} outstanding drafts to ${outPath}`);
}

void main().catch((error) => {
  console.error(error instanceof Error ? error.stack : error);
  process.exit(1);
});
