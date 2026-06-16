#!/usr/bin/env tsx
/**
 * live-stack — the whole brick chain in ONE local process so the PRODUCT
 * extension renders live transcripts (confirmed AND pending) through the modules:
 *
 *   product extension ─capture.v1─►  ingest WS (9099)
 *                                       │  @vexa/pipeline ChunkedTranscriber
 *                                       │    (gate + diarizer + Whisper + LocalAgreement-2,
 *                                       │     internal ClusterNameBinder fed the hints → named)
 *                                       ▼
 *                                    gateway (8056): GET /transcripts/:p/:id · GET /bots · WS /ws
 *                                       ▲  the sidepanel's exact envelope {type:'transcript',speaker,confirmed,pending}
 *
 * Uses the raw ChunkedTranscriber (not the createMixedPipeline contract adapter)
 * BECAUSE the live UI wants the forming PENDING tail — a live affordance the
 * separated-transcript.v1 contract intentionally omits. This mirrors the bot's
 * chunked-host: map ChunkedTranscriber emits → the publisher envelope.
 *
 *   INGEST_PORT=9099 GATEWAY_PORT=8056 \
 *   TRANSCRIPTION_SERVICE_URL=https://transcription.vexa.ai TRANSCRIPTION_SERVICE_TOKEN=… \
 *   npx tsx scripts/live-stack.ts
 */
import * as http from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';
import { ChunkedTranscriber, type ChunkSegment } from '@vexa/mixed-pipeline';
import { SpeakerStreamManager } from '../src/index';   // gmeet lane: not yet carved
import { TranscriptionClient } from '@vexa/transcribe-whisper';
import { decodeAudioFrame, decodeEvent } from '@vexa/capture-codec';

const INGEST_PORT = parseInt(process.env.INGEST_PORT || '9099', 10);
const GATEWAY_PORT = parseInt(process.env.GATEWAY_PORT || '8056', 10);
const TX_URL = process.env.TRANSCRIPTION_SERVICE_URL || '';
const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
const SAMPLE_RATE = 16000, MIXED = 999;

process.on('uncaughtException', (e) => console.error('[live-stack] uncaught:', (e as any)?.message || e));
process.on('unhandledRejection', (e) => console.error('[live-stack] rejection:', (e as any)?.message || e));

interface Seg { segment_id: string; speaker: string; text: string; start: number; absolute_start_time: string; completed: boolean }
const store = new Map<string, Map<string, Seg>>();   // metaKey → segment_id → CONFIRMED seg (history)
const liveClients = new Set<WebSocket>();
const keyOf = (p: string, n: string) => `${p}/${n}`;
const toSeg = (c: ChunkSegment, speaker: string, completed: boolean): Seg => ({
  segment_id: c.segmentId, speaker, text: c.text, start: c.startMs / 1000,
  absolute_start_time: new Date(c.startMs).toISOString(), completed,
});

/** Map a ChunkedTranscriber emit onto the sidepanel's frozen envelope. */
function broadcast(metaKey: string, speaker: string, confirmed: Seg[], pending: Seg[]) {
  if (confirmed.length) {
    const m = store.get(metaKey) || store.set(metaKey, new Map()).get(metaKey)!;
    for (const s of confirmed) m.set(s.segment_id, s);
    for (const s of confirmed) console.log(`  \x1b[32m[${speaker}]\x1b[0m ${s.text}`);
  } else if (pending.length) {
    console.log(`  \x1b[2m[${speaker}] …${pending.map((p) => p.text).join(' ')}\x1b[0m`);
  }
  const msg = JSON.stringify({ type: 'transcript', speaker, confirmed, pending });
  for (const c of liveClients) if (c.readyState === WebSocket.OPEN) c.send(msg);
}

