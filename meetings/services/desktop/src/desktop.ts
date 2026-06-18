/**
 * @vexa/desktop — the meetings ALL-IN-ONE host (gmeet subset).
 *
 * The data plane composed into ONE process — no Docker / Postgres / Redis:
 *
 *   capture.v1 ─► ingest WS (:9099)
 *      ├─ decode frames        @vexa/capture-codec
 *      ├─ gmeet channels  ─►   @vexa/gmeet-pipeline   (channel-routed, glow-named)
 *      ├─ STT egress           @vexa/transcribe-whisper (stt.v1)
 *      └─ store + deliver  ─►   in-memory + gateway
 *   gateway (:8056): POST /extension/sessions · GET /bots · GET /transcripts/{p}/{n} · WS /ws
 *
 * The SAME bricks the cloud splits across meeting-api + collector + gateway, composed as one
 * deployable — "a service is internally a modular monolith." Exposes startDesktop() so the eval
 * loop can drive it in-process. STT is the real backend (TRANSCRIPTION_SERVICE_URL/_TOKEN).
 */
import * as http from 'node:http';
import { WebSocketServer, WebSocket } from 'ws';
import { TranscriptionClient } from '@vexa/transcribe-whisper';
import { createGmeetPipeline, type TranscriptSegment } from '@vexa/gmeet-pipeline';
import { decodeAudioFrame } from '@vexa/capture-codec';

const SAMPLE_RATE = 16000;

interface Meeting { id: number; platform: string; native_meeting_id: string; status: string; start_time: string; segments: TranscriptSegment[]; }
export interface DesktopOptions { ingestPort?: number; gatewayPort?: number; txUrl?: string; txToken?: string; quiet?: boolean; }
export interface Desktop { ingestPort: number; gatewayPort: number; close(): Promise<void>; }

