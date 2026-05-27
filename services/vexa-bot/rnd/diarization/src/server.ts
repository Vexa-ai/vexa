/**
 * MVP0 RnD harness — bot-native edition.
 *
 *   tab capture (browser → /audio WS)
 *        │  Float32 PCM frames @ 16kHz mono
 *        ▼
 *   Diarizer (THE seam this pack adds)
 *        │  (speakerId, speakerName) per frame
 *        ▼
 *   bot's SpeakerStreamManager.feedAudio(speakerId, frame)
 *        │  ← unchanged bot code from here down
 *        ▼
 *   bot's TranscriptionClient.transcribe(unconfirmedWindow)
 *        │
 *        ▼
 *   bot's SpeakerStreamManager.handleTranscriptionResult(...)
 *        │  word-prefix confirmation (LocalAgreement-2)
 *        ▼
 *   JsonlSegmentPublisher.publishTranscript(confirmed[], pending[])
 *        │  same payload shape as bot's Redis publish; written to JSONL
 *        ▼
 *   broadcast TranscriptBundle to /transcript WS for dashboard
 *
 * Everything from `SpeakerStreamManager.feedAudio()` down is the production
 * bot's code, imported and used as-is. The Diarizer step is the ONLY new
 * code in the pack. JsonlSegmentPublisher mirrors SegmentPublisher's wire
 * payloads exactly; flip to real Redis later by swapping its
 * implementation.
 */

import express from 'express';
import http from 'http';
import path from 'path';
import { fileURLToPath } from 'url';
import { randomUUID } from 'crypto';
import { WebSocketServer, WebSocket } from 'ws';

import { SpeakerStreamManager } from '../../../core/src/services/speaker-streams';
import { TranscriptionClient } from '../../../core/src/services/transcription-client';
import type { TranscriptionSegment } from '../../../core/src/services/segment-publisher';

import { VadRoundRobinDiarizer } from './vad-round-robin-diarizer';
import { JsonlSegmentPublisher, type TranscriptBundle } from './jsonl-segment-publisher';
import type { DashboardEvent } from './ws-protocol';
import { SAMPLE_RATE } from './ws-protocol';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT ?? 43500);
const NUM_SPEAKERS = Number(process.env.NUM_SPEAKERS ?? 2);
const TRANSCRIPTION_URL = process.env.TRANSCRIPTION_URL ?? '';
const TRANSCRIPTION_API_TOKEN = process.env.TRANSCRIPTION_API_TOKEN ?? '';
const EVIDENCE_DIR =
  process.env.EVIDENCE_DIR ??
  path.resolve(__dirname, '..', '..', '..', '..', '..', '.agents', 'packs', 'pack-msteams-local-diarization-rnd', 'mvp0');