// ── gateway (8056): history + status + live WS ──
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'X-API-Key, Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
const gatewayHttp = http.createServer((req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end(); }
  const url = new URL(req.url || '', `http://localhost:${GATEWAY_PORT}`);
  res.setHeader('Content-Type', 'application/json'); for (const [k, v] of Object.entries(CORS)) res.setHeader(k, v);
  const m = url.pathname.match(/^\/transcripts\/([^/]+)\/([^/]+)/);
  if (m) return res.end(JSON.stringify({ segments: [...(store.get(keyOf(m[1], m[2]))?.values() || [])] }));
  if (url.pathname === '/bots') return res.end(JSON.stringify([]));
  res.writeHead(404); res.end('{}');
});
new WebSocketServer({ server: gatewayHttp, path: '/ws' }).on('connection', (ws) => {
  liveClients.add(ws);
  ws.on('message', (d) => { try { if (JSON.parse(d.toString()).action === 'subscribe') ws.send(JSON.stringify({ type: 'subscribed' })); } catch {} });
  ws.on('close', () => liveClients.delete(ws));
});
gatewayHttp.listen(GATEWAY_PORT, () => console.log(`[live-stack] gateway  http://localhost:${GATEWAY_PORT}  (/transcripts /bots /ws)`));

// ── ingest (9099): capture.v1 → ChunkedTranscriber → envelope ──
const txClient = TX_URL ? new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 }) : null;
const ingest = new WebSocketServer({ port: INGEST_PORT });
console.log(`[live-stack] ingest   ws://localhost:${INGEST_PORT}/ingest   STT: ${TX_URL || 'NONE'}`);

