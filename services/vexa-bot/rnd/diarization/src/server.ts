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
import { OnnxLocalDiarizer, type CommitEvent } from './onnx-local-diarizer';
import { JsonlSegmentPublisher, type TranscriptBundle } from './jsonl-segment-publisher';
import type { Diarizer } from './diarizer';
import type { DashboardEvent } from './ws-protocol';
import { SAMPLE_RATE } from './ws-protocol';
import { metrics } from './metrics';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT ?? 43500);
const NUM_SPEAKERS = Number(process.env.NUM_SPEAKERS ?? 2);
const TRANSCRIPTION_URL = process.env.TRANSCRIPTION_URL ?? '';
const TRANSCRIPTION_API_TOKEN = process.env.TRANSCRIPTION_API_TOKEN ?? '';
/** DIARIZER selects the seam implementation. "stub" = MVP0 VAD round-robin
 *  (default; no Python deps). "pyannote" = MVP1 PyannoteSidecarDiarizer
 *  (requires sidecar venv + HF_TOKEN). One-line swap composition root. */
const DIARIZER = (process.env.DIARIZER ?? 'stub').toLowerCase();
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
  console.log(`[harness] PORT=${PORT}  NUM_SPEAKERS=${NUM_SPEAKERS}  DIARIZER=${DIARIZER}`);
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

  // Diarizer — THE new seam this pack adds. Selectable via DIARIZER env:
  //   DIARIZER=stub  → MVP0 VadRoundRobinDiarizer (bot's Silero VAD + round-robin)
  //   DIARIZER=onnx  → MVP1 OnnxLocalDiarizer (wespeaker-resnet34-LM via
  //                    onnxruntime-node + transformers.js fbank + TS online
  //                    clustering; pure-Node, no Python)
  // Deferred-routing seam. Set by the audio-WS handler each time a client
  // connects so per-session state (pendingFrames, the bound speakerManager)
  // is fresh. The diarizer dispatches commits here via a thin trampoline so
  // we can swap the closure without recreating the diarizer.
  //
  // Why this exists: OnnxLocalDiarizer commits a speaker LABEL only at the
  // end of an utterance (sometimes seconds after the speaker actually
  // started). The harness's job is to fan PCM frames to the right per-speaker
  // Whisper buffer. If we route per-frame using diarizer.lastLabel, every
  // frame BEFORE the commit lands gets the previous speaker's label — the
  // "speaker switch lags behind utterance switch" failure from YouTube.
  // Instead we buffer frames and drain them retroactively when the commit
  // for their time range arrives.
  let activeCommitHandler: ((ev: CommitEvent) => void) | null = null;

  let diarizer: Diarizer;
  if (DIARIZER === 'onnx') {
    console.log('[harness] DIARIZER=onnx — loading wespeaker ONNX (first run downloads ~25MB from HF)');
    try {
      // Note: NUM_SPEAKERS env is intentionally NOT wired as `maxSpeakers`
      // here. Capping the clusterer at a hint count forces wrong assignments
      // once the cap is hit (the MVP1 v1 bug). Let online clustering allocate
      // freely based on the cosine threshold; only set maxSpeakers when the
      // hint comes from a *reliable* source (e.g. confirmed roster, tile count).
      diarizer = await OnnxLocalDiarizer.create({
        onCommit: (ev) => activeCommitHandler?.(ev),
      });
    } catch (err: any) {
      console.error(`[harness] OnnxLocalDiarizer failed to start: ${err.message}`);
      console.error('[harness] falling back to VadRoundRobinDiarizer stub');
      diarizer = await VadRoundRobinDiarizer.create({ numSpeakers: NUM_SPEAKERS });
    }
  } else {
    diarizer = await VadRoundRobinDiarizer.create({ numSpeakers: NUM_SPEAKERS });
  }
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

    const txT0 = Date.now();
    try {
      const result = await transcription.transcribe(audioBuffer);
      metrics.recordTranscription({ latencyMs: Date.now() - txT0, ok: true });
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
      const msg = String(err?.message ?? err);
      metrics.recordTranscription({
        latencyMs: Date.now() - txT0,
        ok: false,
        fatal: true,
        busy503: /503/.test(msg) || /Service busy/i.test(msg),
        retries: 0,
      });
      speakerManager.handleTranscriptionResult(speakerId, '');
    }
  };

  await publisher.publishSessionStart();

  // ── HTTP + WS server ─────────────────────────────────────────────────
  const app = express();
  app.use('/static', express.static(path.join(__dirname, '..', 'public')));
  app.get('/', (_req, res) => res.sendFile(path.join(__dirname, '..', 'public', 'capture.html')));
  app.get('/dashboard', (_req, res) => res.sendFile(path.join(__dirname, '..', 'public', 'dashboard.html')));
  app.get('/metrics', (_req, res) => res.json(metrics.snapshot()));

  // Synthetic-corpus browser: list rendered WAVs with inline audio + ground truth.
  const corpusDir = path.join(__dirname, '..', 'eval', 'corpus');
  app.use('/corpus/files', express.static(corpusDir));
  app.get('/corpus', (_req, res) => res.sendFile(path.join(__dirname, '..', 'public', 'corpus.html')));
  app.get('/corpus/index.json', async (_req, res) => {
    try {
      const fs = await import('fs/promises');
      const entries = await fs.readdir(corpusDir);
      const wavs = entries.filter((e) => e.endsWith('.wav')).sort();
      const items = await Promise.all(wavs.map(async (wav) => {
        const id = wav.replace(/\.wav$/, '');
        const gtPath = path.join(corpusDir, `${id}.ground-truth.json`);
        const hxPath = path.join(corpusDir, `${id}.harness-output.json`);
        const has = async (p: string) => fs.access(p).then(() => true, () => false);
        return {
          id,
          wav: `/corpus/files/${wav}`,
          ground_truth: (await has(gtPath)) ? `/corpus/files/${id}.ground-truth.json` : null,
          harness_output: (await has(hxPath)) ? `/corpus/files/${id}.harness-output.json` : null,
        };
      }));
      res.json({ items });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

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
    metrics.reset();
    publisher.resetSessionStart();
    void publisher.publishSessionStart();

    // Append-only mirror of every frame this session received, in arrival
    // order. Written to EVIDENCE_DIR/captured-<ts>.wav on disconnect so a
    // problem session can be replayed offline through the diarizer.
    const sessionPcmChunks: Float32Array[] = [];
    let sessionSampleCount = 0;
    // Per-connection buffer of raw frames awaiting a commit decision. Frames
    // older than the most-recent commit's tEnd that didn't land inside any
    // committed range are dropped on the next commit (they were silence or
    // sub-threshold noise the diarizer chose not to embed).
    const pendingFrames: Array<{ ts: number; pcm: Float32Array }> = [];
    // Speakers we've already announced — gated separately from
    // speakerManager.hasSpeaker because we may want to emit the "joined"
    // dashboard event at commit time (when the cluster is first observed),
    // not when the diarizer's lastLabel happens to fall on a fresh ID.
    const announcedSpeakers = new Set<string>();

    const announce = (speakerId: string, atMs: number) => {
      if (announcedSpeakers.has(speakerId)) return;
      announcedSpeakers.add(speakerId);
      if (!speakerManager.hasSpeaker(speakerId)) {
        speakerManager.addSpeaker(speakerId, speakerId);
      }
      void publisher.publishSpeakerEvent({
        speaker: speakerId,
        type: 'joined',
        timestamp: atMs,
      });
      broadcast({
        kind: 'speaker_event',
        speaker: speakerId,
        event_type: 'SPEAKER_START',
        timestamp_ms: atMs,
        relative_ms: atMs - publisher.sessionStartMs,
      });
    };

    activeCommitHandler = (ev) => {
      // Resolve label rewrites transitively — a commit's speakerId may
      // already be aliased by an earlier mergeClose() pass.
      let resolved = ev.speakerId;
      const rewrites = diarizer.getLabelRewrites?.() ?? new Map<string, string>();
      while (rewrites.has(resolved)) resolved = rewrites.get(resolved)!;

      announce(resolved, ev.tStartMs);

      // Drain pendingFrames in time order. Frames inside [tStartMs, tEndMs]
      // are routed to this speaker's stream; frames before tStartMs are
      // dropped (silence / non-speech the diarizer chose not to embed).
      let i = 0;
      let routed = 0;
      let dropped = 0;
      while (i < pendingFrames.length) {
        const pf = pendingFrames[i];
        if (pf.ts > ev.tEndMs) break;
        if (pf.ts >= ev.tStartMs) {
          speakerManager.feedAudio(resolved, pf.pcm);
          routed++;
        } else {
          dropped++;
        }
        i++;
      }
      if (i > 0) pendingFrames.splice(0, i);
      if (routed > 0) metrics.recordFrameRouted(routed);
      if (dropped > 0) metrics.recordFrameDropped(dropped);
    };

    ws.on('message', async (data, isBinary) => {
      if (!isBinary || !(data instanceof Buffer)) return;
      if (data.byteLength < 8) return;
      const wallClockMs = data.readDoubleLE(0);
      const pcmBytes = data.byteLength - 8;
      const numSamples = pcmBytes / 4;
      const frame = new Float32Array(numSamples);
      for (let i = 0; i < numSamples; i++) frame[i] = data.readFloatLE(8 + i * 4);

      // Mirror every frame for offline replay. Stored as Float32 in-memory;
      // serialized to 16-bit PCM wav on session close.
      sessionPcmChunks.push(frame);
      sessionSampleCount += frame.length;

      try {
        if (DIARIZER === 'onnx') {
          // Deferred routing: buffer the frame and let the diarizer's
          // onCommit drain it once a speaker label is decided.
          pendingFrames.push({ ts: wallClockMs, pcm: frame });
          metrics.recordFrameIn();
          // Soft cap to avoid unbounded growth if the diarizer never
          // commits (e.g. continuous silence). 60s at 16kHz/1024-sample
          // frames ≈ 940 entries.
          if (pendingFrames.length > 1500) {
            const overflow = pendingFrames.length - 1500;
            pendingFrames.splice(0, overflow);
            metrics.recordFrameOverflow(overflow);
          }
          await diarizer.process(frame, wallClockMs);
        } else {
          // Stub diarizer (VAD round-robin) doesn't commit; route per-frame.
          const { speakerId, speakerName } = await diarizer.process(frame, wallClockMs);
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
        }
      } catch (err: any) {
        console.error('[harness] processFrame error:', err.message);
      }
    });

    ws.on('close', async () => {
      console.log('[harness] audio client disconnected — flushing speaker buffers');
      // Detach the commit handler before flushing so a late commit from
      // diarizer.flush() / future GC doesn't try to feed audio into a
      // half-removed speakerManager.
      activeCommitHandler = null;
      pendingFrames.length = 0;

      // Dump captured PCM for offline replay.
      if (sessionSampleCount > SAMPLE_RATE) {
        try {
          const fsMod = await import('fs/promises');
          await fsMod.mkdir(EVIDENCE_DIR, { recursive: true });
          const wavPath = path.join(EVIDENCE_DIR, `captured-${Date.now()}.wav`);
          const wav = encodeWav16kMono(sessionPcmChunks, sessionSampleCount);
          await fsMod.writeFile(wavPath, wav);
          console.log(`[harness] captured PCM → ${wavPath} (${(sessionSampleCount / SAMPLE_RATE).toFixed(1)}s)`);
        } catch (err: any) {
          console.error('[harness] capture-wav write failed:', err.message);
        }
      }
      sessionPcmChunks.length = 0;
      sessionSampleCount = 0;
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
    console.log(`[harness]   metrics:   http://localhost:${PORT}/metrics`);
    console.log(`[harness]   redis-emit-log: ${jsonlPath}`);
  });

  // Broadcast metrics snapshot to dashboard clients once per second. The
  // dashboard reads this and renders the metrics panel; the same snapshot
  // is served on /metrics for scripts/Prometheus-style scrapes.
  setInterval(() => {
    if (dashboardClients.size === 0) return;
    broadcast({ kind: 'metrics', snapshot: metrics.snapshot() });
  }, 1000).unref();
}

/** Serialize float PCM chunks to a single 16-bit little-endian WAV (16kHz mono). */
function encodeWav16kMono(chunks: Float32Array[], totalSamples: number): Buffer {
  const byteLen = totalSamples * 2;
  const buf = Buffer.alloc(44 + byteLen);
  buf.write('RIFF', 0, 'ascii');
  buf.writeUInt32LE(36 + byteLen, 4);
  buf.write('WAVE', 8, 'ascii');
  buf.write('fmt ', 12, 'ascii');
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);
  buf.writeUInt16LE(1, 22);
  buf.writeUInt32LE(SAMPLE_RATE, 24);
  buf.writeUInt32LE(SAMPLE_RATE * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write('data', 36, 'ascii');
  buf.writeUInt32LE(byteLen, 40);
  let off = 44;
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++) {
      const s = Math.max(-1, Math.min(1, chunk[i]));
      buf.writeInt16LE(Math.round(s * 32767), off);
      off += 2;
    }
  }
  return buf;
}

main().catch((err) => {
  console.error('[harness] fatal:', err);
  process.exit(1);
});
