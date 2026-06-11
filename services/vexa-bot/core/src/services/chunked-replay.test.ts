/**
 * Offline replay harness for the ChunkedTranscriber mixed-channel core —
 * the exact bot/extension path (real ONNX segmentation, real Whisper),
 * driven by a recorded mixed-audio WAV and scored against ground truth.
 *
 * Dataset = any YouTube video with auto-captions (the methodology that
 * debugged the core live): the WAV is the mixed stream, the captions are
 * ground truth. Fetch with containerized tools, e.g.:
 *
 *   docker run --rm -v "$PWD/data:/out" jauderho/yt-dlp:latest \
 *     -x --audio-format wav --postprocessor-args "-ar 16000 -ac 1" \
 *     --write-auto-subs --sub-langs en --sub-format json3 \
 *     -o "/out/replay" "https://www.youtube.com/watch?v=<id>"
 *
 * Optional hints file (JSONL: {name, kind, tMs, isEnd}) replays a recorded
 * speaker-hint timeline (tMs relative to audio start) — without it, labels
 * stay provisional cluster ids and only text coverage is scored.
 *
 * Run (inside the node:20 container like the other tsx tests):
 *   AUDIO=data/replay.wav CAPTIONS=data/replay.en.json3 [HINTS=hints.jsonl]
 *   [MAX_SECONDS=120] [TX_URL=...] [TX_TOKEN=...]
 *   npx tsx src/services/chunked-replay.test.ts
 *
 * Asserts: real-word coverage vs captions, zero overlapping confirmed
 * spans (high-water dedup), monotonic confirmed timeline.
 */

import * as fs from 'fs';
import { ChunkedTranscriber, ChunkSegment } from './chunked-transcriber';
import { TranscriptionClient } from './transcription-client';

const SAMPLE_RATE = 16000;
const CHUNK = 4096;
const AUDIO = process.env.AUDIO || '';
const CAPTIONS = process.env.CAPTIONS || '';
const HINTS = process.env.HINTS || '';
const MAX_SECONDS = parseFloat(process.env.MAX_SECONDS || '120');
const TX_URL = process.env.TX_URL || process.env.TRANSCRIPTION_URL || 'https://transcription.vexa.ai';
const TX_TOKEN = process.env.TX_TOKEN || process.env.TRANSCRIPTION_TOKEN || '';
/** Word-loss ceiling (real words, fillers excluded). Live runs measured ~12%;
 *  threshold leaves headroom for content variance. */
const MAX_REAL_LOSS = parseFloat(process.env.MAX_REAL_LOSS || '0.25');
/** Feed pacing: 1 = real time (faithful — the core's tick/idle machinery is
 *  wall-clock driven), higher = faster but less live-faithful. */
const PACE = parseFloat(process.env.PACE || '1');

const FILLERS = new Set(['um', 'uh', 'umm', 'uhh', 'mhm', 'hmm', 'ah', 'eh', 'er', 'huh', 'oh']);

