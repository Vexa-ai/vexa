/**
 * clean-audio-replay — drive the REAL ChunkedTranscriber with CLEAN, known-good audio
 * (a 16 kHz mono WAV) and nothing else: no tape, no platform hints, no capture path.
 *
 * Sibling of tape-replay.ts, and deliberately narrower. tape-replay measures the NAMING
 * path on a captured tape (whose audio may already be damaged by capture). This one
 * measures the TRANSCRIPTION path on audio we know is intact, so a quality regression can
 * be attributed to the pipeline (chunking · ring cut · LocalAgreement · prompt threading ·
 * submit cadence) rather than to capture.
 *
 *   npx tsx src/clean-audio-replay.ts --wav clean.wav --language ru \
 *     --stt-url http://localhost:8085 --out out.txt [--realtime] [--frame-ms 20]
 *
 * VEXA_STT_TOKEN carries the bearer token (never on the command line).
 * Without --stt-url the pipeline runs against a stub, which only exercises structure.
 * `--segmenter oneshot` replaces pyannote with a single always-on turn window, isolating
 * the confirm/publish machinery from segmentation variance.
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { ChunkedTranscriber, type BoundarySource } from './index.js';
import { TranscriptionClient } from '@vexa/transcribe-whisper';
import type { BoundaryEvent } from './pyannote-segmenter.js';

const arg = (name: string): string | undefined => {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 ? process.argv[i + 1] : undefined;
};
const WAV = arg('wav');
if (!WAV) { console.error('usage: tsx src/clean-audio-replay.ts --wav <16k mono wav> [--language ru] [--stt-url URL] [--out txt]'); process.exit(2); }

/** Minimal RIFF reader for the one shape this harness accepts: PCM s16, mono, 16 kHz. */
function readWav16kMono(path: string): Float32Array {
  const b = readFileSync(path);
  if (b.toString('ascii', 0, 4) !== 'RIFF' || b.toString('ascii', 8, 12) !== 'WAVE') throw new Error('not a RIFF/WAVE file');
  let off = 12, fmt: { channels: number; rate: number; bits: number } | null = null, data: Buffer | null = null;
  while (off + 8 <= b.length) {
    const id = b.toString('ascii', off, off + 4);
    const size = b.readUInt32LE(off + 4);
    const body = b.subarray(off + 8, off + 8 + size);
    if (id === 'fmt ') fmt = { channels: body.readUInt16LE(2), rate: body.readUInt32LE(4), bits: body.readUInt16LE(14) };
    if (id === 'data') data = body;
    off += 8 + size + (size % 2);
  }
  if (!fmt || !data) throw new Error('missing fmt/data chunk');
  if (fmt.channels !== 1 || fmt.rate !== 16000 || fmt.bits !== 16) {
    throw new Error(`expected 16kHz mono s16, got ${fmt.rate}Hz ${fmt.channels}ch ${fmt.bits}bit`);
  }
  const n = Math.floor(data.length / 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) out[i] = data.readInt16LE(i * 2) / 32768;
  return out;
}

