/**
 * L3 — recording.v1 through the desktop INGEST path (ARCHITECTURE.md §5). NO live
 * meeting, NO real STT: start the real host on ephemeral ports, open the ingest WS,
 * and feed synthetic recording.v1 chunks (`encodeRecordingChunk`, capture-codec)
 * over the SAME wire the extension uses. Assert the composition root decodes them
 * (decodeRecordingChunk before decodeAudioFrame), routes them to the RecordingSink,
 * assembles a master on is_final, writes a REAL file to disk, and serves it over the
 * gateway GET route. Proves: capture-codec wire → ingest branch → sink → @vexa/recording
 * assemble → disk + gateway — the offline-provable L1→L3 slice, transport real, externals none.
 * Run: npx tsx src/recording-e2e.test.ts
 */
import { existsSync, readFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { WebSocket } from 'ws';
import { encodeRecordingChunk } from '@vexa/capture-codec';
import { buildRecordingMaster } from '@vexa/recording';
import { startDesktop } from './desktop.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => { console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`); if (!cond) failed++; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// A canonical 44-byte WAV header + payload (16 kHz mono s16le) — the recording.v1
// `wav` format the desktop assembles via RIFF header-merge.
function wavChunk(payload: number[]): Uint8Array {
  const dataSize = payload.length;
  const buf = Buffer.alloc(44 + dataSize);
  buf.write('RIFF', 0, 'ascii'); buf.writeUInt32LE(36 + dataSize, 4); buf.write('WAVE', 8, 'ascii');
  buf.write('fmt ', 12, 'ascii'); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(16000, 24); buf.writeUInt32LE(32000, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34);
  buf.write('data', 36, 'ascii'); buf.writeUInt32LE(dataSize, 40); Buffer.from(payload).copy(buf, 44);
  return new Uint8Array(buf);
}

async function main() {
  const dir = mkdtempSync(join(tmpdir(), 'vexa-rec-'));
  const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, recordingsDir: dir });   // NO txUrl → no STT
  const platform = 'google_meet', native = 'rec-e2e';
  try {
    const ws = new WebSocket(`ws://localhost:${desk.ingestPort}/ingest?platform=${platform}&native_meeting_id=${native}`);
    await new Promise<void>((res, rej) => { ws.on('open', () => res()); ws.on('error', rej); });

    // Two media chunks (sent out of seq order to prove ordering survives the wire)
    // + the empty is_final COMPLETED signal — exactly the extension tee's shape.
    const c0 = wavChunk([0x11, 0x22, 0x33, 0x44]);
    const c1 = wavChunk([0x55, 0x66, 0x77, 0x88]);
    ws.send(Buffer.from(encodeRecordingChunk(1, false, 'wav', c1)));
    ws.send(Buffer.from(encodeRecordingChunk(0, false, 'wav', c0)));
    ws.send(Buffer.from(encodeRecordingChunk(2, true, 'wav', new Uint8Array(0))));
    await sleep(300);   // let the host decode + assemble + write
    ws.close();

    // The expected master = buildRecordingMaster over the SEQ-ORDERED chunks (the
    // independent oracle: same contract codec the host used).
    const want = buildRecordingMaster('wav', [Buffer.from(c0), Buffer.from(c1)]);

    // 1) A real file landed on disk. The host sanitizes the session key
    //    (`platform/native`) to a filesystem-safe name (non [A-Za-z0-9._-] → '_').
    const onDisk = join(dir, `${`${platform}/${native}`.replace(/[^a-zA-Z0-9._-]/g, '_')}.wav`);
    check('a real recording file is written to disk', existsSync(onDisk), onDisk);
    if (existsSync(onDisk)) {
      const bytes = readFileSync(onDisk);
      check('on-disk master == buildRecordingMaster(seq-ordered chunks)', Buffer.compare(bytes, want) === 0, `${bytes.length}B vs ${want.length}B`);
      check('on-disk master is a valid RIFF/WAVE container', bytes.toString('ascii', 0, 4) === 'RIFF' && bytes.toString('ascii', 8, 12) === 'WAVE');
    }

    // 2) The gateway serves it back over its GET route (the desktop = local receiver + server).
    let served: Response | null = null;
    for (let i = 0; i < 30 && !served?.ok; i++) { await sleep(100); served = await fetch(`http://localhost:${desk.gatewayPort}/recordings/${platform}/${native}`); }
    check('gateway serves the recording over GET /recordings/{p}/{n}', !!served?.ok, `status ${served?.status}`);
    if (served?.ok) {
      check('served content-type is audio/wav', (served.headers.get('content-type') || '').includes('audio/wav'), served.headers.get('content-type') || '');
      const servedBytes = Buffer.from(await served.arrayBuffer());
      check('served bytes == the assembled master', Buffer.compare(servedBytes, want) === 0, `${servedBytes.length}B`);
    }

    // 3) A recording chunk must NOT have leaked into the transcript path (no STT, no segments).
    const tr = await (await fetch(`http://localhost:${desk.gatewayPort}/transcripts/${platform}/${native}`)).json();
    check('recording chunks did NOT leak into the transcript store', (tr.segments || []).length === 0, `${(tr.segments || []).length} segs`);
  } finally {
    await desk.close();
    rmSync(dir, { recursive: true, force: true });
  }

  if (failed) { console.error(`\n❌ recording-e2e (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ recording-e2e (L3): synthetic recording.v1 → ingest WS → decode → RecordingSink → @vexa/recording assemble → real file on disk → served by the gateway. No live meeting.');
  process.exit(0);
}

main().catch((e) => { console.error(e); process.exit(1); });
