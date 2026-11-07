/**
 * Regression — a silent call (app.dev, Auto-detect): room noise was sent to STT, which invented
 * Icelandic/Russian lines ("Hvað er hljóði? Hvað er hljóði?"). Noise never reaches STT now, and once a
 * speaker's language is detected with agreement it is pinned for that speaker instead of re-detected
 * on every window.
 *   tsx src/noise-gate.test.ts
 */
import { createGmeetPipeline, type TranscriptSegment, type TranscriptSink } from './index.js';
import { setLogger } from './log.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

let checks = 0;
function ok(cond: boolean, msg: string): void {
  if (!cond) throw new Error(`assertion failed: ${msg}`);
  console.log(`  ✅ ${msg}`);
  checks++;
}
setLogger(() => {});
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const SR = 16000, FRAME = 2048;
let seed = 7;
const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
/** Room noise: broadband, RMS ~0.004–0.009, drifting slowly (a fan, a keyboard far away). */
const noise = (i: number) => { const a = 0.006 + 0.003 * Math.sin(i / 7); const f = new Float32Array(FRAME); for (let k = 0; k < FRAME; k++) f[k] = (rnd() * 2 - 1) * a * 1.7; return f; };
const speech = () => { const f = new Float32Array(FRAME); for (let k = 0; k < FRAME; k++) f[k] = 0.08 * Math.sin(k / 5) * (0.6 + 0.4 * Math.sin(k / 300)); return f; };

const hints: (string | undefined)[] = [];
let requests = 0;
const transcribe = async (pcm: Float32Array, _prompt?: string, hint?: { language?: string }): Promise<TranscriptionResult> => {
  requests++;
  hints.push(hint?.language);
  await sleep(10);
  return { text: 'добрый день коллеги', language: 'ru', language_probability: 0.95, duration: pcm.length / SR,
    segments: [{ start: 0, end: pcm.length / SR, text: 'добрый день коллеги' }] };
};
const confirmed: TranscriptSegment[] = [];
const sink: TranscriptSink = { segment: (s) => confirmed.push(s), draft: () => {}, finalize: () => {} };
const pipe = createGmeetPipeline({ transcribe, sink, coverageLogIntervalMs: 0, config: { submitInterval: 0.1, minAudioDuration: 0.5 } });
let ts = 1_800_000_000_000;
// 12 s of room noise on one participant's track, fed continuously.
for (let i = 0; i < (12 * SR) / FRAME; i++) { pipe.feedAudio(0, 'Anna', noise(i), ts); ts += (FRAME / SR) * 1000; if (i % 8 === 0) await sleep(5); }
await sleep(300);
ok(requests === 0, `room noise is never sent to STT (${requests} requests)`);
// Then speech, in a few phrases separated by short pauses.
for (let p = 0; p < 4; p++) {
  for (let i = 0; i < 20; i++) { pipe.feedAudio(0, 'Anna', speech(), ts); ts += (FRAME / SR) * 1000; if (i % 4 === 0) await sleep(20); }
  for (let i = 0; i < 10; i++) { pipe.feedAudio(0, 'Anna', noise(i), ts); ts += (FRAME / SR) * 1000; }
  await sleep(250);
}
await pipe.dispose();
ok(requests > 0 && confirmed.length > 0, `speech after the noise is transcribed (${requests} requests, ${confirmed.length} segments)`);
const firstPinned = hints.findIndex((h) => h === 'ru');
ok(firstPinned >= 2 && hints.slice(firstPinned).every((h) => h === 'ru'),
  `after two agreeing detections the speaker's language is pinned for every later request (${JSON.stringify(hints)})`);
console.log(`\n✅ noise-gate: ${checks} checks passed`);
process.exit(0);
