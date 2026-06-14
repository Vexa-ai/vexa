#!/usr/bin/env tsx
/**
 * bench-mixed — Lane 1 "bench:mixed" mixed-pipeline benchmark BRICK.
 *
 *   spec (URL+range)  ──yt-dlp+ffmpeg──►  16kHz mono wav (fixture cache)
 *                                          │
 *                                          ├─► StreamCaptureWriter → stream.capture (ch 999)
 *                                          │      └─► createMixedPipeline (gate+diarizer+Whisper)
 *                                          │             └─► ours.separated-transcript.v1.jsonl
 *                                          │
 *                                          └─► Deepgram (nova-2, diarize) → deepgram.ref.json (cached golden)
 *
 * FAITHFUL: feeds the pipeline at real-time 1× (the ChunkedTranscriber confirms
 * on wall-clock timers, so firehosing drops most of the transcript). Full meeting
 * by default (spec start_s/duration_s = 0). A run takes ~the meeting's length.
 *
 * Selects the 2-min window of interest (most speakers/switches/turns), plays only
 * that into the pipeline, and writes the artifacts a HUMAN judges in `bench:view`:
 * ours.separated-transcript.v1.jsonl + reference.jsonl (word-clipped to the window).
 * Mechanical numbers (src/bench/score.ts) are supporting signals only.
 *
 *   tsx --env-file-if-exists=.env scripts/bench-mixed.ts [spec.json]   # BENCH_SPEED=1 (faithful)
 *   then: npm run bench:view   → http://localhost:8077  (side-by-side + playback)
 *
 * Isolation: a script — imports the brick's own src/, the contracts, and
 * @vexa/recorder (relative, like mixed-replay reaches contracts). NOT services/.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { execFileSync, spawnSync } from 'node:child_process';
import { createMixedPipeline, TranscriptionClient } from '../src/index';
import { decodeAudioFrame } from '../../../contracts/capture/v1/schema';
import { StreamCaptureWriter } from '../../recorder/src/stream-capture';
import { score, type ScoredSegment, type Scorecard } from '../src/bench/score';

// ── threshold constants (the optimization-setup hook; not gated this pass) ──
const SEG_F1_MIN = 0.50;   // headline: boundary F1 @±500ms we want to beat
const WER_MAX = 0.40;      // transcription budget vs Deepgram reference
const IOU_MIN = 0.40;      // mean segment IoU floor

const SAMPLE_RATE = 16000;
const MIXED_CHANNEL = 999;
const FRAME_SECONDS = 0.5;          // ~0.5s of samples per capture frame
const FRAME_SAMPLES = SAMPLE_RATE * FRAME_SECONDS;

const GATE = process.env.BENCH_GATE === '1';

interface BenchSpec {
  name: string;
  youtube_url: string;
  start_s: number;
  duration_s: number;
  language: string;
}

const FFMPEG = fs.existsSync(path.join(os.homedir(), 'bin', 'ffmpeg'))
  ? path.join(os.homedir(), 'bin', 'ffmpeg')
  : 'ffmpeg';

function fixtureCacheRoot(): string {
  return process.env.VEXA_FIXTURE_CACHE || path.join(os.homedir(), '.vexa', 'fixtures');
}

function log(msg: string) { console.log(msg); }

// ───────────────────────── 1. load spec ─────────────────────────
function loadSpec(): BenchSpec {
  const specPath = process.argv[2] || path.join(__dirname, '..', 'bench', 'specs', 'podcast-520.json');
  if (!fs.existsSync(specPath)) { console.error(`[bench] spec not found: ${specPath}`); process.exit(1); }
  const spec = JSON.parse(fs.readFileSync(specPath, 'utf8')) as BenchSpec;
  log(`[bench] spec: ${spec.name}  ${spec.youtube_url}  [${spec.start_s}s +${spec.duration_s}s]  lang=${spec.language}`);
  return spec;
}

// ───────────────────── 2. fetch + prep audio (cached) ─────────────────────
function which(cmd: string): boolean {
  return spawnSync('sh', ['-c', `command -v ${cmd}`], { encoding: 'utf8' }).status === 0;
}

function prepAudio(spec: BenchSpec, benchDir: string): string {
  const wavPath = path.join(benchDir, 'audio.wav');
  if (fs.existsSync(wavPath) && fs.statSync(wavPath).size > 1000) {
    log(`[bench] audio cached: ${wavPath}`);
    return wavPath;
  }
  if (!which('yt-dlp')) { console.error('[bench] yt-dlp not found in PATH — cannot fetch audio.'); process.exit(2); }

  const srcPath = path.join(benchDir, 'source.media');
  if (!fs.existsSync(srcPath)) {
    // Format fallback: prefer audio-only, but YouTube's SABR/n-challenge can strip
    // audio-only formats on older yt-dlp — fall back to a combined format (we extract
    // audio with ffmpeg downstream anyway).
    const formatSelectors = ['bestaudio/best', 'best'];
    let ok = false;
    for (const fmt of formatSelectors) {
      log(`[bench] yt-dlp ▶ downloading (-f ${fmt})…`);
      const r = spawnSync('yt-dlp', ['-f', fmt, '-o', srcPath, '--no-playlist', '--force-overwrites', '--no-update', spec.youtube_url], { stdio: 'inherit' });
      if (r.status === 0 && fs.existsSync(srcPath) && fs.statSync(srcPath).size > 1000) { ok = true; break; }
      log(`[bench] yt-dlp -f ${fmt} did not yield audio; trying next selector…`);
    }
    if (!ok) {
      console.error(`[bench] yt-dlp FAILED for all format selectors. The video may be unavailable, age-gated, SABR-only, or require cookies/a newer yt-dlp. Pass --cookies-from-browser to yt-dlp manually and place a media file at ${srcPath}, then re-run.`);
      process.exit(2);
    }
  } else {
    log(`[bench] source cached: ${srcPath}`);
  }

  // start_s<=0 → from the top; duration_s<=0 → the FULL meeting (no -t). A
  // faithful quality benchmark needs the whole meeting, not a slice.
  const cut: string[] = ['-y'];
  if (spec.start_s > 0) cut.push('-ss', String(spec.start_s));
  if (spec.duration_s > 0) cut.push('-t', String(spec.duration_s));
  log(`[bench] ffmpeg ▶ ${spec.duration_s > 0 ? `cut [${spec.start_s},${spec.start_s + spec.duration_s}]` : `FULL meeting from ${spec.start_s}s`} → 16kHz mono wav`);
  const r2 = spawnSync(FFMPEG, [
    ...cut,
    '-i', srcPath, '-ac', '1', '-ar', String(SAMPLE_RATE), '-c:a', 'pcm_s16le', wavPath,
  ], { stdio: 'inherit' });
  if (r2.status !== 0 || !fs.existsSync(wavPath)) { console.error('[bench] ffmpeg FAILED.'); process.exit(2); }
  log(`[bench] audio ready: ${wavPath} (${(fs.statSync(wavPath).size / 1024).toFixed(0)} KiB)`);
  return wavPath;
}

// ── minimal WAV (s16le PCM, mono) reader → Float32Array + raw bytes ──
function readWavFloat32(wavPath: string): { samples: Float32Array; sampleRate: number; bytes: Buffer } {
  const bytes = fs.readFileSync(wavPath);
  // find 'fmt ' and 'data' chunks (skip arbitrary leading chunks)
  if (bytes.toString('ascii', 0, 4) !== 'RIFF' || bytes.toString('ascii', 8, 12) !== 'WAVE') {
    throw new Error('not a RIFF/WAVE file');
  }
  let off = 12, sampleRate = SAMPLE_RATE, bitsPerSample = 16, channels = 1, dataOff = -1, dataLen = 0;
  while (off + 8 <= bytes.length) {
    const id = bytes.toString('ascii', off, off + 4);
    const size = bytes.readUInt32LE(off + 4);
    const body = off + 8;
    if (id === 'fmt ') {
      channels = bytes.readUInt16LE(body + 2);
      sampleRate = bytes.readUInt32LE(body + 4);
      bitsPerSample = bytes.readUInt16LE(body + 14);
    } else if (id === 'data') {
      dataOff = body; dataLen = size; break;
    }
    off = body + size + (size & 1);
  }
  if (dataOff < 0) throw new Error('no data chunk');
  if (bitsPerSample !== 16) throw new Error(`expected 16-bit PCM, got ${bitsPerSample}`);
  const n = Math.floor(dataLen / 2 / channels);
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    // mono assumed (channels=1); if >1, take channel 0
    const s = bytes.readInt16LE(dataOff + i * 2 * channels);
    samples[i] = s / 32768;
  }
  return { samples, sampleRate, bytes };
}

interface Window { start: number; end: number; why: string }

/**
 * Pick the 2-min window MOST INTERESTING for pipeline assessment from the full
 * Deepgram reference — the span that stresses segmentation + diarization hardest:
 * many distinct speakers, many speaker switches, dense turns, mostly speech (not
 * silence). That's where the pipeline's failures actually show.
 */
