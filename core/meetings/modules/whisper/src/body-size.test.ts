/**
 * An STT request body stays small: a capped 30 s window is uploaded as lossless FLAC, under 1 MB,
 * and decodes bit-exact (checked with ffmpeg when present). Before: one WAV request carried 160 s
 * (5.1 MB); proxies cut such uploads mid-body on a slow uplink.
 *   npx tsx src/body-size.test.ts
 */
import { createServer } from 'node:http';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { TranscriptionClient } from './index.js';
import { encodeFlac } from './flac.js';
import { setLogger } from './log.js';

setLogger(() => {});
let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const SR = 16000;
const speechLike = (sec: number) => {
  const x = new Float32Array(sec * SR);
  for (let i = 0; i < x.length; i++) x[i] = 0.25 * Math.sin(i / 11) * Math.abs(Math.sin(i / 3000)) * (i % 32000 < 24000 ? 1 : 0.01);
  return x;
};

async function run() {
  let bodies: number[] = [], types: string[] = [];
  const server = createServer((req, res) => {
    let n = 0;
    req.on('data', (c: Buffer) => { n += c.length; });
    req.on('end', () => {
      bodies.push(n); types.push(String(req.headers['content-type']));
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ text: 'ok', language: 'en', duration: 30, segments: [] }));
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  const window = speechLike(30);
  await new TranscriptionClient({ serviceUrl: url, maxRetries: 0 }).transcribe(window);
  await new TranscriptionClient({ serviceUrl: url, maxRetries: 0, audioFormat: 'wav' }).transcribe(window);
  server.close();
  const [flac, wav] = bodies;
  console.log(`  bytes per 30 s request: wav ${wav}, flac ${flac}`);
  check('a 30 s window uploads under 1 MB', flac < 1_000_000, String(flac));
  check('FLAC is smaller than WAV for the same window', flac < wav * 0.8, `flac=${flac} wav=${wav}`);

  const f = encodeFlac(window);
  check('the body is a FLAC stream', Buffer.from(f.subarray(0, 4)).toString('latin1') === 'fLaC');
  let ffmpeg = true;
  try { execFileSync('ffmpeg', ['-version'], { stdio: 'ignore' }); } catch { ffmpeg = false; }
  if (ffmpeg) {
    const d = mkdtempSync(join(tmpdir(), 'flac-'));
    writeFileSync(join(d, 'a.flac'), f);
    execFileSync('ffmpeg', ['-v', 'error', '-i', join(d, 'a.flac'), '-f', 's16le', '-acodec', 'pcm_s16le', join(d, 'a.raw'), '-y']);
    const raw = readFileSync(join(d, 'a.raw'));
    let same = raw.length === window.length * 2;
    for (let i = 0; same && i < window.length; i++) {
      const s = Math.max(-1, Math.min(1, window[i]));
      if (raw.readInt16LE(i * 2) !== Math.round(s < 0 ? s * 0x8000 : s * 0x7fff)) same = false;
    }
    check('ffmpeg decodes it bit-exact', same);
  } else console.log('  (ffmpeg absent: bit-exact decode not checked here)');

  if (failed) { console.log(`\n❌ ${failed} body-size check(s) failed`); process.exit(1); }
  console.log('✅ body-size: capped windows upload as small lossless FLAC');
}
run().catch((e) => { console.error(e); process.exit(1); });
