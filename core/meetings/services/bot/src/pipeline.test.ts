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
import { createBotPipeline, createTranscribe } from './pipeline.js';
import type { Invocation } from './config.js';
import type { TranscriptSegment } from './contracts.js';
import type { TranscriptSink } from './ports.js';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import type { ChunkedTranscriberCallbacks, TeamsCsrcGmeetPipelineOptions } from '@vexa/mixed-pipeline';

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

/** A capturing bot-port TranscriptSink — records every published segment (confirmed + drafts) and
 *  every retracted id (the pending-tail withdrawal path). */
function captureSink(): TranscriptSink & { readonly published: TranscriptSegment[]; readonly retracted: string[] } {
  const published: TranscriptSegment[] = [];
  const retracted: string[] = [];
  return { published, retracted, async publish(seg) { published.push(seg); }, async retract(ids) { retracted.push(...ids); } };
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

  // ── 4) createTranscribe threads invocation.transcriptionModel → the STT wire (#522) ──
  // The one hop the bot owns: invocation.v1 → TranscriptionClient config. Observed at the wire
  // (stubbed fetch, real client), so the whole bot-side thread is closed, not just the client.
  {
    const realFetch = globalThis.fetch;
    const modelParts: Array<string | null> = [];
    (globalThis as any).fetch = async (_url: unknown, init: { body: Buffer }) => {
      const m = Buffer.from(init.body).toString('latin1').match(/name="model"\r\n\r\n([^\r]*)\r\n/);
      modelParts.push(m ? m[1] : null);
      return new Response(JSON.stringify({ text: '', language: 'en', duration: 0.1, segments: [] }), { status: 200 });
    };
    const pcm = new Float32Array(1600).fill(0.05);
    await createTranscribe(baseInv({ transcriptionServiceUrl: 'http://stt.test', transcriptionModel: 'whisper-large-v3-turbo' }))(pcm);
    await createTranscribe(baseInv({ transcriptionServiceUrl: 'http://stt.test' }))(pcm);
    (globalThis as any).fetch = realFetch;
    check('invocation.transcriptionModel rides the model form part', modelParts[0] === 'whisper-large-v3-turbo', JSON.stringify(modelParts[0]));
    check('no transcriptionModel → default whisper-1 (wire unchanged)', modelParts[1] === 'whisper-1', JSON.stringify(modelParts[1]));
  }

  // ── 5) LEGACY MIXED LANE (Zoom/Jitsi) speaker-label boundary (#890): a turn the lane has NOT
  //     yet attributed publishes under its provisional cluster id (speaker 'seg_N'). At the bot
  //     boundary that must become the stable 'Speaker' label — NEVER the seg_N string as a display
  //     name — so per-speaker consumers group unattributed turns as ONE speaker, not hundreds.
  //     segment_id/speaker_key keep the unique turn key (the repaint anchor for late attribution);
  //     a REAL name passes through untouched (the predicate only rewrites /^seg_\d+$/). The internal
  //     mixed-pipeline still uses seg_N as its key (claim.smoke.test.ts) — this rewrite is ABOVE it. ──
  {
    const sink = captureSink();
    let cb: ChunkedTranscriberCallbacks | null = null;
    const factory = async (c: ChunkedTranscriberCallbacks) => {
      cb = c;
      return { feedAudio() { /* stub */ }, recordHint() { /* stub */ }, async dispose() { /* stub */ } };
    };
    const pipe = createBotPipeline(baseInv({ platform: 'zoom' }), sink, { createMixedTranscriber: factory });
    await pipe.start();   // triggers the transcriber factory → captures the mixed lane's publish callback
    check('mixed lane: transcriber factory wired (publish callback captured)', !!cb, 'factory not called');

    // The mixed lane confirms an UNATTRIBUTED turn: speaker is the provisional cluster id seg_54,
    // the confirmed segment id is the unique turn key turn:54:0.
    cb!.publish('seg_54', [{ text: 'hello there', startMs: 1000, endMs: 2000, language: 'en', segmentId: 'turn:54:0' }], []);
    // …and later, a REAL name confirm — must survive verbatim.
    cb!.publish('Alice', [{ text: 'hi', startMs: 2000, endMs: 3000, language: 'en', segmentId: 'turn:55:0' }], []);
    await sleep(20);   // sink.publish() is async fire-and-forget out of the mixed lane's publish

    const junk = sink.published.find((s) => s.segment_id === 'turn:54:0');
    const real = sink.published.find((s) => s.segment_id === 'turn:55:0');
    // Founder ruling (rc.3 witness call): a row we could not attribute publishes an EMPTY speaker.
    // "Speaker" advertised a failed claim to the customer in their own transcript; a blank reads as
    // continuation. The refusal is not hidden — speaker_key below still carries the track identity,
    // and the observations sidecar carries why — it just stops being addressed to the reader.
    check('unattributed turn: speaker is EMPTY, never the seg_N id and never a "Speaker" placeholder',
      junk?.speaker === '', JSON.stringify(junk));
    check('unattributed turn: segment_id keeps the unique turn key (late-attribution repaint anchor)',
      junk?.segment_id === 'turn:54:0', junk?.segment_id);
    check('unattributed turn: speaker_key keeps the unique turn key — the identity survives the blank',
      junk?.speaker_key === 'turn:54:0', junk?.speaker_key);
    // The letter tracks are the other spelling of "a distinct person we could not name", and the
    // founder's ruling was about unknown speakers plural — they blank too.
    cb!.publish('Speaker B', [{ text: 'and this', startMs: 3000, endMs: 4000, language: 'en', segmentId: 'turn:56:0' }], []);
    await sleep(20);
    const letter = sink.published.find((s) => s.segment_id === 'turn:56:0');
    check('a letter track ("Speaker B") also publishes an empty speaker',
      letter?.speaker === '', JSON.stringify(letter));
    check('…and keeps its own identity in speaker_key', letter?.speaker_key === 'turn:56:0', letter?.speaker_key);
    check('no seg_N string ever leaks as a display speaker across the mixed lane',
      sink.published.every((s) => !/^seg_\d+$/.test(s.speaker ?? '')), JSON.stringify(sink.published.map((s) => s.speaker)));
    check('a REAL speaker name passes through the boundary untouched',
      real?.speaker === 'Alice', JSON.stringify(real));
    check('mixed-lane segments are transcript.v1-valid (ajv vs SSOT)',
      sink.published.length > 0 && sink.published.every((s) => !!validateSeg(s)), ajv.errorsText(validateSeg.errors));
    // REGRESSION (Teams/Zoom live render): the mixed lane must stamp absolute_start_time at the
    // producer, exactly as the gmeet lane does (check above). A null here makes the dashboard's live
    // renderer SKIP every pending draft (it keys on absolute time), so Teams transcripts only appeared
    // after a reload (the REST read re-derives it). The gmeet-only stamp fix once missed this mapper.
    check('mixed-lane segments carry absolute_start_time == epoch(start) (producer-stamped live-render key)',
      sink.published.length > 0 && sink.published.every((s) =>
        !!s.absolute_start_time &&
        Math.abs(new Date(s.absolute_start_time).getTime() / 1000 - (s.start ?? 0)) < 1),
      JSON.stringify(sink.published.map((s) => ({ id: s.segment_id, abs: s.absolute_start_time, start: s.start }))));
  }

  // ── 6) TEAMS CSRC/GMEET ADAPTER: production selects the virtual-channel lane, forwards every
  //     identity input the capture bridge already collects, preserves one stable speaker key per
  //     CSRC, and retracts a cleared draft. This is the composition seam module-only tests cannot
  //     see: it proves the live path selects the virtual-channel lane, not a diarizer. ──
  {
    const sink = captureSink();
    let options: TeamsCsrcGmeetPipelineOptions | null = null;
    const calls: string[] = [];
    let disposed = false;
    const factory = (received: TeamsCsrcGmeetPipelineOptions) => {
      options = received;
      return {
        feedMixedAudio: (_pcm: Float32Array, tsMs: number) => calls.push(`audio:${tsMs}`),
        recordTransportEvent: (event: { csrc: number; active: boolean; tMs: number }) => calls.push(`csrc:${event.csrc}:${event.active}`),
        recordHint: (name: string) => calls.push(`hint:${name}`),
        recordCaption: (name: string) => calls.push(`caption:${name}`),
        recordRosterName: (name: string) => calls.push(`roster:${name}`),
        recordRosterCoverage: (named: number, participants: number) => calls.push(`coverage:${named}/${participants}`),
        async dispose() { disposed = true; },
      };
    };
    let legacyMixedFactoryCalled = false;
    const pipe = createBotPipeline(baseInv({ platform: 'teams' }), sink, {
      createTeamsTranscriber: factory,
      createMixedTranscriber: async () => {
        legacyMixedFactoryCalled = true;
        throw new Error('Teams must not construct the legacy mixed lane');
      },
    });
    await pipe.start();
    pipe.feedMixedAudio(FRAME, 1_000);
    pipe.recordTransportEvent?.({ csrc: 201, active: true, tMs: 1_000 });
    pipe.recordHint('Alice', 1_050, false);
    pipe.recordCaptionName?.('Alice', 1_100);
    pipe.recordRosterName?.('Alice', 1_150);
    pipe.recordRosterCoverage?.(1, 2, 1_200);

    options!.onSegment({
      csrc: 201, speaker: 'Speaker A', sourceKey: 'csrc-201:1', segmentId: 'csrc-201:1:1000',
      text: 'forming', startMs: 1_000, endMs: 1_800, completed: false, language: 'en',
    });
    options!.onSegment({
      csrc: 201, speaker: 'Alice', sourceKey: 'csrc-201:1', segmentId: 'csrc-201:1:1000',
      text: 'confirmed words', startMs: 1_000, endMs: 2_000, completed: true, language: 'en',
    });
    options!.onSegment({
      csrc: 840, speaker: 'Speaker B', sourceKey: 'csrc-840:1', segmentId: 'csrc-840:1:2000',
      text: '', startMs: 2_000, endMs: 2_000, completed: false, language: 'en',
    });
    await sleep(20);

    check('Teams selects the CSRC/GMeet factory, never the legacy Pyannote-capable mixed factory',
      !!options && !legacyMixedFactoryCalled);
    check('Teams forwards mixed PCM plus CSRC, hint, caption, roster, and coverage evidence',
      ['audio:1000', 'csrc:201:true', 'hint:Alice', 'caption:Alice', 'roster:Alice', 'coverage:1/2']
        .every((entry) => calls.includes(entry)), JSON.stringify(calls));
    const named = sink.published.find((segment) => segment.text === 'confirmed words');
    check('Teams publishes the earned human name and a stable CSRC speaker key',
      named?.speaker === 'Alice' && named.speaker_key === 'csrc:201', JSON.stringify(named));
    check('Teams provisional Speaker A/B labels stay internal',
      sink.published.find((segment) => segment.text === 'forming')?.speaker === '', JSON.stringify(sink.published));
    check('Teams segment is transcript.v1-valid and producer-stamped for live rendering',
      !!named && !!validateSeg(named) && named.absolute_start_time === new Date(1_000).toISOString(),
      `${JSON.stringify(named)} ${ajv.errorsText(validateSeg.errors)}`);
    check('Teams cleared draft retracts its exact durable id',
      sink.retracted.includes('csrc-840:1:2000'), JSON.stringify(sink.retracted));
    await pipe.stop();
    check('Teams stop flushes/disposes the CSRC/GMeet lane', disposed);
  }

  // ── 7) LEGACY MIXED LANE pending RETRACTION (transcript de-dup): the mixed lane republishes its pending
  //     tail as a FULL-REPLACE block. The bot's egress is append-only + the terminal upserts by id, so a
  //     draft id that DROPS OUT of the block (confirmed under a new seq id, tail shrank, turn closed) must
  //     be RETRACTED or it lingers as a stale "unattached" duplicate (and an over-read past the turn
  //     boundary re-appears when the next turn transcribes the same audio). Assert the diff → retract. ──
  {
    const sink = captureSink();
    let cb: ChunkedTranscriberCallbacks | null = null;
    const factory = async (c: ChunkedTranscriberCallbacks) => {
      cb = c;
      return { feedAudio() { /* stub */ }, recordHint() { /* stub */ }, async dispose() { /* stub */ } };
    };
    const pipe = createBotPipeline(baseInv({ platform: 'zoom' }), sink, { createMixedTranscriber: factory });
    await pipe.start();
    const seg = (id: string, s: number, e: number) => ({ text: 't', startMs: s, endMs: e, language: 'en', segmentId: id });

    // Open turn: two pending drafts published (nothing departed yet).
    cb!.publishPending('Speaker', [seg('turn:1:p0', 1000, 2000), seg('turn:1:p1', 2000, 3000)]);
    // A confirm lands: the leading draft confirms under a NEW seq id and the tail SHRINKS to one — the
    // dropped draft (turn:1:p1) must be retracted, the surviving draft (turn:1:p0) must NOT.
    cb!.publish('Speaker', [seg('turn:1:0', 1000, 2000)], [seg('turn:1:p0', 2000, 2500)]);
    // Turn closes: pending emptied → the last surviving draft is retracted; only confirmed seq ids remain.
    cb!.clearPending();
    await sleep(20);

    check('retraction: a draft that dropped out of the pending block was retracted (turn:1:p1)',
      sink.retracted.includes('turn:1:p1'), JSON.stringify(sink.retracted));
    check('retraction: the surviving-then-closed draft was retracted on close (turn:1:p0)',
      sink.retracted.includes('turn:1:p0'), JSON.stringify(sink.retracted));
    check('retraction: a CONFIRMED seq segment is NEVER retracted (durable content survives)',
      !sink.retracted.includes('turn:1:0'), JSON.stringify(sink.retracted));
    check('retraction: the confirmed segment WAS published (real content kept)',
      sink.published.some((s) => s.segment_id === 'turn:1:0' && s.completed), JSON.stringify(sink.published.map((s) => s.segment_id)));
  }

  if (failed) { console.error(`\n❌ pipeline (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ pipeline (L3): capture→lane→stt→bot.TranscriptSink.publish emits schema-valid, correctly-attributed transcript.v1 segments (real gmeet lane · mock stt · capturing sink).');
}

void main();