function selectWindow(ref: ScoredSegment[], audioSeconds: number, winSec: number): Window {
  if (audioSeconds <= winSec) return { start: 0, end: audioSeconds, why: 'audio shorter than window' };
  const step = 5;
  let best = { start: 0, score: -1, speakers: 0, switches: 0, turns: 0 };
  for (let s = 0; s + winSec <= audioSeconds; s += step) {
    const e = s + winSec;
    const inWin = ref.filter((u) => u.end > s && u.start < e).sort((a, b) => a.start - b.start);
    if (!inWin.length) continue;
    const speakers = new Set(inWin.map((u) => u.speaker)).size;
    let switches = 0;
    for (let i = 1; i < inWin.length; i++) if (inWin[i].speaker !== inWin[i - 1].speaker) switches++;
    const coverage = inWin.reduce((a, u) => a + (Math.min(e, u.end) - Math.max(s, u.start)), 0) / winSec;
    if (coverage < 0.5) continue; // skip mostly-silent windows
    const sc = speakers * 3 + switches * 2 + inWin.length; // diarization+segmentation stress
    if (sc > best.score) best = { start: s, score: sc, speakers, switches, turns: inWin.length };
  }
  return {
    start: best.start, end: best.start + winSec,
    why: `${best.speakers} speakers · ${best.switches} switches · ${best.turns} turns (score ${best.score})`,
  };
}

