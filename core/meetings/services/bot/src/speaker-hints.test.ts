/**
 * L2/L3 — the mixed-lane speaker-hint wiring, OFFLINE (no browser, no pyannote, no whisper).
 *
 * Pins the seams #498 names:
 *   • C2 kind fidelity: the platform's TRUE hint kind reaches the transcriber —
 *     'dom-outline' for Teams, 'dom-active' for Zoom AND jitsi (observed via an
 *     injected MixedTranscriberFactory at the exact recordHint seam);
 *   • C1 counters: received advances per hint; matched/missed advance on the
 *     transcriber's onHintOutcome; a hint with no overlapping turn counts missed;
 *   • C3 clock guard: an implausibly-skewed (non-epoch) hint tMs is re-stamped to
 *     epoch with a LOUD warning — never silently bound to nothing; epoch times and
 *     the undefined-tMs fallback pass through;
 *   • C5 host parity: one scripted timeline (audio + boundaries + hints) through the
 *     desktop-style wiring (ChunkedTranscriber + 'dom-outline' directly — the
 *     extension's shape, clients/extension/src/inpage.ts) and through the bot's
 *     teams lane produces identical named segments.
 * Run: npx tsx src/speaker-hints.test.ts
 */
import { ChunkedTranscriber, type BoundarySource, type ChunkSegment } from '@vexa/mixed-pipeline';
import type { BoundaryEvent } from '@vexa/mixed-pipeline';
import { createBotPipeline, hintKindForPlatform, type BotPipeline } from './pipeline.js';
import { makeSpeakerHintSink } from './capture-bridge.js';
import type { Invocation } from './config.js';
import type { TranscriptSink } from './ports.js';
import type { TranscriptSegment } from './contracts.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const inv = (platform: Invocation['platform']): Invocation => ({
  platform, meetingUrl: 'https://example.test/m', botName: 'Vexa',
  redisUrl: 'redis://localhost:6379', transcribeEnabled: false,
});
const nullSink: TranscriptSink = { async publish() { /* discard */ } };

/** A spy transcriber factory — records exactly what the bot forwards. */
function spyFactory() {
  const hints: { name: string; kind: string; tMs: number; isEnd?: boolean }[] = [];
  let cb: Parameters<NonNullable<Parameters<typeof createBotPipeline>[2]['createMixedTranscriber']>>[0] | null = null;
  const factory = async (c: typeof cb & object) => {
    cb = c;
    return {
      feedAudio() { /* not under test */ },
      recordHint(name: string, kind: string, tMs: number, isEnd = false) { hints.push({ name, kind, tMs, isEnd }); },
      async dispose() { /* nothing */ },
    };
  };
  return { hints, factory: factory as NonNullable<Parameters<typeof createBotPipeline>[2]['createMixedTranscriber']>, getCb: () => cb };
}

