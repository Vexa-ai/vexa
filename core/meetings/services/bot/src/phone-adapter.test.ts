/**
 * L3 — the dial-in (phone) adapter. OFFLINE, NO browser, NO SIP stack, NO whisper, NO redis.
 *
 * The claim under test is exactly one sentence: **a 16 kHz WAV — audio that never went through a
 * browser — pushed through the phone adapter comes out of the pipeline as transcript.v1 segments
 * attributed to the room.** That is the whole question the dial-in spike exists to answer, and it
 * is answerable without a trunk, a provider or a phone.
 *
 * It drives the REAL @vexa/gmeet-pipeline lane through `createPhonePipeline` with a MOCK
 * transcribe (stt.v1) and a capturing bot-port TranscriptSink, same harness as pipeline.test.ts.
 *
 * Also pinned here: the feature flag is the entire blast radius (flag off ⇒ the adapter refuses
 * to run at all), and the decoder handles what a PSTN trunk actually delivers (G.711 µ-law at
 * 8 kHz), not only the fixture format.
 *
 * Run: npx tsx src/phone-adapter.test.ts
 */
import Ajv2020, { type ValidateFunction } from 'ajv/dist/2020.js';
import addFormats from 'ajv-formats';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  PHONE_CHANNEL,
  PHONE_TARGET_SAMPLE_RATE,
  createPhonePipeline,
  decodeWav,
  framesOf,
  muLawToFloat,
  phoneMeetingUrl,
  pumpPhoneCall,
  resampleTo16k,
  toMono,
  wavToPipelinePcm,
  type PhoneCall,
} from './phone-adapter.js';
import { parseInvocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';
import type { TranscriptSink } from './ports.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const threw = async (fn: () => unknown): Promise<string | null> => {
  try { await fn(); return null; } catch (e: any) { return e?.message ?? 'threw'; }
};

// ── transcript.v1 validator (ajv against the PUBLISHED schema, loaded by path; P8) ──
const HERE = dirname(fileURLToPath(import.meta.url));
const TX_SCHEMA = join(HERE, '..', '..', '..', 'contracts', 'transcript.v1', 'transcript.schema.json');
const txSchema = JSON.parse(readFileSync(TX_SCHEMA, 'utf8'));
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(txSchema);
const validateSeg: ValidateFunction = ajv.compile({ $ref: `${txSchema.$id}#/$defs/TranscriptSegment` });

function captureSink(): TranscriptSink & { readonly published: TranscriptSegment[] } {
  const published: TranscriptSegment[] = [];
  return { published, async publish(seg) { published.push(seg); }, async retract() { /* unused here */ } };
}

/** Build a real RIFF/WAVE file: 16-bit linear PCM, mono, 16 kHz — what a recorded dial-in leg is. */
function makeWav16k(seconds: number): Buffer {
  const sr = 16_000;
  const n = Math.round(sr * seconds);
  const data = Buffer.alloc(n * 2);
  for (let i = 0; i < n; i++) {
    // Speech-shaped enough to be non-trivial audio: a 220 Hz carrier under a 3 Hz envelope.
    const t = i / sr;
    const v = Math.sin(2 * Math.PI * 220 * t) * (0.4 + 0.3 * Math.sin(2 * Math.PI * 3 * t));
    data.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(v * 32767))), i * 2);
  }
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);       // fmt chunk size
  header.writeUInt16LE(1, 20);        // WAVE_FORMAT_PCM
  header.writeUInt16LE(1, 22);        // mono
  header.writeUInt32LE(sr, 24);
  header.writeUInt32LE(sr * 2, 28);   // byte rate
  header.writeUInt16LE(2, 32);        // block align
  header.writeUInt16LE(16, 34);       // bits per sample
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

