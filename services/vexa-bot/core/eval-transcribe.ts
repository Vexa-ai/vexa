/**
 * End-to-end diarize -> overlap-stitch -> REAL Whisper harness
 * (pack-msteams-diarization-cutover #394 RnD).
 *
 * The offline eval-diarizer stops at onCommit, so we never see whether the
 * segmentation + overlap stitching actually produces good transcripts. This
 * harness closes that loop: it runs the EXACT shipping OnnxLocalDiarizer over a
 * WAV, then routes each committed segment's audio under two policies and calls
 * the REAL transcription-service (the same TranscriptionClient -> /v1/audio/
 * transcriptions the bot uses) so we can read the result.
 *
 * Policies:
 *   baseline : every commit -> its own diarized cluster (overlap commits stay
 *              in whatever noisy cluster wespeaker gave the mixed embedding).
 *   stitch   : non-overlap commit -> its own cluster; an overlap region ->
 *              BOTH the previous non-overlap speaker (so [spk1]+[overlap]) AND
 *              the next non-overlap speaker (so [overlap]+[spk2]). This is the
 *              "attribute the overlap to both the outgoing and incoming turn"
 *              idea — validated here by actually transcribing both buffers.
 *
 * Usage (must run on the transcription network with the token):
 *   docker run --rm --network vexa-network \
 *     -e DIAR_CACHE=/hfcache -e TX_URL=http://transcription-lb \
 *     -e TX_TOKEN=... \
 *     ... npx tsx eval-transcribe.ts --wav /data/dgclip.wav --out /data/dgclip
 */
import { env as transformersEnv } from '@huggingface/transformers';
import { OnnxLocalDiarizer, CommitEvent } from './src/services/diarization/onnx-local-diarizer';
import { TranscriptionClient } from './src/services/transcription-client';
import * as fs from 'fs';

const SAMPLE_RATE = 16000;
const FRAME = 4096;

function arg(name: string, def?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : def;
}

function readWavMono16(path: string): Float32Array {
  const buf = fs.readFileSync(path);
  let off = 12, numChannels = 1, dataOff = -1, dataLen = 0, bps = 16;
  while (off + 8 <= buf.length) {
    const id = buf.toString('ascii', off, off + 4);
    const size = buf.readUInt32LE(off + 4);
    if (id === 'fmt ') { numChannels = buf.readUInt16LE(off + 10); bps = buf.readUInt16LE(off + 22); }
    else if (id === 'data') { dataOff = off + 8; dataLen = size; }
    off += 8 + size + (size & 1);
  }
  if (dataOff < 0 || bps !== 16) throw new Error('need PCM16 data chunk');
  const n = Math.floor(dataLen / 2 / numChannels);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (let c = 0; c < numChannels; c++) acc += buf.readInt16LE(dataOff + (i * numChannels + c) * 2);
    out[i] = acc / numChannels / 32768;
  }
  return out;
}

interface Commit { speakerId: string; startS: number; endS: number; isOverlap: boolean; }

/** slice audio [startS,endS) -> Float32Array (session-relative time == sample idx). */
function slice(samples: Float32Array, startS: number, endS: number): Float32Array {
  const a = Math.max(0, Math.floor(startS * SAMPLE_RATE));
  const b = Math.min(samples.length, Math.ceil(endS * SAMPLE_RATE));
  return samples.subarray(a, b);
}

