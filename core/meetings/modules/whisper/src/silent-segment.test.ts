/**
 * A segment the STT places over silence is dropped at the source ("Thank you." / "Продолжение
 * следует…" at the end of a voice note): its audio is checked, not just its confidence.
 *   npx tsx src/silent-segment.test.ts
 */
import { createServer } from 'node:http';
import { TranscriptionClient } from './index.js';
import { setLogger } from './log.js';

setLogger(() => {});
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => { console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`); if (!cond) failed++; };

const server = createServer((req, res) => {
  req.resume();
  req.on('end', () => {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ text: 'семнадцать Thank you.', language: 'ru', duration: 4, segments: [
      { start: 0, end: 1.5, text: 'семнадцать', avg_logprob: -0.2, no_speech_prob: 0.01 },
      { start: 2, end: 4, text: 'Thank you.', avg_logprob: -0.3, no_speech_prob: 0.2 },
    ] }));
  });
});
await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
const pcm = new Float32Array(4 * 16000);
pcm.fill(0.05, 0, 1.5 * 16000);                     // speech for 1.5 s, then silence
const r = await new TranscriptionClient({ serviceUrl: url, maxRetries: 0 }).transcribe(pcm, 'ru');
check('the spoken segment is kept', r.segments.some((s) => s.text === 'семнадцать'), JSON.stringify(r.segments));
check('the segment over silence is dropped, and so is its text', !r.segments.some((s) => s.text.includes('Thank')) && !r.text.includes('Thank'), JSON.stringify(r));
server.close();
if (failed) process.exit(1);
console.log('✅ silent-segment: text the STT places over silence never reaches the transcript');
