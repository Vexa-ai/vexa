/**
 * Ingest server — the bot's Node pipeline, with a WebSocket front door.
 *
 * This is the SAME transcription pipeline the Playwright bot runs
 * (SpeakerStreamManager → TranscriptionClient → SegmentPublisher), with one
 * difference: instead of receiving per-speaker audio from an in-process
 * `page.exposeFunction('__vexaPerSpeakerAudioData', …)` callback, it receives
 * it over a WebSocket from a Chrome extension running inside the user's own
 * already-joined meeting tab.
 *
 * Because the user is a real, already-admitted participant, there is no
 * Playwright launch, no join, and no admission phase here — only the half of
 * the bot that turns audio into transcripts.
 *
 * Wire protocol (per WebSocket connection = one capture session):
 *   - Connect:  ws://host:PORT/ingest?platform=google_meet&native_meeting_id=<id>&api_key=<key>
 *   - Text frame  {type:'speakers', speakers:{"0":"Alice","1":"Bob"}}  — index→name map
 *   - Binary frame [Int32LE speakerIndex][Float32LE pcm…]            — one audio chunk
 *   - Server → client {type:'ready', meeting_id} | {type:'error', message}
 *
 * Meeting binding: on connect we POST the api_key + (platform, native_meeting_id)
 * to meeting-api /extension/sessions, which get-or-creates the meeting row and
 * mints a MeetingToken. That token is what the collector validates, and the
 * meeting_id is what the existing dashboard already renders live.
 */

import http from 'http';
import { WebSocketServer, WebSocket } from 'ws';
import { log } from './utils';
import { SpeakerStreamManager } from './services/speaker-streams';
import { TranscriptionClient } from './services/transcription-client';
import { SegmentPublisher, TranscriptionSegment } from './services/segment-publisher';
import { isHallucination } from './services/hallucination-filter';
import { MixedAudioPipeline } from './services/mixed-audio-pipeline';

const PORT = parseInt(process.env.INGEST_PORT || '8090', 10);

function envNum(name: string, fallback: number): number {
  const v = process.env[name];
  const n = v ? parseFloat(v) : NaN;
  return Number.isFinite(n) ? n : fallback;
}
const MEETING_API_URL = process.env.MEETING_API_URL || 'http://meeting-api:8080';
const REDIS_URL = process.env.REDIS_URL || 'redis://redis:6379';
const TRANSCRIPTION_SERVICE_URL = process.env.TRANSCRIPTION_SERVICE_URL || 'http://transcription-service:8083';
const TRANSCRIPTION_SERVICE_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN;

interface ExtensionSession {
  meeting_id: number;
  token: string;
  platform: string;
  native_meeting_id: string;
}

/** Live WS sessions per meeting — drives the deferred finalize below. */
const liveSessionsByMeeting = new Map<number, Set<string>>();
/** Live sockets per meeting — a NEW session supersedes (closes) older ones so
 *  SW reloads / reconnects never leave zombies double-writing one meeting. */
const liveSocketsByMeeting = new Map<number, Map<string, WebSocket>>();
const FINALIZE_GRACE_MS = envNum('INGEST_FINALIZE_GRACE_MS', 60_000);

/**
 * Finalize the meeting (active → completed) after the grace period, unless a
 * new session for it reconnected (pause/resume, tab reload). Extension
 * meetings have no bot container, so nothing else ever completes them.
 */
function scheduleFinalize(meetingId: number, apiKey: string, platform: string, nativeMeetingId: string): void {
  setTimeout(async () => {
    const live = liveSessionsByMeeting.get(meetingId);
    if (live && live.size > 0) return; // resumed — keep the meeting active
    try {
      const resp = await fetch(`${MEETING_API_URL}/extension/sessions/end`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-API-Key': apiKey },
        body: JSON.stringify({ platform, native_meeting_id: nativeMeetingId }),
      });
      log(`[Ingest] Finalized meeting ${meetingId} (${nativeMeetingId}): ${resp.status}`);
    } catch (err: any) {
      log(`[Ingest] Finalize failed for meeting ${meetingId}: ${err.message}`);
    }
  }, FINALIZE_GRACE_MS);
}

/**
 * Ask meeting-api to get-or-create the meeting row for this user+meeting and
 * mint a MeetingToken. Identity is the user's API key, forwarded verbatim.
 */
