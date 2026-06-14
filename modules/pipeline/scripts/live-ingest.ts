#!/usr/bin/env tsx
/**
 * live-ingest — wire the PRODUCT extension into the mixed pipeline AND record
 * the two replayable fixtures from the same real-time pass:
 *
 *   product extension ──capture.v1 (WS)──►  [tee]
 *                                            │
 *                                            ├─► stream.capture   (FAITHFUL capture.v1: every
 *                                            │     timestamped frame + event, in order — the
 *                                            │     wire serialization, so replay reproduces
 *                                            │     the live timeline exactly)
 *                                            │
 *                                            └─► createMixedPipeline ──► separated-transcript.v1.jsonl
 *                                                  (gate + diarizer + Whisper;            (the OUTPUT golden,
 *                                                   opaque cluster ids, no naming)         keyed by cluster id)
 *
 *   TRANSCRIPTION_SERVICE_URL=… TRANSCRIPTION_SERVICE_TOKEN=… \
 *   OUT=~/.vexa/fixtures/rt/zoom-2923712604 npx tsx scripts/live-ingest.ts
 *
 * Point the product extension's ingestUrl → ws://localhost:9099/ingest, join, Start.
 * On stop, both fixtures are on disk; mixed-replay.ts reproduces the golden from stream.capture.
 */
import { WebSocketServer } from 'ws';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { createMixedPipeline, TranscriptionClient } from '../src/index';
import { decodeAudioFrame, decodeEvent } from '../../../contracts/capture/v1/schema';

const PORT = parseInt(process.env.PORT || '9099', 10);
const TX_URL = process.env.TRANSCRIPTION_SERVICE_URL || '';
const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
const SAMPLE_RATE = 16000;
const MIXED_CHANNEL = 999;
const MIC_CHANNEL = 1000;

process.on('uncaughtException', (e) => console.error('[live-ingest] uncaught:', (e as any)?.message || e));
process.on('unhandledRejection', (e) => console.error('[live-ingest] unhandledRejection:', (e as any)?.message || e));

const txClient = TX_URL
  ? new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 })
  : null;

const wss = new WebSocketServer({ port: PORT });
console.log(`[live-ingest] listening ws://localhost:${PORT}/ingest`);
console.log(`[live-ingest] STT: ${TX_URL || 'NONE (diarization-only)'}`);

wss.on('connection', async (ws, req) => {
  const url = new URL(req.url || '', `http://localhost:${PORT}`);
  const platform = url.searchParams.get('platform') || 'unknown';
  const nativeMeetingId = url.searchParams.get('native_meeting_id') || '?';
  const language = url.searchParams.get('language');

  // ── fixture sinks ──
  const outDir = process.env.OUT || path.join(os.homedir(), '.vexa', 'fixtures', 'rt', `${platform}-${nativeMeetingId}`);
  fs.rmSync(outDir, { recursive: true, force: true });
  fs.mkdirSync(outDir, { recursive: true });
  const streamLog = fs.createWriteStream(path.join(outDir, 'stream.capture'));   // faithful capture.v1 wire log
  const segLog = fs.createWriteStream(path.join(outDir, 'separated-transcript.v1.jsonl')); // output golden
  let segCount = 0;
  console.log(`[live-ingest] ▶ ${platform} #${nativeMeetingId} → ${outDir}`);

  // framed record: [u8 type 0=audio 1=event][u32LE len][payload]
  const writeRecord = (type: number, payload: Buffer) => {
    const hdr = Buffer.alloc(5); hdr.writeUInt8(type, 0); hdr.writeUInt32LE(payload.length, 1);
    streamLog.write(hdr); streamLog.write(payload);
  };

  const pipeline = await createMixedPipeline({
    language: language && language !== 'auto' ? language : undefined,
    transcribe: async (pcm, prompt) => { if (!txClient) throw new Error('no STT'); return txClient.transcribe(pcm, undefined, prompt); },
    sink: {
      segment: (s) => { segLog.write(JSON.stringify(s) + '\n'); segCount++; console.log(`  \x1b[32m[${s.speakerKey}]\x1b[0m ${s.start.toFixed(1)}–${s.end.toFixed(1)}s  ${s.text}`); },
      finalize: () => new Promise<void>((r) => segLog.end(() => r())),
    },
    log: (m) => console.log(`  \x1b[2m${m}\x1b[0m`),
  });

  ws.send(JSON.stringify({ type: 'ready', meeting_id: nativeMeetingId }));

  let mixedF = 0, micF = 0, evF = 0;
  const seen = new Set<number>();
  ws.on('message', (data: any, isBinary: boolean) => {
    try {
      const b = Buffer.from(data);
      if (isBinary) {
        writeRecord(0, b);                       // TEE every channel's frame, verbatim + ts
        const f = decodeAudioFrame(b.buffer, b.byteOffset, b.byteLength);
        if (!f) return;
        if (!seen.has(f.speakerIndex)) { seen.add(f.speakerIndex); console.log(`[live-ingest] channel ${f.speakerIndex}${f.speakerIndex === MIXED_CHANNEL ? ' = MIXED → diarizer' : f.speakerIndex === MIC_CHANNEL ? ' = MIC (You)' : ''}`); }
        if (f.speakerIndex === MIXED_CHANNEL) { mixedF++; pipeline.feedAudio(f.samples, f.ts); }
        else if (f.speakerIndex === MIC_CHANNEL) micF++;
        return;
      }
      writeRecord(1, b);                          // TEE events (hints, chat) for the downstream brick
      evF++;
      const ev = decodeEvent(b.toString('utf8'));
      if (ev?.kind === 'chat') console.log(`  \x1b[35m[chat] ${ev.speaker || '?'}: ${ev.text || ''}\x1b[0m`);
    } catch (e: any) { console.error('[live-ingest] msg error:', e?.message); }
  });

  const hb = setInterval(() => console.log(`[live-ingest] \x1b[36m· mixed=${mixedF}f mic=${micF}f events=${evF} | segments=${segCount}\x1b[0m`), 5000);

  const finish = async () => {
    clearInterval(hb);
    console.log('[live-ingest] ■ draining…');
    try { await pipeline.dispose(); } catch (e: any) { console.error('dispose:', e?.message); }
    await new Promise<void>((r) => streamLog.end(() => r()));
    fs.writeFileSync(path.join(outDir, 'meta.json'), JSON.stringify({ platform, native_meeting_id: nativeMeetingId, topology: 'mixed', sample_rate: SAMPLE_RATE, capture: 'capture.v1/stream' }, null, 2));
    console.log(`[live-ingest] done. fixtures: ${outDir}/{stream.capture, separated-transcript.v1.jsonl, meta.json}  (${segCount} segments)`);
  };
  ws.on('close', finish);
  ws.on('error', finish);
});
