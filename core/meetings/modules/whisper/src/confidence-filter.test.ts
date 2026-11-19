/**
 * The opt-in STT confidence filter (#1783): with `confidenceFilter: 'strict'` the client also drops
 * segments whose window Whisper itself scored as unlikely speech (average log-probability under
 * -1.0), on top of the always-on rule. Off by default: the same answer keeps those segments.
 *   npx tsx src/confidence-filter.test.ts
 */
import { createServer } from 'node:http';
import { TranscriptionClient, isLowConfidenceSegment, confidenceFilterFromEnv } from './index.js';
import { setLogger } from './log.js';

const logs: string[] = [];
setLogger((m: string) => { logs.push(m); });
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => { console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`); if (!cond) failed++; };

// ── the rule ──────────────────────────────────────────────────────────────────────────────────────
check('off: a -1.1 log-probability segment is kept (the always-on rule drops only below -1.3)',
  isLowConfidenceSegment({ avg_logprob: -1.1, no_speech_prob: 0 }) === false);
check('strict: a -1.1 log-probability segment is dropped',
  isLowConfidenceSegment({ avg_logprob: -1.1, no_speech_prob: 0 }, 'strict') === true);
check('strict: a fluent segment is kept', isLowConfidenceSegment({ avg_logprob: -0.3, no_speech_prob: 0, compression_ratio: 1.85 }, 'strict') === false);
check('strict: a hesitant but real segment (-0.9) is kept', isLowConfidenceSegment({ avg_logprob: -0.9, no_speech_prob: 0 }, 'strict') === false);
check('strict: the threshold itself is kept (strict <)', isLowConfidenceSegment({ avg_logprob: -1.0 }, 'strict') === false);
check('strict: the always-on rule still applies (compression 2.5)', isLowConfidenceSegment({ avg_logprob: -0.2, compression_ratio: 2.5 }, 'strict') === true);
check('env: only "strict" turns it on', confidenceFilterFromEnv('strict') === 'strict' && confidenceFilterFromEnv(' STRICT ') === 'strict'
  && confidenceFilterFromEnv(undefined) === 'off' && confidenceFilterFromEnv('on') === 'off');
check('strict: a segment with no scores is kept (nothing to judge)', isLowConfidenceSegment({}, 'strict') === false);

// ── the client ────────────────────────────────────────────────────────────────────────────────────
const server = createServer((req, res) => {
  req.resume();
  req.on('end', () => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ text: 'hello there you', language: 'en', duration: 4, segments: [
      { start: 0, end: 2, text: 'hello there', avg_logprob: -0.25, no_speech_prob: 0, compression_ratio: 1.1 },
      { start: 2, end: 4, text: 'you', avg_logprob: -1.2, no_speech_prob: 0, compression_ratio: 0.3 },
    ] }));
  });
});
await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
const pcm = new Float32Array(10 * 16000).fill(0.05);   // a window as long as the measured ones
const off = await new TranscriptionClient({ serviceUrl: url, maxRetries: 0 }).transcribe(pcm, 'en');
check('off (default): both segments reach the lane', off.segments.length === 2 && off.text === 'hello there you', JSON.stringify(off));
logs.length = 0;
const on = await new TranscriptionClient({ serviceUrl: url, maxRetries: 0, confidenceFilter: 'strict' }).transcribe(pcm, 'en');
check('strict: the low-confidence segment and its text are dropped', on.segments.length === 1 && on.text === 'hello there', JSON.stringify(on));
const short = await new TranscriptionClient({ serviceUrl: url, maxRetries: 0, confidenceFilter: 'strict' }).transcribe(new Float32Array(3 * 16000).fill(0.05), 'en');
check('strict: a short window (3 s, under STRICT_MIN_AUDIO_SEC) keeps the segment', short.segments.length === 2, JSON.stringify(short));
check('strict: the drop is logged with its scores', logs.some((l) => /\[STT-CONFIDENCE\].*-1\.2/.test(l)), logs.join(' | '));
server.close();
if (failed) { console.error(`\n❌ confidence-filter: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('✅ confidence-filter: strict mode drops segments Whisper scored as unlikely speech; off keeps today\'s behaviour');
