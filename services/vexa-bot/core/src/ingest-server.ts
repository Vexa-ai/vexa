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

import { WebSocketServer, WebSocket } from 'ws';
import { log } from './utils';
import { SpeakerStreamManager } from './services/speaker-streams';
import { TranscriptionClient } from './services/transcription-client';
import { SegmentPublisher, TranscriptionSegment } from './services/segment-publisher';
import { isHallucination } from './services/hallucination-filter';

const PORT = parseInt(process.env.INGEST_PORT || '8090', 10);
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
class CaptureSession {
  private speakerManager: SpeakerStreamManager;
  private transcriptionClient: TranscriptionClient;
  private segmentPublisher: SegmentPublisher;
  private confirmedBatches: Map<string, TranscriptionSegment[]> = new Map();
  private lastDetectedLanguage: Map<string, string> = new Map();
  private knownSpeakers: Set<number> = new Set();
  private closed = false;

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

    this.speakerManager = new SpeakerStreamManager({
      sampleRate: 16000,
      minAudioDuration: 3,
      submitInterval: 2,
      confirmThreshold: 2,
      maxBufferDuration: 30,
      idleTimeoutSec: 15,
    });

    this.wirePipeline();
  }

  async start(): Promise<void> {
    this.segmentPublisher.resetSessionStart();
    await this.segmentPublisher.publishSessionStart();
    log(`[Ingest] Session started meeting=${this.session.meeting_id} uid=${this.connectionId}`);
  }

  /** index → name map from the content script's in-page DOM resolution. */
  updateSpeakers(speakers: Record<string, string>): void {
    for (const [idxStr, name] of Object.entries(speakers)) {
      const index = parseInt(idxStr, 10);
      if (Number.isNaN(index) || !name) continue;
      const speakerId = `spk-${index}`;
      if (this.speakerManager.hasSpeaker(speakerId)) {
        this.speakerManager.updateSpeakerName(speakerId, name);
      } else {
        this.speakerManager.addSpeaker(speakerId, name);
        this.knownSpeakers.add(index);
      }
    }
  }

  /** One per-speaker audio chunk from the page. */
  feedAudio(speakerIndex: number, pcm: Float32Array): void {
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
        await this.segmentPublisher.publishTranscript(speakerName, speakerConfirmed, pending);
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
      this.confirmedBatches.get(speakerId)!.push({
        speaker: speakerName,
        text: transcript,
        start: startSec,
        end: endSec,
        language: lang,
        completed: true,
        segment_id: fullSegmentId,
        absolute_start_time: new Date(bufferStartMs).toISOString(),
        absolute_end_time: new Date(bufferEndMs).toISOString(),
      });
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
  const wss = new WebSocketServer({ port: PORT, path: '/ingest' });
  log(`[Ingest] WebSocket server listening on :${PORT}/ingest`);

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
    try {
      const session = await resolveSession(apiKey, platform, nativeMeetingId);
      capture = new CaptureSession(session, connectionId, explicitLanguage && explicitLanguage !== 'auto' ? explicitLanguage : null);
      await capture.start();
      ws.send(JSON.stringify({ type: 'ready', meeting_id: session.meeting_id }));
      log(`[Ingest] Connection ready meeting=${session.meeting_id} native=${nativeMeetingId}`);
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
      } catch { /* ignore malformed control frame */ }
    });

    const cleanup = async () => {
      if (capture) { await capture.stop(); capture = null; }
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
