/**
 * MVP0 RnD harness server.
 *
 * Routes:
 *   GET  /                — capture page (browser shares a tab here)
 *   GET  /dashboard       — live diarized transcript view
 *   GET  /static/*        — capture.js, dashboard.js
 *   WS   /audio           — binary PCM from capture page
 *   WS   /transcript      — JSON events to dashboard
 *
 * Hot reload via tsx watch (see scripts/dev.sh). The Diarizer is constructed
 * once at process start; on file change the whole Node process restarts
 * (acceptable at MVP0 since the only model loaded is Silero VAD ~2MB —
 * MVP1's pyannote sidecar will live in a separate process so its weights
 * survive harness reloads).
 */

import express from 'express';
import http from 'http';
import path from 'path';
import { WebSocketServer, WebSocket } from 'ws';
import { fileURLToPath } from 'url';

import { TranscriptionClient } from './transcription-client';
import { VadRoundRobinDiarizer } from './stub-diarizer';
import { DiarizationPipeline } from './pipeline';
import type { DashboardEvent } from './ws-protocol';
import { SAMPLE_RATE } from './ws-protocol';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const PORT = Number(process.env.PORT ?? 43500);
const TRANSCRIPTION_URL = process.env.TRANSCRIPTION_URL ?? '';
const NUM_SPEAKERS = Number(process.env.NUM_SPEAKERS ?? 2);

async function probeTranscription(url: string): Promise<{ reachable: boolean; error?: string }> {
  if (!url) return { reachable: false, error: 'TRANSCRIPTION_URL not set' };
  try {
    const probe = url.replace(/\/+$/, '');
    const healthUrl = probe.endsWith('/v1/audio/transcriptions')
      ? probe.replace('/v1/audio/transcriptions', '/health')
      : `${probe}/health`;
    const controller = new AbortController();
    const t = setTimeout(() => controller.abort(), 2000);
    try {
      const res = await fetch(healthUrl, { signal: controller.signal });
      return { reachable: res.ok };
    } finally {
      clearTimeout(t);
    }
  } catch (err: any) {
    return { reachable: false, error: err.message ?? String(err) };
  }
}

async function main() {
  console.log('[harness] starting MVP0 diarization RnD harness');
  console.log(`[harness] PORT=${PORT}`);
  console.log(`[harness] NUM_SPEAKERS=${NUM_SPEAKERS}`);
  console.log(`[harness] TRANSCRIPTION_URL=${TRANSCRIPTION_URL || '(unset — placeholder transcripts)'}`);

  const transcriptionStatus = await probeTranscription(TRANSCRIPTION_URL);
  console.log(
    transcriptionStatus.reachable
      ? `[harness] transcription service reachable: ${TRANSCRIPTION_URL}`
      : `[harness] transcription service NOT reachable: ${transcriptionStatus.error ?? 'unknown'} — dashboard will show placeholder transcripts`,
  );

  const transcription = transcriptionStatus.reachable
    ? new TranscriptionClient({ serviceUrl: TRANSCRIPTION_URL, sampleRate: SAMPLE_RATE })
    : null;

  const diarizer = new VadRoundRobinDiarizer({ numSpeakers: NUM_SPEAKERS });
  console.log(`[harness] diarizer ready: ${diarizer.name}`);

  // Broadcaster for dashboard clients
  const dashboardClients = new Set<WebSocket>();
  function broadcast(event: DashboardEvent) {
    const msg = JSON.stringify(event);
    for (const ws of dashboardClients) {
      if (ws.readyState === ws.OPEN) {
        ws.send(msg);
      }
    }
  }

  const pipeline = new DiarizationPipeline({
    diarizer,
    transcription,
    onSegment: (event) => {
      console.log(
        `[harness] segment t=${event.t0}..${event.t1} speaker=${event.speaker} text="${event.text.slice(0, 80)}"`,
      );
      broadcast(event);
    },
    onError: (err) => console.error('[harness] pipeline error:', err.message),
  });
  pipeline.start();

  const app = express();
  app.use('/static', express.static(path.join(__dirname, '..', 'public')));
  app.get('/', (_req, res) => {
    res.sendFile(path.join(__dirname, '..', 'public', 'capture.html'));
  });
  app.get('/dashboard', (_req, res) => {
    res.sendFile(path.join(__dirname, '..', 'public', 'dashboard.html'));
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
    pipeline.reset();
    ws.on('message', async (data, isBinary) => {
      if (!isBinary || !(data instanceof Buffer)) return;
      // Wire format: Float64 ts (8 bytes) + Float32[] PCM
      if (data.byteLength < 8) return;
      const wallClockMs = data.readDoubleLE(0);
      const pcmBytes = data.byteLength - 8;
      const numSamples = pcmBytes / 4;
      const frame = new Float32Array(numSamples);
      for (let i = 0; i < numSamples; i++) {
        frame[i] = data.readFloatLE(8 + i * 4);
      }
      try {
        await pipeline.processFrame(frame, wallClockMs);
      } catch (err: any) {
        console.error('[harness] processFrame error:', err.message);
      }
    });
    ws.on('close', () => {
      console.log('[harness] audio client disconnected');
      pipeline.stop();
      pipeline.start();
    });
    ws.on('error', (err) => console.error('[harness] audio ws error:', err.message));
  });

  transcriptWss.on('connection', (ws) => {
    console.log('[harness] dashboard client connected');
    dashboardClients.add(ws);
    // Initial info events
    ws.send(JSON.stringify({ kind: 'diarizer-info', name: diarizer.name, numSpeakers: NUM_SPEAKERS } satisfies DashboardEvent));
    ws.send(
      JSON.stringify({
        kind: 'transcription-status',
        reachable: transcriptionStatus.reachable,
        url: TRANSCRIPTION_URL,
        error: transcriptionStatus.error,
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
  });
}

main().catch((err) => {
  console.error('[harness] fatal:', err);
  process.exit(1);
});