ingest.on('connection', async (ws, req) => {
  const url = new URL(req.url || '', `http://localhost:${INGEST_PORT}`);
  const platform = url.searchParams.get('platform') || 'unknown';
  const nativeId = url.searchParams.get('native_meeting_id') || '?';
  const language = url.searchParams.get('language');
  const metaKey = keyOf(platform, nativeId);
  console.log(`[live-stack] ▶ ${metaKey}`);

  const lang = language && language !== 'auto' ? language : undefined; // explicit lang stops Whisper mis-detecting short clips
  const transcribe = async (pcm: Float32Array, prompt?: string) => { if (!txClient) throw new Error('no STT'); return txClient.transcribe(pcm, lang, prompt); };

  // Remote (channel 999) → diarized; ChunkedTranscriber's internal binder resolves
  // `speaker` from the active-speaker hints (else the cluster id). Confirmed + PENDING.
  const tc = await ChunkedTranscriber.create({
    language: lang, transcribe,
    publish: (speaker, confirmed, pending) =>
      broadcast(metaKey, speaker, confirmed.map((c) => toSeg(c, speaker, true)), pending.map((p) => toSeg(p, speaker, false))),
    publishPending: (speaker, pending) => broadcast(metaKey, speaker, [], pending.map((p) => toSeg(p, speaker, false))),
    clearPending: (speaker) => broadcast(metaKey, speaker, [], []),
    rename: (_old, next, segs) => broadcast(metaKey, next, segs.map((s) => toSeg(s, next, true)), []),
    log: (m) => console.log(`  \x1b[2m${m}\x1b[0m`),
  });

  // Mic ("You") → its OWN ChunkedTranscriber so it gets the same confirmed + PENDING
  // flow as the remote (SpeakerStreamManager is confirm-only — no pending). The label
  // is forced to "You"; the diarizer's cluster id is irrelevant for a known speaker.
  const micTc = await ChunkedTranscriber.create({
    language: lang, transcribe,
    publish: (_s, confirmed, pending) => broadcast(metaKey, 'You', confirmed.map((c) => toSeg(c, 'You', true)), pending.map((p) => toSeg(p, 'You', false))),
    publishPending: (_s, pending) => broadcast(metaKey, 'You', [], pending.map((p) => toSeg(p, 'You', false))),
    clearPending: () => broadcast(metaKey, 'You', [], []),
    rename: () => { /* always "You" */ },
    log: () => { /* quiet */ },
  });

  // MULTISTREAM path (Google Meet): each participant is a SEPARATE channel
  // (0,1,2,…) of KNOWN identity → SpeakerStreamManager, named by speaker-joined
  // events. No diarization (the channel IS the speaker). This is the gmeet
  // strategy; zoom/teams never produce these channels (they're mixed → 999).
  const multi = new SpeakerStreamManager({ sampleRate: SAMPLE_RATE, minAudioDuration: 3, submitInterval: 3, confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15 });
  const chanName = new Map<number, string>();   // channel index → latest resolved name
  const added = new Set<number>();
  multi.onSegmentReady = async (sid, _n, audio) => {
    try { if (!txClient) return multi.handleTranscriptionResult(sid, ''); const r = await txClient.transcribe(audio, lang); multi.handleTranscriptionResult(sid, (r?.text || '').trim(), r?.segments?.[r.segments.length - 1]?.end); }
    catch { multi.handleTranscriptionResult(sid, ''); }
  };
  multi.onSegmentConfirmed = (sid, name, text) => {
    if (!text.trim()) return;
    const idx = Number(sid.replace('ch-', ''));
    const spk = chanName.get(idx) || name || sid;
    broadcast(metaKey, spk, [{ segment_id: `${metaKey}:${sid}:${Date.now()}`, speaker: spk, text, start: Date.now() / 1000, absolute_start_time: new Date().toISOString(), completed: true }], []);
  };

  ws.send(JSON.stringify({ type: 'ready', meeting_id: nativeId }));
  let mixedF = 0, micF = 0, otherF = 0, hints = 0; const seen = new Set<number>();
  ws.on('message', (data: any, isBinary: boolean) => {
    try {
      const b = Buffer.from(data);
      if (isBinary) {
        const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength); if (!f) return;
        if (!seen.has(f.speakerIndex)) { seen.add(f.speakerIndex); console.log(`[live-stack] channel ${f.speakerIndex}${f.speakerIndex === MIXED ? ' = MIXED → diarizer' : f.speakerIndex === 1000 ? ' = MIC → "You"' : ''}`); }
        if (f.speakerIndex === MIXED) { mixedF++; tc.feedAudio(f.samples, f.ts); }
        else if (f.speakerIndex === 1000) { micF++; micTc.feedAudio(f.samples, f.ts); }
        else { // per-participant (gmeet multistream)
          otherF++;
          const id = `ch-${f.speakerIndex}`;
          if (!added.has(f.speakerIndex)) { added.add(f.speakerIndex); multi.addSpeaker(id, chanName.get(f.speakerIndex) || `Speaker ${f.speakerIndex + 1}`); }
          multi.feedAudio(id, f.samples);
        }
        return;
      }
      const ev = decodeEvent(b.toString('utf8'));
      if (ev?.kind === 'active-speaker' && ev.speaker) { hints++; tc.recordHint(ev.speaker, (ev.detail?.hint as any) || 'dom-active', ev.ts, !!(ev.detail as any)?.isEnd); }
      // speaker-joined carries the channel→name map for the multistream path.
      else if (ev?.kind === 'speaker-joined' && (ev.detail as any)?.index != null) {
        const idx = Number((ev.detail as any).index);
        if (idx !== 1000 && idx !== MIXED && ev.speaker) {
          chanName.set(idx, ev.speaker);
          if (!added.has(idx)) { added.add(idx); multi.addSpeaker(`ch-${idx}`, ev.speaker); }
        }
      }
    } catch (e: any) { console.error('[live-stack] msg:', e?.message); }
  });
  const hb = setInterval(() => console.log(`[live-stack] \x1b[36m· ${metaKey}  mixed=${mixedF}f mic=${micF}f other=${otherF}f hints=${hints} channels=[${[...seen].join(',')}]\x1b[0m`), 5000);
  const finish = async () => { clearInterval(hb); try { await tc.dispose(); } catch {} try { await micTc.dispose(); } catch {} try { (multi as any).destroy?.(); } catch {} console.log(`[live-stack] ■ ${metaKey} closed`); };
  ws.on('close', finish); ws.on('error', finish);
});