// ───────── 3. write stream.capture fixture (channel 999) ─────────
async function writeFixture(spec: BenchSpec, benchDir: string, samples: Float32Array, win: Window) {
  const writer = new StreamCaptureWriter(benchDir, {
    platform: 'bench',
    nativeMeetingId: spec.name,
    topology: 'mixed',
    sampleRate: SAMPLE_RATE,
    language: spec.language,
  });
  let frames = 0;
  for (let i = 0; i < samples.length; i += FRAME_SAMPLES) {
    const chunk = samples.subarray(i, Math.min(i + FRAME_SAMPLES, samples.length));
    const tsMs = (i / SAMPLE_RATE) * 1000; // window-relative, ms from 0
    writer.audio(MIXED_CHANNEL, tsMs, new Float32Array(chunk));
    frames++;
  }
  await writer.finalize();
  // augment meta.json with source provenance + the assessed window (snake_case)
  const metaPath = path.join(benchDir, 'meta.json');
  const meta = JSON.parse(fs.readFileSync(metaPath, 'utf8'));
  meta.capture = 'capture.v1/stream';
  meta.source = { youtube_url: spec.youtube_url };
  meta.window = { start_s: win.start, end_s: win.end, why: win.why };
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
  log(`[bench] fixture (window): ${benchDir}/stream.capture  (${frames} frames, ch ${MIXED_CHANNEL})`);
}

// ───────────── 4. run OUR pipeline over the fixture ─────────────
interface RunInfo { speed: number; drainMs: number; audioSeconds: number; wallSeconds: number; faithful: boolean }

