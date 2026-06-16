#!/usr/bin/env tsx
/**
 * run — evaluate the mixed pipeline's segmentation/transcription against the
 * Deepgram reference, over a chosen region of a pulled fixture.
 *
 *   1. cut the [start,end] region from the fixture audio (→ region.wav for playback)
 *   2. stream it through OUR mixed lane (real pyannote segmenter + Whisper,
 *      clustering OFF) — capturing the confirmed segments AND every pyannote
 *      boundary it emits
 *   3. render a side-by-side page: Deepgram (left) vs Vexa (right), timestamp-
 *      aligned, with audio playback + segmentation-boundary pointers
 *
 * The agent picks the region (start/end) from deepgram.json per the desired
 * "quality" (frequent speaker changes, a monologue, overlap, …) — see CLAUDE.md.
 *
 *   TRANSCRIPTION_SERVICE_URL=… TRANSCRIPTION_SERVICE_TOKEN=… \
 *     tsx run.ts --id <id> --start <sec> --end <sec> [--speed 3]
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ChunkedTranscriber, PyannoteSegmenter, type BoundarySource, type BoundaryEvent } from '@vexa/mixed-pipeline';
import { TranscriptionClient } from '@vexa/transcribe-whisper';
import { renderPage } from './lib/page.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = process.env.EVAL_FIXTURES || path.join(HERE, 'fixtures');
const SR = 16000;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const arg = (k: string, d?: string) => { const i = process.argv.indexOf(`--${k}`); return i >= 0 ? process.argv[i + 1] : d; };
const id = arg('id'); const start = Number(arg('start', '0')); const end = Number(arg('end', '60'));
const speed = Number(arg('speed', '3'));
if (!id) { console.error('usage: tsx run.ts --id <id> --start <sec> --end <sec> [--speed 3]'); process.exit(2); }
const dir = path.join(FIXTURES, id);
const txUrl = process.env.TRANSCRIPTION_SERVICE_URL;
if (!txUrl) { console.error('set TRANSCRIPTION_SERVICE_URL (+ _TOKEN) — our Whisper egress'); process.exit(2); }

// ── 1. cut the region ──────────────────────────────────────────────
function readWav(p: string): Int16Array {
  const b = fs.readFileSync(p); return new Int16Array(b.buffer, b.byteOffset + 44, (b.length - 44) >> 1);
}
function writeWav(p: string, pcm16: Int16Array): void {
  const hdr = Buffer.alloc(44); const dataLen = pcm16.length * 2;
  hdr.write('RIFF', 0); hdr.writeUInt32LE(36 + dataLen, 4); hdr.write('WAVE', 8);
  hdr.write('fmt ', 12); hdr.writeUInt32LE(16, 16); hdr.writeUInt16LE(1, 20); hdr.writeUInt16LE(1, 22);
  hdr.writeUInt32LE(SR, 24); hdr.writeUInt32LE(SR * 2, 28); hdr.writeUInt16LE(2, 32); hdr.writeUInt16LE(16, 34);
  hdr.write('data', 36); hdr.writeUInt32LE(dataLen, 40);
  fs.writeFileSync(p, Buffer.concat([hdr, Buffer.from(pcm16.buffer, pcm16.byteOffset, dataLen)]));
}
const all = readWav(path.join(dir, 'audio.wav'));
const i0 = Math.round(start * SR), i1 = Math.min(all.length, Math.round(end * SR));
const region16 = all.slice(i0, i1);
const regionWavName = `region-${start}-${end}.wav`;
const regionWavPath = path.join(dir, regionWavName);
writeWav(regionWavPath, region16);
const regionF32 = new Float32Array(region16.length);
for (let i = 0; i < region16.length; i++) regionF32[i] = region16[i] / 32768;
console.log(`[run] region ${start}-${end}s (${(regionF32.length / SR).toFixed(0)}s) → ${regionWavName}`);

// ── 2. Deepgram reference for the region (shifted region-relative) ──
const dg = JSON.parse(fs.readFileSync(path.join(dir, 'deepgram.json'), 'utf8'));
const refUtts = (dg.results?.utterances || [])
  .filter((u: any) => u.end > start && u.start < end)
  .map((u: any) => ({ speaker: `S${u.speaker}`, text: u.transcript, start: Math.max(0, u.start - start), end: Math.min(end - start, u.end - start) }));

// ── 3. stream through our mixed lane ───────────────────────────────
const client = new TranscriptionClient({ serviceUrl: txUrl, apiToken: process.env.TRANSCRIPTION_SERVICE_TOKEN, sampleRate: SR });
const boundaries: BoundaryEvent[] = [];
const vexa: { speaker: string; text: string; start: number; end: number }[] = [];
// streaming proof: count real Whisper round-trips + real segmenter inference frames
let sttCalls = 0, sttSamples = 0, segFrames = 0;
const tc = await ChunkedTranscriber.create({
  language: 'en',                                      // @vexa/mixed-pipeline: segmentation-separated, hints-only naming, NO clustering (dropped per plan)
  transcribe: (pcm, prompt) => { sttCalls++; sttSamples += pcm.length; return client.transcribe(pcm, 'en', prompt); },
  publish: (speaker, confirmed) => { for (const c of confirmed) vexa.push({ speaker, text: c.text, start: c.startMs / 1000, end: c.endMs / 1000 }); },  // pending (3rd arg) is the live draft — eval keeps only confirmed
  publishPending: () => {}, clearPending: () => {}, rename: () => {},
  makeSegmenter: async (onBoundary): Promise<BoundarySource> => {
    const seg = await PyannoteSegmenter.create({ inferIntervalMs: 500, onBoundary: (ev) => { boundaries.push(ev); onBoundary(ev); } });
    return { appendFrame: (pcm, ts) => { segFrames++; return seg.appendFrame(pcm, ts); }, reset: () => seg.reset() };
  },
  log: () => {},
});
console.log(`[run] streaming through the mixed lane @${speed}× (real pyannote + Whisper)…`);
const FRAME = 4096; const t0 = Date.now(); let framesFed = 0;
for (let off = 0; off < regionF32.length; off += FRAME) {
  const tsMs = (off / SR) * 1000;
  const wait = tsMs / speed - (Date.now() - t0); if (wait > 0) await sleep(wait);
  tc.feedAudio(regionF32.subarray(off, Math.min(off + FRAME, regionF32.length)), tsMs);
  framesFed++;
}
await sleep(2000);            // let the tail submit
await tc.dispose();           // flush + final confirm
console.log(`[run] streamed: ${framesFed} frames fed (${FRAME}-sample chunks, not one buffer) → ${segFrames} reached real PyannoteSegmenter.appendFrame · ${sttCalls} real Whisper round-trips to ${new URL(txUrl).host} (${(sttSamples / SR).toFixed(0)}s of audio submitted)`);
console.log(`[run] vexa: ${vexa.length} segments · pyannote: ${boundaries.length} boundaries (${boundaries.reduce((m: any, b) => ((m[b.kind] = (m[b.kind] || 0) + 1), m), {}) && JSON.stringify(boundaries.reduce((m: any, b) => ((m[b.kind] = (m[b.kind] || 0) + 1), m), {}))})`);

// ── 4. render the side-by-side page ────────────────────────────────
const meta = JSON.parse(fs.readFileSync(path.join(dir, 'meta.json'), 'utf8'));
// inline the audio so the page is self-contained (file:// blocks local media in Chrome)
const audioDataUri = `data:audio/wav;base64,${fs.readFileSync(regionWavPath).toString('base64')}`;
const html = renderPage({
  meta, start, end, audioDataUri,
  deepgram: refUtts,
  vexa,
  boundaries: boundaries.map((b) => ({ t: b.tMs / 1000, kind: b.kind, conf: b.confidence })),
});
const out = arg('out', path.join(dir, `eval-${start}-${end}.html`))!;
fs.writeFileSync(out, html);
console.log(`[run] ✓ page: ${out}\n       view it (audio playback needs http, not file://):  npm run serve -- --id ${id}`);
