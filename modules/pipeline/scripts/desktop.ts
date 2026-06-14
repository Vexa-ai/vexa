#!/usr/bin/env tsx
/**
 * desktop — the Vexa Desktop backend MVP (all-Node, step 1 of VEXA-DESKTOP.md).
 *
 * The whole data plane in ONE hot process, no Docker / Postgres / Redis:
 *
 *   extension ─capture.v1─► ingest WS (9099)
 *      ├─ pipeline (mixed ‖ multistream + mic) ─► attribution (internal binder)
 *      ├─ delivery: in-proc WS broadcast ─► dashboard /ws            (no Redis)
 *      ├─ recording tee: StreamCaptureWriter ─► stream.capture       (collect while you watch)
 *      └─ control plane + confirmed history ─► node:sqlite           (no Postgres)
 *   gateway (8056): POST /extension/sessions(/end) · GET /bots(/id) · GET /transcripts · WS /ws
 *   STT ↗ hosted (TRANSCRIPTION_SERVICE_URL)
 *
 *   INGEST_PORT=9099 GATEWAY_PORT=8056 VEXA_DESKTOP_DB=~/.vexa/desktop.db \
 *   TRANSCRIPTION_SERVICE_URL=… TRANSCRIPTION_SERVICE_TOKEN=… npx tsx scripts/desktop.ts
 *
 * MVP lives in pipeline/scripts so the brick's node_modules resolves the heavy
 * diarizer deps; the Electron shell + @vexa/* packaging is a later step.
 */
import * as http from 'node:http';
import * as path from 'node:path';
import * as os from 'node:os';
import { WebSocketServer, WebSocket } from 'ws';
import { ChunkedTranscriber, SpeakerStreamManager, TranscriptionClient, ClusterNameBinder, type ChunkSegment, type HintKind } from '../src/index';
import { decodeAudioFrame, decodeEvent } from '../../../contracts/capture/v1/schema';
import { StreamCaptureWriter } from '../../recorder/src/stream-capture';
import { openStore } from './desktop-store';

const INGEST_PORT = parseInt(process.env.INGEST_PORT || '9099', 10);
const GATEWAY_PORT = parseInt(process.env.GATEWAY_PORT || '8056', 10);
const TX_URL = process.env.TRANSCRIPTION_SERVICE_URL || '';
const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
const FIXTURE_ROOT = process.env.VEXA_FIXTURE_CACHE || path.join(os.homedir(), '.vexa', 'fixtures');
const SAMPLE_RATE = 16000, MIXED = 999, MIC = 1000;

process.on('uncaughtException', (e) => console.error('[desktop] uncaught:', (e as any)?.message || e));
process.on('unhandledRejection', (e) => console.error('[desktop] rejection:', (e as any)?.message || e));

const store = openStore();
console.log(`[desktop] lite-db: ${store.path}`);

interface Seg { segment_id: string; speaker: string; text: string; start: number; absolute_start_time: string; completed: boolean }
const liveClients = new Map<WebSocket, Set<string>>();  // ws → subscribed metaKeys (empty set = all, for diagnostics)
const meetingByKey = new Map<string, number>();   // platform/native → meeting_id
const captureConnByKey = new Map<string, WebSocket>(); // platform/native → the ONE active capture ws
const keyOf = (p: string, n: string) => `${p}/${n}`;
const toSeg = (c: ChunkSegment, speaker: string, completed: boolean): Seg => ({
  segment_id: c.segmentId, speaker, text: c.text, start: c.startMs / 1000,
  absolute_start_time: new Date(c.startMs).toISOString(), completed,
});

// Map a DB meeting row to the prod meeting shape the dashboard expects:
// native_meeting_id (from native_id) + data as a parsed object.
const mapMeetingRow = (m: any) => ({
  ...m,
  native_meeting_id: m.native_id,
  data: (() => { try { return typeof m.data === 'string' ? JSON.parse(m.data) : (m.data ?? {}); } catch { return {}; } })(),
});

