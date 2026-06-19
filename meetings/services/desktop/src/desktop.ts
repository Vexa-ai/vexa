/**
 * @vexa/desktop — the meetings ALL-IN-ONE host (dual-lane: gmeet + mixed).
 *
 * The data plane composed into ONE process — no Docker / Postgres / Redis:
 *
 *   capture.v1 ─► ingest WS (:9099)
 *      ├─ decode frames        @vexa/capture-codec
 *      ├─ gmeet (per-channel) ─► @vexa/gmeet-pipeline (channel-routed, glow name ON the audio frame)
 *      ├─ mixed (zoom/teams/  ─► @vexa/mixed-pipeline (mix=ch999 + "You"=ch1000, pyannote-cut, named
 *      │   youtube)                                   from active-speaker HINTS on EVENT frames)
 *      ├─ STT egress           @vexa/transcribe-whisper (stt.v1)
 *      ├─ recording.v1     ─►   RecordingSink port → @vexa/recording buildRecordingMaster → file
 *      │   (same ingest WS; decodeRecordingChunk discriminates it from an audio frame)
 *      └─ store + deliver  ─►   in-memory + gateway
 *   gateway (:8056): POST /extension/sessions · GET /bots · GET /transcripts/{p}/{n}
 *                  · GET /recordings/{p}/{n} (serve the assembled master) · WS /ws
 *
 * The SAME bricks the cloud splits across meeting-api + collector + gateway, composed as one
 * deployable — "a service is internally a modular monolith." Exposes startDesktop() so the eval
 * loop can drive it in-process. STT is the real backend (TRANSCRIPTION_SERVICE_URL/_TOKEN).
 */