/** Build an 8 kHz G.711 µ-law WAV — what a PSTN trunk actually hands over. */
function makeWavMulaw8k(seconds: number): Buffer {
  const sr = 8_000;
  const n = Math.round(sr * seconds);
  const data = Buffer.alloc(n);
  for (let i = 0; i < n; i++) data[i] = 0xff - (i % 32); // arbitrary but deterministic µ-law bytes
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(7, 20);        // WAVE_FORMAT_MULAW
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sr, 24);
  header.writeUInt32LE(sr, 28);
  header.writeUInt16LE(1, 32);
  header.writeUInt16LE(8, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

const ROOM = 'Room 3 — Lisbon';
const CALL: PhoneCall = {
  address: '+15551234567',
  nativeMeetingId: '+15551234567:20260917T143000Z',
  roomLabel: ROOM,
  startedAt: Date.UTC(2026, 8, 17, 14, 30, 0),
};
// Fast lane config — confirm in ~hundreds of ms instead of the 2 s production default.
const FAST = { minAudioDuration: 0.15, submitInterval: 0.1, confirmThreshold: 2, maxBufferDuration: 5, idleTimeoutSec: 2, sampleRate: PHONE_TARGET_SAMPLE_RATE };

async function main(): Promise<void> {
  const dir = mkdtempSync(join(tmpdir(), 'vexa-phone-'));
  try {
    // ── 1) the flag is the whole blast radius ────────────────────────────────────────
    delete process.env.VEXA_PHONE_PLATFORM;
    check('flag unset ⇒ createPhonePipeline refuses',
      /VEXA_PHONE_PLATFORM/.test((await threw(() => createPhonePipeline(CALL, captureSink()))) ?? ''));
    check('flag unset ⇒ pumpPhoneCall refuses',
      /VEXA_PHONE_PLATFORM/.test((await threw(() => pumpPhoneCall({ feedAudio() {} } as any, new Float32Array(16), CALL))) ?? ''));

    process.env.VEXA_PHONE_PLATFORM = '1';

    // ── 2) the WAV is read off disk and decoded ──────────────────────────────────────
    const wavPath = join(dir, 'dialin-leg.wav');
    writeFileSync(wavPath, makeWav16k(3));
    const bytes = readFileSync(wavPath);
    const decoded = decodeWav(bytes);
    check('WAV decodes at 16 kHz mono', decoded.sampleRate === 16_000 && decoded.channels === 1,
      `${decoded.sampleRate} Hz / ${decoded.channels} ch`);
    check('WAV decodes to ~3 s of samples', Math.abs(decoded.pcm.length - 48_000) <= 1, String(decoded.pcm.length));
    check('decoded samples are normalized to [-1, 1]', decoded.pcm.every((v) => v >= -1 && v <= 1));
    check('the audio is not silence', decoded.pcm.some((v) => Math.abs(v) > 0.1));

    const pcm = wavToPipelinePcm(bytes);
    check('wavToPipelinePcm yields 16 kHz mono', pcm.length === decoded.pcm.length, String(pcm.length));
    const frames = framesOf(pcm);
    check('framing at 200 ms yields 15 frames of 3200 samples', frames.length === 15 && frames[0].length === 3200,
      `${frames.length} frames of ${frames[0]?.length}`);

    // ── 3) THE CLAIM: WAV → phone adapter → pipeline → transcript.v1 segments ────────
    let calls = 0;
    const transcribe = async (): Promise<TranscriptionResult> => {
      calls++;
      return { text: 'the room is ready', language: 'en', duration: 0.2, segments: [{ start: 0, end: 0.2, text: 'the room is ready' }] };
    };
    const sink = captureSink();
    const pipe = createPhonePipeline(CALL, sink, { transcribe, config: FAST });
    await pipe.start();

    const fedAt: number[] = [];
    const pumped = await pumpPhoneCall(pipe, pcm, CALL, { paceMs: 110, onFrame: (_i, ts) => fedAt.push(ts) });
    await sleep(400);
    await pipe.stop();   // dispose → flush every turn → finalize

    check('every frame crossed the adapter', pumped.framesFed === 15 && pumped.samplesFed === pcm.length,
      JSON.stringify(pumped));
    check('the pumped audio duration matches the WAV', Math.abs(pumped.audioMs - 3000) <= 20, String(pumped.audioMs));
    check('the stt port was driven by a NON-BROWSER source', calls >= 2, `calls=${calls}`);

    const seg = sink.published.find((s) => s.completed);
    check('a segment reached the bot TranscriptSink.publish', !!seg, JSON.stringify(sink.published).slice(0, 400));
    check('segment.text == transcribed text', seg?.text === 'the room is ready', seg?.text);
    check('the ROOM is the speaker (no per-person names invented)',
      sink.published.length > 0 && sink.published.every((s) => s.speaker === ROOM),
      JSON.stringify([...new Set(sink.published.map((s) => s.speaker))]));
    check('every published segment is transcript.v1-valid (ajv vs SSOT)',
      sink.published.length > 0 && sink.published.every((s) => !!validateSeg(s)), ajv.errorsText(validateSeg.errors));
    check('the room rides ONE channel (channel 0)',
      !!seg && new RegExp(`^ch-${PHONE_CHANNEL}:`).test(seg.speaker_key ?? ''), seg?.speaker_key);
    check('frame timestamps are EPOCH ms starting at the call start',
      fedAt[0] === CALL.startedAt && fedAt[fedAt.length - 1] > 1.7e12, `${fedAt[0]}..${fedAt[fedAt.length - 1]}`);
    check('absolute_start_time lands in 2026, not 1970',
      !!seg?.absolute_start_time && new Date(seg.absolute_start_time).getUTCFullYear() === 2026,
      seg?.absolute_start_time);

    // ── 4) an unnamed room is refused, not transcribed anonymously ───────────────────
    check('a call with no roomLabel is refused',
      /roomLabel/.test((await threw(() => pumpPhoneCall(pipe, pcm, { ...CALL, roomLabel: '' }, { paceMs: 0 }))) ?? ''));

    // ── 5) what a PSTN trunk actually delivers: G.711 µ-law at 8 kHz ─────────────────
    const mulawPath = join(dir, 'dialin-leg-g711.wav');
    writeFileSync(mulawPath, makeWavMulaw8k(1));
    const mulaw = decodeWav(readFileSync(mulawPath));
    check('µ-law WAV decodes at 8 kHz', mulaw.sampleRate === 8_000 && mulaw.pcm.length === 8_000,
      `${mulaw.sampleRate} Hz / ${mulaw.pcm.length} samples`);
    check('µ-law expansion is in range', mulaw.pcm.every((v) => v >= -1 && v <= 1));
    check('µ-law 0xFF is the quietest positive code', Math.abs(muLawToFloat(0xff)) < 0.001, String(muLawToFloat(0xff)));
    const up = resampleTo16k(mulaw.pcm, 8_000);
    check('8 kHz upsamples to 16 kHz (2× the samples)', up.length === 16_000, String(up.length));
    check('a 16 kHz stream is returned untouched by the resampler', resampleTo16k(pcm, 16_000) === pcm);
    check('interleaved stereo folds to mono', toMono(Float32Array.from([1, -1, 0.5, -0.5]), 2).length === 2);

    // ── 6) refusals rather than a transcript of noise ────────────────────────────────
    check('a non-RIFF buffer is refused', /not a RIFF/.test((await threw(() => decodeWav(Buffer.from('nope')))) ?? ''));
    const bad = Buffer.from(makeWav16k(0.1));
    bad.writeUInt16LE(2, 20); // WAVE_FORMAT_ADPCM — a format we deliberately do not guess at
    check('an unsupported WAV format is refused by name',
      /unsupported format 2/.test((await threw(() => decodeWav(bad))) ?? ''));

    // ── 7) the DISPATCH contract is untouched — invocation.v1 still knows four platforms ──
    // A phone call is INBOUND: nothing dispatches a container at it, so `phone` must NOT become
    // a dispatchable platform by side effect of this spike. This is the negative control on the
    // contract itself, and it is what lets the widening live in PipelineInvocation alone.
    const invJson = JSON.stringify({ platform: 'phone', meetingUrl: 'tel:+15551234567', botName: 'Vexa', redisUrl: 'redis://x' });
    check('parseInvocation still REFUSES platform "phone" from VEXA_BOT_CONFIG',
      (await threw(() => parseInvocation(invJson))) !== null,
      'invocation.v1 accepted phone — the dispatch contract was widened by accident');
    check('parseInvocation still ACCEPTS the four dispatchable platforms',
      (await threw(() => parseInvocation(JSON.stringify({ platform: 'jitsi', meetingUrl: 'https://meet.jit.si/daily', botName: 'Vexa', redisUrl: 'redis://x' })))) === null);

    // ── 8) the dial-in address surfaces as the meeting's origin ──────────────────────
    check('a tel: address renders as a tel: URI', phoneMeetingUrl(CALL) === 'tel:+15551234567');
    check('a sip: address renders as a sip: URI',
      phoneMeetingUrl({ ...CALL, address: 'room-3@calls.example.org' }) === 'sip:room-3@calls.example.org');
  } finally {
    delete process.env.VEXA_PHONE_PLATFORM;
    rmSync(dir, { recursive: true, force: true });
  }

  console.log(failed ? `\n${failed} check(s) FAILED` : '\nphone-adapter: all checks passed');
  process.exit(failed ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
