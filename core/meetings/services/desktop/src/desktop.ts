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
import { ownerOnly, type CanAccess } from './access.js';

const SAMPLE_RATE = 16000;

/** The gmeet lane's cadence, read from the SAME BOT_SPEAKER_* knobs the shipped bot reads
 *  (services/bot/src/config.ts), defaulting to the SAME values the lane itself defaults to.
 *  The desktop is the hot loop people judge latency and fragmentation in, so it has to run the
 *  configuration that ships — a desktop-only cadence makes every observation describe a
 *  deployment nobody has. */
const speakerLaneConfig = () => ({
  sampleRate: SAMPLE_RATE,
  minAudioDuration: Number(process.env.BOT_SPEAKER_MIN_AUDIO_SEC ?? 2),
  submitInterval: Number(process.env.BOT_SPEAKER_SUBMIT_INTERVAL_SEC ?? 2),
  confirmThreshold: Number(process.env.BOT_SPEAKER_CONFIRM_THRESHOLD ?? 2),
  maxBufferDuration: Number(process.env.BOT_SPEAKER_MAX_BUFFER_SEC ?? 30),
  idleTimeoutSec: Number(process.env.BOT_SPEAKER_IDLE_TIMEOUT_SEC ?? 15),
});
const REC_CONTENT_TYPE: Record<string, string> = { wav: 'audio/wav', webm: 'audio/webm' };
const MIXED_CHANNEL = 999, MIC_CHANNEL = 1000;   // mixed lane: one mixed remote stream + the local "You" mic
const MIXED_PLATFORMS = new Set(['zoom', 'teams', 'msteams', 'youtube']);   // everything else (google_meet, …) → gmeet

// Minimal HTML escape for the few values we interpolate into the player page
// (platform / native id are user-influenced) — never trust them in markup.
const escHtml = (s: string): string =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');

