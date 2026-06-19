#!/usr/bin/env node
// benchmark — the LOSS oracle ("post probe"). The live pipeline transcribes under
// real-time pressure (streaming, segmentation, confirm gating) and can DROP turns
// entirely — and the final transcript can't reveal what's missing (you can't see a
// hole from the confirmed segments alone). So: re-transcribe the tape's FULL audio
// OFFLINE in one pass (no streaming, no segmentation pressure) against the SAME STT
// the pipeline uses, then diff that reference against what the pipeline CONFIRMED.
// Content the full pass heard but the live transcript lacks = LOST.
//
//   node benchmark.mjs <tape.jsonl> [platform] [native]
//   needs STT: TRANSCRIPTION_SERVICE_URL + TRANSCRIPTION_SERVICE_TOKEN (same as the desktop)
//   GATEWAY=http://localhost:8056   where the live transcript is read
//   LANG=                           force a language (default: let whisper detect)
//   CHUNK_S=24                       offline window length sent to STT per call
//   MATCH_WINDOW_S=12                a ref word counts as "kept" if it appears in live ±this
//   LOST_RECALL=0.4  MIN_LOST_WORDS=5   a ref span is LOST if <RECALL of its (content) words
//                                       survive AND it has ≥MIN_LOST_WORDS content words
import fs from 'node:fs';
import readline from 'node:readline';

const TAPE = process.argv[2];
if (!TAPE) { console.error('usage: benchmark.mjs <tape.jsonl> [platform] [native]'); process.exit(1); }
const GATEWAY = (process.env.GATEWAY || 'http://localhost:8056').replace(/\/+$/, '');
let TX = (process.env.TRANSCRIPTION_SERVICE_URL || '').replace(/\/+$/, '');
const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
if (!TX) { console.error('[benchmark] set TRANSCRIPTION_SERVICE_URL (+_TOKEN) — the same STT the desktop uses'); process.exit(1); }
if (!TX.endsWith('/v1/audio/transcriptions')) TX += '/v1/audio/transcriptions';
const LANG = process.env.LANG || undefined;
const RATE = 16000;
const CHUNK_S = Number(process.env.CHUNK_S || 24);
const MATCH_W = Number(process.env.MATCH_WINDOW_S || 12);
const LOST_RECALL = Number(process.env.LOST_RECALL || 0.4);
const MIN_LOST_WORDS = Number(process.env.MIN_LOST_WORDS || 5);

// ── tape audio decode (mirrors @vexa/capture-codec decodeAudioFrame) ──
//   int32 speakerIndex@0 (high bit = a name follows) · float64 ts@4 · then Float32 PCM.
//   999 = the mixed remote channel (everyone you'd transcribe); 1000 = local mic.
function decodeFrame(buf) {
  if (buf.length < 12) return null;
  const raw = buf.readInt32LE(0);
  const ts = buf.readDoubleLE(4);
  const named = raw < 0;
  const speakerIndex = named ? (raw & 0x7fffffff) : raw;
  let pcmStart = 12;
  if (named) { const nameLen = buf.readInt32LE(12); pcmStart = 16 + ((nameLen + 3) & ~3); }
  const n = Math.max(0, (buf.length - pcmStart) >> 2);
  const samples = new Float32Array(n);
  for (let i = 0; i < n; i++) samples[i] = buf.readFloatLE(pcmStart + i * 4);
  return { speakerIndex, ts, samples };
}

function float32ToWav(samples, rate = RATE) {
  const buf = Buffer.alloc(44 + samples.length * 2);
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + samples.length * 2, 4); buf.write('WAVE', 8);
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36); buf.writeUInt32LE(samples.length * 2, 40);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    buf.writeInt16LE(Math.round(s * 32767), 44 + i * 2);
  }
  return buf;
}

// faster-whisper junk filter — same thresholds as the pipeline's stt.v1 egress
// (@vexa/whisper isLowConfidenceSegment), so the reference doesn't count hallucinations.
const lowConf = (s) =>
  (s.no_speech_prob > 0.6 && s.avg_logprob < -1.0) ||
  (s.compression_ratio > 2.4) ||
  (s.avg_logprob < -1.3);

