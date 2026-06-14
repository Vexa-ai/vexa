#!/usr/bin/env tsx
/**
 * diarize-fixture — run the extracted WeSpeaker/pyannote diarizer over a
 * capture.v1 fixture's MIXED channel (999) and report the speaker clustering.
 *
 *   tsx scripts/diarize-fixture.ts <fixture-dir>
 *
 * Diarization-first milestone for the mixed-pipeline brick: proves the
 * extracted diarizer (modules/pipeline/src/diarization) loads its ONNX models
 * and clusters real captured audio offline — no STT, no name binding (cluster
 * ids only; naming is the downstream speaker-attribution brick). Emits turns in
 * separated-transcript.v1 shape (text empty until STT is wired) to
 * <fixture-dir>/diarize.json.
 */
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { OnnxLocalDiarizer, type CommitEvent } from '../src/diarization/onnx-local-diarizer';

const dir = process.argv[2];
if (!dir || !existsSync(dir)) { console.error('usage: tsx scripts/diarize-fixture.ts <fixture-dir>'); process.exit(1); }

// ── load the mixed channel (999), else the largest wav ──
const audioDir = join(dir, 'audio');
const wavs = readdirSync(audioDir).filter(f => f.endsWith('.wav'));
const wavName = wavs.find(f => f.includes('999'))
  || wavs.map(f => ({ f, s: statSync(join(audioDir, f)).size })).sort((a, b) => b.s - a.s)[0]?.f;
if (!wavName) { console.error(`no .wav under ${audioDir}`); process.exit(1); }
const wavPath = join(audioDir, wavName);

/** Minimal PCM WAV → Float32 (handles 16-bit mono; reads fmt from header). */
function readWav(path: string): { pcm: Float32Array; sampleRate: number } {
  const b = readFileSync(path);
  // find 'fmt ' and 'data' chunks
  let off = 12, sampleRate = 16000, bits = 16, channels = 1, dataOff = 44, dataLen = b.length - 44;
  while (off + 8 <= b.length) {
    const id = b.toString('ascii', off, off + 4);
    const size = b.readUInt32LE(off + 4);
    if (id === 'fmt ') { channels = b.readUInt16LE(off + 10); sampleRate = b.readUInt32LE(off + 12); bits = b.readUInt16LE(off + 22); }
    else if (id === 'data') { dataOff = off + 8; dataLen = size; break; }
    off += 8 + size + (size & 1);
  }
  const n = Math.floor(dataLen / (bits / 8) / channels);
  const pcm = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const s = b.readInt16LE(dataOff + i * 2 * channels); // first channel
    pcm[i] = s / 32768;
  }
  return { pcm, sampleRate };
}

const { pcm, sampleRate } = readWav(wavPath);
const durS = pcm.length / sampleRate;
console.log(`▶ diarize ${wavPath}\n  ${pcm.length} samples @ ${sampleRate}Hz = ${durS.toFixed(1)}s`);
if (sampleRate !== 16000) console.warn(`  ⚠ expected 16kHz; got ${sampleRate} — diarizer assumes 16kHz`);

const commits: CommitEvent[] = [];
const diarizer = await OnnxLocalDiarizer.create({ onCommit: (ev) => commits.push(ev) });
console.log('  diarizer ready (models loaded). feeding audio…');

// Feed in real-capture-sized frames (4096 samples ≈ 256ms), monotonic ts.
const FRAME = 4096;
const t0 = Date.now();
for (let i = 0; i < pcm.length; i += FRAME) {
  const frame = pcm.subarray(i, Math.min(i + FRAME, pcm.length));
  const tsMs = (i / sampleRate) * 1000;
  await diarizer.process(frame, tsMs);
  if (i % (FRAME * 200) === 0 && i > 0) process.stdout.write(`\r  fed ${((i / pcm.length) * 100).toFixed(0)}%  commits=${commits.length}`);
}
process.stdout.write('\n');
console.log(`  feed done in ${((Date.now() - t0) / 1000).toFixed(1)}s — ${commits.length} commits`);

// ── build turns: contiguous same-cluster commits ──
const rewrites = diarizer.getLabelRewrites?.() || new Map<string, string>();
const resolve = (id: string) => { let r = id; while (rewrites.has(r)) r = rewrites.get(r)!; return r; };

const turns: { cluster: string; start: number; end: number; commits: number }[] = [];
for (const ev of commits) {
  const cluster = resolve(ev.speakerId);
  const last = turns[turns.length - 1];
  if (last && last.cluster === cluster && ev.tStartMs - last.end <= 2500) {
    last.end = Math.max(last.end, ev.tEndMs); last.commits++;
  } else {
    turns.push({ cluster, start: ev.tStartMs, end: ev.tEndMs, commits: 1 });
  }
}

const clusters = [...new Set(turns.map(t => t.cluster))];
const mmss = (ms: number) => `${String(Math.floor(ms / 60000)).padStart(2, '0')}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}`;
console.log(`\n──────── diarization result ────────`);
console.log(`distinct clusters: ${clusters.length}  [${clusters.join(', ')}]`);
console.log(`turns: ${turns.length}`);
for (const t of turns) console.log(`  [${t.cluster}  ${mmss(t.start)}–${mmss(t.end)}]  ${((t.end - t.start) / 1000).toFixed(1)}s  (${t.commits} commits)`);

// separated-transcript.v1 shape (text/words empty until STT wired)
const segments = turns.map(t => ({
  speakerKey: t.cluster, text: '', start: t.start / 1000, end: t.end / 1000, words: [], topology: 'mixed' as const,
}));
const outPath = join(dir, 'diarize.json');
writeFileSync(outPath, JSON.stringify({ source: wavPath, durationS: durS, clusters: clusters.length, segments }, null, 2));
console.log(`\n→ ${outPath} (${segments.length} segments, ${clusters.length} clusters)`);
diarizer.reset();