/** Live delivery (WS) + persist CONFIRMED to SQLite. */
function broadcast(metaKey: string, speaker: string, confirmed: Seg[], pending: Seg[]) {
  if (confirmed.length) {
    const id = meetingByKey.get(metaKey);
    const [platform, nativeId] = metaKey.split('/');
    if (id != null) for (const s of confirmed) {
      try { store.addSegment(id, platform, nativeId, s); } catch (e: any) { console.error('[desktop] persist:', e?.message); }
    }
    for (const s of confirmed) console.log(`  \x1b[32m[${speaker}]\x1b[0m ${s.text}`);
  } else if (pending.length) {
    console.log(`  \x1b[2m[${speaker}] …${pending.map((p) => p.text).join(' ')}\x1b[0m`);
  }
  const msg = JSON.stringify({ type: 'transcript', speaker, confirmed, pending });
  // Scope to subscribers of THIS meeting — a client only gets its own meeting's
  // transcripts (empty subscription = all, used by diagnostics). Without this,
  // every /ws client receives every meeting (cross-meeting leak).
  for (const [c, keys] of liveClients) {
    if (c.readyState === WebSocket.OPEN && (keys.size === 0 || keys.has(metaKey))) c.send(msg);
  }
}

// ── gateway (8056): control plane + history + live WS ──
const CORS = { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': 'X-API-Key, Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS' };
function readBody(req: http.IncomingMessage): Promise<any> {
  return new Promise((resolve) => { let b = ''; req.on('data', (c) => (b += c)); req.on('end', () => { try { resolve(b ? JSON.parse(b) : {}); } catch { resolve({}); } }); });
}
const gatewayHttp = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(204, CORS); return res.end(); }
  const url = new URL(req.url || '', `http://localhost:${GATEWAY_PORT}`);
  res.setHeader('Content-Type', 'application/json'); for (const [k, v] of Object.entries(CORS)) res.setHeader(k, v);
  const send = (o: any, code = 200) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(o)); };

  // control plane
  if (req.method === 'POST' && url.pathname === '/extension/sessions') {
    const b = await readBody(req);
    const platform = b.platform || 'unknown', native = b.native_meeting_id || b.native_id || '?';
    const { meeting_id, session_uid } = store.resolveSession(platform, native);
    return send({ meeting_id, session_uid, platform, native_meeting_id: native, token: b.token || 'local' });
  }
  if (req.method === 'POST' && url.pathname === '/extension/sessions/end') {
    const b = await readBody(req); if (b.meeting_id != null) store.endMeeting(Number(b.meeting_id)); return send({ ok: true });
  }
  if (req.method === 'GET' && url.pathname === '/bots') {
    // Prod /bots shape: { meetings:[…], has_more }. The dashboard proxy reads
    // data.meetings and expects native_meeting_id + data as an object; the
    // extension only health-checks resp.ok (body ignored), so this satisfies both.
    return send({ meetings: (store.listMeetings() as any[]).map(mapMeetingRow), has_more: false });
  }
  // Single meeting by numeric id — the dashboard's meeting-detail page (getMeeting).
  const meet = url.pathname.match(/^\/meetings\/(\d+)$/);
  if (req.method === 'GET' && meet) { const m = store.getMeeting(Number(meet[1])); return m ? send(mapMeetingRow(m)) : send({ error: 'not found' }, 404); }
  const bot = url.pathname.match(/^\/bots\/id\/(\d+)/);
  if (req.method === 'GET' && bot) { const m = store.getMeeting(Number(bot[1])); return m ? send(mapMeetingRow(m)) : send({ error: 'not found' }, 404); }
  const tr = url.pathname.match(/^\/transcripts\/([^/]+)\/([^/]+)/);
  if (req.method === 'GET' && tr) {
    // Prod /transcripts shape: the meeting envelope { id, platform,
    // native_meeting_id, status, … } PLUS segments + recordings. The dashboard
    // builds its Meeting from this response (data.id.toString()), so the
    // metadata must be present, not just segments.
    const platform = decodeURIComponent(tr[1]); const nativeId = decodeURIComponent(tr[2]);
    const m = store.getMeetingByNative(platform, nativeId);
    const envelope = m ? mapMeetingRow(m)
      : { id: 0, platform, native_meeting_id: nativeId, status: 'unknown', start_time: null, end_time: null, data: {} };
    return send({ ...envelope, segments: store.getTranscripts(platform, nativeId), recordings: [] });
  }

  // ── Rest of the dashboard's gateway surface (see dashboard-contract.mjs). ──
  // Desktop is a single-user local VIEWER: read endpoints serve real/empty data;
  // bot-control endpoints are no-ops or end the local session; features with no
  // local backend (sharing) return an honest 501; recordings/master 404 is the
  // dashboard's designed "not ready" path. Every entry is pinned by the contract gate.

  // GET /bots/status — "running bots" = the active local meetings.
  if (req.method === 'GET' && url.pathname === '/bots/status') {
    const running = (store.listMeetings() as any[]).filter((m) => m.status === 'active')
      .map((m) => ({ container_id: `local-${m.id}`, meeting_id: m.id, platform: m.platform, native_meeting_id: m.native_id }));
    return send({ running_bots: running });
  }
  // GET /bots/{platform}/{native}/chat — meeting chat. Desktop tees chat events to
  // the recording but doesn't persist them to lite-db, so this is honestly empty.
  const chat = url.pathname.match(/^\/bots\/([^/]+)\/([^/]+)\/chat$/);
  if (req.method === 'GET' && chat) {
    const m = store.getMeetingByNative(decodeURIComponent(chat[1]), decodeURIComponent(chat[2])) as any;
    return send({ messages: [], meeting_id: m?.id ?? 0 });
  }
  // PUT /bots/{platform}/{native}/config — no live bot to reconfigure; no-op ok.
  if (req.method === 'PUT' && /^\/bots\/[^/]+\/[^/]+\/config$/.test(url.pathname)) return send({ ok: true });
  // DELETE /bots/{platform}/{native} — "stop bot" ⇒ end the active local session.
  const stopBot = url.pathname.match(/^\/bots\/([^/]+)\/([^/]+)$/);
  if (req.method === 'DELETE' && stopBot) {
    const m = store.getMeetingByNative(decodeURIComponent(stopBot[1]), decodeURIComponent(stopBot[2])) as any;
    if (m) store.endMeeting(m.id);
    return send({ ok: true });
  }
  // POST /meetings/{id}/transcribe — desktop transcribes live; report current segments.
  const tx = url.pathname.match(/^\/meetings\/(\d+)\/transcribe$/);
  if (req.method === 'POST' && tx) {
    const m = store.getMeeting(Number(tx[1])) as any;
    const segs = m ? store.getTranscripts(m.platform, m.native_id) : [];
    return send({ status: 'ok', segment_count: segs.length, language: 'auto' });
  }
  // PATCH /meetings/{platform}/{native} — viewer: data edits aren't persisted; echo
  // the meeting so the client's mapMeeting() succeeds.
  const patchMeet = url.pathname.match(/^\/meetings\/([^/]+)\/([^/]+)$/);
  if (req.method === 'PATCH' && patchMeet) {
    const m = store.getMeetingByNative(decodeURIComponent(patchMeet[1]), decodeURIComponent(patchMeet[2])) as any;
    return m ? send(mapMeetingRow(m)) : send({ error: 'not found' }, 404);
  }
  // POST /transcripts/{platform}/{native}/share — no share service in desktop.
  if (req.method === 'POST' && /^\/transcripts\/[^/]+\/[^/]+\/share$/.test(url.pathname))
    return send({ error: 'Transcript sharing is not available in Vexa Desktop' }, 501);

  send({ error: 'not found', path: url.pathname }, 404);
});
new WebSocketServer({ server: gatewayHttp, path: '/ws' }).on('connection', (ws) => {
  liveClients.set(ws, new Set());
  ws.on('message', (d) => {
    try {
      const m = JSON.parse(d.toString());
      if (m.action === 'subscribe') {
        // sidepanel sends { action:'subscribe', meetings:[{platform, native_id}] }
        const keys = Array.isArray(m.meetings)
          ? m.meetings.map((x: any) => `${x.platform}/${x.native_id ?? x.native_meeting_id}`)
          : [];
        liveClients.set(ws, new Set(keys));
        ws.send(JSON.stringify({ type: 'subscribed' }));
      }
    } catch { /* ignore */ }
  });
  ws.on('close', () => liveClients.delete(ws));
});
gatewayHttp.listen(GATEWAY_PORT, () => console.log(`[desktop] gateway  http://localhost:${GATEWAY_PORT}  (/extension/sessions /bots /transcripts /ws)`));

