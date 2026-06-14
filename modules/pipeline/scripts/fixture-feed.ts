#!/usr/bin/env tsx
/**
 * fixture-feed — the integration-test workhorse (release plan, Layer 3).
 *
 * Replays a capture.v1 fixture into a RUNNING deployment's ingest WS — exactly
 * as the product extension / bot would — then asserts transcript.v1 came back out
 * the API. Deployment-agnostic: point it at the dev `live-stack`, or a real
 * Lite / Compose / Helm ingest+gateway. No live meeting, no GPU flakiness.
 *
 *   INGEST_URL=ws://localhost:9099/ingest GATEWAY_URL=http://localhost:8056 \
 *   FIXTURE=~/.vexa/fixtures/rt/zoom-2923712604 API_KEY=test \
 *   EXPECT_SEGMENTS=1 EXPECT_SPEAKERS="Alexia R,Jonnie Boy" SPEED=12 \
 *     tsx scripts/fixture-feed.ts
 *
 * Exit 0 if ≥EXPECT_SEGMENTS transcript.v1 segments came out (and any
 * EXPECT_SPEAKERS are present); non-zero otherwise — so it drops into CI as a
 * pass/fail integration gate, run once per {deployment × platform} cell.
 */
import { WebSocket } from 'ws';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { decodeAudioFrame } from '../../../contracts/capture/v1/schema';

const INGEST_URL = process.env.INGEST_URL || 'ws://localhost:9099/ingest';
const GATEWAY_URL = (process.env.GATEWAY_URL || 'http://localhost:8056').replace(/\/+$/, '');
const FIXTURE = process.env.FIXTURE;
const API_KEY = process.env.API_KEY || 'test';
const SPEED = parseFloat(process.env.SPEED || '12');            // × real-time
const EXPECT_SEGMENTS = parseInt(process.env.EXPECT_SEGMENTS || '1', 10);
const EXPECT_SPEAKERS = (process.env.EXPECT_SPEAKERS || '').split(',').map(s => s.trim()).filter(Boolean);
const DRAIN_MAX_MS = parseInt(process.env.DRAIN_MAX_MS || '120000', 10);
const SAMPLE_RATE = 16000;

if (!FIXTURE) { console.error('set FIXTURE=<dir with stream.capture + meta.json>'); process.exit(2); }
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const meta = JSON.parse(readFileSync(join(FIXTURE, 'meta.json'), 'utf8'));
  const platform = String(meta.platform);
  const nativeId = String(meta.native_meeting_id);
  const language = meta.language || 'en';
  const buf = readFileSync(join(FIXTURE, 'stream.capture'));
  console.log(`[fixture-feed] ${platform} #${nativeId}  ${(buf.length / 1e6).toFixed(1)}MB  →  ${INGEST_URL}  @${SPEED}× realtime`);

  const qs = new URLSearchParams({ platform, native_meeting_id: nativeId, api_key: API_KEY, language });
  const ws = new WebSocket(`${INGEST_URL}?${qs.toString()}`);
  await new Promise<void>((res, rej) => { ws.once('open', () => res()); ws.once('error', rej); });
  await new Promise<void>((res) => {           // wait for the `ready` handshake
    const onMsg = (d: any) => { try { if (JSON.parse(d.toString()).type === 'ready') { ws.off('message', onMsg); res(); } } catch { /* binary */ } };
    ws.on('message', onMsg);
    setTimeout(res, 3000);                      // some ingests don't handshake — proceed anyway
  });

  // Walk the framed wire log: [u8 type 0=audio 1=event][u32LE len][payload].
  // Send each payload over the WS, paced to SPEED× real-time by the audio ts so
  // the backend's wall-clock turn/idle timers behave proportionally.
  const t0 = Date.now(); let firstTs: number | null = null, audio = 0, events = 0, off = 0;
  while (off + 5 <= buf.length) {
    const type = buf.readUInt8(off); const len = buf.readUInt32LE(off + 1); off += 5;
    const payload = buf.subarray(off, off + len); off += len;
    if (type === 0) {
      const f = decodeAudioFrame(payload.buffer, payload.byteOffset, payload.byteLength);
      if (f) { if (firstTs === null) firstTs = f.ts; const target = (f.ts - firstTs) / SPEED; const waited = Date.now() - t0; if (target > waited) await sleep(target - waited); }
      ws.send(payload, { binary: true }); audio++;
    } else {
      ws.send(payload.toString('utf8')); events++;
    }
  }
  console.log(`[fixture-feed] fed ${audio} audio + ${events} event frames in ${((Date.now() - t0) / 1000).toFixed(0)}s; closing → backend flushes`);
  ws.close();

  // Poll the API until the segment count stabilizes (drain) or DRAIN_MAX_MS.
  const url = `${GATEWAY_URL}/transcripts/${platform}/${nativeId}`;
  let segs: any[] = [], stable = 0; const startPoll = Date.now();
  while (Date.now() - startPoll < DRAIN_MAX_MS) {
    await sleep(3000);
    try {
      const r = await fetch(url, { headers: { 'X-API-Key': API_KEY } });
      const body: any = await r.json();
      const next = body.segments || body || [];
      if (Array.isArray(next)) { if (next.length === segs.length) stable++; else stable = 0; segs = next; }
      process.stdout.write(`\r[fixture-feed] draining… segments=${segs.length}`);
      if (segs.length >= EXPECT_SEGMENTS && stable >= 3) break;
    } catch { /* gateway not ready */ }
  }
  process.stdout.write('\n');

  const speakers = [...new Set(segs.map((s: any) => s.speaker).filter(Boolean))];
  console.log(`[fixture-feed] API ${url} → ${segs.length} segments; speakers: ${speakers.join(', ') || '(none)'}`);
  const missing = EXPECT_SPEAKERS.filter((n) => !speakers.some((s) => String(s).includes(n)));
  const ok = segs.length >= EXPECT_SEGMENTS && missing.length === 0;
  if (missing.length) console.error(`  ✗ missing expected speakers: ${missing.join(', ')}`);
  console.log(ok ? `✅ PASS — transcript.v1 reproduced from fixture (${segs.length} ≥ ${EXPECT_SEGMENTS})` : `❌ FAIL`);
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('[fixture-feed] error:', e?.message || e); process.exit(2); });