async function runOurs(spec: BenchSpec, benchDir: string): Promise<{ segs: ScoredSegment[]; run: RunInfo }> {
  const TX_URL = process.env.TRANSCRIPTION_SERVICE_URL || '';
  const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
  if (!TX_URL) { console.error('[bench] TRANSCRIPTION_SERVICE_URL unset — cannot run our pipeline (check modules/pipeline/.env).'); process.exit(3); }
  const txClient = new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 });

  const outPath = path.join(benchDir, 'ours.separated-transcript.v1.jsonl');
  const out = fs.createWriteStream(outPath);
  const segs: ScoredSegment[] = [];
  const t0 = Date.now();

  const pipeline = await createMixedPipeline({
    language: spec.language && spec.language !== 'auto' ? spec.language : undefined,
    transcribe: async (pcm, prompt) => txClient.transcribe(pcm, undefined, prompt),
    sink: {
      segment: (s) => {
        out.write(JSON.stringify(s) + '\n');
        segs.push({ speaker: s.speakerKey, text: s.text, start: s.start, end: s.end });
      },
      finalize: () => new Promise<void>((r) => out.end(() => r())),
    },
    log: (m) => log(`  \x1b[2m${m}\x1b[0m`),
  });

  // FAITHFUL real-time feed. The ChunkedTranscriber closes turns and confirms
  // pending→committed text on WALL-CLOCK timers (idleTimer/submitInterval), so
  // feeding must be paced to real audio-time. Firehosing the whole meeting then
  // disposing collapses the confirmation cadence and drops most of the
  // transcript — measuring an artifact, not the pipeline. SPEED=1 is the ONLY
  // behavior-faithful rate (the timers are fixed wall-clock intervals; >1 makes
  // each window cover more audio than production, so it's a smoke at best).
  const SPEED = Number(process.env.BENCH_SPEED || 1) || 1;
  if (SPEED !== 1) log(`[bench] ⚠ BENCH_SPEED=${SPEED} — NOT behavior-faithful (wall-clock timers); use 1 for real metrics.`);
  const buf = fs.readFileSync(path.join(benchDir, 'stream.capture'));
  let off = 0, fed = 0, samplesFed = 0;
  const wallStart = Date.now();
  while (off + 5 <= buf.length) {
    const type = buf.readUInt8(off); const len = buf.readUInt32LE(off + 1); off += 5;
    const payload = buf.subarray(off, off + len); off += len;
    if (type !== 0) continue;
    const f = decodeAudioFrame(payload.buffer, payload.byteOffset, payload.byteLength);
    if (!f || f.speakerIndex !== MIXED_CHANNEL) continue;
    pipeline.feedAudio(f.samples, f.ts);
    fed++; samplesFed += f.samples.length;
    // pace: wall-clock tracks audio-time / SPEED (the live cadence the timers expect)
    const sleepMs = (wallStart + (samplesFed / SAMPLE_RATE) * 1000 / SPEED) - Date.now();
    if (sleepMs > 4) await new Promise((r) => setTimeout(r, sleepMs));
    if (fed % 200 === 0) log(`[bench] ⏱ fed ${(samplesFed / SAMPLE_RATE).toFixed(0)}s audio · ${segs.length} segments so far`);
  }
  // Let the wall-clock idle timer close the final turn before dispose — prod
  // confirms trailing pending after a silence gap; immediate dispose drops it.
  const DRAIN_MS = Number(process.env.BENCH_DRAIN_MS || 8000);
  log(`[bench] fed ${fed} frames (${(samplesFed / SAMPLE_RATE).toFixed(0)}s real-time) — draining ${DRAIN_MS}ms for trailing confirmation…`);
  await new Promise((r) => setTimeout(r, DRAIN_MS));
  await pipeline.dispose();
  const dur = (Date.now() - t0) / 1000;
  log(`[bench] ours: ${segs.length} segments in ${dur.toFixed(1)}s wall (speed=${SPEED}×) → ${outPath}`);
  return { segs, run: { speed: SPEED, drainMs: DRAIN_MS, audioSeconds: samplesFed / SAMPLE_RATE, wallSeconds: dur, faithful: SPEED === 1 } };
}

// ───────────── 5. Deepgram reference (cached golden) ─────────────
async function deepgramRef(spec: BenchSpec, benchDir: string, wavBytes: Buffer): Promise<ScoredSegment[]> {
  const refPath = path.join(benchDir, 'deepgram.ref.json');
  let raw: any;
  if (fs.existsSync(refPath) && fs.statSync(refPath).size > 100) {
    log(`[bench] deepgram ref cached: ${refPath}`);
    raw = JSON.parse(fs.readFileSync(refPath, 'utf8'));
  } else {
    const key = process.env.DEEPGRAM_API_KEY || '';
    if (!key) { console.error('[bench] DEEPGRAM_API_KEY unset and no cached deepgram.ref.json — cannot build reference.'); process.exit(4); }
    const url = `https://api.deepgram.com/v1/listen?model=nova-2&diarize=true&utterances=true&punctuate=true&smart_format=true&language=${encodeURIComponent(spec.language)}`;
    log(`[bench] deepgram ▶ nova-2 diarize (POST ${(wavBytes.length / 1024).toFixed(0)} KiB wav)…`);
    const resp = await fetch(url, {
      method: 'POST',
      headers: { Authorization: `Token ${key}`, 'Content-Type': 'audio/wav' },
      body: new Uint8Array(wavBytes),
    });
    if (!resp.ok) {
      const body = await resp.text().catch(() => '');
      console.error(`[bench] deepgram FAILED ${resp.status}: ${body.slice(0, 300)}`);
      process.exit(4);
    }
    raw = await resp.json();
    fs.writeFileSync(refPath, JSON.stringify(raw, null, 2));
    log(`[bench] deepgram ref → ${refPath} (cached golden)`);
  }
  const utterances: any[] = raw?.results?.utterances || [];
  const segs: ScoredSegment[] = utterances.map((u) => ({
    speaker: `dg-${u.speaker ?? 0}`,
    text: String(u.transcript || ''),
    start: Number(u.start || 0),
    end: Number(u.end || 0),
  }));
  log(`[bench] deepgram: ${segs.length} utterances, ${new Set(segs.map((s) => s.speaker)).size} speakers`);
  return segs;
}