async function sttChunk(samples, language) {
  const wav = float32ToWav(samples);
  const boundary = `----benchFB${Math.floor(samples.length).toString(36)}`;
  const field = (n, v) => Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${n}"\r\n\r\n${v}\r\n`);
  const parts = [
    Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n`),
    wav, Buffer.from('\r\n'),
    field('model', 'whisper-1'), field('response_format', 'verbose_json'),
    field('timestamp_granularities', 'word'),
  ];
  if (language) parts.push(field('language', language));
  parts.push(Buffer.from(`--${boundary}--\r\n`));
  const body = Buffer.concat(parts);
  const headers = { 'Content-Type': `multipart/form-data; boundary=${boundary}` };
  if (TX_TOKEN) headers.Authorization = `Bearer ${TX_TOKEN}`;
  const r = await fetch(TX, { method: 'POST', headers, body });
  if (!r.ok) throw new Error(`STT ${r.status}: ${(await r.text().catch(() => '')).slice(0, 120)}`);
  const d = await r.json();
  return (d.segments || []).filter((s) => !lowConf(s));
}

const STOP = new Set(('the a an and or but if so of to in on at for is are was were be been do does did i you he she ' +
  'we they it this that these those my your our so just like yeah ok okay um uh ' +
  'и в на с по а но не что это как мы вы он она они я бы же то да нет вот так у о от за из').split(/\s+/));
const words = (t) => (t || '').toLowerCase().replace(/[^\p{L}\p{N}\s']/gu, ' ').split(/\s+/).filter(Boolean);
const content = (t) => words(t).filter((w) => w.length > 2 && !STOP.has(w));

async function main() {
  // 1) decode the tape's mixed-channel audio into time-anchored chunks. The live
  // transcript timestamps in ABSOLUTE epoch seconds (the capture frame ts), so the
  // reference must too. Per-frame marks map a whisper within-chunk offset back to real
  // epoch time, so the gaps we drop by concatenating don't drift the match window.
  const rl = readline.createInterface({ input: fs.createReadStream(TAPE), crlfDelay: Infinity });
  let header = null, t0 = null;
  const chunks = []; let cur = [], curN = 0, curMarks = [];
  const flush = () => {
    if (curN > 0) { const a = new Float32Array(curN); let o = 0; for (const s of cur) { a.set(s, o); o += s.length; } chunks.push({ samples: a, marks: curMarks }); }
    cur = []; curN = 0; curMarks = [];
  };
  for await (const line of rl) {
    if (!line) continue;
    const m = JSON.parse(line);
    if (!header) { header = m; continue; }
    if (!m.bin) continue;
    const f = decodeFrame(Buffer.from(m.d, 'base64'));
    if (!f || f.speakerIndex !== 999 || !f.samples.length) continue;
    if (t0 === null) t0 = f.ts;
    curMarks.push({ off: curN / RATE, tsec: f.ts / 1000 });   // concat-offset (s) → real epoch (s)
    cur.push(f.samples); curN += f.samples.length;
    if (curN / RATE >= CHUNK_S) flush();
  }
  flush();
  const t0sec = (t0 ?? 0) / 1000;
  const mapOff = (marks, off) => { let m = marks[0]; for (const k of marks) { if (k.off <= off) m = k; else break; } return m ? m.tsec + (off - m.off) : t0sec + off; };
  const platform = process.argv[3] || header.platform;
  const native = process.argv[4] || header.native;
  const totalS = chunks.reduce((a, c) => a + c.samples.length, 0) / RATE;
  if (!chunks.length) { console.error('[benchmark] no mixed (ch999) audio in this tape — capture never minted'); process.exit(1); }
  console.log(`[benchmark] ${TAPE}\n[benchmark] ${platform}/${native} · ${chunks.length} chunks · ${totalS.toFixed(0)}s mixed audio · STT ${TX.replace(/\/v1.*/, '')}`);

  // 2) full-audio reference (offline, one pass per chunk — natural whisper segmentation)
  const ref = [];
  for (let i = 0; i < chunks.length; i++) {
    try {
      const segs = await sttChunk(chunks[i].samples, LANG);
      for (const s of segs) { const txt = (s.text || '').trim(); if (txt) ref.push({ start: mapOff(chunks[i].marks, s.start || 0), text: txt }); }
      process.stdout.write(`\r[benchmark] reference ${i + 1}/${chunks.length} chunks…   `);
    } catch (e) { console.log(`\n[benchmark] ⚠ chunk ${i + 1} STT failed: ${e.message}`); }
  }
  process.stdout.write('\n');

  // 3) the live transcript the pipeline confirmed
  let live = [];
  try {
    const d = await (await fetch(`${GATEWAY}/transcripts/${platform}/${encodeURIComponent(native)}`)).json();
    live = (d.segments || []).map((s) => ({ start: s.start || 0, text: s.text || '' }));
  } catch (e) { console.error(`[benchmark] live transcript unreachable on ${GATEWAY} (${e.message})`); process.exit(1); }

  // 4) diff. A ref content word is GLOBAL-kept if it appears anywhere in live, and
  // IN-PLACE-kept if it appears within ±MATCH_W of when it was said. TRUE LOSS = content
  // absent EVERYWHERE (high precision — immune to timing drift / the corpus reusing a clip
  // elsewhere only masks loss, so global is the conservative call). The gap between
  // in-place and global recall ≈ content the pipeline kept but mistimed or mis-attributed.
  const liveIdx = new Map();                 // word → sorted live times (epoch s)
  for (const s of live) for (const w of words(s.text)) { if (!liveIdx.has(w)) liveIdx.set(w, []); liveIdx.get(w).push(s.start); }
  for (const arr of liveIdx.values()) arr.sort((a, b) => a - b);
  const keptGlobal = (w) => liveIdx.has(w);
  const keptInPlace = (w, t) => { const a = liveIdx.get(w); return !!a && a.some((lt) => Math.abs(lt - t) <= MATCH_W); };

  let refW = 0, gW = 0, pW = 0; const lost = [], misplaced = [];
  for (const r of ref) {
    const cw = content(r.text); if (!cw.length) continue;
    const g = cw.filter(keptGlobal).length, p = cw.filter((w) => keptInPlace(w, r.start)).length;
    refW += cw.length; gW += g; pW += p;
    if (cw.length < MIN_LOST_WORDS) continue;
    if (g / cw.length < LOST_RECALL) lost.push({ ...r, recall: g / cw.length, cw: cw.length });          // absent everywhere = truly lost
    else if (p / cw.length < LOST_RECALL) misplaced.push({ ...r, recall: p / cw.length, cw: cw.length }); // present, but not when said
  }
  const gPct = refW ? Math.round((100 * gW) / refW) : 100;
  const pPct = refW ? Math.round((100 * pW) / refW) : 100;

  console.log(`\nreference: ${ref.length} segments · ${refW} content words   |   live: ${live.length} segments`);
  console.log(`CONTENT RECALL (full-audio words present ANYWHERE in live): ${gPct}%   (${refW - gW} words absent everywhere)`);
  console.log(`IN-PLACE RECALL (also within ±${MATCH_W}s of when said):     ${pPct}%   (${gPct - pPct}pt gap = present but displaced)`);
  console.log(`✗ TRULY-LOST spans (content absent everywhere, ≥${MIN_LOST_WORDS} words): ${lost.length}`);
  for (const l of lost.slice(0, 10)) console.log(`    @${(l.start - t0sec).toFixed(0)}s (kept ${Math.round(l.recall * 100)}%): "${l.text.slice(0, 88)}"`);
  console.log(`~ MISPLACED spans (heard, but mistimed/mis-attributed — not where said): ${misplaced.length}`);
  for (const l of misplaced.slice(0, 8)) console.log(`    @${(l.start - t0sec).toFixed(0)}s (in-place ${Math.round(l.recall * 100)}%): "${l.text.slice(0, 88)}"`);
  console.log(`\nBENCH ${platform}/${native} ref_segs=${ref.length} ref_words=${refW} recall=${gPct}% inplace=${pPct}% lost_spans=${lost.length} misplaced=${misplaced.length} missing_words=${refW - gW}`);
}
main().catch((e) => { console.error('[benchmark]', e.message); process.exit(1); });