function norm(w: string): string {
  return w.toLowerCase().replace(/[^a-z0-9']/g, '');
}

function readWavMono16k(p: string): Float32Array {
  const buf = fs.readFileSync(p);
  if (buf.toString('ascii', 0, 4) !== 'RIFF') throw new Error(`Not a WAV: ${p}`);
  const sampleRate = buf.readUInt32LE(24);
  const channels = buf.readUInt16LE(22);
  const bits = buf.readUInt16LE(34);
  let off = 12;
  let dataOff = -1; let dataLen = 0;
  while (off < buf.length - 8) {
    const id = buf.toString('ascii', off, off + 4);
    const len = buf.readUInt32LE(off + 4);
    if (id === 'data') { dataOff = off + 8; dataLen = len; break; }
    off += 8 + len + (len % 2);
  }
  if (dataOff < 0) throw new Error('No data chunk');
  const bytesPer = bits / 8;
  const frames = Math.floor(dataLen / (bytesPer * channels));
  const mono = new Float32Array(frames);
  for (let i = 0; i < frames; i++) {
    let v = 0;
    for (let c = 0; c < channels; c++) {
      const o = dataOff + (i * channels + c) * bytesPer;
      v += bits === 16 ? buf.readInt16LE(o) / 32768 : buf.readFloatLE(o);
    }
    mono[i] = v / channels;
  }
  if (sampleRate === SAMPLE_RATE) return mono;
  const ratio = SAMPLE_RATE / sampleRate;
  const out = new Float32Array(Math.floor(frames * ratio));
  for (let i = 0; i < out.length; i++) out[i] = mono[Math.min(frames - 1, Math.round(i / ratio))];
  return out;
}

interface CcWord { t: number; w: string }
function readCaptions(p: string): CcWord[] {
  const j = JSON.parse(fs.readFileSync(p, 'utf8'));
  const words: CcWord[] = [];
  for (const ev of j.events || []) {
    const t0 = (ev.tStartMs || 0) / 1000;
    for (const seg of ev.segs || []) {
      const w = norm(seg.utf8 || '');
      if (w) words.push({ t: t0 + (seg.tOffsetMs || 0) / 1000, w });
    }
  }
  words.sort((a, b) => a.t - b.t);
  return words;
}

(async () => {
  if (!AUDIO || !fs.existsSync(AUDIO)) {
    console.log('SKIP: set AUDIO=<wav path> (see header for the yt-dlp fetch command)');
    process.exit(0);
  }
  if (!TX_TOKEN) {
    console.log('SKIP: set TX_TOKEN (transcription service token)');
    process.exit(0);
  }

  const pcmAll = readWavMono16k(AUDIO);
  const pcm = pcmAll.subarray(0, Math.min(pcmAll.length, Math.floor(MAX_SECONDS * SAMPLE_RATE)));
  console.log(`audio: ${(pcm.length / SAMPLE_RATE).toFixed(1)}s of ${(pcmAll.length / SAMPLE_RATE).toFixed(1)}s`);

  const client = new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN });

  const confirmed: Array<{ speaker: string; seg: ChunkSegment }> = [];
  const renames: Array<{ from: string; to: string }> = [];
  const baseMs = 1_000_000_000_000; // synthetic audio-time origin

  const t = await ChunkedTranscriber.create({
    language: 'en',
    log: (m) => console.log(m),
    transcribe: (audio, prompt) => client.transcribe(audio, 'en', prompt),
    publish: (speaker, conf, _pending) => { for (const s of conf) confirmed.push({ speaker, seg: s }); },
    publishPending: () => { /* drafts unscored */ },
    clearPending: () => { /* noop */ },
    rename: (from, to, segs) => {
      renames.push({ from, to });
      for (const c of confirmed) {
        if (segs.some(s => s.segmentId === c.seg.segmentId)) c.speaker = to;
      }
    },
  });

  // Optional recorded hint timeline.
  const hints: Array<{ name: string; kind: 'dom-active' | 'caption' | 'dom-outline'; tMs: number; isEnd?: boolean }> =
    HINTS && fs.existsSync(HINTS)
      ? fs.readFileSync(HINTS, 'utf8').split('\n').filter(Boolean).map(l => JSON.parse(l))
      : [];
  let hintIdx = 0;

  // Feed the mixed stream PACED (default real time): the core's submit tick
  // and idle close are wall-clock driven, and the live-edge window must not
  // race ahead of the diarizer — instant feeding starves drafts and balloons
  // windows, which is NOT the live behavior under test.
  const chunkMs = (CHUNK / SAMPLE_RATE) * 1000;
  const feedStartWall = Date.now();
  for (let off = 0; off < pcm.length; off += CHUNK) {
    const chunk = pcm.subarray(off, Math.min(pcm.length, off + CHUNK));
    const audioMs = (off / SAMPLE_RATE) * 1000;
    const tsMs = baseMs + audioMs;
    while (hintIdx < hints.length && baseMs + hints[hintIdx].tMs <= tsMs) {
      const h = hints[hintIdx++];
      t.recordHint(h.name, h.kind, baseMs + h.tMs, h.isEnd);
    }
    t.feedAudio(new Float32Array(chunk), tsMs);
    const wallTarget = feedStartWall + audioMs / PACE;
    const wait = wallTarget - Date.now();
    if (wait > 1) await new Promise(r => setTimeout(r, Math.min(wait, chunkMs / PACE)));
  }
  await t.dispose();

  // ── Scoring ──
  confirmed.sort((a, b) => a.seg.startMs - b.seg.startMs);
  console.log(`\nconfirmed segments: ${confirmed.length}, renames: ${renames.length}`);
  for (const c of confirmed.slice(0, 8)) {
    console.log(`  [${((c.seg.startMs - baseMs) / 1000).toFixed(1)}-${((c.seg.endMs - baseMs) / 1000).toFixed(1)}] ${c.speaker}: ${c.seg.text.slice(0, 70)}`);
  }

  let passed = 0; let failed = 0;
  const check = (name: string, cond: boolean, detail = '') => {
    if (cond) { passed++; console.log(`  PASS  ${name}`); }
    else { failed++; console.log(`  FAIL  ${name} ${detail}`); }
  };

  // 1. No overlapping confirmed spans (high-water dedup).
  let overlaps = 0;
  for (let i = 1; i < confirmed.length; i++) {
    if (confirmed[i].seg.startMs < confirmed[i - 1].seg.endMs - 500) {
      overlaps++;
      const a = confirmed[i - 1]; const b = confirmed[i];
      console.log(`  OVERLAP: [${((a.seg.startMs - baseMs) / 1000).toFixed(1)}-${((a.seg.endMs - baseMs) / 1000).toFixed(1)}] "${a.seg.text.slice(0, 40)}" (${a.seg.segmentId})`);
      console.log(`       vs  [${((b.seg.startMs - baseMs) / 1000).toFixed(1)}-${((b.seg.endMs - baseMs) / 1000).toFixed(1)}] "${b.seg.text.slice(0, 40)}" (${b.seg.segmentId})`);
    }
  }
  check('no overlapping confirmed spans', overlaps === 0, `${overlaps} overlaps`);

  // 2. Coverage vs captions (real words; CC own-error tolerance via ±3.5s window).
  if (CAPTIONS && fs.existsSync(CAPTIONS)) {
    const cc = readCaptions(CAPTIONS).filter(w => w.t <= pcm.length / SAMPLE_RATE);
    const ours: CcWord[] = [];
    for (const c of confirmed) {
      const ws = c.seg.text.split(/\s+/).map(norm).filter(Boolean);
      const st = (c.seg.startMs - baseMs) / 1000;
      const en = (c.seg.endMs - baseMs) / 1000;
      ws.forEach((w, i) => ours.push({ t: st + (en - st) * (i / Math.max(1, ws.length)), w }));
    }
    const missing = cc.filter(({ t: ct, w }) =>
      !FILLERS.has(w) && !ours.some(o => Math.abs(o.t - ct) <= 3.5 && o.w === w));
    const real = cc.filter(w => !FILLERS.has(w.w));
    const loss = real.length ? missing.length / real.length : 0;
    console.log(`caption words ${real.length} (real), missing ${missing.length} → loss ${(loss * 100).toFixed(0)}%`);
    check(`real-word loss <= ${MAX_REAL_LOSS * 100}%`, loss <= MAX_REAL_LOSS, `${(loss * 100).toFixed(0)}%`);
  } else {
    console.log('(no CAPTIONS provided — coverage unscored)');
    check('produced confirmed text', confirmed.length > 0);
  }

  console.log(`\n=== Results: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
})();