/**
 * Build the window reference from the cached Deepgram response, CLIPPING each
 * utterance to the window by its word-level timestamps. A turn straddling the
 * window edge keeps only its in-window words (correct text + tight bounds);
 * utterances entirely outside the window drop out. window-relative seconds.
 */
function windowReference(benchDir: string, win: Window, winSec: number): ScoredSegment[] {
  const raw = JSON.parse(fs.readFileSync(path.join(benchDir, 'deepgram.ref.json'), 'utf8'));
  const ut: any[] = raw?.results?.utterances || [];
  const out: ScoredSegment[] = [];
  for (const u of ut) {
    if (!(u.end > win.start && u.start < win.start + winSec)) continue;
    const words = (u.words || []).filter((w: any) => w.end > win.start && w.start < win.start + winSec);
    if (!words.length) continue;
    const text = words.map((w: any) => w.punctuated_word || w.word).join(' ').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    out.push({
      speaker: `dg-${u.speaker ?? 0}`,
      text,
      start: Math.max(0, words[0].start - win.start),
      end: Math.min(winSec, words[words.length - 1].end - win.start),
    });
  }
  return out;
}

// ───────────── 6/7. scorecard ─────────────
function passLabel(ok: boolean): string {
  if (GATE) return ok ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m';
  return ok ? '\x1b[32m✓ PASS\x1b[0m \x1b[2m(report-only)\x1b[0m' : '\x1b[33m✗ MISS\x1b[0m \x1b[2m(report-only)\x1b[0m';
}

function printScorecard(spec: BenchSpec, sc: Scorecard, benchDir: string, meta: any, run: RunInfo) {
  const f1pass = sc.segmentation500.boundaryF1 >= SEG_F1_MIN;
  const werpass = sc.transcription.wer <= WER_MAX;
  const ioupass = sc.meanIoU >= IOU_MIN;

  const L = (s: string) => console.log(s);
  L('');
  L('════════════════════════════════════════════════════════════════');
  L(`  bench:mixed scorecard — ${spec.name}   mode=${GATE ? 'GATE' : 'report'}`);
  L('════════════════════════════════════════════════════════════════');
  L(`  segments:   ours=${sc.oursSegments}   deepgram=${sc.refSegments}`);
  L('');
  L('  ① SEGMENTATION (primary) — boundary P / R / F1');
  for (const m of [sc.segmentation200, sc.segmentation500]) {
    L(`     ±${m.toleranceMs}ms:  P=${m.boundaryPrecision.toFixed(3)}  R=${m.boundaryRecall.toFixed(3)}  F1=${m.boundaryF1.toFixed(3)}   (matched ${m.matchedBoundaries}/${m.refBoundaries} ref, ${m.oursBoundaries} ours)`);
  }
  L(`     mean IoU (greedy time-match): ${sc.meanIoU.toFixed(3)}`);
  L(`     headline F1@±500ms ≥ ${SEG_F1_MIN}:  ${passLabel(f1pass)}`);
  L(`     mean IoU ≥ ${IOU_MIN}:              ${passLabel(ioupass)}`);
  L('');
  L('  ② TRANSCRIPTION — WER (ours vs deepgram, normalized)');
  L(`     WER=${sc.transcription.wer.toFixed(3)}   (edits=${sc.transcription.editDistance}, ref=${sc.transcription.refWords}w, ours=${sc.transcription.oursWords}w)`);
  L(`     WER ≤ ${WER_MAX}:                   ${passLabel(werpass)}`);
  L('');
  L('  ③ CLUSTER COUNT (informational, not gated)');
  L(`     ours clusters=${sc.clusters.oursClusters}   deepgram speakers=${sc.clusters.refSpeakers}   delta=${sc.clusters.delta >= 0 ? '+' : ''}${sc.clusters.delta}`);
  L('════════════════════════════════════════════════════════════════');

  const scorecard = {
    spec,
    mode: GATE ? 'gate' : 'report',
    thresholds: { SEG_F1_MIN, WER_MAX, IOU_MIN },
    metrics: sc,
    pass: { segF1: f1pass, wer: werpass, iou: ioupass },
    run: {
      // faithfulness — the judge skill's Step-0 precheck reads these
      speed: run.speed,
      faithful: run.faithful,
      drainMs: run.drainMs,
      audioSeconds: run.audioSeconds,
      wallSeconds: run.wallSeconds,
      sampleRate: SAMPLE_RATE,
      mixedChannel: MIXED_CHANNEL,
      frameSeconds: FRAME_SECONDS,
      oursSegments: sc.oursSegments,
      refSegments: sc.refSegments,
      meta,
    },
  };
  const scPath = path.join(benchDir, 'scorecard.json');
  fs.writeFileSync(scPath, JSON.stringify(scorecard, null, 2));
  L(`  scorecard → ${scPath}`);
  L('');

  if (GATE && !(f1pass && werpass && ioupass)) {
    console.error('[bench] GATE failed — one or more thresholds missed.');
    process.exit(5);
  }
}

