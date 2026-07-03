#!/usr/bin/env node
// replay — re-send a stored raw signal into a desktop ingest, VERBATIM, at the captured
// real-time pacing. Reproduces a live session's exact capture.v1 stream (binary audio frames
// + text name hints) deterministically, so pipeline bugs (flicker hijacks, oversegmentation,
// lost transcripts) can be debugged with NO live meeting. Watch the replayed transcript with:
//   pnpm observe <platform> <native>
//
// TWO input formats, auto-detected from the first line:
//   • legacy TAPE  — `{v:1, platform, native, …}` header, then `{t, bin, d:base64}` frames
//                    (written by the desktop when VEXA_RECORD_TAPE=<dir> is set; sent verbatim).
//   • captured-signal.v1 — `{type:"captured_signal_header", platform, native_meeting_id, …}`
//                    header, then CapturedFrame lines `{ts, speakerIndex, speakerName?, hint?,
//                    pcm:base64, lane}` (the O-TEL-1 telemetry tap's output). Each frame is
//                    RE-ENCODED here into the @vexa/capture-codec wire shape and sent, so it
//                    drives the EXACT same pipeline (O-TEL-2 — deterministic offline repro).
//                    The OFFLINE, server-free twin of this is services/bot/src/replay.test.ts.
//
//   node replay.mjs <signal.jsonl>
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
if (!TAPE) { console.error('usage: replay.mjs <signal.jsonl>  (legacy tape OR captured-signal.v1)'); process.exit(1); }
const INGEST = (process.env.INGEST || 'ws://localhost:9099').replace(/\/+$/, '');
const SPEED = Math.max(0.1, Number(process.env.SPEED || 1));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── @vexa/capture-codec wire encoder (inlined; eval is zero-npm-dep, not a workspace pkg). ──
// Matches modules/capture-codec/src/index.ts byte-for-byte: no-name = [Int32 track][Float64 ts]
// [Float32 pcm…]; named = high-bit track + [Int32 nameLen][UTF-8 name, 4B-padded][Float32 pcm…].
const NAME_FLAG = 0x80000000 | 0;
function encodeAudioFrame(speakerIndex, ts, pcm, speakerName) {
  const name = speakerName && speakerName.length ? speakerName : '';
  if (!name) {
    const buf = new ArrayBuffer(12 + pcm.length * 4);
    const view = new DataView(buf);
    view.setInt32(0, speakerIndex, true);
    view.setFloat64(4, ts, true);
    new Float32Array(buf, 12).set(pcm);
    return buf;
  }
  const nameBytes = new TextEncoder().encode(name);
  const padded = (nameBytes.length + 3) & ~3;
  const buf = new ArrayBuffer(16 + padded + pcm.length * 4);
  const view = new DataView(buf);
  view.setInt32(0, speakerIndex | NAME_FLAG, true);
  view.setFloat64(4, ts, true);
  view.setInt32(12, nameBytes.length, true);
  new Uint8Array(buf, 16, nameBytes.length).set(nameBytes);
  new Float32Array(buf, 16 + padded).set(pcm);
  return buf;
}
// A captured-signal.v1 frame's base64 PCM → Float32Array.
function framePcm(f) { const b = Buffer.from(f.pcm, 'base64'); return new Float32Array(b.buffer, b.byteOffset, b.byteLength / 4); }
// An active-speaker hint frame the mixed lane consumes (the desktop's event-frame shape).
const hintEvent = (f) => JSON.stringify({ kind: 'active-speaker', speaker: f.hint, ts: f.ts, detail: { hint: 'dom-active' } });

function connectAndReady(header) {
  // captured-signal.v1 uses native_meeting_id; the legacy tape uses native.
  const platform = process.env.REPLAY_PLATFORM || header.platform;
  const native = process.env.REPLAY_NATIVE || header.native || header.native_meeting_id;
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
  let header = null, kind = null, ws = null, t0 = 0, base = 0, sent = 0, audio = 0, hints = 0;
  for await (const line of rl) {
    if (!line) continue;
    const m = JSON.parse(line);
    if (!header) {                                  // first line = the session header
      header = m;
      kind = header.type === 'captured_signal_header' ? 'captured-signal' : 'tape';
      if (kind === 'tape' && !header.platform) { console.error('[replay] bad input — first line is not a recognized header'); process.exit(1); }
      console.log(`[replay] format: ${kind === 'captured-signal' ? 'captured-signal.v1 (re-encoded → codec wire)' : 'legacy tape (verbatim)'}`);
      ws = await connectAndReady(header);
      t0 = Date.now();
      continue;
    }
    if (kind === 'captured-signal') {
      // Re-pace to the captured epoch ts (the first frame anchors t=0). speakerName → named gmeet
      // frame; hint → a mixed-lane active-speaker event; else an unnamed audio frame.
      if (!base) base = m.ts;
      const wait = (m.ts - base) / SPEED - (Date.now() - t0);
      if (wait > 0) await sleep(wait);
      if (m.hint) { ws.send(hintEvent(m)); hints++; }
      else { ws.send(encodeAudioFrame(m.speakerIndex, m.ts, framePcm(m), m.speakerName)); audio++; }
    } else {
      const wait = m.t / SPEED - (Date.now() - t0);   // re-pace to the captured arrival times
      if (wait > 0) await sleep(wait);
      if (m.bin) { const b = Buffer.from(m.d, 'base64'); ws.send(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)); audio++; }
      else { ws.send(m.d); hints++; }
    }
    if (++sent % 250 === 0) console.log(`[replay] t=${((Date.now() - t0) / 1000).toFixed(1)}s · sent ${sent} (${audio} audio, ${hints} hint)`);
  }
  console.log(`[replay] done — ${sent} frames (${audio} audio, ${hints} hint). Flushing pipeline…`);
  await sleep(2000);                                // let the pipeline emit trailing confirms
  ws?.close();
}
main().catch((e) => { console.error('[replay]', e.message); process.exit(1); });
