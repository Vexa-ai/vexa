/**
 * Whole-file word-level transcript via the REAL transcription-lb (vexa-bot's
 * Whisper). NO diarization — just words + timestamps. Chunks the wav into
 * windows, offsets word times, dumps a flat words[] for the switch-mark viewer.
 */
import { TranscriptionClient } from './src/services/transcription-client';
import * as fs from 'fs';

const SR = 16000, CHUNK_S = 28;
function arg(n: string, d?: string) { const i = process.argv.indexOf(`--${n}`); return i >= 0 ? process.argv[i + 1] : d; }

function readWav(path: string): Float32Array {
  const b = fs.readFileSync(path); let off = 12, ch = 1, dOff = -1, dLen = 0, bps = 16;
  while (off + 8 <= b.length) { const id = b.toString('ascii', off, off + 4), sz = b.readUInt32LE(off + 4);
    if (id === 'fmt ') { ch = b.readUInt16LE(off + 10); bps = b.readUInt16LE(off + 22); }
    else if (id === 'data') { dOff = off + 8; dLen = sz; } off += 8 + sz + (sz & 1); }
  const n = Math.floor(dLen / 2 / ch), out = new Float32Array(n);
  for (let i = 0; i < n; i++) { let a = 0; for (let c = 0; c < ch; c++) a += b.readInt16LE(dOff + (i * ch + c) * 2); out[i] = a / ch / 32768; }
  return out;
}

async function main() {
  const wav = arg('wav')!, out = arg('out')!;
  const tx = new TranscriptionClient({ serviceUrl: process.env.TX_URL || 'http://transcription-lb', apiToken: process.env.TX_TOKEN, maxRetries: 2 });
  const s = readWav(wav); const dur = s.length / SR;
  const words: { word: string; start: number; end: number; prob: number }[] = [];
  for (let off = 0; off < s.length; off += CHUNK_S * SR) {
    const piece = s.subarray(off, Math.min(off + CHUNK_S * SR, s.length));
    const t0 = off / SR;
    const r = await tx.transcribe(piece, 'en');
    for (const seg of r.segments || []) for (const w of seg.words || [])
      words.push({ word: w.word, start: +(t0 + w.start).toFixed(2), end: +(t0 + w.end).toFixed(2), prob: +((w as any).probability ?? 0).toFixed(2) });
    console.log(`[words] ${(t0).toFixed(0)}-${(t0 + piece.length / SR).toFixed(0)}s -> ${words.length} words total`);
  }
  fs.writeFileSync(out, JSON.stringify({ wav, durationS: +dur.toFixed(1), words }, null, 2));
  console.log(`[words] wrote ${out} (${words.length} words)`);
  process.exit(0);
}
main().catch((e) => { console.error('FATAL', e); process.exit(1); });