// A self-contained HTML5 <audio> player for an assembled recording.v1 master.
// Dependency-free (one inline HTML string, no JS framework) — it simply points an
// <audio> element at the bytes route. `src` is a SAME-ORIGIN absolute path, so no
// escaping beyond the attribute quote is needed; platform/native are escaped.
function recordingPlayerPage(platform: string, native: string, src: string, format?: string): string {
  const title = `Vexa recording — ${escHtml(platform)}/${escHtml(native)}`;
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${title}</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 0; padding: 2rem; }
  main { max-width: 40rem; margin: 0 auto; }
  h1 { font-size: 1.1rem; font-weight: 600; }
  .meta { color: #888; font-size: .85rem; margin-bottom: 1rem; word-break: break-all; }
  audio { width: 100%; }
  a.dl { display: inline-block; margin-top: 1rem; font-size: .9rem; }
</style>
</head>
<body>
<main>
  <h1>${escHtml(platform)} — ${escHtml(native)}</h1>
  <p class="meta">recording.v1 master${format ? ` · ${escHtml(format)}` : ''}</p>
  <audio controls preload="metadata" src="${src}"></audio>
  <a class="dl" href="${src}" download>Download</a>
</main>
</body>
</html>`;
}

interface Meeting { id: number; platform: string; native_meeting_id: string; status: string; start_time: string; segments: TranscriptSegment[]; }

/** UPSERT a confirmed segment into the store by segment_id. A repaint/rename
 *  (late-box claim, cluster re-resolve) re-publishes the SAME id with the corrected
 *  speaker — replace in place (preserving order) so the persisted transcript matches
 *  what live WS clients show, instead of keeping the stale provisional copy (empty
 *  speaker) AS WELL AS the named one (the duplicate GET /transcripts was returning). */
export function upsertSegment(segments: TranscriptSegment[], seg: TranscriptSegment): void {
  const i = segments.findIndex((x) => x.segment_id === seg.segment_id);
  if (i >= 0) segments[i] = seg; else segments.push(seg);
}
export interface DesktopOptions { ingestPort?: number; gatewayPort?: number; txUrl?: string; txToken?: string; quiet?: boolean; recordingsDir?: string; noSignalMs?: number; canAccess?: CanAccess; }
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

  // ── P20 / ADR-0012 — complete mediation: every read path consults canAccess ──
  // The SEAM (default owner-only = allow-all on the single-user localhost; real
  // grants land additively behind this same port — ADR-0003). Resolved ONCE here.
  // Subject is derived from the request: the X-API-Key header on HTTP routes, the
  // api_key query param on the headerless /ws connection, else 'local'.
  const canAccess: CanAccess = opts.canAccess ?? ownerOnly;
  const subjectOf = (req: http.IncomingMessage): string => {
    const h = req.headers['x-api-key'];
    const v = Array.isArray(h) ? h[0] : h;
    return v && v.length ? v : 'local';
  };

  // Telemetry ring buffer — the extension POSTs merged diagnostics (page
  // attribution state + session state + per-track frame counters) every ~10s
  // while a session is live (background.ts shipTelemetry). We keep the last N
  // entries in memory so a live attribution bug is inspectable via GET /telemetry
  // with no client-side copy-paste, and /telemetry NEVER 404s (telemetry must
  // not break capture). Secrets/transcript text never enter telemetry (the
  // client scrubs DOM free-text before sending).
  const TELEMETRY_MAX = 200;
  const telemetry: Array<{ at: string; reason: string; body: any }> = [];

  // ── recording.v1 receiver (P5) — the desktop is the LOCAL receiver (ADR-0005). ──
  // The DISK + SERVE adapter lives HERE (the composition root); the RecordingSink
  // port (recording-sink.ts) holds the pure assembly. On is_final the port assembles
  // the master via @vexa/recording buildRecordingMaster and calls onMaster → we write
  // the file (recordingsDir, env-configurable) + remember its path for the gateway GET.
  const recordingsDir = opts.recordingsDir ?? process.env.VEXA_RECORDINGS_DIR ?? join(process.cwd(), '.recordings');
  try { mkdirSync(recordingsDir, { recursive: true }); } catch { /* best-effort */ }
  const recordings = new Map<string, { path: string; format: string }>();   // key → on-disk master
  // Active ingest sessions by meeting key → a finalizer the gateway's
  // POST /extension/sessions/end can invoke to end the session NOW (dispose the
  // pipeline, flush the recording, mark completed) without waiting for the WS to
  // close on its reconnect grace. Registered by each ingest connection.
  const activeSessions = new Map<string, () => Promise<void>>();
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
  // Persist a CONFIRMED segment, UPSERTING by segment_id. A later repaint/rename
  // (late-box claim, cluster re-resolve) re-publishes the SAME id with the corrected
  // speaker — replace in place so the store matches what live WS clients show (they
  // upsert by id) instead of accumulating the stale provisional copy (empty speaker)
  // ALONGSIDE the named one — the "ghost duplicate" GET /transcripts was returning.
  const persist = (m: Meeting | undefined, seg: TranscriptSegment): void => {
    if (m) upsertSegment(m.segments, seg);
  };
  const broadcast = (key: string, seg: TranscriptSegment) => {
    const m = meetings.get(key);
    if (seg.completed) persist(m, seg);
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
    for (const s of confirmed) persist(m, s);
    for (const s of confirmed) log(`  [${speaker}] ${s.text}`);
    const msg = JSON.stringify({ type: 'transcript', meeting: key, speaker, confirmed, pending });
    for (const [c, keys] of liveClients) if (c.readyState === WebSocket.OPEN && (keys.size === 0 || keys.has(key))) c.send(msg);
  };

  // ── P18 (fail loud + attributable): surface an engine/dependency FAULT — e.g. an STT
  // 402 "out of balance" — on an observable, attributed channel (log · /telemetry · a /ws
  // `health` frame) instead of degrading silently to "no transcript". Throttled per
  // (meeting,source,kind) so a repeating fault doesn't flood. ──
  const faultSeen = new Map<string, number>();
  const FAULT_THROTTLE_MS = 15000;
  const reportFault = (key: string, fault: unknown) => {
    const f = fault as { source?: string; kind?: string; status?: number; detail?: string; message?: string };
    const source = f?.source ?? 'engine';
    const kind = f?.kind ?? 'unknown';
    const id = `${key}|${source}|${kind}`;
    const now = Date.now();
    if (now - (faultSeen.get(id) ?? 0) < FAULT_THROTTLE_MS) return;   // already surfaced recently
    faultSeen.set(id, now);
    const detail = f?.detail ?? f?.message ?? '';
    log(`  ⚠ ENGINE FAULT [${source}:${kind}] ${key}${detail ? ` — ${detail}` : ''}`);
    telemetry.push({ at: new Date().toISOString(), reason: 'engine_fault', body: { meeting: key, source, kind, status: f?.status, detail } });
    while (telemetry.length > TELEMETRY_MAX) telemetry.shift();
    const msg = JSON.stringify({ type: 'health', meeting: key, source, kind, status: f?.status, detail });
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
    // POST /extension/sessions/end — finalize a session: the extension's Stop is
    // a CONTRACT (background.ts stopCapture) — "stop" must mean stopped NOW, not
    // after the ingest reconnect grace. Run the live finalizer (dispose pipeline,
    // flush recording, mark completed + drop the WS) if one is registered; either
    // way mark the meeting completed so the panel + history agree. Idempotent.
    if (req.method === 'POST' && url.pathname === '/extension/sessions/end') {
      const b = await readBody(req);
      const p = b.platform || 'unknown';
      const n = b.native_meeting_id || b.native_id || '?';
      const k = keyOf(p, n);
      const fin = activeSessions.get(k);
      if (fin) { try { await fin(); } catch (e: any) { log(`[desktop] session end finalize FAILED for ${k}: ${e?.message || e}`); } }
      const m = meetings.get(k);
      if (m) m.status = 'completed';
      return send({ ok: true, platform: p, native_meeting_id: n, meeting_id: m?.id ?? 0, status: 'completed', finalized: !!fin });
    }
    // POST /telemetry — accept the extension's JSON diagnostics and log+retain
    // them (ring buffer). MUST NEVER 404 (telemetry must not break capture). The
    // body is the merged extension state (session + page attribution + per-track
    // frame counters); transcript text never enters it (the client scrubs it).
    if (req.method === 'POST' && url.pathname === '/telemetry') {
      const b = await readBody(req);
      const reason = String(b?.reason || 'telemetry');
      telemetry.push({ at: new Date().toISOString(), reason, body: b });
      while (telemetry.length > TELEMETRY_MAX) telemetry.shift();
      const st = b?.state || {};
      log(`[telemetry] ${reason} ${st.platform ?? '?'}/${st.nativeMeetingId ?? '?'} status=${st.status ?? '?'} wsOpen=${b?.wsOpen ?? '?'}`);
      return send({ ok: true });
    }
    // GET /telemetry — read the ring buffer back (debugging needs no client-side
    // copy-paste). ?reason=… filters; ?limit=N caps the count (newest last).
    if (req.method === 'GET' && url.pathname === '/telemetry') {
      const reason = url.searchParams.get('reason');
      const limit = Math.max(1, Math.min(TELEMETRY_MAX, Number(url.searchParams.get('limit')) || TELEMETRY_MAX));
      const items = (reason ? telemetry.filter((t) => t.reason === reason) : telemetry).slice(-limit);
      return send({ count: items.length, total: telemetry.length, items });
    }
    // GET /bots — P20: return ONLY the meetings the subject canAccess (filter the
    // list; a forbidden meeting must not even be enumerable to a denied reader).
    if (req.method === 'GET' && url.pathname === '/bots') {
      const subject = subjectOf(req);
      const visible = [...meetings.values()].filter((m) => canAccess(subject, keyOf(m.platform, m.native_meeting_id), 'read'));
      return send({ meetings: visible, has_more: false });
    }
    // GET /health — liveness + dependency readiness (P18). The body reports each
    // dependency's state so an operator/orchestrator sees "is STT configured?" without
    // parsing logs. 200 when STT is configured; 503 (degraded) when it isn't — a desktop
    // with no STT silently never transcribes, which this makes observable.
    if (req.method === 'GET' && url.pathname === '/health') {
      const live = [...meetings.values()].filter((m) => m.status === 'active').length;
      const ok = !!txClient;
      return send({ ok, checks: { stt: txClient ? 'configured' : 'unconfigured', stt_url: TX_URL || null }, live_sessions: live, recordings_dir: recordingsDir }, ok ? 200 : 503);
    }
    const tr = url.pathname.match(/^\/transcripts\/([^/]+)\/([^/]+)/);
    if (req.method === 'GET' && tr) {
      const p = decodeURIComponent(tr[1]), n = decodeURIComponent(tr[2]);
      if (!canAccess(subjectOf(req), keyOf(p, n), 'read')) return send({ error: 'forbidden' }, 403);   // P20
      const m = meetings.get(keyOf(p, n));
      return send(m ?? { id: 0, platform: p, native_meeting_id: n, status: 'unknown', segments: [] });
    }
    // GET /recordings/{p}/{n}/player — a minimal, dependency-free HTML5 <audio>
    // page that plays the assembled master via the EXISTING GET /recordings/{p}/{n}
    // (ADR-0005). Matched BEFORE the bytes route so the trailing /player wins.
    const play = url.pathname.match(/^\/recordings\/([^/]+)\/([^/]+)\/player\/?$/);
    if (req.method === 'GET' && play) {
      const p = decodeURIComponent(play[1]);
      const n = decodeURIComponent(play[2]);
      if (!canAccess(subjectOf(req), keyOf(p, n), 'read')) return send({ error: 'forbidden' }, 403);   // P20
      const entry = recordings.get(keyOf(p, n));
      const src = `/recordings/${encodeURIComponent(p)}/${encodeURIComponent(n)}`;
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', ...CORS });
      return res.end(recordingPlayerPage(p, n, src, entry?.format));
    }
    // GET /recordings/{p}/{n} — serve the assembled recording.v1 master (the disk
    // ADAPTER's read side; the all-in-one path's equivalent of meeting-api's file serve).
    const rec = url.pathname.match(/^\/recordings\/([^/]+)\/([^/]+)/);
    if (req.method === 'GET' && rec) {
      const p = decodeURIComponent(rec[1]), n = decodeURIComponent(rec[2]);
      if (!canAccess(subjectOf(req), keyOf(p, n), 'read')) return send({ error: 'forbidden' }, 403);   // P20
      const entry = recordings.get(keyOf(p, n));
      if (!entry || !existsSync(entry.path)) return send({ error: 'no recording', platform: p, native_meeting_id: n }, 404);
      const body = readFileSync(entry.path);
      res.writeHead(200, { 'Content-Type': REC_CONTENT_TYPE[entry.format] || 'application/octet-stream', 'Content-Length': body.length, ...CORS });
      return res.end(body);
    }
    send({ error: 'not found', path: url.pathname }, 404);
  });
  const wss = new WebSocketServer({ server: gateway, path: '/ws' });
  wss.on('connection', (ws, req) => {
    liveClients.set(ws, new Set());
    // P20: the /ws connection carries no header we can read per-message — derive the
    // subject from the `api_key` query param on the connection URL (else 'local').
    const subject = new URL(req.url || '', 'http://localhost').searchParams.get('api_key') || 'local';
    ws.on('message', (d) => { try { const m = JSON.parse(d.toString()); if (m.action === 'subscribe') {
      // DROP denied keys from the subscription set — a forbidden meeting yields no
      // transcript/health broadcast for the subject. An EMPTY set means "all meetings"
      // (broadcast's keys.size===0 fan-out), so when the client DID request meetings
      // but every one was denied we install a sentinel that matches no real key — a
      // deny must shrink, never widen, what the client receives.
      const requested: string[] = (m.meetings || []).map((x: any) => `${x.platform}/${x.native_id ?? x.native_meeting_id}`);
      const allowed = requested.filter((k) => canAccess(subject, k, 'read'));
      const set = requested.length && allowed.length === 0 ? new Set([' deny-all']) : new Set(allowed);
      liveClients.set(ws, set);
      ws.send(JSON.stringify({ type: 'subscribed' }));
    } } catch { /* */ } });
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
    // VEXA_STT_TAP=1 logs every submission's WINDOW and what came back. A transcript hole is
    // ambiguous without it — audio that was never submitted and audio that was submitted and
    // answered with nothing look identical downstream, and they have different causes.
    const sttTap = process.env.VEXA_STT_TAP === '1';
    const transcribe = async (pcm: Float32Array, prompt?: string) => {
      if (!txClient) throw new Error('no STT (set TRANSCRIPTION_SERVICE_URL)');
      if (!sttTap) return txClient.transcribe(pcm, lang, prompt);
      const dur = pcm.length / SAMPLE_RATE;
      const t0 = Date.now();
      try {
        const r = await txClient.transcribe(pcm, lang, prompt);
        log(`  [stt] ${dur.toFixed(2)}s in ${Date.now() - t0}ms -> ${JSON.stringify((r.text || '').trim().slice(0, 70))}`);
        return r;
      } catch (e) {
        log(`  [stt] ${dur.toFixed(2)}s FAILED in ${Date.now() - t0}ms: ${String(e).slice(0, 90)}`);
        throw e;
      }
    };
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
        onError: (e) => reportFault(key, e),   // P18: STT/engine fault → observable health frame
      });
      micTc = await ChunkedTranscriber.create({
        language: lang, transcribe,
        publish: (_s, conf, pend) => broadcastBatch(key, 'You', conf.map((c) => toTx(c, 'You', true)), pend.map((c) => toTx(c, 'You', false))),
        publishPending: (_s, pend) => broadcastBatch(key, 'You', [], pend.map((c) => toTx(c, 'You', false))),
        clearPending: () => broadcastBatch(key, 'You', [], []),
        rename: () => { /* the local mic is always "You" */ },
        log: () => { /* quiet */ },
        onError: (e) => reportFault(key, e),   // P18
      });
    } else if (txClient) {
      pipe = createGmeetPipeline({
        transcribe,
        config: speakerLaneConfig(),
        sink: { segment: (t) => broadcast(key, t), draft: (t) => { if (t.text.trim()) broadcast(key, t); }, finalize: () => { /* live */ } },
        onError: (e) => reportFault(key, e),   // P18: STT/engine fault → observable health frame
      });
    }
    let mixF = 0, micF = 0, evHints = 0, recF = 0;
    const hb = isMixed ? setInterval(() => log(`  [hb ${key}] ch999=${mixF}f ch1000=${micF}f hints=${evHints} rec=${recF}`), 5000) : null;
    // P18 liveness (server-side): a connected session NOT receiving audio frames is the
    // desktop's own "active but silent" — surface it as a 'no-signal' fault on the observable
    // channels (/ws health · /telemetry · log), so absence is reported, not invisible. Mirrors
    // the extension's client-side capture-liveness; here it's the receiver's view.
    const NO_SIGNAL_MS = opts.noSignalMs ?? (Number(process.env.VEXA_NO_SIGNAL_MS) || 8000);
    const sessionStartMs = Date.now();
    let lastFrameMs = 0;
    const watchdog = setInterval(() => {
      if (finished) return;
      const since = Date.now() - (lastFrameMs || sessionStartMs);
      if (since > NO_SIGNAL_MS) reportFault(key, { source: 'capture', kind: 'no-signal', detail: `no audio frames for ${Math.round(since / 1000)}s — capture not flowing (stream not minted / muted / no speakers?)` });
    }, Math.max(250, Math.min(3000, NO_SIGNAL_MS)));
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
          if (f) { lastFrameMs = Date.now(); if (f.speakerIndex === MIXED_CHANNEL) { mixF++; tc?.feedAudio(f.samples, f.ts); } else if (f.speakerIndex === MIC_CHANNEL) { micF++; micTc?.feedAudio(f.samples, f.ts); } }
        } else {                          // mixed lane: the WHO signal rides EVENT frames (active-speaker hints)
          try { const ev = JSON.parse(data.toString()); if (ev?.kind === 'active-speaker' && ev.speaker) { evHints++; tc?.recordHint(ev.speaker, (ev.detail?.hint as HintKind) || 'dom-active', ev.ts ?? ev.tMs ?? 0, !!ev.detail?.isEnd); } } catch { /* */ }
        }
        return;
      }
      if (!isBinary) return;            // gmeet: the glow name rides on the audio frame; events ignored
      const b = Buffer.from(data);
      if (tryRecording(b)) return;                                         // recording.v1 chunk — not transcription audio
      const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength);   // capture.v1 audio frame
      // route by CHANNEL; name = glow at capture, EXCEPT the local mic (ch1000) which is always "You"
      // (the mic frame carries no glow name → would otherwise fall to the 'Speaker' unknownLabel).
      if (f) { lastFrameMs = Date.now(); pipe?.feedAudio(f.speakerIndex, f.speakerIndex === MIC_CHANNEL ? 'You' : f.speakerName, f.samples, f.ts); }
    });
    let finished = false;
    const finish = async () => {
      if (finished) return; finished = true;
      if (activeSessions.get(key) === finalize) activeSessions.delete(key);
      if (hb) clearInterval(hb); clearInterval(watchdog); tape?.end(); recSink.close(key);
      try { await tc?.dispose(); await micTc?.dispose(); await pipe?.dispose(); } catch { /* */ }
      const m = meetings.get(key); if (m) m.status = 'completed'; log(`[desktop] ■ ${key}`);
    };
    // The finalizer the gateway's /extension/sessions/end calls: finish the
    // pipeline (flush + mark completed) AND drop the socket so the client's WS
    // tears down too. finish() is idempotent, so the subsequent 'close' is a no-op.
    const finalize = async () => { try { ws.close(); } catch { /* */ } await finish(); };
    activeSessions.set(key, finalize);
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