async function probeTranscription(url: string, apiToken: string): Promise<{ reachable: boolean; error?: string }> {
  if (!url) return { reachable: false, error: 'TRANSCRIPTION_URL not set' };
  try {
    const base = url.replace(/\/+$/, '');
    const healthUrl = base.endsWith('/v1/audio/transcriptions') ? base.replace('/v1/audio/transcriptions', '/health') : `${base}/health`;
    const headers: Record<string, string> = {};
    if (apiToken) headers['Authorization'] = `Bearer ${apiToken}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 2500);
    try {
      const res = await fetch(healthUrl, { signal: controller.signal, headers });
      return { reachable: res.ok };
    } finally {
      clearTimeout(timer);
    }
  } catch (err: any) {
    return { reachable: false, error: err.message ?? String(err) };
  }
}

async function main() {
  console.log('[harness] MVP0 diarization RnD — bot-native edition');
  console.log(`[harness] PORT=${PORT}  NUM_SPEAKERS=${NUM_SPEAKERS}`);
  console.log(`[harness] TRANSCRIPTION_URL=${TRANSCRIPTION_URL || '(unset)'}`);
  console.log(`[harness] TRANSCRIPTION_API_TOKEN=${TRANSCRIPTION_API_TOKEN ? '(set, ' + TRANSCRIPTION_API_TOKEN.length + ' chars)' : '(unset)'}`);
  console.log(`[harness] EVIDENCE_DIR=${EVIDENCE_DIR}`);

  const sessionUid = `rnd-mvp0-${randomUUID()}`;
  const meetingId = `rnd-mvp0-${Date.now()}`;
  const platform = 'teams-rnd-mvp0';
  const syntheticToken = 'rnd-mvp0-no-jwt';
  const jsonlPath = path.join(EVIDENCE_DIR, 'redis-emit-log.jsonl');

  const transcriptionStatus = await probeTranscription(TRANSCRIPTION_URL, TRANSCRIPTION_API_TOKEN);
  console.log(
    transcriptionStatus.reachable
      ? `[harness] transcription service reachable: ${TRANSCRIPTION_URL}`
      : `[harness] transcription service NOT reachable: ${transcriptionStatus.error ?? 'unknown'}`,
  );

  // Production bot's TranscriptionClient (services/vexa-bot/core/src/services/transcription-client.ts) — used as-is.
  const transcription = transcriptionStatus.reachable
    ? new TranscriptionClient({
        serviceUrl: TRANSCRIPTION_URL,
        apiToken: TRANSCRIPTION_API_TOKEN || undefined,
        sampleRate: SAMPLE_RATE,
      })
    : null;

  // Production bot's SpeakerStreamManager (services/vexa-bot/core/src/services/speaker-streams.ts) — used as-is.
  // Same submission cadence as production: 2s timer, 2s min audio, 2-confirm threshold.
  const speakerManager = new SpeakerStreamManager({
    minAudioDuration: 2,
    submitInterval: 2,
    confirmThreshold: 2,
    sampleRate: SAMPLE_RATE,
  });

  const dashboardClients = new Set<WebSocket>();
  function broadcast(event: DashboardEvent) {
    const msg = JSON.stringify(event);
    for (const ws of dashboardClients) {
      if (ws.readyState === ws.OPEN) ws.send(msg);
    }
  }

  // JSONL-shim for the bot's SegmentPublisher. Same wire shapes the bot
  // would publish to Redis — written to disk instead, and forwarded to
  // the dashboard as transcript bundles.
  const publisher = new JsonlSegmentPublisher({
    outPath: jsonlPath,
    meetingId,
    sessionUid,
    platform,
    token: syntheticToken,
    onTranscriptBundle: (bundle: TranscriptBundle) => {
      broadcast({
        kind: 'transcript',
        meeting_id: String(bundle.meeting.id),
        speaker: bundle.speaker,
        confirmed: bundle.confirmed,
        pending: bundle.pending,
        ts: bundle.ts,
      });
    },
  });

  // Diarizer — THE new seam this pack adds. Construct after deps so logs are clean.
  const diarizer = await VadRoundRobinDiarizer.create({ numSpeakers: NUM_SPEAKERS });
  console.log(`[harness] diarizer ready: ${diarizer.name}`);

  // Per-speaker confirmed-batch accumulator. Mirrors production's batching:
  // SpeakerStreamManager emits confirmed segments one-by-one via
  // onSegmentConfirmed; we collect per-speaker and call publishTranscript
  // (the production atomic confirmed+pending bundle write) from
  // onSegmentReady after each Whisper round-trip.
  const confirmedBatches = new Map<string, TranscriptionSegment[]>();
  /** Last detected language per speaker — used for onSegmentConfirmed since the
   *  Whisper result isn't available there. Matches production index.ts:161. */
  const lastLanguagePerSpeaker = new Map<string, string>();

  // Wire SpeakerStreamManager callbacks. Mirrors production
  // (services/vexa-bot/core/src/index.ts ~1340-1530):
  //   onSegmentConfirmed → collect into per-speaker confirmedBatches
  //                        (does NOT publish on its own)
  //   onSegmentReady     → transcribe, feed handleTranscriptionResult,
  //                        then publishTranscript with the drained
  //                        confirmedBatches + a fresh pending[] from the
  //                        current Whisper draft (filtered to drop any
  //                        items already promoted to confirmed).
  speakerManager.onSegmentConfirmed = (_speakerId, speakerName, transcript, bufferStartMs, bufferEndMs, segmentId) => {
    const startSec = (bufferStartMs - publisher.sessionStartMs) / 1000;
    const endSec = (bufferEndMs - publisher.sessionStartMs) / 1000;
    const seg: TranscriptionSegment = {
      speaker: speakerName,
      text: transcript,
      start: startSec,
      end: endSec,
      language: lastLanguagePerSpeaker.get(speakerName) ?? 'en',
      completed: true,
      segment_id: `${publisher.sessionUid}:${segmentId}`,
      source: 'audio',
      absolute_start_time: new Date(bufferStartMs).toISOString(),
      absolute_end_time: new Date(bufferEndMs).toISOString(),
    };
    const batch = confirmedBatches.get(speakerName) ?? [];
    batch.push(seg);
    confirmedBatches.set(speakerName, batch);
  };

  speakerManager.onSegmentReady = async (speakerId, speakerName, audioBuffer) => {
    if (!transcription) {
      // No transcription backend — emit a synthetic placeholder as a
      // single confirmed segment so the dashboard still demonstrates
      // pipeline shape. Mark Whisper result as empty so the manager
      // doesn't wait forever.
      const startMs = speakerManager.getBufferStartMs(speakerId);
      const endMs = Date.now();
      const placeholder: TranscriptionSegment = {
        speaker: speakerName,
        text: `[transcription service offline — ${(audioBuffer.length / SAMPLE_RATE).toFixed(2)}s of ${speakerName}'s audio buffered]`,
        start: (startMs - publisher.sessionStartMs) / 1000,
        end: (endMs - publisher.sessionStartMs) / 1000,
        language: 'unknown',
        completed: true,
        segment_id: `${publisher.sessionUid}:${speakerId}:placeholder:${endMs}`,
        source: 'audio',
        absolute_start_time: new Date(startMs).toISOString(),
        absolute_end_time: new Date(endMs).toISOString(),
      };
      await publisher.publishTranscript(speakerName, [placeholder], []);
      speakerManager.handleTranscriptionResult(speakerId, '');
      return;
    }

    try {
      const result = await transcription.transcribe(audioBuffer);
      if (result.language && result.language !== 'unknown') {
        lastLanguagePerSpeaker.set(speakerName, result.language);
      }

      // Feed into manager — this may trigger onSegmentConfirmed callbacks
      // that populate confirmedBatches.
      const lastSeg = result.segments[result.segments.length - 1];
      speakerManager.handleTranscriptionResult(
        speakerId,
        result.text,
        lastSeg?.end,
        result.segments.map((s) => ({ text: s.text, start: s.start, end: s.end })),
      );

      // Build pending from the current Whisper draft — one entry per Whisper
      // segment so sentence boundaries survive into the dashboard.
      if (!result.text) return;
      const lang = lastLanguagePerSpeaker.get(speakerName) ?? result.language ?? 'en';
      const bufStartMs = speakerManager.getBufferStartMs(speakerId);
      const startSec = (bufStartMs - publisher.sessionStartMs) / 1000;
      const draft = result.segments.length > 0 ? result.segments : [{ text: result.text, start: 0, end: 0 }];
      const pendingSegs: TranscriptionSegment[] = draft
        .map((ws): TranscriptionSegment => ({
          speaker: speakerName,
          text: (ws.text || '').trim(),
          start: startSec + (ws.start || 0),
          end: startSec + (ws.end || 0),
          language: lang,
          completed: false,
          source: 'audio',
          absolute_start_time: new Date(bufStartMs + (ws.start || 0) * 1000).toISOString(),
          absolute_end_time: new Date(bufStartMs + (ws.end || 0) * 1000).toISOString(),
        }))
        .filter((s) => s.text);

      // Drain this speaker's confirmed batch (collected via onSegmentConfirmed).
      const speakerConfirmed = confirmedBatches.get(speakerName) ?? [];
      confirmedBatches.set(speakerName, []);

      // Filter pending entries that already overlap with the just-confirmed text.
      const confirmedTexts = speakerConfirmed.map((c) => c.text.trim());
      const pending = pendingSegs.filter((p) => {
        const pt = p.text.trim();
        return !confirmedTexts.some((ct) => pt === ct || pt.startsWith(ct) || ct.startsWith(pt));
      });

      await publisher.publishTranscript(speakerName, speakerConfirmed, pending);
    } catch (err: any) {
      console.error(`[harness] transcription error for ${speakerName}:`, err.message);
      speakerManager.handleTranscriptionResult(speakerId, '');
    }
  };

  await publisher.publishSessionStart();

  // ── HTTP + WS server ─────────────────────────────────────────────────
  const app = express();
  app.use('/static', express.static(path.join(__dirname, '..', 'public')));
  app.get('/', (_req, res) => res.sendFile(path.join(__dirname, '..', 'public', 'capture.html')));
  app.get('/dashboard', (_req, res) => res.sendFile(path.join(__dirname, '..', 'public', 'dashboard.html')));

  const server = http.createServer(app);
  const audioWss = new WebSocketServer({ noServer: true });
  const transcriptWss = new WebSocketServer({ noServer: true });

  server.on('upgrade', (req, socket, head) => {
    const url = req.url ?? '';
    if (url.startsWith('/audio')) {
      audioWss.handleUpgrade(req, socket, head, (ws) => audioWss.emit('connection', ws, req));
    } else if (url.startsWith('/transcript')) {
      transcriptWss.handleUpgrade(req, socket, head, (ws) => transcriptWss.emit('connection', ws, req));
    } else {
      socket.destroy();
    }
  });

  audioWss.on('connection', (ws) => {
    console.log('[harness] audio client connected');
    diarizer.reset();
    publisher.resetSessionStart();
    void publisher.publishSessionStart();

    ws.on('message', async (data, isBinary) => {
      if (!isBinary || !(data instanceof Buffer)) return;
      if (data.byteLength < 8) return;
      const wallClockMs = data.readDoubleLE(0);
      const pcmBytes = data.byteLength - 8;
      const numSamples = pcmBytes / 4;
      const frame = new Float32Array(numSamples);
      for (let i = 0; i < numSamples; i++) frame[i] = data.readFloatLE(8 + i * 4);

      try {
        // THE seam — replaces the Teams caption-DOM routing step.
        const { speakerId, speakerName } = await diarizer.process(frame, wallClockMs);

        // From here down: bot code unchanged.
        if (!speakerManager.hasSpeaker(speakerId)) {
          speakerManager.addSpeaker(speakerId, speakerName);
          await publisher.publishSpeakerEvent({
            speaker: speakerName,
            type: 'joined',
            timestamp: wallClockMs,
          });
          broadcast({
            kind: 'speaker_event',
            speaker: speakerName,
            event_type: 'SPEAKER_START',
            timestamp_ms: wallClockMs,
            relative_ms: wallClockMs - publisher.sessionStartMs,
          });
        }
        speakerManager.feedAudio(speakerId, frame);
      } catch (err: any) {
        console.error('[harness] processFrame error:', err.message);
      }
    });

    ws.on('close', async () => {
      console.log('[harness] audio client disconnected — flushing speaker buffers');
      // Final flush — matches production's cleanup path.
      const activeSpeakers = speakerManager.getActiveSpeakers();
      for (const speakerId of activeSpeakers) {
        await speakerManager.flushSpeaker(speakerId, true);
      }
      speakerManager.removeAll();
      await publisher.publishSessionEnd();
    });

    ws.on('error', (err) => console.error('[harness] audio ws error:', err.message));
  });

  transcriptWss.on('connection', (ws) => {
    console.log('[harness] dashboard client connected');
    dashboardClients.add(ws);
    ws.send(
      JSON.stringify({
        kind: 'session_info',
        diarizer_name: diarizer.name,
        num_speakers: NUM_SPEAKERS,
        session_uid: sessionUid,
        meeting_id: meetingId,
        platform,
        transcription_url: TRANSCRIPTION_URL,
        transcription_reachable: transcriptionStatus.reachable,
        transcription_error: transcriptionStatus.error,
      } satisfies DashboardEvent),
    );
    ws.on('close', () => {
      dashboardClients.delete(ws);
      console.log('[harness] dashboard client disconnected');
    });
    ws.on('error', (err) => console.error('[harness] dashboard ws error:', err.message));
  });

  server.listen(PORT, () => {
    console.log(`[harness] listening on http://localhost:${PORT}`);
    console.log(`[harness]   capture:   http://localhost:${PORT}/`);
    console.log(`[harness]   dashboard: http://localhost:${PORT}/dashboard`);
    console.log(`[harness]   redis-emit-log: ${jsonlPath}`);
  });
}

main().catch((err) => {
  console.error('[harness] fatal:', err);
  process.exit(1);
});
