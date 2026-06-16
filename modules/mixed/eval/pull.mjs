#!/usr/bin/env node
// pull — build a reusable eval fixture from a YouTube link.
//   1. yt-dlp downloads the audio, ffmpeg → 16 kHz mono WAV (what our pipeline ingests)
//   2. Deepgram (nova, diarize) transcribes + diarizes it = the reference ("ground truth")
// Output: fixtures/<id>/{audio.wav, deepgram.json, meta.json}  (fixtures/ is git-ignored).
//
//   DEEPGRAM_API_KEY=… node pull.mjs "https://www.youtube.com/watch?v=…"
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = process.env.EVAL_FIXTURES || path.join(HERE, 'fixtures');
const DG_KEY = process.env.DEEPGRAM_API_KEY || process.env.DG_KEY;
const url = process.argv[2];
if (!url) { console.error('usage: node pull.mjs <youtube-url>'); process.exit(2); }
if (!DG_KEY) { console.error('set DEEPGRAM_API_KEY (or DG_KEY)'); process.exit(2); }

const sh = (cmd, args) => execFileSync(cmd, args, { stdio: ['ignore', 'pipe', 'inherit'] }).toString().trim();

const id = sh('yt-dlp', ['--print', '%(id)s', '--no-warnings', url]).split('\n').pop();
const title = sh('yt-dlp', ['--print', '%(title)s', '--no-warnings', url]).split('\n').pop();
const dir = path.join(FIXTURES, id);
fs.mkdirSync(dir, { recursive: true });
const audio = path.join(dir, 'audio.wav');

if (!fs.existsSync(audio)) {
  console.log(`[pull] ${id} "${title}" — downloading audio…`);
  const raw = path.join(dir, 'raw.m4a');
  sh('yt-dlp', ['-f', 'bestaudio/best', '-o', raw, '--no-warnings', '--force-overwrites', url]);
  console.log('[pull] → 16 kHz mono wav…');
  sh('ffmpeg', ['-y', '-i', raw, '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', audio]);
  fs.rmSync(raw, { force: true });
}
const durSec = (fs.statSync(audio).size - 44) / (16000 * 2);
console.log(`[pull] audio.wav ready (${durSec.toFixed(0)}s)`);

const dgPath = path.join(dir, 'deepgram.json');
if (!fs.existsSync(dgPath)) {
  console.log('[pull] Deepgram transcribe + diarize…');
  const qs = new URLSearchParams({ model: 'nova-2', diarize: 'true', punctuate: 'true', utterances: 'true', smart_format: 'true' });
  const r = await fetch(`https://api.deepgram.com/v1/listen?${qs}`, {
    method: 'POST',
    headers: { Authorization: `Token ${DG_KEY}`, 'Content-Type': 'audio/wav' },
    body: fs.readFileSync(audio),
  });
  if (!r.ok) { console.error(`deepgram ${r.status}: ${(await r.text()).slice(0, 200)}`); process.exit(1); }
  fs.writeFileSync(dgPath, JSON.stringify(await r.json()));
}
const dg = JSON.parse(fs.readFileSync(dgPath, 'utf8'));
const utts = dg.results?.utterances || [];
const speakers = new Set(utts.map((u) => u.speaker));
console.log(`[pull] deepgram.json ready — ${utts.length} utterances, ${speakers.size} speakers`);

fs.writeFileSync(path.join(dir, 'meta.json'), JSON.stringify({ id, title, url, durSec, utterances: utts.length, speakers: speakers.size }, null, 2));
console.log(`[pull] ✓ fixture: ${dir}`);
console.log(`       next: pick a region from deepgram.json and run.ts (see CLAUDE.md)`);
