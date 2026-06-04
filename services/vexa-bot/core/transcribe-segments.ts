/**
 * CUT-AT-SWITCH transcription — the real thing.
 * Stream the audio; on each speaker-change signal, close the current buffer and
 * start a fresh one; transcribe EACH buffer independently through the real
 * Whisper. No Whisper call ever spans a switch => no cross-speaker contamination.
 * NO diarization, NO speaker labels — just cut + transcribe per turn.
 */
import { TranscriptionClient } from './src/services/transcription-client';
import * as fs from 'fs';

const SR = 16000;
function arg(n: string, d?: string) { const i = process.argv.indexOf(`--${n}`); return i >= 0 ? process.argv[i + 1] : d; }
function readWav(path: string): Float32Array {
  const b = fs.readFileSync(path); let off = 12, ch = 1, dOff = -1, dLen = 0;
  while (off + 8 <= b.length) { const id = b.toString('ascii', off, off + 4), sz = b.readUInt32LE(off + 4);
    if (id === 'fmt ') ch = b.readUInt16LE(off + 10); else if (id === 'data') { dOff = off + 8; dLen = sz; } off += 8 + sz + (sz & 1); }
  const n = Math.floor(dLen / 2 / ch), out = new Float32Array(n);
  for (let i = 0; i < n; i++) { let a = 0; for (let c = 0; c < ch; c++) a += b.readInt16LE(dOff + (i * ch + c) * 2); out[i] = a / ch / 32768; }
  return out;
}

// Consolidate raw pyannote boundaries into switch points (one per change-region).
const NEWVOICE = new Set(['silence→speaker', 'speaker→speaker', 'overlap-onset']);
function switchPoints(bnds: any[], gap = 0.75) {
  const bs = [...bnds].sort((a, b) => a.tMs - b.tMs); const cuts: { t: number; strong: boolean }[] = []; let i = 0;
  while (i < bs.length) {
    const cl = [bs[i]]; let j = i + 1;
    while (j < bs.length && (bs[j].tMs - cl[cl.length - 1].tMs) / 1000 < gap) { cl.push(bs[j]); j++; }
    const nv = cl.filter((c) => NEWVOICE.has(c.kind));
    if (nv.length) cuts.push({ t: nv[nv.length - 1].tMs / 1000, strong: cl.some((c) => c.kind === 'speaker→speaker' || c.kind === 'overlap-onset') });
    i = j;
  }
  return cuts;
}

async function main() {
  const wav = arg('wav')!, bndPath = arg('bnd')!, out = arg('out')!;
  const tx = new TranscriptionClient({ serviceUrl: process.env.TX_URL || 'http://transcription-lb', apiToken: process.env.TX_TOKEN, maxRetries: 2 });
  const s = readWav(wav); const dur = s.length / SR;
  const cuts = switchPoints(JSON.parse(fs.readFileSync(bndPath, 'utf8')).boundaries);
  const edges = [0, ...cuts.map((c) => c.t), dur];          // buffer = audio between two switch points
  const segs: any[] = [];
  for (let k = 0; k < edges.length - 1; k++) {
    const a = edges[k], b = edges[k + 1]; if (b - a < 0.18) continue;
    const piece = s.subarray(Math.floor(a * SR), Math.floor(b * SR));   // this turn's isolated buffer
    const r = await tx.transcribe(piece, 'en');                          // Whisper on JUST this buffer
    const text = (r.text || '').trim();
    // word timestamps, offset to absolute file time (a = buffer start)
    const words: { word: string; start: number; end: number }[] = [];
    for (const seg of r.segments || []) for (const w of seg.words || [])
      words.push({ word: w.word, start: +(a + w.start).toFixed(2), end: +(a + w.end).toFixed(2) });
    const strong = k > 0 && cuts[k - 1] ? cuts[k - 1].strong : false;   // the switch that opened this buffer
    segs.push({ idx: k, start: +a.toFixed(2), end: +b.toFixed(2), durS: +(b - a).toFixed(2), startedByStrongSwitch: strong, text, words });
    console.log(`[buf ${segs.length}] ${a.toFixed(1)}-${b.toFixed(1)}s (${(b - a).toFixed(1)}s) ${strong ? 'STRONG' : 'pause'} -> "${text.slice(0, 64)}"`);
  }
  fs.writeFileSync(out, JSON.stringify({ wav, durationS: +dur.toFixed(1), nSwitches: cuts.length, segments: segs }, null, 2));
  console.log(`[segments] wrote ${out} — ${segs.length} buffers cut at ${cuts.length} switch points, each Whispered alone`);
  process.exit(0);
}
main().catch((e) => { console.error('FATAL', e); process.exit(1); });
