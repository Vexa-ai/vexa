/**
 * L3 — the raw-signal tape path cannot leave VEXA_RECORD_TAPE (card 48159bf01965).
 * `platform` and `native_meeting_id` come raw from the ingest query; before the fix they were
 * pasted into the tape path, so `native_meeting_id=../../../escape` resolved outside the tape
 * dir (when a `tape-<platform>-..` directory exists), and otherwise the open failed with an
 * unhandled stream 'error' that took the host down. Asserts: the tape lands inside the dir,
 * nothing is written outside it, and the host is still serving afterwards.
 * Run: npx tsx src/tape.test.ts
 */
import { mkdtempSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { WebSocket } from 'ws';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => { console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`); if (!cond) failed++; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
let crash: unknown = null;
process.on('uncaughtException', (e) => { crash = e; });

const filesUnder = (d: string): string[] => readdirSync(d).flatMap((f) => {
  const p = join(d, f); return statSync(p).isDirectory() ? filesUnder(p) : [p];
});

async function main() {
  const base = mkdtempSync(join(tmpdir(), 'vexa-tape-'));
  const dir = join(base, 'tapes');
  mkdirSync(dir);
  // The directory a traversal needs to resolve outside `dir` on Linux/macOS.
  mkdirSync(join(dir, 'tape-google_meet-..'));
  process.env.VEXA_RECORD_TAPE = dir;
  const { startDesktop } = await import('./desktop.js');
  const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, recordingsDir: join(base, 'rec') });
  try {
    for (const native of ['../../../escape', '..%2F..%2F..%2Fescape2']) {
      const ws = new WebSocket(`ws://127.0.0.1:${desk.ingestPort}/ingest?platform=google_meet&native_meeting_id=${encodeURIComponent(decodeURIComponent(native))}`);
      await new Promise<void>((res, rej) => { ws.on('open', () => res()); ws.on('error', rej); });
      ws.send('{"type":"event","kind":"probe"}');
      await sleep(200);
      ws.close();
      await sleep(100);
    }
    const ws2 = new WebSocket(`ws://127.0.0.1:${desk.ingestPort}/ingest?platform=..%2F..%2Fp&native_meeting_id=ok`);
    await new Promise<void>((res, rej) => { ws2.on('open', () => res()); ws2.on('error', rej); });
    await sleep(200); ws2.close(); await sleep(100);

    check('no uncaught exception in the host', crash === null, String(crash));
    const outside = readdirSync(base).filter((f) => f !== 'tapes' && f !== 'rec');
    check('nothing written outside the tape dir', outside.length === 0, outside.join(', '));
    const tapes = filesUnder(dir).filter((p) => p.endsWith('.jsonl'));
    const rd = resolve(dir) + sep;
    check('three tapes recorded', tapes.length === 3, `${tapes.length}: ${tapes.join(', ')}`);
    check('every tape is directly inside the tape dir', tapes.every((p) => resolve(p).startsWith(rd) && !resolve(p).slice(rd.length).includes(sep)), tapes.join(', '));
    const h = await fetch(`http://127.0.0.1:${desk.gatewayPort}/health`).catch(() => null);
    check('host still answers /health', !!h, 'no response');
  } finally {
    await desk.close();
    rmSync(base, { recursive: true, force: true });
  }
  if (failed) { console.error(`\n❌ tape (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ tape (L3): raw query ids are sanitized; the tape path stays inside VEXA_RECORD_TAPE.');
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