async function main(): Promise<void> {
  const pcm = readWav16kMono(WAV!);
  const frameMs = Number(arg('frame-ms') ?? 20);
  const frameLen = Math.round((frameMs / 1000) * 16000);
  const durationMs = (pcm.length / 16000) * 1000;
  const language = arg('language') ?? 'en';
  const segmenterMode = arg('segmenter') ?? 'pyannote';
  console.log(`wav: ${(durationMs / 1000).toFixed(1)}s, ${frameMs}ms frames, language=${language}, segmenter=${segmenterMode}`);

  const rows: { id: string; speaker: string; text: string; startMs: number; endMs: number }[] = [];
  const durable = new Map<string, { text: string; startMs: number; endMs: number; speaker: string }>();
  let pendingIds = new Set<string>();
  let retracted = 0;
  const reconcilePending = (pending: { segmentId: string }[]): void => {
    const next = new Set(pending.map((c) => c.segmentId));
    for (const id of pendingIds) if (!next.has(id)) { durable.delete(id); retracted++; }
    pendingIds = next;
  };

  const sttUrl = arg('stt-url');
  const client = sttUrl
    ? new TranscriptionClient({ serviceUrl: sttUrl, apiToken: process.env.VEXA_STT_TOKEN, model: arg('stt-model') })
    : null;
  let sttCalls = 0, sttFailures = 0, sttAudioMs = 0;

  let emit: ((ev: BoundaryEvent) => void) | null = null;
  const boundaries: BoundaryEvent[] = [];
  const tc = await ChunkedTranscriber.create({
    language,
    transcribe: async (chunk: Float32Array, prompt?: string) => {
      sttCalls++; sttAudioMs += (chunk.length / 16000) * 1000;
      if (!client) {
        const secs = Math.max(1, Math.floor(chunk.length / 16000));
        const text = Array.from({ length: secs }, (_, i) => `w${i}`).join(' ');
        return { text, language, language_probability: 0.99, duration: chunk.length / 16000,
          segments: [{ text, start: 0, end: chunk.length / 16000, no_speech_prob: 0.01, avg_logprob: -0.2, compression_ratio: 1.1 } as any] };
      }
      try { return await client.transcribe(chunk, language, prompt); }
      catch { sttFailures++; return { text: '', language, duration: chunk.length / 16000, segments: [] }; }
    },
    publish: (speaker, confirmed, pending) => {
      for (const c of confirmed) durable.set(c.segmentId, { speaker, text: c.text, startMs: c.startMs, endMs: c.endMs });
      reconcilePending(pending ?? []);
      for (const p of pending ?? []) durable.set(p.segmentId, { speaker, text: p.text, startMs: p.startMs, endMs: p.endMs });
    },
    publishPending: (speaker, segs) => {
      reconcilePending(segs);
      for (const p of segs) durable.set(p.segmentId, { speaker, text: p.text, startMs: p.startMs, endMs: p.endMs });
    },
    clearPending: () => { reconcilePending([]); },
    rename: (_o, newS, segs) => { for (const s of segs) durable.set(s.segmentId, { speaker: newS, text: s.text, startMs: s.startMs, endMs: s.endMs }); },
    ...(segmenterMode === 'oneshot'
      ? { makeSegmenter: async (onBoundary: (ev: BoundaryEvent) => void): Promise<BoundarySource> => {
          emit = onBoundary;
          return { appendFrame: async () => {}, reset: () => {} };
        } }
      : { makeSegmenter: async (onBoundary: (ev: BoundaryEvent) => void): Promise<BoundarySource> => {
          // The REAL segmenter, with every boundary recorded: the cut timeline is the
          // first thing to check when audio never reaches STT.
          const { PyannoteSegmenter } = await import('./pyannote-segmenter.js');
          return await PyannoteSegmenter.create({ inferIntervalMs: 500, onBoundary: (ev) => { boundaries.push(ev); onBoundary(ev); } });
        } }),
    log: (m: string) => { if (process.argv.includes('--verbose')) console.log(m); },
  });

  const t0 = 1_700_000_000_000;   // any stable epoch base; only deltas matter
  if (segmenterMode === 'oneshot') emit!({ tMs: t0, kind: 'silence→speaker', confidence: 0.9 });

  const realtime = process.argv.includes('--realtime');
  const wall0 = Date.now();
  for (let off = 0, i = 0; off < pcm.length; off += frameLen, i++) {
    const ts = t0 + (off / 16000) * 1000;
    if (realtime) {
      const wait = wall0 + (ts - t0) - Date.now();
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
    }
    tc.feedAudio(pcm.subarray(off, Math.min(pcm.length, off + frameLen)), ts);
    if (!realtime && i % 50 === 0) await new Promise((r) => setImmediate(r));
  }
  if (segmenterMode === 'oneshot') emit!({ tMs: t0 + durationMs, kind: 'speaker→silence', confidence: 0.9 });
  await tc.dispose();

  for (const [id, v] of durable) rows.push({ id, ...v });
  rows.sort((a, b) => a.startMs - b.startMs);
  const text = rows.map((r) => r.text).join(' ');
  console.log(`\nrows: ${rows.length} · retracted drafts: ${retracted} · words: ${text.split(/\s+/).filter(Boolean).length}`);
  console.log(`STT: ${sttCalls} call(s), ${sttFailures} failure(s), ${(sttAudioMs / 1000).toFixed(1)}s of audio submitted ` +
    `(${(sttAudioMs / durationMs).toFixed(2)}× the source duration)`);
  console.log(`turn stats: ${JSON.stringify(tc.stats())}`);

  const out = arg('out');
  if (out) { writeFileSync(out, text + '\n'); console.log(`wrote transcript → ${out}`); }
  const outBounds = arg('out-boundaries');
  if (outBounds) { writeFileSync(outBounds, JSON.stringify(boundaries.map((b) => ({ s: (b.tMs - t0) / 1000, kind: b.kind })), null, 0) + '\n'); console.log(`wrote ${boundaries.length} boundaries → ${outBounds}`); }
  const outJson = arg('out-json');
  if (outJson) { writeFileSync(outJson, JSON.stringify(rows, null, 2) + '\n'); console.log(`wrote rows → ${outJson}`); }
}

void main().catch((e) => { console.error('clean-audio-replay failed:', e); process.exit(1); });
