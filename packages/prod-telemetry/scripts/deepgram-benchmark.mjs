#!/usr/bin/env node
/**
 * Deepgram single-shot ground-truth pass over a raw-capture dir.
 *   node deepgram-benchmark.mjs <capture-dir>
 * Env: DEEPGRAM_API_KEY. Writes ground_truth.json next to the audio.
 * Off the bot hot path — an offline analytics pass over the S3 corpus, used to
 * (a) benchmark our realtime pipeline (WER vs Deepgram) and (b) feed analytical AI.
 */
import { readdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const dir = process.argv[2];
const KEY = process.env.DEEPGRAM_API_KEY;
if (!dir || !KEY) { console.error('usage: DEEPGRAM_API_KEY=… node deepgram-benchmark.mjs <capture-dir>'); process.exit(1); }

const audioDir = join(dir, 'audio');
const wavs = readdirSync(audioDir).filter(f => f.endsWith('.wav'));
const results = [];
for (const wav of wavs) {
  const buf = readFileSync(join(audioDir, wav));
  const res = await fetch('https://api.deepgram.com/v1/listen?model=nova-3&smart_format=true', {
    method: 'POST',
    headers: { Authorization: `Token ${KEY}`, 'Content-Type': 'audio/wav' },
    body: buf,
  });
  const j = await res.json();
  const transcript = j?.results?.channels?.[0]?.alternatives?.[0]?.transcript ?? '';
  results.push({ file: wav, bytes: buf.length, transcript });
  console.log(`${wav}: ${transcript.slice(0, 80)}${transcript.length > 80 ? '…' : ''}`);
}
writeFileSync(join(dir, 'ground_truth.json'),
  JSON.stringify({ source: dir, model: 'deepgram/nova-3', generated_for: 'ground-truth-benchmark', tracks: results }, null, 2));
console.log(`→ ${join(dir, 'ground_truth.json')} (${results.length} tracks)`);