export async function startDesktop(opts: DesktopOptions = {}): Promise<Desktop> {
  const log = opts.quiet ? (_m: string) => { /* */ } : (m: string) => console.log(m);
  const TX_URL = opts.txUrl ?? process.env.TRANSCRIPTION_SERVICE_URL ?? '';
  const TX_TOKEN = opts.txToken ?? process.env.TRANSCRIPTION_SERVICE_TOKEN ?? '';
  const txClient = TX_URL ? new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 }) : null;

  // In-memory store (single-process; sqlite is a later refinement).
  const meetings = new Map<string, Meeting>();
  let nextId = 1;
  const keyOf = (p: string, n: string) => `${p}/${n}`;
  const resolve = (p: string, n: string): Meeting => {
    const k = keyOf(p, n);
    let m = meetings.get(k);
    if (!m) { m = { id: nextId++, platform: p, native_meeting_id: n, status: 'active', start_time: new Date().toISOString(), segments: [] }; meetings.set(k, m); }
    return m;
  };

  // Live delivery (WS) + persist CONFIRMED to the store.
  const liveClients = new Map<WebSocket, Set<string>>();
  const broadcast = (key: string, seg: TranscriptSegment) => {
    const m = meetings.get(key);
    if (seg.completed && m) m.segments.push(seg);
    if (seg.completed) log(`  [${seg.speaker}] ${seg.text}`);
    const msg = JSON.stringify({ type: 'transcript', meeting: key, confirmed: seg.completed ? [seg] : [], pending: seg.completed ? [] : [seg] });
    for (const [c, keys] of liveClients) if (c.readyState === WebSocket.OPEN && (keys.size === 0 || keys.has(key))) c.send(msg);
  };

  // ── gateway (control plane + history + live WS) ──
  const CORS: Record<string, string> = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'X-API-Key, Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
  const readBody = (req: http.IncomingMessage): Promise<any> => new Promise((r) => { let b = ''; req.on('data', (c) => (b += c)); req.on('end', () => { try { r(b ? JSON.parse(b) : {}); } catch { r({}); } }); });
  const gateway = http.createServer(async (req, res) => {
    const send = (o: any, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json', ...CORS }); res.end(JSON.stringify(o)); };
    if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end(); }
    const url = new URL(req.url || '', 'http://localhost');
    if (req.method === 'POST' && url.pathname === '/extension/sessions') {
      const b = await readBody(req);
      const m = resolve(b.platform || 'unknown', b.native_meeting_id || b.native_id || '?');
      return send({ meeting_id: m.id, platform: m.platform, native_meeting_id: m.native_meeting_id, token: 'local' });
    }
    if (req.method === 'GET' && url.pathname === '/bots') return send({ meetings: [...meetings.values()], has_more: false });
    const tr = url.pathname.match(/^\/transcripts\/([^/]+)\/([^/]+)/);
    if (req.method === 'GET' && tr) {
      const m = meetings.get(keyOf(decodeURIComponent(tr[1]), decodeURIComponent(tr[2])));
      return send(m ?? { id: 0, platform: decodeURIComponent(tr[1]), native_meeting_id: decodeURIComponent(tr[2]), status: 'unknown', segments: [] });
    }
    send({ error: 'not found', path: url.pathname }, 404);
  });
  const wss = new WebSocketServer({ server: gateway, path: '/ws' });
  wss.on('connection', (ws) => {
    liveClients.set(ws, new Set());
    ws.on('message', (d) => { try { const m = JSON.parse(d.toString()); if (m.action === 'subscribe') { liveClients.set(ws, new Set((m.meetings || []).map((x: any) => `${x.platform}/${x.native_id ?? x.native_meeting_id}`))); ws.send(JSON.stringify({ type: 'subscribed' })); } } catch { /* */ } });
    ws.on('close', () => liveClients.delete(ws));
  });
  await new Promise<void>((r) => gateway.listen(opts.gatewayPort ?? 8056, () => r()));
  const gatewayPort = (gateway.address() as { port: number }).port;

  // ── ingest (capture.v1 → gmeet-pipeline → broadcast) ──
  const ingest = new WebSocketServer({ port: opts.ingestPort ?? 9099 });
  await new Promise<void>((r) => ingest.on('listening', () => r()));
  const ingestPort = (ingest.address() as { port: number }).port;
  ingest.on('connection', (ws, req) => {
    const url = new URL(req.url || '', 'http://localhost');
    const platform = url.searchParams.get('platform') || 'unknown';
    const native = url.searchParams.get('native_meeting_id') || '?';
    const language = url.searchParams.get('language');
    const key = keyOf(platform, native);
    resolve(platform, native);
    const lang = language && language !== 'auto' ? language : undefined;
    const transcribe = async (pcm: Float32Array, prompt?: string) => { if (!txClient) throw new Error('no STT (set TRANSCRIPTION_SERVICE_URL)'); return txClient.transcribe(pcm, lang, prompt); };
    const pipe = txClient ? createGmeetPipeline({
      transcribe,
      config: { sampleRate: SAMPLE_RATE, minAudioDuration: 2, submitInterval: 1.5, confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15 },
      sink: { segment: (t) => broadcast(key, t), draft: (t) => { if (t.text.trim()) broadcast(key, t); }, finalize: () => { /* live */ } },
    }) : null;
    log(`[desktop] ▶ ${key}`);
    ws.send(JSON.stringify({ type: 'ready' }));
    ws.on('message', (data: any, isBinary: boolean) => {
      if (!isBinary) return;            // gmeet subset: the glow name rides on the audio frame; events ignored
      const b = Buffer.from(data);
      const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength);   // capture.v1 audio frame
      if (f) pipe?.feedAudio(f.speakerIndex, f.speakerName, f.samples, f.ts);   // route by CHANNEL, name = glow at capture
    });
    const finish = async () => { try { await pipe?.dispose(); } catch { /* */ } const m = meetings.get(key); if (m) m.status = 'completed'; log(`[desktop] ■ ${key}`); };
    ws.on('close', finish);
    ws.on('error', finish);
  });

  log(`[desktop] ingest ws://localhost:${ingestPort}/ingest · gateway http://localhost:${gatewayPort} · STT ${TX_URL || 'NONE'}`);
  return {
    ingestPort, gatewayPort,
    close: async () => {
      for (const c of wss.clients) c.terminate();
      wss.close();
      ingest.close();
      gateway.closeAllConnections?.();   // force-drop keep-alive (fetch) conns so close() resolves
      await new Promise<void>((r) => gateway.close(() => r()));
    },
  };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  startDesktop().catch((e) => { console.error(e); process.exit(1); });
}
