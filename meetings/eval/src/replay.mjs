#!/usr/bin/env node
// replay — re-send a recorded raw-signal tape into a desktop ingest, VERBATIM, at the
// captured real-time pacing. Reproduces a live session's exact capture.v1 stream
// (binary audio frames + text name hints) deterministically, so pipeline bugs
// (flicker hijacks, oversegmentation, lost transcripts) can be debugged with NO live
// meeting. Tapes are written by the desktop when VEXA_RECORD_TAPE=<dir> is set.
// Watch the replayed transcript with:  pnpm observe <platform> <native>
//
//   node replay.mjs <tape.jsonl>
//   INGEST=ws://localhost:9099    target desktop ingest (default)
//   SPEED=1                       replay rate (SPEED=4 → 4× faster; segmentation is
//                                 driven by the embedded audio ts so it stays correct,
//                                 but wall-clock TTL-finalize may differ — keep 1 for
//                                 faithful repro)
//   REPLAY_PLATFORM / REPLAY_NATIVE   relabel the session key — e.g. replay a zoom tape
//                                 as 'teams' (same mixed pipeline), or avoid clashing
//                                 with a live session of the same id.
import fs from 'node:fs';
import readline from 'node:readline';

const TAPE = process.argv[2];
if (!TAPE) { console.error('usage: replay.mjs <tape.jsonl>'); process.exit(1); }
const INGEST = (process.env.INGEST || 'ws://localhost:9099').replace(/\/+$/, '');
const SPEED = Math.max(0.1, Number(process.env.SPEED || 1));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function connectAndReady(header) {
  const platform = process.env.REPLAY_PLATFORM || header.platform;
  const native = process.env.REPLAY_NATIVE || header.native;
  const q = `platform=${encodeURIComponent(platform)}&native_meeting_id=${encodeURIComponent(native)}`
          + (header.language ? `&language=${encodeURIComponent(header.language)}` : '');
  const url = `${INGEST}/?${q}`;
  console.log(`[replay] ${TAPE}\n[replay] → ${url} · ${SPEED}× · recorded ${header.startedAt || '?'}`);
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    ws.onerror = (e) => { console.log('[replay] ws error:', e?.message || 'connect failed — is the desktop ingest up on :9099?'); reject(new Error('connect failed')); };
    ws.onclose = () => console.log('[replay] ws closed');
    let resolved = false;
    const go = () => { if (!resolved) { resolved = true; resolve(ws); } };
    // Wait for the desktop's {type:'ready'} like the real client does (fall through after 2s).
    ws.onopen = () => { ws.onmessage = (ev) => { try { if (JSON.parse(ev.data).type === 'ready') go(); } catch { /* */ } }; setTimeout(go, 2000); };
  });
}

async function main() {
  const rl = readline.createInterface({ input: fs.createReadStream(TAPE), crlfDelay: Infinity });
  let header = null, ws = null, t0 = 0, sent = 0, audio = 0, hints = 0;
  for await (const line of rl) {
    if (!line) continue;
    const m = JSON.parse(line);
    if (!header) {                                  // first line = the session header
      header = m;
      if (!header.platform) { console.error('[replay] bad tape — first line is not a header'); process.exit(1); }
      ws = await connectAndReady(header);
      t0 = Date.now();
      continue;
    }
    const wait = m.t / SPEED - (Date.now() - t0);   // re-pace to the captured arrival times
    if (wait > 0) await sleep(wait);
    if (m.bin) { const b = Buffer.from(m.d, 'base64'); ws.send(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); audio++; }
    else { ws.send(m.d); hints++; }
    if (++sent % 250 === 0) console.log(`[replay] t=${((Date.now() - t0) / 1000).toFixed(1)}s · sent ${sent} (${audio} audio, ${hints} hint)`);
  }
  console.log(`[replay] done — ${sent} frames (${audio} audio, ${hints} hint). Flushing pipeline…`);
  await sleep(2000);                                // let the pipeline emit trailing confirms
  ws?.close();
}
main().catch((e) => { console.error('[replay]', e.message); process.exit(1); });