function concat(parts: Float32Array[]): Float32Array {
  const n = parts.reduce((s, p) => s + p.length, 0);
  const out = new Float32Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

interface Turn { speaker: string; startS: number; endS: number; body: Float32Array; }
interface Overlap { startS: number; endS: number; audio: Float32Array; prevIdx: number; nextIdx: number; }
interface Word { word: string; start: number; end: number; prob: number; }
interface TxOut { text: string; words: Word[]; avgLogprob: number; noSpeech: number; }
type Seg = { speaker: string; start: number; end: number; text: string; stitched: boolean; conf?: number; loser?: { speaker: string; conf: number; text: string } };

const CTX_S = 2.0; // seconds of neighbor context to give Whisper around an overlap

/** Build turns (consecutive same-speaker non-overlap commits) + the list of
 *  overlap regions, each tagged with its preceding/following turn index. */
function buildTimeline(commits: Commit[], samples: Float32Array): { turns: Turn[]; overlaps: Overlap[] } {
  const MERGE_GAP = 8.0;
  const tmp: { speaker: string; startS: number; endS: number; parts: Float32Array[] }[] = [];
  for (const c of commits) {
    if (c.isOverlap) continue;
    const last = tmp[tmp.length - 1];
    if (last && last.speaker === c.speakerId && c.startS - last.endS < MERGE_GAP) {
      last.parts.push(slice(samples, c.startS, c.endS)); last.endS = c.endS;
    } else tmp.push({ speaker: c.speakerId, startS: c.startS, endS: c.endS, parts: [slice(samples, c.startS, c.endS)] });
  }
  const turns: Turn[] = tmp.map((t) => ({ speaker: t.speaker, startS: t.startS, endS: t.endS, body: concat(t.parts) }));
  const overlaps: Overlap[] = [];
  for (const c of commits) {
    if (!c.isOverlap) continue;
    let prevIdx = -1, nextIdx = -1;
    for (let i = 0; i < turns.length; i++) { if (turns[i].endS <= c.startS + 0.05) prevIdx = i; }
    for (let i = 0; i < turns.length; i++) { if (turns[i].startS >= c.endS - 0.05) { nextIdx = i; break; } }
    overlaps.push({ startS: c.startS, endS: c.endS, audio: slice(samples, c.startS, c.endS), prevIdx, nextIdx });
  }
  return { turns, overlaps };
}

function lastSeconds(buf: Float32Array, s: number): Float32Array {
  const n = Math.min(buf.length, Math.round(s * SAMPLE_RATE));
  return buf.subarray(buf.length - n);
}
function firstSeconds(buf: Float32Array, s: number): Float32Array {
  const n = Math.min(buf.length, Math.round(s * SAMPLE_RATE));
  return buf.subarray(0, n);
}
function meanProb(ws: Word[]): number { return ws.length ? ws.reduce((s, w) => s + w.prob, 0) / ws.length : -1; }

async function transcribeBuf(buf: Float32Array, tx: TranscriptionClient): Promise<TxOut | null> {
  if (buf.length < 0.2 * SAMPLE_RATE) return null;
  try {
    const r = await tx.transcribe(buf, 'en');
    const words: Word[] = [];
    let lp = 0, ns = 0, n = 0;
    for (const sg of r.segments || []) {
      if (sg.avg_logprob != null) { lp += sg.avg_logprob; n++; }
      if (sg.no_speech_prob != null) ns += sg.no_speech_prob;
      for (const w of sg.words || []) words.push({ word: w.word, start: w.start, end: w.end, prob: (w as any).probability ?? 0 });
    }
    return { text: (r.text || '').trim(), words, avgLogprob: n ? lp / n : 0, noSpeech: (r.segments?.length ? ns / r.segments.length : 0) };
  } catch (e: any) {
    if (e.statusCode === 401) throw e;
    console.error(`[transcribe] buf FAILED: ${e.statusCode || ''} ${e.message}`);
    return null;
  }
}

/** Transcribe a turn body in <=24s chunks. */
async function transcribeBody(t: Turn, tx: TranscriptionClient): Promise<Seg[]> {
  const MAX = 24 * SAMPLE_RATE; const out: Seg[] = [];
  for (let off = 0; off < t.body.length; off += MAX) {
    const piece = t.body.subarray(off, Math.min(off + MAX, t.body.length));
    const r = await transcribeBuf(piece, tx);
    if (r && r.text) out.push({ speaker: t.speaker, start: +(t.startS + off / SAMPLE_RATE).toFixed(2), end: +(t.startS + (off + piece.length) / SAMPLE_RATE).toFixed(2), text: r.text, stitched: false });
  }
  return out;
}

/** Attribute one overlap to the higher-CONFIDENCE neighbor.
 *  Transcribe [A-tail + overlap] and [overlap + B-head]; isolate the words that
 *  fall in the overlap window on each side; keep the side whose overlap words
 *  have higher mean Whisper probability (the other side hallucinated). */
async function attributeOverlap(ov: Overlap, turns: Turn[], tx: TranscriptionClient): Promise<Seg | null> {
  const ovDurS = ov.audio.length / SAMPLE_RATE;
  let a: { speaker: string; conf: number; text: string } | null = null;
  let b: { speaker: string; conf: number; text: string } | null = null;
  if (ov.prevIdx >= 0) {
    const ctx = lastSeconds(turns[ov.prevIdx].body, CTX_S);
    const r = await transcribeBuf(concat([ctx, ov.audio]), tx);
    if (r) { const ctxS = ctx.length / SAMPLE_RATE; const ws = r.words.filter((w) => w.start >= ctxS - 0.15); a = { speaker: turns[ov.prevIdx].speaker, conf: meanProb(ws), text: ws.map((w) => w.word).join(' ').trim() }; }
  }
  if (ov.nextIdx >= 0) {
    const ctx = firstSeconds(turns[ov.nextIdx].body, CTX_S);
    const r = await transcribeBuf(concat([ov.audio, ctx]), tx);
    if (r) { const ws = r.words.filter((w) => w.end <= ovDurS + 0.15); b = { speaker: turns[ov.nextIdx].speaker, conf: meanProb(ws), text: ws.map((w) => w.word).join(' ').trim() }; }
  }
  const cands = [a, b].filter((x): x is { speaker: string; conf: number; text: string } => !!x && !!x.text);
  if (cands.length === 0) return null;
  cands.sort((x, y) => y.conf - x.conf);
  const win = cands[0], lose = cands[1];
  return { speaker: win.speaker, start: +ov.startS.toFixed(2), end: +ov.endS.toFixed(2), text: win.text, stitched: true, conf: +win.conf.toFixed(3), loser: lose ? { speaker: lose.speaker, conf: +lose.conf.toFixed(3), text: lose.text } : undefined };
}

async function main() {
  const wavPath = arg('wav')!;
  const outBase = arg('out')!;
  if (process.env.DIAR_CACHE) (transformersEnv as any).cacheDir = process.env.DIAR_CACHE;
  const txUrl = process.env.TX_URL || 'http://transcription-lb';
  const txToken = process.env.TX_TOKEN;
  const tx = new TranscriptionClient({ serviceUrl: txUrl, apiToken: txToken, maxRetries: 2 });

  console.log(`[transcribe] reading ${wavPath}`);
  const samples = readWavMono16(wavPath);
  const durS = samples.length / SAMPLE_RATE;

  const userCfg = JSON.parse(arg('config', '{}')!);
  console.log(`[transcribe] diarizer config=${JSON.stringify(userCfg)}`);
  const commits: Commit[] = [];
  const diarizer = await OnnxLocalDiarizer.create({
    maxUtteranceMs: 3000,
    pyannoteInferIntervalMs: 250,
    changePointDistThreshold: 0.40,
    ...userCfg,
    onCommit: (ev: CommitEvent) =>
      commits.push({ speakerId: ev.speakerId, startS: ev.tStartMs / 1000, endS: ev.tEndMs / 1000, isOverlap: !!ev.isOverlap }),
  });
  console.log(`[transcribe] diarizer ready; feeding ${durS.toFixed(0)}s ...`);
  for (let i = 0; i < samples.length; i += FRAME) {
    await diarizer.process(samples.subarray(i, Math.min(i + FRAME, samples.length)), (i / SAMPLE_RATE) * 1000);
  }
  const overlapN = commits.filter((c) => c.isOverlap).length;
  console.log(`[transcribe] ${commits.length} commits, ${overlapN} overlap; clusters=${new Set(commits.map((c) => c.speakerId)).size}`);

  const { turns, overlaps } = buildTimeline(commits, samples);
  console.log(`[transcribe] timeline: ${turns.length} turns, ${overlaps.length} overlap regions`);

  // ---- bodies (shared by both policies) ----
  const bodySegs: Seg[] = [];
  for (const t of turns) bodySegs.push(...await transcribeBody(t, tx));

  // ---- baseline: overlaps stay standalone in their own diarized cluster ----
  const baseline: Seg[] = [...bodySegs];
  for (const c of commits.filter((x) => x.isOverlap)) {
    const r = await transcribeBuf(slice(samples, c.startS, c.endS), tx);
    if (r && r.text) baseline.push({ speaker: c.speakerId, start: +c.startS.toFixed(2), end: +c.endS.toFixed(2), text: r.text, stitched: false });
  }
  baseline.sort((a, b) => a.start - b.start);
  fs.writeFileSync(`${outBase}.baseline.json`, JSON.stringify({ wav: wavPath, durationS: durS, policy: 'baseline', clusters: [...new Set(baseline.map((s) => s.speaker))], transcript: baseline }, null, 2));

  // ---- stitch: each overlap attributed to the higher-CONFIDENCE neighbor ----
  const stitch: Seg[] = [...bodySegs];
  console.log(`[transcribe] === attributing ${overlaps.length} overlaps by Whisper confidence ===`);
  for (const ov of overlaps) {
    const seg = await attributeOverlap(ov, turns, tx);
    if (seg) {
      stitch.push(seg);
      const lo = seg.loser ? `  (dropped ${seg.loser.speaker} conf=${seg.loser.conf} "${seg.loser.text}")` : '';
      console.log(`[overlap] ${seg.start}s -> ${seg.speaker} conf=${seg.conf} "${seg.text}"${lo}`);
    }
  }
  stitch.sort((a, b) => a.start - b.start);
  fs.writeFileSync(`${outBase}.stitch.json`, JSON.stringify({ wav: wavPath, durationS: durS, policy: 'stitch', clusters: [...new Set(stitch.map((s) => s.speaker))], transcript: stitch }, null, 2));
  console.log(`[transcribe] wrote ${outBase}.{baseline,stitch}.json  (stitched=${stitch.filter((s) => s.stitched).length})`);
  process.exit(0);
}

main().catch((e) => { console.error('[transcribe] FATAL', e); process.exit(1); });