import * as http from 'node:http';
import { createWriteStream, mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { WebSocketServer, WebSocket } from 'ws';
import { TranscriptionClient } from '@vexa/transcribe-whisper';
import { createGmeetPipeline, type TranscriptSegment } from '@vexa/gmeet-pipeline';
import { ChunkedTranscriber, type ChunkSegment, type HintKind } from '@vexa/mixed-pipeline';
import { decodeAudioFrame, decodeRecordingChunk } from '@vexa/capture-codec';
import { createRecordingSink, type RecordingMaster } from './recording-sink.js';

const SAMPLE_RATE = 16000;
const REC_CONTENT_TYPE: Record<string, string> = { wav: 'audio/wav', webm: 'audio/webm' };
const MIXED_CHANNEL = 999, MIC_CHANNEL = 1000;   // mixed lane: one mixed remote stream + the local "You" mic
const MIXED_PLATFORMS = new Set(['zoom', 'teams', 'msteams', 'youtube']);   // everything else (google_meet, …) → gmeet

interface Meeting { id: number; platform: string; native_meeting_id: string; status: string; start_time: string; segments: TranscriptSegment[]; }
export interface DesktopOptions { ingestPort?: number; gatewayPort?: number; txUrl?: string; txToken?: string; quiet?: boolean; recordingsDir?: string; }
export interface Desktop { ingestPort: number; gatewayPort: number; recordingsDir: string; close(): Promise<void>; }

export async function startDesktop(opts: DesktopOptions = {}): Promise<Desktop> {
  const log = opts.quiet ? (_m: string) => { /* */ } : (m: string) => console.log(m);
  const TX_URL = opts.txUrl ?? process.env.TRANSCRIPTION_SERVICE_URL ?? '';
  const TX_TOKEN = opts.txToken ?? process.env.TRANSCRIPTION_SERVICE_TOKEN ?? '';
  const txClient = TX_URL ? new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 }) : null;

  // In-memory store (single-process; sqlite is a later refinement).
  const meetings = new Map<string, Meeting>();
  let nextId = 1;
  const keyOf = (p: string, n: string) => `${p}/${n}`;

  // ── recording.v1 receiver (P5) — the desktop is the LOCAL receiver (ADR-0005). ──
  // The DISK + SERVE adapter lives HERE (the composition root); the RecordingSink
  // port (recording-sink.ts) holds the pure assembly. On is_final the port assembles
  // the master via @vexa/recording buildRecordingMaster and calls onMaster → we write
  // the file (recordingsDir, env-configurable) + remember its path for the gateway GET.
  const recordingsDir = opts.recordingsDir ?? process.env.VEXA_RECORDINGS_DIR ?? join(process.cwd(), '.recordings');
  try { mkdirSync(recordingsDir, { recursive: true }); } catch { /* best-effort */ }
  const recordings = new Map<string, { path: string; format: string }>();   // key → on-disk master
  const recSink = createRecordingSink({
    log,
    onMaster: (m: RecordingMaster) => {
      const safe = m.key.replace(/[^a-zA-Z0-9._-]/g, '_');   // platform/native → filesystem-safe name
      const path = join(recordingsDir, `${safe}.${m.format}`);
      try { writeFileSync(path, m.bytes); recordings.set(m.key, { path, format: m.format }); log(`[desktop] ⏹ recording master → ${path} (${m.bytes.length}B)`); }
      catch (e: any) { log(`[desktop] recording write FAILED for ${m.key}: ${e?.message || e}`); }
    },
  });
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

  // Mixed lane: a ChunkedTranscriber emit (speaker + confirmed/pending ChunkSegment batches) → the
  // same transcript.v1 envelope. Confirmed + pending MUST travel together (a confirm with empty
  // pending deletes the client's draft block). ChunkSegment start/endMs are audio-time ms.
  const toTx = (c: ChunkSegment, speaker: string, completed: boolean): TranscriptSegment => ({
    segment_id: c.segmentId, speaker, text: c.text, start: c.startMs / 1000, end: c.endMs / 1000, language: c.language, completed,
  });
  const broadcastBatch = (key: string, speaker: string, confirmed: TranscriptSegment[], pending: TranscriptSegment[]) => {
    const m = meetings.get(key);
    if (m) for (const s of confirmed) m.segments.push(s);
    for (const s of confirmed) log(`  [${speaker}] ${s.text}`);
    const msg = JSON.stringify({ type: 'transcript', meeting: key, speaker, confirmed, pending });
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
    // GET /recordings/{p}/{n} — serve the assembled recording.v1 master (the disk
    // ADAPTER's read side; the all-in-one path's equivalent of meeting-api's file serve).
    const rec = url.pathname.match(/^\/recordings\/([^/]+)\/([^/]+)/);
    if (req.method === 'GET' && rec) {
      const entry = recordings.get(keyOf(decodeURIComponent(rec[1]), decodeURIComponent(rec[2])));
      if (!entry || !existsSync(entry.path)) return send({ error: 'no recording', platform: decodeURIComponent(rec[1]), native_meeting_id: decodeURIComponent(rec[2]) }, 404);
      const body = readFileSync(entry.path);
      res.writeHead(200, { 'Content-Type': REC_CONTENT_TYPE[entry.format] || 'application/octet-stream', 'Content-Length': body.length, ...CORS });
      return res.end(body);
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
  ingest.on('connection', async (ws, req) => {
    const url = new URL(req.url || '', 'http://localhost');
    const platform = url.searchParams.get('platform') || 'unknown';
    const native = url.searchParams.get('native_meeting_id') || '?';
    const language = url.searchParams.get('language');
    const key = keyOf(platform, native);
    resolve(platform, native);
    const lang = language && language !== 'auto' ? language : undefined;
    const transcribe = async (pcm: Float32Array, prompt?: string) => { if (!txClient) throw new Error('no STT (set TRANSCRIPTION_SERVICE_URL)'); return txClient.transcribe(pcm, lang, prompt); };
    const isMixed = MIXED_PLATFORMS.has(platform);

    // ── Raw-signal tape recorder (env-gated VEXA_RECORD_TAPE=<dir>) ──
    // Append this session's VERBATIM capture.v1 ingest stream — binary audio frames
    // (ch999 mix / ch1000 mic / per-channel gmeet) + text event hints (active-speaker)
    // — to a JSONL tape, so any live bug can be replayed deterministically with no
    // meeting (eval.sh replay <tape>). A SECOND 'message' listener that never touches
    // the pipeline path. Off unless VEXA_RECORD_TAPE is set.
    let tape: ReturnType<typeof createWriteStream> | null = null;
    if (process.env.VEXA_RECORD_TAPE) {
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      const tapePath = `${process.env.VEXA_RECORD_TAPE}/tape-${platform}-${native}-${stamp}.jsonl`;
      tape = createWriteStream(tapePath);
      tape.write(JSON.stringify({ v: 1, platform, native, language: lang ?? null, startedAt: new Date().toISOString() }) + '\n');
      const tape0 = Date.now();
      ws.on('message', (data: any, isBinary: boolean) => {
        if (!tape) return;
        const t = Date.now() - tape0;
        if (isBinary) { const b = Buffer.from(data); tape.write(JSON.stringify({ t, bin: true, d: b.toString('base64') }) + '\n'); }
        else tape.write(JSON.stringify({ t, bin: false, d: data.toString() }) + '\n');
      });
      log(`[desktop] ⏺ recording raw tape → ${tapePath}`);
    }

    // ── MIXED lane (zoom/teams/youtube): one mixed remote stream (ch 999) + the local "You" mic
    //    (ch 1000), each its OWN ChunkedTranscriber. pyannote cuts; the speaker is the max-overlap
    //    active-speaker HINT (event frames → recordHint). No per-channel streams, no diarization. ──
    let tc: ChunkedTranscriber | null = null, micTc: ChunkedTranscriber | null = null;
    // ── GMEET lane (per-channel): the glow name rides the audio frame; the pipeline routes by channel. ──
    let pipe: ReturnType<typeof createGmeetPipeline> | null = null;

    // seg_N is the pipeline's PROVISIONAL cluster id (an as-yet unattributed turn). On
    // YouTube (single upstream, no participant identities) that placeholder is the intended
    // label — it shows how segments split. On Teams/Zoom an unattributed turn must publish
    // an EMPTY speaker, never a seg_N (a wrong identity leaking through). Map at the wire
    // only; seg_N stays the internal key for late re-resolution / repaint.
    const showSp = (sp: string) => (platform !== 'youtube' && /^seg_\d+$/.test(sp)) ? '' : sp;

    if (isMixed && txClient) {
      tc = await ChunkedTranscriber.create({
        language: lang, transcribe,
        publish: (sp, conf, pend) => { const s = showSp(sp); broadcastBatch(key, s, conf.map((c) => toTx(c, s, true)), pend.map((c) => toTx(c, s, false))); },
        publishPending: (sp, pend) => { const s = showSp(sp); broadcastBatch(key, s, [], pend.map((c) => toTx(c, s, false))); },
        clearPending: (sp) => broadcastBatch(key, showSp(sp), [], []),
        rename: (_old, next, segs) => { const s = showSp(next); broadcastBatch(key, s, segs.map((c) => toTx(c, s, true)), []); },
        log: (m) => log(`  ${m}`),
      });
      micTc = await ChunkedTranscriber.create({
        language: lang, transcribe,
        publish: (_s, conf, pend) => broadcastBatch(key, 'You', conf.map((c) => toTx(c, 'You', true)), pend.map((c) => toTx(c, 'You', false))),
        publishPending: (_s, pend) => broadcastBatch(key, 'You', [], pend.map((c) => toTx(c, 'You', false))),
        clearPending: () => broadcastBatch(key, 'You', [], []),
        rename: () => { /* the local mic is always "You" */ },
        log: () => { /* quiet */ },
      });
    } else if (txClient) {
      pipe = createGmeetPipeline({
        transcribe,
        config: { sampleRate: SAMPLE_RATE, minAudioDuration: 2, submitInterval: 1.5, confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15 },
        sink: { segment: (t) => broadcast(key, t), draft: (t) => { if (t.text.trim()) broadcast(key, t); }, finalize: () => { /* live */ } },
      });
    }
    let mixF = 0, micF = 0, evHints = 0, recF = 0;
    const hb = isMixed ? setInterval(() => log(`  [hb ${key}] ch999=${mixF}f ch1000=${micF}f hints=${evHints} rec=${recF}`), 5000) : null;
    log(`[desktop] ▶ ${key} (${isMixed ? 'mixed' : 'gmeet'})`);
    ws.send(JSON.stringify({ type: 'ready' }));
    // recording.v1 branch — try decodeRecordingChunk FIRST on every binary frame.
    // It returns null on a capture AUDIO frame (the REC1-magic is the built-in
    // discriminator), non-null on a recording chunk → route to the RecordingSink
    // and report handled, so the audio path is skipped. Same wire, two frame types.
    const tryRecording = (b: Buffer): boolean => {
      const r = decodeRecordingChunk(b.buffer, b.byteOffset, b.byteLength);
      if (!r) return false;
      recF++;
      recSink.chunk(key, r.seq, r.isFinal, r.format, r.bytes);
      return true;
    };
    ws.on('message', (data: any, isBinary: boolean) => {
      if (isMixed) {
        if (isBinary) {
          const b = Buffer.from(data);
          if (tryRecording(b)) return;                                       // recording.v1 chunk — not transcription audio
          const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength);   // capture.v1 audio frame
          if (f) { if (f.speakerIndex === MIXED_CHANNEL) { mixF++; tc?.feedAudio(f.samples, f.ts); } else if (f.speakerIndex === MIC_CHANNEL) { micF++; micTc?.feedAudio(f.samples, f.ts); } }
        } else {                          // mixed lane: the WHO signal rides EVENT frames (active-speaker hints)
          try { const ev = JSON.parse(data.toString()); if (ev?.kind === 'active-speaker' && ev.speaker) { evHints++; tc?.recordHint(ev.speaker, (ev.detail?.hint as HintKind) || 'dom-active', ev.ts ?? ev.tMs ?? 0, !!ev.detail?.isEnd); } } catch { /* */ }
        }
        return;
      }
      if (!isBinary) return;            // gmeet: the glow name rides on the audio frame; events ignored
      const b = Buffer.from(data);
      if (tryRecording(b)) return;                                         // recording.v1 chunk — not transcription audio
      const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength);   // capture.v1 audio frame
      if (f) pipe?.feedAudio(f.speakerIndex, f.speakerName, f.samples, f.ts);   // route by CHANNEL, name = glow at capture
    });
    const finish = async () => { if (hb) clearInterval(hb); tape?.end(); recSink.close(key); try { await tc?.dispose(); await micTc?.dispose(); await pipe?.dispose(); } catch { /* */ } const m = meetings.get(key); if (m) m.status = 'completed'; log(`[desktop] ■ ${key}`); };
    ws.on('close', finish);
    ws.on('error', finish);
  });

  log(`[desktop] ingest ws://localhost:${ingestPort}/ingest · gateway http://localhost:${gatewayPort} · STT ${TX_URL || 'NONE'} · recordings ${recordingsDir}`);
  return {
    ingestPort, gatewayPort, recordingsDir,
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