// ── ingest (9099): capture.v1 → pipeline → envelope + tee + persist ──
const txClient = TX_URL ? new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 }) : null;
const ingest = new WebSocketServer({ port: INGEST_PORT });
console.log(`[desktop] ingest   ws://localhost:${INGEST_PORT}/ingest   STT: ${TX_URL || 'NONE'}`);

ingest.on('connection', async (ws, req) => {
  const url = new URL(req.url || '', `http://localhost:${INGEST_PORT}`);
  const platform = url.searchParams.get('platform') || 'unknown';
  const nativeId = url.searchParams.get('native_meeting_id') || '?';
  const language = url.searchParams.get('language');
  const metaKey = keyOf(platform, nativeId);
  // One capture per meeting. A second tab/window capturing the same meeting would
  // merge a duplicate audio stream into this session → 2-3× PCM (the stutter).
  // Last-writer-wins: drop the previous capture connection for this meeting.
  const prevConn = captureConnByKey.get(metaKey);
  if (prevConn && prevConn !== ws) { console.log(`[desktop] ⚠ ${metaKey} superseded by a new capture — closing the previous one`); try { prevConn.close(4001, 'superseded'); } catch { /* already gone */ } }
  captureConnByKey.set(metaKey, ws);
  const { meeting_id } = store.resolveSession(platform, nativeId);
  meetingByKey.set(metaKey, meeting_id);
  console.log(`[desktop] ▶ ${metaKey}  meeting_id=${meeting_id}`);

  // recording tee — collect the fixture WHILE delivering live (the whole point)
  const fixtureDir = path.join(FIXTURE_ROOT, 'capture', 'v1', `${platform}-${nativeId}-${meeting_id}`);
  const rec = new StreamCaptureWriter(fixtureDir, { platform, nativeMeetingId: nativeId, language: language && language !== 'auto' ? language : undefined });

  const lang = language && language !== 'auto' ? language : undefined;
  const transcribe = async (pcm: Float32Array, prompt?: string) => { if (!txClient) throw new Error('no STT'); return txClient.transcribe(pcm, lang, prompt); };

  const tc = await ChunkedTranscriber.create({
    language: lang, transcribe,
    publish: (speaker, confirmed, pending) => broadcast(metaKey, speaker, confirmed.map((c) => toSeg(c, speaker, true)), pending.map((p) => toSeg(p, speaker, false))),
    publishPending: (speaker, pending) => broadcast(metaKey, speaker, [], pending.map((p) => toSeg(p, speaker, false))),
    clearPending: (speaker) => broadcast(metaKey, speaker, [], []),
    rename: (_old, next, segs) => broadcast(metaKey, next, segs.map((s) => toSeg(s, next, true)), []),
    log: (m) => console.log(`  \x1b[2m${m}\x1b[0m`),
  });
  const micTc = await ChunkedTranscriber.create({
    language: lang, transcribe,
    publish: (_s, confirmed, pending) => broadcast(metaKey, 'You', confirmed.map((c) => toSeg(c, 'You', true)), pending.map((p) => toSeg(p, 'You', false))),
    publishPending: (_s, pending) => broadcast(metaKey, 'You', [], pending.map((p) => toSeg(p, 'You', false))),
    clearPending: () => broadcast(metaKey, 'You', [], []),
    rename: () => { /* always "You" */ },
    log: () => { /* quiet */ },
  });

  // multistream (gmeet): per-participant OPAQUE channels. Audio stays per-
  // participant; the channel→name BINDING is done DOWNSTREAM here by the
  // cluster-vote binder, fed the SAME active-speaker hints (overlap-robust,
  // hysteresis, repaint) — NOT in capture. Each channel id IS the binder entity.
  const multi = new SpeakerStreamManager({ sampleRate: SAMPLE_RATE, minAudioDuration: 3, submitInterval: 3, confirmThreshold: 3, maxBufferDuration: 30, idleTimeoutSec: 15 });
  const added = new Set<number>();
  // Tighter window than the default ±2500ms: bind a channel's segment to the name
  // that was lit DURING it, not one that merely lit up near it — so clashes don't
  // grab the overlapping speaker, and the label tracks the real-time glow.
  const channelBinder = new ClusterNameBinder({ matchToleranceMs: 800 });
  const channelSegs = new Map<string, Seg[]>();        // channel id → published (confirmed) segments, for repaint
  const channelName = new Map<string, string>();       // channel id → current resolved name (this turn only)
  const channelLastFrameMs = new Map<string, number>(); // channel id → ts of its last audio frame
  // A gap longer than this on a channel ends its turn: Meet may rotate the channel
  // to a different participant, so its name is forgotten and the next turn maps fresh.
  const CHANNEL_TURN_GAP_MS = 1500;
  const channelDraftName = new Map<string, string>();  // channel id → NAME its live pending draft sits under
  const channelDraftSeg = new Map<string, Seg>();      // channel id → current pending draft seg (to re-home on rename)
  // The client keys pending drafts by SPEAKER NAME (pendingBySpeaker) and replaces
  // them wholesale. So a draft published under the provisional "ch-0" is NOT cleared
  // when the confirm lands under "Vexa" — it orphans (the ch-0/Vexa duplicate). Every
  // rename/confirm must therefore clear the draft under its OLD name explicitly.
  const setChannelDraft = (sid: string, name: string, seg: Seg | null) => {
    const prev = channelDraftName.get(sid);
    if (prev && prev !== name) broadcast(metaKey, prev, [], []);   // drop the stale draft under the old name
    if (seg) { channelDraftName.set(sid, name); channelDraftSeg.set(sid, seg); broadcast(metaKey, name, [], [seg]); }
    else { channelDraftName.delete(sid); channelDraftSeg.delete(sid); broadcast(metaKey, name, [], []); }
  };
  // UNKNOWN-until-confident: a channel segment is NAMED only when a hint was lit
  // confidently DURING it (pure window-match ≥ MIN_CONF). Otherwise it stays
  // "Speaker" (unknown) and is resolved LATER, once, when a confident hint lands —
  // never the channel's stale name. So a segment never shows a wrong name that
  // then flips: it goes unknown → correct, or stays unknown.
  const UNKNOWN = 'Speaker';
  const MIN_CONF = 0.6;
  const unresolved: { sid: string; seg: Seg; tStartMs: number; tEndMs: number }[] = [];
  const confidentName = (sid: string, tStartMs: number, tEndMs: number): string | null => {
    const m = channelBinder.matchWindow({ clusterId: sid, tStartMs, tEndMs });
    return m && m.confidence >= MIN_CONF ? m.name : null;
  };
  multi.onSegmentReady = async (sid, _n, audio) => {
    try { if (!txClient) return multi.handleTranscriptionResult(sid, ''); const r = await txClient.transcribe(audio, lang); multi.handleTranscriptionResult(sid, (r?.text || '').trim(), r?.segments?.[r.segments.length - 1]?.end); }
    catch { multi.handleTranscriptionResult(sid, ''); }
  };
  multi.onSegmentConfirmed = (sid, _name, text, startMs, endMs, segId) => {
    if (!text.trim()) return;
    const name = confidentName(sid, startMs, endMs);
    const spk = name ?? UNKNOWN;
    if (name) channelName.set(sid, name);
    const seg: Seg = { segment_id: segId || `${metaKey}:${sid}:${startMs}`, speaker: spk, text, start: startMs / 1000, absolute_start_time: new Date(startMs).toISOString(), completed: true };
    let cs = channelSegs.get(sid); if (!cs) { cs = []; channelSegs.set(sid, cs); } cs.push(seg);
    setChannelDraft(sid, spk, null);          // confirm supersedes the live draft
    broadcast(metaKey, spk, [seg], []);
    if (!name) { unresolved.push({ sid, seg, tStartMs: startMs, tEndMs: endMs }); if (unresolved.length > 40) unresolved.shift(); }
  };
  multi.onSegmentPending = (sid, _name, text, startMs) => {
    const spk = channelName.get(sid) ?? UNKNOWN; // last CONFIDENT name for this channel, else unknown
    const seg = text.trim()
      ? { segment_id: `${metaKey}:${sid}:pending`, speaker: spk, text, start: startMs / 1000, absolute_start_time: new Date(startMs).toISOString(), completed: false }
      : null;
    setChannelDraft(sid, spk, seg);
  };

  ws.send(JSON.stringify({ type: 'ready', meeting_id }));
  let mixedF = 0, micF = 0, otherF = 0, hints = 0; const seen = new Set<number>();
  ws.on('message', (data: any, isBinary: boolean) => {
    try {
      const b = Buffer.from(data);
      if (isBinary) {
        rec.rawAudio(b);                                  // tee
        const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength); if (!f) return;
        if (!seen.has(f.speakerIndex)) { seen.add(f.speakerIndex); console.log(`[desktop] channel ${f.speakerIndex}${f.speakerIndex === MIXED ? ' = MIXED → diarizer' : f.speakerIndex === MIC ? ' = MIC → "You"' : ''}`); }
        if (f.speakerIndex === MIXED) { mixedF++; tc.feedAudio(f.samples, f.ts); }
        else if (f.speakerIndex === MIC) { micF++; micTc.feedAudio(f.samples, f.ts); }
        else { // opaque channel; named only by a confident hint lit during its turn
          otherF++; const id = `ch-${f.speakerIndex}`;
          if (!added.has(f.speakerIndex)) { added.add(f.speakerIndex); multi.addSpeaker(id, id); }
          const prevTs = channelLastFrameMs.get(id);
          if (prevTs !== undefined && f.ts - prevTs > CHANNEL_TURN_GAP_MS) channelName.delete(id); // turn ended → may have rotated; map fresh
          channelLastFrameMs.set(id, f.ts);
          multi.feedAudio(id, f.samples);
        }
        return;
      }
      rec.rawEvent(b);                                    // tee events (chat + hints)
      const ev = decodeEvent(b.toString('utf8'));
      // active-speaker = the ONLY naming signal. Feed BOTH binders: tc names the
      // mixed 999 clusters, channelBinder names the multistream channels. Only the
      // active platform's path has commits to resolve, so the cross-feed is inert.
      if (ev?.kind === 'active-speaker' && ev.speaker) {
        hints++;
        const kind = ((ev.detail?.hint as any) || 'dom-active') as HintKind;
        const isEnd = !!(ev.detail as any)?.isEnd;
        tc.recordHint(ev.speaker, kind, ev.ts, isEnd);
        channelBinder.recordHint({ name: ev.speaker, tMs: ev.ts, kind, isEnd });
        // A new glow may now confidently name a still-UNKNOWN segment — resolve it
        // ONCE (unknown → name; never name → another name). Drop entries too old to
        // ever match (the hint log only holds recent turns).
        for (let k = unresolved.length - 1; k >= 0; k--) {
          const u = unresolved[k];
          if (ev.ts - u.tEndMs > 25000) { unresolved.splice(k, 1); continue; }
          const name = confidentName(u.sid, u.tStartMs, u.tEndMs);
          if (name) {
            u.seg.speaker = name; channelName.set(u.sid, name);
            broadcast(metaKey, name, [u.seg], []); // same segment_id → client UPSERTs UNKNOWN→name
            unresolved.splice(k, 1);
          }
        }
      }
      // speaker-joined is roster only now — channels come from audio; no channel→name binding.
    } catch (e: any) { console.error('[desktop] msg:', e?.message); }
  });
  const hb = setInterval(() => console.log(`[desktop] \x1b[36m· ${metaKey}  mixed=${mixedF}f mic=${micF}f other=${otherF}f hints=${hints} channels=[${[...seen].join(',')}]\x1b[0m`), 5000);
  const finish = async () => {
    clearInterval(hb);
    try { await tc.dispose(); } catch { /* */ } try { await micTc.dispose(); } catch { /* */ } try { (multi as any).destroy?.(); } catch { /* */ }
    try { const dir = await rec.finalize(); console.log(`[desktop] ■ fixture: ${dir}`); } catch { /* */ }
    if (captureConnByKey.get(metaKey) === ws) captureConnByKey.delete(metaKey); // only if not already superseded
    store.endMeeting(meeting_id);
    console.log(`[desktop] ■ ${metaKey} closed (meeting_id=${meeting_id})`);
  };
  ws.on('close', finish); ws.on('error', finish);
});