// ───────────────────────── main ─────────────────────────
(async () => {
  const spec = loadSpec();
  const benchDir = path.join(fixtureCacheRoot(), 'bench', spec.name);
  fs.mkdirSync(benchDir, { recursive: true });
  log(`[bench] benchdir: ${benchDir}`);

  const wavPath = prepAudio(spec, benchDir);          // FULL meeting wav
  const { samples, bytes } = readWavFloat32(wavPath);
  const audioSeconds = samples.length / SAMPLE_RATE;
  log(`[bench] audio: ${samples.length} samples (${(audioSeconds / 60).toFixed(1)} min @ ${SAMPLE_RATE}Hz)`);

  // 1. Deepgram transcribe+diarize the FULL conversation (one batch call, cached).
  const fullRef = await deepgramRef(spec, benchDir, bytes);

  // 2. Find the 2-min place of interest for pipeline assessment.
  const winSec = Number(process.env.BENCH_WINDOW_SECONDS || 120);
  const win = selectWindow(fullRef, audioSeconds, winSec);
  log(`[bench] ▶ window of interest: [${win.start}–${win.end}s] (${(win.start / 60).toFixed(1)}–${(win.end / 60).toFixed(1)} min) — ${win.why}`);

  // 3. Play ONLY that window into the pipeline, faithful real-time (~2 min).
  const winSamples = new Float32Array(samples.subarray(Math.floor(win.start * SAMPLE_RATE), Math.floor(win.end * SAMPLE_RATE)));
  await writeFixture(spec, benchDir, winSamples, win);
  const meta = JSON.parse(fs.readFileSync(path.join(benchDir, 'meta.json'), 'utf8'));
  const { segs: ours, run } = await runOurs(spec, benchDir);

  // 4. Reference = Deepgram utterances CLIPPED to the window by their words, then
  //    rebased to window-relative. Clipping by words (not whole utterances) is
  //    essential: a turn straddling the window edge would otherwise drag in
  //    pre/post-window speech that isn't in window.wav.
  const refWin = windowReference(benchDir, win, winSec);
  fs.writeFileSync(path.join(benchDir, 'reference.jsonl'), refWin.map((r) => JSON.stringify(r)).join('\n') + '\n');
  log(`[bench] reference (window, word-clipped): ${refWin.length} utterances → ${benchDir}/reference.jsonl`);

  // 5. Supporting mechanical numbers + scorecard (window-scoped).
  const sc = score(ours, refWin);
  printScorecard(spec, sc, benchDir, meta, run);

  if (!run.faithful) {
    log(`[bench] ⚠ NON-FAITHFUL run (speed=${run.speed}≠1) — judge nothing. Re-run with BENCH_SPEED=1.`);
  } else {
    log(`[bench] ✓ faithful real-time window run. NOW LOOK:`);
    log(`[bench]   npm run bench:view   → http://localhost:8077  (side-by-side + synced playback — you judge)`);
  }
  process.exit(0);
})().catch((e) => { console.error('[bench] fatal:', e?.stack || e?.message || e); process.exit(1); });
