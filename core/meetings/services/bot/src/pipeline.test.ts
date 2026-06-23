/**
 * L3 — pipeline adapter (capture → lane → stt → transcript.v1). OFFLINE, NO browser/whisper/redis.
 *
 * Drives the REAL @vexa/gmeet-pipeline lane through the bot's `createBotPipeline` with a MOCK
 * transcribe (stt.v1) and a capturing bot-port TranscriptSink, and asserts:
 *   • feeding synthetic per-channel PCM frames drives the stt port (lane→transcribe wired);
 *   • the lane's segment/draft output is RECONCILED onto the bot's TranscriptSink.publish;
 *   • each published segment is a transcript.v1-VALID TranscriptSegment (ajv against the published
 *     transcript.schema.json — same pattern as transcript-redis.test.ts) and correctly attributed;
 *   • two overlapping channels transcribe independently with no cross-channel mislabel;
 *   • stop() disposes the lane (flush every turn → finalize).
 * Run: npx tsx src/pipeline.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createBotPipeline } from './pipeline.js';
import type { Invocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';
import type { TranscriptSink } from './ports.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// ── transcript.v1 validator (ajv against the PUBLISHED schema, loaded by path; P8) ──
const HERE = dirname(fileURLToPath(import.meta.url));
const TX_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'transcript.v1', 'transcript.schema.json');
const txSchema = JSON.parse(readFileSync(TX_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(txSchema);
const validateSeg: ValidateFunction = ajv.compile({ $ref: `${txSchema.$id}#/$defs/TranscriptSegment` });

/** A capturing bot-port TranscriptSink — records every published segment (confirmed + drafts). */
function captureSink(): TranscriptSink & { readonly published: TranscriptSegment[] } {
  const published: TranscriptSegment[] = [];
  return { published, async publish(seg) { published.push(seg); } };
}

const baseInv = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'Vexa',
  redisUrl: 'redis://localhost:6379', transcribeEnabled: true, ...over,
});

const SR = 16000;
const FRAME_MS = 200;
const FRAME = new Float32Array((SR * FRAME_MS) / 1000).fill(0.05);
// Fast lane config — confirm in ~hundreds of ms instead of the 2s production default.
const FAST = { minAudioDuration: 0.15, submitInterval: 0.1, confirmThreshold: 2, maxBufferDuration: 5, idleTimeoutSec: 2, sampleRate: SR };

async function main(): Promise<void> {
  // ── 1) single glow-bound speaker: capture(ch0='Alice') → lane → stt → bot TranscriptSink ──
  {
    let calls = 0;
    const transcribe = async (): Promise<TranscriptionResult> => {
      calls++;
      return { text: 'hello world', language: 'en', duration: 0.2, segments: [{ start: 0, end: 0.2, text: 'hello world' }] };
    };
    const sink = captureSink();
    const pipe = createBotPipeline(baseInv(), sink, { transcribe, config: FAST });
    await pipe.start();

    let ts = 1000;
    for (let i = 0; i < 12; i++) { pipe.feedAudio(0, 'Alice', FRAME, ts); ts += FRAME_MS; await sleep(110); }
    await sleep(300);
    await pipe.stop();   // dispose → flush every turn → finalize

    const seg = sink.published.find((s) => s.speaker === 'Alice' && s.completed);
    check('stt port was driven (lane→transcribe wired)', calls >= 2, `calls=${calls}`);
    check('a segment reached the bot TranscriptSink.publish for Alice', !!seg, JSON.stringify(sink.published));
    check('segment.text == transcribed text', seg?.text === 'hello world', seg?.text);
    check('segment.source == glow-bound (named at capture)', seg?.source === 'glow-bound', seg?.source);
    check('segment.speaker_key is the channel turn key', !!seg && /^ch-0:/.test(seg.speaker_key ?? ''), seg?.speaker_key);
    check('segment timing is seconds (0 ≤ start < end, finite)',
      !!seg && seg.start >= 0 && seg.end > seg.start && isFinite(seg.end), `${seg?.start}..${seg?.end}`);
    check('every published segment is transcript.v1-valid (ajv vs SSOT)',
      sink.published.length > 0 && sink.published.every((s) => !!validateSeg(s)), ajv.errorsText(validateSeg.errors));
    // REGRESSION: the producer stamps a CANONICAL absolute_start_time (the wall clock) so no consumer
    // re-derives it from `start` (a relative-offset assumption put timestamps ~56 years out — the 2082
    // bug). It must be present AND equal the epoch `start`, not a meeting-start + start sum.
    check('segment carries absolute_start_time == epoch(start) (no downstream re-derivation needed)',
      !!seg?.absolute_start_time &&
        Math.abs(new Date(seg.absolute_start_time).getTime() / 1000 - (seg.start ?? 0)) < 1,
      `${seg?.absolute_start_time} vs start=${seg?.start}`);
  }

  // ── 2) two channels, overlapping turns: each transcribes independently, names stay bound ──
  {
    const transcribe = async (pcm: Float32Array): Promise<TranscriptionResult> => {
      const text = pcm[0] > 0.07 ? 'second speaker line' : 'first speaker line';
      return { text, language: 'en', duration: 0.2, segments: [{ start: 0, end: 0.2, text }] };
    };
    const A = new Float32Array((SR * FRAME_MS) / 1000).fill(0.05);
    const B = new Float32Array((SR * FRAME_MS) / 1000).fill(0.09);
    const sink = captureSink();
    const pipe = createBotPipeline(baseInv(), sink, { transcribe, config: FAST });
    await pipe.start();

    let ts = 1000;
    for (let i = 0; i < 12; i++) { pipe.feedAudio(0, 'Alice', A, ts); pipe.feedAudio(1, 'Bob', B, ts); ts += FRAME_MS; await sleep(110); }
    await sleep(300);
    await pipe.stop();

    const alice = sink.published.find((s) => s.speaker === 'Alice' && s.completed);
    const bob = sink.published.find((s) => s.speaker === 'Bob' && s.completed);
    check('overlap: Alice segment present + correctly attributed', alice?.text === 'first speaker line', JSON.stringify(sink.published));
    check('overlap: Bob segment present + correctly attributed', bob?.text === 'second speaker line', JSON.stringify(sink.published));
    check('overlap: no cross-channel mislabel (ch0→Alice, ch1→Bob)',
      alice?.text !== 'second speaker line' && bob?.text !== 'first speaker line');
    check('overlap: all segments transcript.v1-valid', sink.published.every((s) => !!validateSeg(s)), ajv.errorsText(validateSeg.errors));
  }

  // ── 3) transcribeEnabled=false ⇒ no-op transcribe (recording-only meeting), no throw ──
  {
    const sink = captureSink();
    const pipe = createBotPipeline(baseInv({ transcribeEnabled: false }), sink, { config: FAST });
    await pipe.start();
    let ts = 1000;
    for (let i = 0; i < 6; i++) { pipe.feedAudio(0, 'Alice', FRAME, ts); ts += FRAME_MS; await sleep(60); }
    await pipe.stop();
    check('transcribe disabled: pipeline runs without throwing, emits no text', sink.published.every((s) => s.text === ''), JSON.stringify(sink.published));
  }

  if (failed) { console.error(`\n❌ pipeline (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ pipeline (L3): capture→lane→stt→bot.TranscriptSink.publish emits schema-valid, correctly-attributed transcript.v1 segments (real gmeet lane · mock stt · capturing sink).');
}

void main();
