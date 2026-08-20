/**
 * L2/L3 — the platform speaker-evidence wiring, OFFLINE (no browser, no pyannote, no whisper).
 *
 * Pins the seams #498 names:
 *   • C2 route fidelity: Teams hints reach the CSRC/GMeet lane without touching the
 *     legacy mixed/Pyannote factory; Zoom and Jitsi retain their true `dom-active` kind;
 *   • C1 counters: received advances per hint on Teams; matched/missed remain a
 *     legacy mixed-lane binder instrument for Zoom/Jitsi;
 *   • C3 clock guard: an implausibly-skewed (non-epoch) hint tMs is re-stamped to
 *     epoch with a LOUD warning — never silently bound to nothing; epoch times and
 *     the undefined-tMs fallback pass through;
 *   • C5 Teams composition: mixed PCM, transport/name evidence, transcript publication,
 *     and teardown all cross the injected CSRC/GMeet transcriber seam.
 * Run: npx tsx src/speaker-hints.test.ts
 */
import { ChunkedTranscriber, type BoundarySource, type TeamsCsrcGmeetPipelineOptions } from '@vexa/mixed-pipeline';
import { createBotPipeline, hintKindForPlatform, type BotPipeline } from './pipeline.js';
import { makeSpeakerHintSink } from './capture-bridge.js';
import type { Invocation } from './config.js';
import type { TranscriptSink } from './ports.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const inv = (platform: Invocation['platform']): Invocation => ({
  platform, meetingUrl: 'https://example.test/m', botName: 'Vexa',
  redisUrl: 'redis://localhost:6379', transcribeEnabled: false,
});
const nullSink: TranscriptSink = { async publish() { /* discard */ } };

/** A spy transcriber factory — records exactly what the bot forwards. */
function mixedSpyFactory() {
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

/** A spy for the production Teams-only CSRC/GMeet construction seam. */
function teamsSpyFactory() {
  const hints: { name: string; tMs: number; isEnd?: boolean }[] = [];
  const audio: { samples: number; tMs: number }[] = [];
  const transport: { csrc: number; active: boolean; tMs: number }[] = [];
  let options: TeamsCsrcGmeetPipelineOptions | null = null;
  let disposed = false;
  const factory = (value: TeamsCsrcGmeetPipelineOptions) => {
    options = value;
    return {
      feedMixedAudio(pcm: Float32Array, tMs: number) { audio.push({ samples: pcm.length, tMs }); },
      recordTransportEvent(event: { csrc: number; active: boolean; tMs: number }) { transport.push(event); },
      recordHint(name: string, tMs: number, isEnd = false) { hints.push({ name, tMs, isEnd }); },
      recordCaption() { /* not under test */ },
      recordRosterName() { /* not under test */ },
      recordRosterCoverage() { /* not under test */ },
      async dispose() { disposed = true; },
    };
  };
  return { hints, audio, transport, factory, getOptions: () => options, isDisposed: () => disposed };
}

async function main(): Promise<void> {
  // ── C2: platform evidence reaches the correct transcriber seam ──
  console.log('C2 — platform evidence reaches the correct transcriber');
  check("hintKindForPlatform('teams') == 'dom-outline'", hintKindForPlatform('teams') === 'dom-outline');
  check("hintKindForPlatform('zoom') == 'dom-active'", hintKindForPlatform('zoom') === 'dom-active');
  check("hintKindForPlatform('jitsi') == 'dom-active' (jitsi lane preserved)", hintKindForPlatform('jitsi') === 'dom-active');
  {
    const spy = teamsSpyFactory();
    const pipe = createBotPipeline(inv('teams'), nullSink, {
      createTeamsTranscriber: spy.factory,
      // A legacy factory must never be consulted on Teams.
      createMixedTranscriber: async () => { throw new Error('legacy mixed factory selected for Teams'); },
    });
    await pipe.start();
    pipe.recordHint('Alice', 1234567890123);
    pipe.recordHint('Alice', 1234567891123, true);
    await pipe.stop();
    check('teams: recordHint reaches the CSRC/GMeet lane with name+tMs+isEnd intact',
      spy.hints.length === 2
      && spy.hints[0].name === 'Alice' && spy.hints[0].tMs === 1234567890123 && spy.hints[0].isEnd === false
      && spy.hints[1].isEnd === true,
      JSON.stringify(spy.hints));
  }
  // Zoom is no longer on the mixed transcriber (it rides the per-track lane; its watcher→resolver
  // path is covered by zoom-speaker-wiring.test.ts) — and Teams rides the CSRC/GMeet lane above.
  // Only jitsi forwards hints to the legacy mixed recordHint.
  for (const platform of ['jitsi'] as const) {
    const kind = 'dom-active';
    const spy = mixedSpyFactory();
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
    const spy = teamsSpyFactory();
    const pipe = createBotPipeline(inv('teams'), nullSink, { createTeamsTranscriber: spy.factory });
    await pipe.start();
    pipe.recordHint('Alice', Date.now());
    pipe.recordHint('Bob', Date.now());
    check('received advances per pipeline-received hint', pipe.hintCounters?.received === 2, JSON.stringify(pipe.hintCounters));
    check('Teams forwards both hints through its production seam', spy.hints.length === 2, JSON.stringify(spy.hints));
    await pipe.stop();
  }
  {
    // The matched/missed binder outcome is still truthful on the legacy mixed (Jitsi) lane.
    // (Zoom left the mixed lane for per-track; Teams rides CSRC — jitsi is the sole legacy mixed.)
    const pipe = createBotPipeline(inv('jitsi'), nullSink, {
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

  // ── C5: the bot's Teams adapter crosses every production CSRC/GMeet seam ──
  console.log('C5 — Teams CSRC/GMeet bot composition');
  {
    const spy = teamsSpyFactory();
    const published: Array<{ speaker: string; speakerKey?: string; text: string; completed?: boolean }> = [];
    const sink: TranscriptSink = { async publish(segment) {
      published.push({ speaker: segment.speaker, speakerKey: segment.speaker_key, text: segment.text, completed: segment.completed });
    } };
    const pipe = createBotPipeline(inv('teams'), sink, { createTeamsTranscriber: spy.factory });
    await pipe.start();
    pipe.feedMixedAudio(new Float32Array(1600).fill(0.05), 10_000);
    pipe.recordTransportEvent?.({ csrc: 201, active: true, tMs: 10_000 });
    pipe.recordHint('Alice Fixture', 10_100);
    spy.getOptions()!.onSegment({
      csrc: 201, speaker: 'Alice Fixture', sourceKey: 'csrc-201', segmentId: 'csrc-201:0',
      text: 'hello from the fixture', startMs: 10_000, endMs: 12_000, completed: true, language: 'en',
    });
    await pipe.stop();
    check('mixed PCM reaches the Teams transcriber', spy.audio.length === 1 && spy.audio[0].samples === 1600, JSON.stringify(spy.audio));
    check('CSRC activity reaches the Teams transcriber', spy.transport.length === 1 && spy.transport[0].csrc === 201, JSON.stringify(spy.transport));
    check('Teams rows publish with the stable CSRC speaker key',
      published.length === 1 && published[0].speaker === 'Alice Fixture'
      && published[0].speakerKey === 'csrc:201' && published[0].text === 'hello from the fixture'
      && published[0].completed === true,
      JSON.stringify(published));
    check('Teams teardown disposes the CSRC/GMeet lane', spy.isDisposed());
  }

  console.log(failed === 0 ? '\n✅ speaker-hints: all green' : `\n❌ speaker-hints: ${failed} failure(s)`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e?.stack || e); process.exit(1); });