async function resolveSession(apiKey: string, platform: string, nativeMeetingId: string): Promise<ExtensionSession> {
  const resp = await fetch(`${MEETING_API_URL}/extension/sessions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': apiKey,
    },
    body: JSON.stringify({ platform, native_meeting_id: nativeMeetingId }),
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(`meeting-api /extension/sessions ${resp.status}: ${body.slice(0, 200)}`);
  }
  return await resp.json() as ExtensionSession;
}

/**
 * One capture session: the full reused pipeline, fed by one WebSocket.
 * Mirrors the per-speaker wiring in index.ts (onSegmentReady / onSegmentConfirmed),
 * minus telemetry and Playwright-specific bits.
 */
/** The extension's mixed remote-audio track index (Zoom WASM mode / Teams
 *  tab capture). Audio on this index carries ALL remote participants and is
 *  attributed by diarization, never by direct labeling. */
const MIXED_TRACK_INDEX = 999;

class CaptureSession {
  private speakerManager: SpeakerStreamManager;
  private transcriptionClient: TranscriptionClient;
  private segmentPublisher: SegmentPublisher;
  private confirmedBatches: Map<string, TranscriptionSegment[]> = new Map();
  /** Everything ever confirmed per stream — republished on rename so PG
   *  (UPSERT by segment_id) and the live UI self-correct retroactively. */
  private publishedByStream: Map<string, TranscriptionSegment[]> = new Map();
  private lastDetectedLanguage: Map<string, string> = new Map();
  private knownSpeakers: Set<number> = new Set();
  private closed = false;

  // ── Mixed-track diarization (Zoom/Teams) — the SINGLE single-channel core.
  // Segmentation-driven buffers: MixedAudioPipeline streams audio live into
  // per-segment streams, closes them at segmentation boundaries, and labels
  // them via clustering + hint correlation. This session just maps callbacks
  // onto the unmodified SpeakerStreamManager.
  private mixedPipeline: MixedAudioPipeline | null = null;
  private mixedPipelineReady: Promise<void> | null = null;
  private diarStatsTimer: ReturnType<typeof setInterval> | null = null;

  constructor(
    private session: ExtensionSession,
    private connectionId: string,
    private explicitLanguage: string | null,
  ) {
    this.transcriptionClient = new TranscriptionClient({
      serviceUrl: TRANSCRIPTION_SERVICE_URL,
      apiToken: TRANSCRIPTION_SERVICE_TOKEN,
      minSilenceDurationMs: process.env.MIN_SILENCE_DURATION_MS ? parseInt(process.env.MIN_SILENCE_DURATION_MS) : 100,
    });

    this.segmentPublisher = new SegmentPublisher({
      redisUrl: REDIS_URL,
      meetingId: String(session.meeting_id),
      token: session.token,
      sessionUid: connectionId,
      platform: session.platform,
    });

    // Tuned for live UX (lower latency than the bot's defaults), env-overridable.
    // - minAudioDuration 2s: first draft ~2s sooner than the bot's 3s.
    // - idleTimeoutSec 5s: trailing audio flushes 5s after you stop talking,
    //   not 15s — the bot's 15s was the dominant perceived latency for short
    //   utterances (the tail sat silent for 15s before the final submit).
    this.speakerManager = new SpeakerStreamManager({
      sampleRate: 16000,
      minAudioDuration: envNum('INGEST_MIN_AUDIO_SEC', 2),
      submitInterval: envNum('INGEST_SUBMIT_INTERVAL_SEC', 2),
      confirmThreshold: envNum('INGEST_CONFIRM_THRESHOLD', 2),
      maxBufferDuration: envNum('INGEST_MAX_BUFFER_SEC', 30),
      idleTimeoutSec: envNum('INGEST_IDLE_TIMEOUT_SEC', 5),
    });

    this.wirePipeline();
  }

  async start(): Promise<void> {
    this.segmentPublisher.resetSessionStart();
    await this.segmentPublisher.publishSessionStart();
    if (this.usesMixedDiarization()) {
      // Await model load (~200 ms, models pre-baked in the image) so the
      // pipeline exists before the first audio frame — no frames dropped.
      this.mixedPipelineReady = this.initMixedPipeline();
      await this.mixedPipelineReady;
      // Phase 6 soak signal: periodic diarization stats (names only, no text).
      this.diarStatsTimer = setInterval(() => {
        const st = this.mixedPipeline?.stats();
        if (st) log(`[DiarizeStats] meeting=${this.session.meeting_id} segments=${st.segments} lastLit=${st.lastLit} hints=${JSON.stringify(st.binder.hintTurns)}`);
      }, 30000);
    }
    log(`[Ingest] Session started meeting=${this.session.meeting_id} uid=${this.connectionId}`);
  }

  private usesMixedDiarization(): boolean {
    return this.session.platform === 'zoom' || this.session.platform === 'teams';
  }

  /** The single-channel core: diarizer+gate+binder live in MixedAudioPipeline;
   *  this host wires its turns/renames into the SpeakerStreamManager. */
  private async initMixedPipeline(): Promise<void> {
    this.mixedPipeline = await MixedAudioPipeline.create({
      log: (m) => log(m),
      // Segmentation-driven buffers: audio streams LIVE into the segment's
      // stream (drafts while talking); segmentation closes it; cluster labels
      // (and late hint-resolves) only rename the stream — never move audio.
      onSegmentAudio: (segKey, pcm, atMs) => {
        if (!this.speakerManager.hasSpeaker(segKey)) {
          this.speakerManager.addSpeaker(segKey, segKey);
        }
        this.speakerManager.feedAudio(segKey, pcm, atMs);
      },
      onSegmentLabel: (segKey, displayName, resolution) => {
        if (!this.speakerManager.hasSpeaker(segKey)) {
          this.speakerManager.addSpeaker(segKey, displayName);
          return;
        }
        this.speakerManager.updateSpeakerName(segKey, displayName);
        // Rename ⇒ republish this stream's already-published segments with the
        // SAME segment_ids and the new name: PG UPSERTs them, the live bundle
        // replaces them in clients. Identity is the buffer; name is mutable.
        const published = this.publishedByStream.get(segKey);
        if (published && published.length > 0 && published[0].speaker !== displayName) {
          for (const seg of published) seg.speaker = displayName;
          void this.segmentPublisher.publishTranscript(displayName, published, [], segKey);
          log(`[Diarize] republished ${published.length} segment(s) of ${segKey} as "${displayName}"`);
        }
      },
      onSegmentClose: (segKey) => {
        void this.speakerManager.flushSpeaker(segKey, true);
      },
    });
  }

  /** index → name map from the content script's in-page DOM resolution.
   *  On diarized platforms the mixed track's name is owned by the binder —
   *  direct relabels of it are ignored. */
  updateSpeakers(speakers: Record<string, string>): void {
    for (const [idxStr, name] of Object.entries(speakers)) {
      const index = parseInt(idxStr, 10);
      if (Number.isNaN(index) || !name) continue;
      if (index === MIXED_TRACK_INDEX && this.usesMixedDiarization()) continue;
      const speakerId = `spk-${index}`;
      if (this.speakerManager.hasSpeaker(speakerId)) {
        this.speakerManager.updateSpeakerName(speakerId, name);
      } else {
        this.speakerManager.addSpeaker(speakerId, name);
        this.knownSpeakers.add(index);
      }
    }
  }

  /** Timestamped platform hint: who the UI showed as speaking. Server arrival
   *  time is the timebase (client clocks skew; binder tolerance absorbs the
   *  transport jitter). */
  recordSpeakerActivity(name: string, kind: 'dom-active' | 'caption' | 'dom-outline' = 'dom-active', isEnd = false): void {
    this.mixedPipeline?.recordHint(name, kind, Date.now(), isEnd);
  }

  /** One per-speaker audio chunk from the page. */
  feedAudio(speakerIndex: number, pcm: Float32Array): void {
    if (speakerIndex === MIXED_TRACK_INDEX && this.usesMixedDiarization()) {
      if (this.mixedPipeline) this.mixedPipeline.feedAudio(pcm, Date.now());
      return;
    }
    const speakerId = `spk-${speakerIndex}`;
    if (!this.speakerManager.hasSpeaker(speakerId)) {
      // Audio before a name arrived — register with a placeholder, renamed later.
      this.speakerManager.addSpeaker(speakerId, `Speaker ${speakerIndex + 1}`);
      this.knownSpeakers.add(speakerIndex);
    }
    this.speakerManager.feedAudio(speakerId, pcm);
  }

  async stop(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    if (this.diarStatsTimer) { clearInterval(this.diarStatsTimer); this.diarStatsTimer = null; }
    try { this.mixedPipeline?.dispose(); } catch { /* best effort */ }
    try { this.speakerManager.removeAll(); } catch { /* best effort */ }
    try { await this.segmentPublisher.publishSessionEnd(); } catch { /* best effort */ }
    try { await this.segmentPublisher.close(); } catch { /* best effort */ }
    log(`[Ingest] Session stopped uid=${this.connectionId}`);
  }

  private wirePipeline(): void {
    // onSegmentReady: transcribe the unconfirmed buffer, run quality gates,
    // feed result back for confirmation, publish confirmed+pending bundle.
    this.speakerManager.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
      const lang = this.explicitLanguage || undefined;
      try {
        const contextPrompt = this.speakerManager.getLastConfirmedText(speakerId);
        const result = await this.transcriptionClient.transcribe(audioBuffer, lang, contextPrompt || undefined);
        if (!result || !result.text) {
          this.speakerManager.handleTranscriptionResult(speakerId, '');
          return;
        }

        // Quality gates (mirrors index.ts) — discard low-confidence / noise / hallucinations.
        const prob = result.language_probability ?? 0;
        if (!lang && prob > 0 && prob < 0.3) {
          this.speakerManager.handleTranscriptionResult(speakerId, '');
          return;
        }
        const seg0 = result.segments?.[0];
        if (seg0) {
          const noSpeech = seg0.no_speech_prob ?? 0;
          const logProb = seg0.avg_logprob ?? 0;
          const compression = seg0.compression_ratio ?? 1;
          const duration = (seg0.end || 0) - (seg0.start || 0);
          if ((noSpeech > 0.5 && logProb < -0.7) || (logProb < -0.8 && duration < 2.0) || compression > 2.4) {
            this.speakerManager.handleTranscriptionResult(speakerId, '');
            return;
          }
        }
        if (isHallucination(result.text)) {
          this.speakerManager.handleTranscriptionResult(speakerId, '');
          return;
        }

        if (result.language) this.lastDetectedLanguage.set(speakerId, result.language);

        const lastSeg = result.segments?.[result.segments.length - 1];
        const segEndSec = lastSeg?.end;
        const whisperSegs = result.segments?.map(s => ({ text: s.text, start: s.start, end: s.end }));
        this.speakerManager.handleTranscriptionResult(speakerId, result.text, segEndSec, whisperSegs);

        // Publish: drained confirmed batch + current draft (pending).
        const segLang = this.explicitLanguage || result.language || 'en';
        const bufStart = this.speakerManager.getBufferStartMs(speakerId);
        const startSec = (bufStart - this.segmentPublisher.sessionStartMs) / 1000;
        const whisperSegments = result.segments || [{ text: result.text, start: 0, end: 0 }];
        const pendingSegs: TranscriptionSegment[] = whisperSegments
          .map(ws => ({
            speaker: speakerName,
            text: (ws.text || '').trim(),
            start: startSec + (ws.start || 0),
            end: startSec + (ws.end || 0),
            language: segLang,
            completed: false,
            // Deterministic id: same draft keeps the same identity across
            // re-submissions AND renames (offset within the buffer is stable).
            segment_id: `${this.segmentPublisher.sessionUid}:${speakerId}:p${Math.round((ws.start || 0) * 10)}`,
            absolute_start_time: new Date(bufStart + (ws.start || 0) * 1000).toISOString(),
            absolute_end_time: new Date(bufStart + (ws.end || 0) * 1000).toISOString(),
          }))
          .filter(s => s.text);

        const speakerConfirmed = this.confirmedBatches.get(speakerId) || [];
        this.confirmedBatches.set(speakerId, []);
        const confirmedTextList = speakerConfirmed.map(c => c.text.trim());
        const pending = pendingSegs.filter(p => {
          const pt = p.text.trim();
          return !confirmedTextList.some(ct => pt === ct || pt.startsWith(ct) || ct.startsWith(pt));
        });
        await this.segmentPublisher.publishTranscript(speakerName, speakerConfirmed, pending, speakerId);
      } catch (err: any) {
        log(`[Ingest] transcribe failed for ${speakerName}: ${err.message}`);
        this.speakerManager.handleTranscriptionResult(speakerId, '');
      }
    };

    // onSegmentConfirmed: collect confirmed segments into a per-speaker batch,
    // published atomically with the next pending draft.
    this.speakerManager.onSegmentConfirmed = (speakerId, speakerName, transcript, bufferStartMs, bufferEndMs, segmentId) => {
      if (isHallucination(transcript)) return;
      const lang = this.explicitLanguage || this.lastDetectedLanguage.get(speakerId) || 'en';
      const startSec = (bufferStartMs - this.segmentPublisher.sessionStartMs) / 1000;
      const endSec = (bufferEndMs - this.segmentPublisher.sessionStartMs) / 1000;
      const fullSegmentId = `${this.segmentPublisher.sessionUid}:${segmentId}`;
      if (!this.confirmedBatches.has(speakerId)) this.confirmedBatches.set(speakerId, []);
      const seg: TranscriptionSegment = {
        speaker: speakerName,
        text: transcript,
        start: startSec,
        end: endSec,
        language: lang,
        completed: true,
        segment_id: fullSegmentId,
        absolute_start_time: new Date(bufferStartMs).toISOString(),
        absolute_end_time: new Date(bufferEndMs).toISOString(),
      };
      this.confirmedBatches.get(speakerId)!.push(seg);
      if (!this.publishedByStream.has(speakerId)) this.publishedByStream.set(speakerId, []);
      this.publishedByStream.get(speakerId)!.push(seg);
    };
  }
}

/** Parse a binary audio frame: [Int32LE speakerIndex][Float32LE pcm…]. */
function parseAudioFrame(buf: Buffer): { speakerIndex: number; pcm: Float32Array } | null {
  if (buf.length < 8 || (buf.length - 4) % 4 !== 0) return null;
  const speakerIndex = buf.readInt32LE(0);
  const audioBytes = buf.subarray(4);
  // Copy into a fresh, 4-byte-aligned ArrayBuffer for the Float32Array view.
  const ab = audioBytes.buffer.slice(audioBytes.byteOffset, audioBytes.byteOffset + audioBytes.byteLength);
  return { speakerIndex, pcm: new Float32Array(ab) };
}

export function runIngestServer(): void {
  // Explicit HTTP server: /ingest (WS upgrade) + extension telemetry endpoints.
  // Telemetry exists so extension state (WebRTC hook, captured tracks, speaker
  // attribution, builds, errors) is inspectable server-side — no client-side
  // copy-paste needed: POST /telemetry from the extension background, GET
  // /telemetry?n=20 to read the most recent snapshots.
  const TELEMETRY_MAX = 200;
  const telemetry: any[] = [];
  const httpServer = http.createServer((req, res) => {
    const path = (req.url || '').split('?')[0];
    // CORS: the extension's service worker fetch()es from its own origin
    // (chrome-extension://…) with no host permission for this host — the
    // telemetry endpoint must opt in. JSON POSTs trigger a preflight.
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'content-type');
    if (req.method === 'OPTIONS') { res.writeHead(204).end(); return; }
    if (req.method === 'POST' && path === '/telemetry') {
      let body = '';
      req.on('data', (c) => { body += c; if (body.length > 256 * 1024) req.destroy(); });
      req.on('end', () => {
        try {
          const snap = JSON.parse(body);
          snap.received_at = new Date().toISOString();
          telemetry.push(snap);
          if (telemetry.length > TELEMETRY_MAX) telemetry.splice(0, telemetry.length - TELEMETRY_MAX);
          log(`[ExtTelemetry] ${JSON.stringify(snap)}`);
          res.writeHead(204).end();
        } catch { res.writeHead(400).end('bad json'); }
      });
      return;
    }
    if (req.method === 'GET' && path === '/telemetry') {
      const n = Math.min(parseInt(new URL(req.url || '', 'http://x').searchParams.get('n') || '20', 10) || 20, TELEMETRY_MAX);
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify(telemetry.slice(-n), null, 2));
      return;
    }
    res.writeHead(404).end();
  });
  const wss = new WebSocketServer({ server: httpServer, path: '/ingest' });
  httpServer.listen(PORT);
  log(`[Ingest] WebSocket server listening on :${PORT}/ingest (+ /telemetry)`);

  wss.on('connection', async (ws: WebSocket, req) => {
    const url = new URL(req.url || '', `http://localhost:${PORT}`);
    const platform = url.searchParams.get('platform') || 'google_meet';
    const nativeMeetingId = url.searchParams.get('native_meeting_id') || '';
    const apiKey = url.searchParams.get('api_key') || req.headers['x-api-key'] as string || '';
    const explicitLanguage = url.searchParams.get('language');
    const connectionId = `ext-${platform}-${nativeMeetingId}-${process.hrtime.bigint()}`;

    if (!nativeMeetingId) {
      ws.send(JSON.stringify({ type: 'error', message: 'native_meeting_id required' }));
      ws.close();
      return;
    }

    let capture: CaptureSession | null = null;
    let boundMeetingId: number | null = null;
    try {
      const session = await resolveSession(apiKey, platform, nativeMeetingId);
      capture = new CaptureSession(session, connectionId, explicitLanguage && explicitLanguage !== 'auto' ? explicitLanguage : null);
      await capture.start();
      ws.send(JSON.stringify({ type: 'ready', meeting_id: session.meeting_id }));
      log(`[Ingest] Connection ready meeting=${session.meeting_id} native=${nativeMeetingId}`);
      boundMeetingId = session.meeting_id;
      if (!liveSessionsByMeeting.has(session.meeting_id)) liveSessionsByMeeting.set(session.meeting_id, new Set());
      liveSessionsByMeeting.get(session.meeting_id)!.add(connectionId);
      // Supersede any prior live session for this meeting (zombie from a SW
      // reload, an abrupt disconnect, or a duplicate AUTO_START): one writer
      // per meeting, the newest wins.
      if (!liveSocketsByMeeting.has(session.meeting_id)) liveSocketsByMeeting.set(session.meeting_id, new Map());
      const peers = liveSocketsByMeeting.get(session.meeting_id)!;
      for (const [oldId, oldWs] of peers) {
        log(`[Ingest] Superseding session ${oldId} for meeting ${session.meeting_id} (new session ${connectionId})`);
        try { oldWs.send(JSON.stringify({ type: 'superseded' })); } catch { /* dying socket */ }
        try { oldWs.close(1000); } catch { /* already closed */ }
      }
      peers.clear();
      peers.set(connectionId, ws);
    } catch (err: any) {
      log(`[Ingest] Session bootstrap failed: ${err.message}`);
      ws.send(JSON.stringify({ type: 'error', message: err.message }));
      ws.close();
      return;
    }

    ws.on('message', (data: Buffer, isBinary: boolean) => {
      if (!capture) return;
      if (isBinary) {
        const frame = parseAudioFrame(data);
        if (frame) capture.feedAudio(frame.speakerIndex, frame.pcm);
        return;
      }
      try {
        const msg = JSON.parse(data.toString('utf8'));
        if (msg.type === 'speakers' && msg.speakers) capture.updateSpeakers(msg.speakers);
        // Timestamped platform hint stream (Zoom active-speaker / Teams
        // captions+outline) — feeds the cluster↔name binder.
        if (msg.type === 'speaker_activity' && (msg.name || msg.isEnd)) {
          capture.recordSpeakerActivity(msg.name || '', msg.kind || 'dom-active', !!msg.isEnd);
        }
      } catch { /* ignore malformed control frame */ }
    });

    // Server→extension stop feedback: the dashboard (or API) can stop/delete
    // the meeting at any time; without this watch the session keeps streaming
    // into a finalized meeting and the user sees "stopped" + live transcripts
    // simultaneously. Poll the meeting status with the session's own API key;
    // on any non-live status, notify the extension and close.
    const LIVE_STATUSES = new Set(['active', 'requested', 'joining', 'awaiting_admission']);
    const statusWatch = setInterval(async () => {
      if (boundMeetingId === null) return;
      try {
        const resp = await fetch(`${MEETING_API_URL}/bots/id/${boundMeetingId}`, {
          headers: { 'X-API-Key': apiKey },
        });
        if (!resp.ok) return; // transient API trouble must not kill a live session
        const meeting: any = await resp.json();
        const status = meeting?.status || meeting?.data?.status;
        if (status && !LIVE_STATUSES.has(status)) {
          log(`[Ingest] Meeting ${boundMeetingId} is '${status}' — ending session ${connectionId}`);
          ws.send(JSON.stringify({ type: 'ended', reason: `meeting ${status}` }));
          boundMeetingId = null; // already finalized server-side; skip scheduleFinalize
          ws.close(1000);
        }
      } catch { /* network blip; check again next tick */ }
    }, 15000);

    const cleanup = async () => {
      clearInterval(statusWatch);
      if (capture) { await capture.stop(); capture = null; }
      if (boundMeetingId !== null) {
        liveSessionsByMeeting.get(boundMeetingId)?.delete(connectionId);
        liveSocketsByMeeting.get(boundMeetingId)?.delete(connectionId);
        // Defer the active→completed transition: a pause/reload reconnects
        // within the grace window and keeps the meeting alive.
        scheduleFinalize(boundMeetingId, apiKey, platform, nativeMeetingId);
        boundMeetingId = null;
      }
    };
    ws.on('close', cleanup);
    ws.on('error', cleanup);
  });

  const shutdown = () => { wss.close(); process.exit(0); };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (require.main === module) {
  runIngestServer();
}
