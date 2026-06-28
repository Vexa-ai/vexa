#!/usr/bin/env node
/**
 * Deepgram diarization benchmark — transcription + speaker segmentation over a
 * single pre-recorded WAV (a capture.v1 fixture's mixed channel).
 *
 *   DEEPGRAM_API_KEY=… node deepgram-diarize.mjs <fixture-dir-or-wav>
 *
 * Reference quality bar for the local mixed-pipeline brick (WeSpeaker ONNX +
 * Whisper). Batch pass — Deepgram does STT + diarization in one call. Writes
 * deepgram-diarize.json (separated-transcript.v1-shaped) next to the audio.
 */
import { readFileSync, writeFileSync, existsSync, statSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';

const arg = process.argv[2];
const KEY = process.env.DEEPGRAM_API_KEY;
if (!arg || !KEY) { console.error('usage: DEEPGRAM_API_KEY=… node deepgram-diarize.mjs <fixture-dir-or-wav>'); process.exit(1); }

// Resolve the WAV: a direct .wav, or the mixed/largest wav in <dir>/audio.
function resolveWav(p) {
  if (p.endsWith('.wav')) return p;
  const audioDir = existsSync(join(p, 'audio')) ? join(p, 'audio') : p;
  const wavs = readdirSync(audioDir).filter(f => f.endsWith('.wav'));
  if (!wavs.length) throw new Error(`no .wav under ${audioDir}`);
  // Prefer the mixed remote channel (999), else the largest file.
  const mixed = wavs.find(f => f.includes('999'));
  const pick = mixed || wavs.map(f => ({ f, s: statSync(join(audioDir, f)).size })).sort((a, b) => b.s - a.s)[0].f;
  return join(audioDir, pick);
}

const wavPath = resolveWav(arg);
const buf = readFileSync(wavPath);
console.log(`▶ Deepgram diarize: ${wavPath} (${(buf.length / 1e6).toFixed(1)} MB)`);

const url = 'https://api.deepgram.com/v1/listen?model=nova-3&diarize=true&punctuate=true&smart_format=true&utterances=true';
const res = await fetch(url, {
  method: 'POST',
  headers: { Authorization: `Token ${KEY}`, 'Content-Type': 'audio/wav' },
  body: buf,
});
if (!res.ok) { console.error(`Deepgram ${res.status}: ${await res.text()}`); process.exit(1); }
const j = await res.json();

const alt = j?.results?.channels?.[0]?.alternatives?.[0] ?? {};
const words = alt.words ?? [];
// Prefer Deepgram's own utterance grouping; fall back to grouping words by speaker.
let utterances = j?.results?.utterances;
if (!utterances || !utterances.length) {
  utterances = [];
  let cur = null;
  for (const w of words) {
    const spk = w.speaker ?? 0;
    if (!cur || cur.speaker !== spk) { cur = { speaker: spk, start: w.start, end: w.end, words: [] }; utterances.push(cur); }
    cur.end = w.end; cur.words.push(w);
  }
  utterances.forEach(u => { u.transcript = u.words.map(w => w.punctuated_word || w.word).join(' '); });
}

const mmss = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const speakers = new Set();
console.log('\n──────── speaker-segmented transcript ────────');
for (const u of utterances) {
  speakers.add(u.speaker);
  console.log(`[spk ${u.speaker}  ${mmss(u.start)}–${mmss(u.end)}] ${u.transcript}`);
}

const dur = j?.metadata?.duration ?? (words.length ? words[words.length - 1].end : 0);
console.log('──────────────────────────────────────────────');
console.log(`✔ ${utterances.length} utterances · ${speakers.size} distinct speakers · ${dur.toFixed(1)}s audio`);
console.log(`  full transcript:\n${alt.transcript || '(empty)'}`);

// separated-transcript.v1-shaped output for direct comparison to the pipeline brick.
const segments = utterances.map(u => ({
  speakerKey: `dg-${u.speaker}`,
  text: u.transcript,
  start: u.start,
  end: u.end,
  words: (u.words || []).map(w => ({ word: w.punctuated_word || w.word, start: w.start, end: w.end })),
  topology: 'mixed',
  confidence: u.confidence,
}));
const outPath = join(dirname(wavPath), 'deepgram-diarize.json');
writeFileSync(outPath, JSON.stringify({ source: wavPath, model: 'deepgram/nova-3', diarize: true, speakers: speakers.size, segments }, null, 2));
console.log(`→ ${outPath} (${segments.length} segments, separated-transcript.v1 shape)`);
