#!/usr/bin/env tsx
/**
 * mixed-replay — reproduce separated-transcript.v1 from a faithful capture.v1
 * stream log (stream.capture), with NO live meeting. Single pass, real ts.
 *
 *   TRANSCRIPTION_SERVICE_URL=… TRANSCRIPTION_SERVICE_TOKEN=… \
 *   npx tsx scripts/mixed-replay.ts <fixture-dir>
 *
 * Reads the framed wire log written by live-ingest, feeds every channel-999
 * frame into createMixedPipeline at its captured ts (the ring is ts-indexed, so
 * the live timeline is reproduced exactly), and writes separated-transcript.replay.jsonl.
 * Diff it against separated-transcript.v1.jsonl to prove faithful reproduction.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { createMixedPipeline, TranscriptionClient } from '../src/index';
import { decodeAudioFrame } from '../../../contracts/capture/v1/schema';

const dir = process.argv[2];
const TX_URL = process.env.TRANSCRIPTION_SERVICE_URL || '';
const TX_TOKEN = process.env.TRANSCRIPTION_SERVICE_TOKEN || '';
if (!dir) { console.error('usage: tsx scripts/mixed-replay.ts <fixture-dir>'); process.exit(1); }
const SAMPLE_RATE = 16000, MIXED_CHANNEL = 999;

(async () => {
  const buf = fs.readFileSync(path.join(dir, 'stream.capture'));
  const txClient = TX_URL ? new TranscriptionClient({ serviceUrl: TX_URL, apiToken: TX_TOKEN, sampleRate: SAMPLE_RATE, maxSpeechDurationSec: 15 }) : null;
  const out = fs.createWriteStream(path.join(dir, 'separated-transcript.replay.jsonl'));
  let segs = 0;
  const pipeline = await createMixedPipeline({
    transcribe: async (pcm, prompt) => { if (!txClient) throw new Error('no STT'); return txClient.transcribe(pcm, undefined, prompt); },
    sink: { segment: (s) => { out.write(JSON.stringify(s) + '\n'); segs++; console.log(`  [${s.speakerKey}] ${s.start.toFixed(1)}–${s.end.toFixed(1)}s ${s.text}`); }, finalize: () => new Promise<void>((r) => out.end(() => r())) },
    log: (m) => console.log(`  \x1b[2m${m}\x1b[0m`),
  });

  // walk the framed log: [u8 type][u32LE len][payload]
  let off = 0, mixed = 0;
  while (off + 5 <= buf.length) {
    const type = buf.readUInt8(off); const len = buf.readUInt32LE(off + 1); off += 5;
    const payload = buf.subarray(off, off + len); off += len;
    if (type === 0) {
      const f = decodeAudioFrame(payload.buffer, payload.byteOffset, payload.byteLength);
      if (f && f.speakerIndex === MIXED_CHANNEL) { mixed++; pipeline.feedAudio(f.samples, f.ts); if (mixed % 8 === 0) await new Promise((r) => setImmediate(r)); }
    }
    // type 1 (events) preserved in the log for the downstream speaker-attribution brick; not consumed here.
  }
  console.log(`\n  fed ${mixed} mixed frames — draining…`);
  await pipeline.dispose();
  console.log(`\n→ ${path.join(dir, 'separated-transcript.replay.jsonl')} (${segs} segments)`);
  console.log(`  diff against separated-transcript.v1.jsonl to verify reproduction.`);
  process.exit(0);
})();