async function main(): Promise<void> {
  // ── C2: kind fidelity per platform at the transcriber seam ──
  console.log('C2 — platform hint kind reaches the transcriber');
  check("hintKindForPlatform('teams') == 'dom-outline'", hintKindForPlatform('teams') === 'dom-outline');
  check("hintKindForPlatform('zoom') == 'dom-active'", hintKindForPlatform('zoom') === 'dom-active');
  check("hintKindForPlatform('jitsi') == 'dom-active' (jitsi lane preserved)", hintKindForPlatform('jitsi') === 'dom-active');
  check("hintKindForPlatform('telemost') == 'dom-active'", hintKindForPlatform('telemost') === 'dom-active');
  for (const [platform, kind] of [['teams', 'dom-outline'], ['zoom', 'dom-active'], ['jitsi', 'dom-active']] as const) {
    const spy = spyFactory();
    const pipe = createBotPipeline(inv(platform), nullSink, { createMixedTranscriber: spy.factory });
    await pipe.start();
    pipe.recordHint('Alice', 1234567890123);
    pipe.recordHint('Alice', 1234567891123, true);
    await pipe.stop();
    check(`${platform}: recordHint forwards kind='${kind}' with name+tMs+isEnd intact`,
      spy.hints.length === 2 && spy.hints.every((h) => h.kind === kind)
      && spy.hints[0].name === 'Alice' && spy.hints[0].tMs === 1234567890123 && spy.hints[0].isEnd === false
      && spy.hints[1].isEnd === true,
      JSON.stringify(spy.hints));
  }

  // ── C1: counters — received per hint; matched/missed via onHintOutcome ──
  console.log('C1 — hint-hop counters');
  {
    const spy = spyFactory();
    const pipe = createBotPipeline(inv('teams'), nullSink, { createMixedTranscriber: spy.factory });
    await pipe.start();
    pipe.recordHint('Alice', Date.now());
    pipe.recordHint('Bob', Date.now());
    check('received advances per pipeline-received hint', pipe.hintCounters?.received === 2, JSON.stringify(pipe.hintCounters));
    const cb = spy.getCb()!;
    cb.onHintOutcome?.({ name: 'Alice', kind: 'dom-outline', tMs: Date.now(), outcome: 'matched' });
    cb.onHintOutcome?.({ name: 'Ghost', kind: 'dom-outline', tMs: Date.now(), outcome: 'missed' });
    check('binder outcomes advance matched/missed', pipe.hintCounters?.matched === 1 && pipe.hintCounters?.missed === 1, JSON.stringify(pipe.hintCounters));
    await pipe.stop();
  }
  {
    // End-to-end missed: the REAL transcriber (injected segmenter, no model) — a hint
    // with no overlapping turn increments `missed`, loudly countable.
    const pipe = createBotPipeline(inv('teams'), nullSink, {
      createMixedTranscriber: (cb) => ChunkedTranscriber.create({
        ...cb,
        makeSegmenter: async (): Promise<BoundarySource> => ({ appendFrame: async () => { /* scripted */ }, reset: () => { /* scripted */ } }),
        log: () => { /* quiet */ },
      }),
    });
    await pipe.start();
    pipe.recordHint('Nobody Yet', Date.now());
    check('real transcriber: hint with no overlapping turn → missed', pipe.hintCounters?.missed === 1 && pipe.hintCounters?.received === 1, JSON.stringify(pipe.hintCounters));
    await pipe.stop();
  }

  // ── C3: the epoch clock guard at the bridge seam ──
  console.log('C3 — hint/audio clock contract (epoch ms)');
  {
    const got: { name: string; tMs: number; isEnd?: boolean }[] = [];
    const warns: string[] = [];
    const target: Pick<BotPipeline, 'recordHint'> = { recordHint: (name, tMs, isEnd) => got.push({ name, tMs, isEnd }) };
    const { sink, crossed } = makeSpeakerHintSink(target, (m) => warns.push(m));
    const epoch = Date.now();
    sink('Alice', epoch);                       // same clock domain — passes through untouched
    sink('Bob', 12345);                         // performance.now()-shaped — implausible skew
    sink('Carol', undefined, true);             // no page stamp — Node epoch fallback
    check('bridge-crossed counter counts every arrival', crossed() === 3, String(crossed()));
    check('epoch tMs passes through unchanged', got[0]?.tMs === epoch, String(got[0]?.tMs));
    check('non-epoch tMs re-stamped to epoch (never silently binds nothing)',
      got[1] !== undefined && Math.abs(got[1].tMs - Date.now()) < 5000, String(got[1]?.tMs));
    check('the skew warns LOUDLY, typed', warns.length === 1 && /hint-clock-skew/.test(warns[0] ?? ''), JSON.stringify(warns));
    check('undefined tMs falls back to Node epoch', got[2] !== undefined && Math.abs(got[2].tMs - Date.now()) < 5000 && got[2].isEnd === true, JSON.stringify(got[2]));
  }

  // ── C5: host parity — one timeline, desktop wiring vs bot wiring, identical output ──
  console.log('C5 — desktop-vs-bot differential (same fixture, diffed segments)');
  {
    const stubTranscribe = async () => ({
      text: 'hello from the fixture', language: 'en', duration: 2,
      segments: [{ start: 0, end: 2, text: 'hello from the fixture' }],
    });
    interface Host { published: { speaker: string; text: string; completed: boolean }[]; feed(pcm: Float32Array, t: number): void; hint(name: string, t: number, isEnd?: boolean): void; boundary(ev: BoundaryEvent): void; stop(): Promise<void> }

    // Desktop-style host: ChunkedTranscriber wired the way the extension wires it
    // (direct recordHint with the platform kind — inpage.ts posts kind 'dom-outline').
    const makeDesktopHost = async (): Promise<Host> => {
      let emit!: (ev: BoundaryEvent) => void;
      const published: Host['published'] = [];
      const push = (speaker: string, segs: ChunkSegment[], completed: boolean) => { for (const s of segs) published.push({ speaker, text: s.text, completed }); };
      const tc = await ChunkedTranscriber.create({
        language: 'en', transcribe: stubTranscribe,
        publish: (sp, confirmed, pending) => { push(sp, confirmed, true); push(sp, pending, false); },
        publishPending: (sp, segs) => push(sp, segs, false),
        clearPending: () => { /* fixture */ }, rename: (_o, n, segs) => push(n, segs, true),
        makeSegmenter: async (onB): Promise<BoundarySource> => { emit = onB; return { appendFrame: async () => { /* scripted */ }, reset: () => { /* scripted */ } }; },
        log: () => { /* quiet */ },
      });
      return { published, feed: (p, t) => tc.feedAudio(p, t), hint: (n, t, e) => tc.recordHint(n, 'dom-outline', t, e), boundary: (ev) => emit(ev), stop: () => tc.dispose() };
    };

    // Bot host: the REAL bot teams lane (createBotPipeline) with only the segmenter scripted.
    const makeBotHost = async (): Promise<Host> => {
      let emit!: (ev: BoundaryEvent) => void;
      const published: Host['published'] = [];
      const sink: TranscriptSink = { async publish(seg: TranscriptSegment) { published.push({ speaker: seg.speaker, text: seg.text, completed: !!seg.completed }); } };
      const pipe = createBotPipeline(inv('teams'), sink, {
        transcribe: stubTranscribe,
        createMixedTranscriber: (cb) => ChunkedTranscriber.create({
          ...cb, transcribe: stubTranscribe,
          makeSegmenter: async (onB): Promise<BoundarySource> => { emit = onB; return { appendFrame: async () => { /* scripted */ }, reset: () => { /* scripted */ } }; },
          log: () => { /* quiet */ },
        }),
      });
      await pipe.start();
      return { published, feed: (p, t) => pipe.feedMixedAudio(p, t), hint: (n, t, e) => pipe.recordHint(n, t, e), boundary: (ev) => emit(ev), stop: () => pipe.stop() };
    };

    // ONE scripted timeline through both hosts.
    const drive = async (host: Host): Promise<string> => {
      const frame = new Float32Array(1600).fill(0.05);   // 100ms @16k, above DROP_RMS
      for (let t = 10000; t < 13000; t += 100) host.feed(frame, t);
      host.boundary({ tMs: 10000, kind: 'silence→speaker', confidence: 0.9 });
      await sleep(50);
      host.hint('Alice Fixture', 11000);
      host.boundary({ tMs: 13000, kind: 'speaker→silence', confidence: 0.9 });
      await sleep(150);
      await host.stop();
      const confirmed = host.published.filter((p) => p.completed).map((p) => `${p.speaker}|${p.text}`).sort();
      return JSON.stringify(confirmed);
    };
    const desktopOut = await drive(await makeDesktopHost());
    const botOut = await drive(await makeBotHost());
    check('desktop and bot wiring publish IDENTICAL named segments', desktopOut === botOut && desktopOut.includes('Alice Fixture'),
      `desktop=${desktopOut} bot=${botOut}`);
  }

  console.log(failed === 0 ? '\n✅ speaker-hints: all green' : `\n❌ speaker-hints: ${failed} failure(s)`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e?.stack || e); process.exit(1); });
