/**
 * L2/L3 — transcription language (transcription-language.v1) in the bot. OFFLINE: no browser,
 * whisper or redis.
 *   • the three modes at the STT call: auto sends no language, forced pins every call, restricted
 *     keeps an in-list detection and re-runs an out-of-list one pinned to the fallback;
 *   • reconfigure: present keys replace, absent keys keep, malformed acts are refused unchanged;
 *   • composition: an acts.v1 reconfigure through the tee reaches the transcribe the lane holds
 *     (real TranscriptionClient, stubbed fetch — observed at the wire);
 *   • the mixed lane's segment stamp follows a live change (real ChunkedTranscriber, fake cutter).
 * Run: npx tsx src/transcription-language.test.ts
 */
import { ChunkedTranscriber, type BoundarySource, type ChunkedTranscriberCallbacks, type TeamsCsrcGmeetPipelineOptions } from '@vexa/mixed-pipeline';
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import { parseInvocation, InvocationError, type Invocation } from './config.js';
import type { Act, TranscriptSegment } from './contracts.js';
import { teeActs } from './index.js';
import { createBotPipeline, createTranscribe, languageControlFrom } from './pipeline.js';
import type { ActsSource, TranscriptSink } from './ports.js';
import {
  createLanguageControl,
  languageActHandler,
  languageAwareTranscribe,
  summarize,
  LanguageSettingError,
  type LanguageObservation,
  type SttCall,
} from './transcription-language.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const PCM = new Float32Array(1600).fill(0.05);

/** A fake STT: records the language of every call; detects `detect` when unpinned. */
function fakeStt(detect: string | undefined) {
  const calls: Array<string | undefined> = [];
  const stt: SttCall = async (_pcm, language) => {
    calls.push(language);
    return { text: `t-${language ?? 'auto'}`, language: language ?? (detect as string), duration: 0.1, segments: [] };
  };
  return { stt, calls };
}

const quiet = () => { /* swallow the fallback log line in unit cases */ };

// ── 1) the modes at the STT call ──
{
  const auto = fakeStt('fr');
  await languageAwareTranscribe(auto.stt, createLanguageControl({}), quiet)(PCM);
  check('auto: the call carries no language', auto.calls.length === 1 && auto.calls[0] === undefined, JSON.stringify(auto.calls));

  const forced = fakeStt('fr');
  const tf = languageAwareTranscribe(forced.stt, createLanguageControl({ language: 'de' }), quiet);
  await tf(PCM); await tf(PCM, 'prompt');
  check('forced: every call pinned to the language', JSON.stringify(forced.calls) === '["de","de"]', JSON.stringify(forced.calls));

  const inList = fakeStt('en');
  const ti = languageAwareTranscribe(inList.stt, createLanguageControl({ allowedLanguages: ['de', 'en'] }), quiet);
  const ri = await ti(PCM);
  check('restricted: an in-list detection is kept without a second call',
    inList.calls.length === 1 && inList.calls[0] === undefined && ri.language === 'en' && ti.fallbacks === 0, JSON.stringify(inList.calls));

  const outWithLang = fakeStt('fr');
  const lines: string[] = [];
  const to = languageAwareTranscribe(outWithLang.stt, createLanguageControl({ language: 'en', allowedLanguages: ['de', 'en'] }), (l) => lines.push(l));
  const ro = await to(PCM);
  check('restricted: an out-of-list detection re-runs pinned to `language`',
    outWithLang.calls.length === 2 && outWithLang.calls[0] === undefined && outWithLang.calls[1] === 'en' && ro.language === 'en',
    JSON.stringify(outWithLang.calls));
  check('restricted: the fallback is counted and logged', to.fallbacks === 1 && lines.length === 1 && /re-run pinned to en \(fallback #1\)/.test(lines[0]), `${to.fallbacks} ${JSON.stringify(lines)}`);

  const outFirst = fakeStt('fr');
  await languageAwareTranscribe(outFirst.stt, createLanguageControl({ allowedLanguages: ['de', 'en'] }), quiet)(PCM);
  check('restricted without language: fallback is the first list entry',
    outFirst.calls.length === 2 && outFirst.calls[0] === undefined && outFirst.calls[1] === 'de', JSON.stringify(outFirst.calls));

  const missing = fakeStt('');
  await languageAwareTranscribe(missing.stt, createLanguageControl({ allowedLanguages: ['de', 'en'] }), quiet)(PCM);
  check('restricted: a missing detection falls back too', missing.calls.length === 2 && missing.calls[1] === 'de', JSON.stringify(missing.calls));

  const single = fakeStt('fr');
  await languageAwareTranscribe(single.stt, createLanguageControl({ allowedLanguages: ['de'] }), quiet)(PCM);
  check('restricted to one code: pinned, one call', JSON.stringify(single.calls) === '["de"]', JSON.stringify(single.calls));
}

// ── 2) reconfigure semantics ──
{
  const { stt, calls } = fakeStt('fr');
  const control = createLanguageControl({ language: 'en' });
  const logs: string[] = [];
  const errors: string[] = [];
  const observed: LanguageObservation[] = [];
  const handle = languageActHandler(control, { log: (l) => logs.push(l), error: (l) => errors.push(l), observe: (o) => observed.push(o) });
  const transcribe = languageAwareTranscribe(stt, control, quiet);
  const last = () => calls[calls.length - 1];

  handle({ action: 'reconfigure', language: 'de', allowedLanguages: [] });
  await transcribe(PCM);
  check('reconfigure {de, []} → next call carries de', last() === 'de', String(last()));
  check('applied change logs old → new', logs[0] === '[bot] transcription language: forced en → forced de (reconfigure)', JSON.stringify(logs));
  check('applied change is observed', observed.length === 1 && observed[0].language === 'de' && observed[0].cause === 'reconfigure', JSON.stringify(observed));

  handle({ action: 'reconfigure', language: null, allowedLanguages: [] });
  await transcribe(PCM);
  check('reconfigure {null, []} → next call carries none', calls.length === 2 && last() === undefined, JSON.stringify(calls));

  handle({ action: 'reconfigure', allowedLanguages: ['de', 'en'] });
  check('partial act (list only) keeps language', control.get().language === null && control.get().allowedLanguages.join() === 'de,en', summarize(control.get()));
  handle({ action: 'reconfigure', language: 'en' });
  check('partial act (language only) keeps the list', control.get().language === 'en' && control.get().allowedLanguages.join() === 'de,en', summarize(control.get()));

  const before = summarize(control.get());
  const n = errors.length;
  handle({ action: 'reconfigure', language: 'German' });
  handle({ action: 'reconfigure', allowedLanguages: 'de' as unknown as string[] });
  handle({ action: 'reconfigure', language: 'fr' });
  check('three malformed acts refused loudly', errors.length === n + 3 && errors.slice(n).every((e) => /REFUSED/.test(e)), JSON.stringify(errors.slice(n)));
  check('refused acts change nothing', summarize(control.get()) === before, `${summarize(control.get())} vs ${before}`);
  const callsBefore = calls.length;
  await transcribe(PCM);
  check('the next call after a refusal is unchanged (restricted, unpinned first)', calls[callsBefore] === undefined && calls.length === callsBefore + 2 && last() === 'en', JSON.stringify(calls.slice(callsBefore)));

  const untouchedLogs = logs.length;
  handle({ action: 'reconfigure', task: 'translate' });
  check('task-only act leaves the setting and says task is unsupported', logs.length === untouchedLogs && /task "translate" is not supported/.test(errors[errors.length - 1]), JSON.stringify(errors.slice(-1)));
  handle({ action: 'leave' } as Act);
  check('other acts are ignored', logs.length === untouchedLogs);

  // in-flight calls finish with the setting they started with
  let release: () => void = () => {};
  const seen: Array<string | undefined> = [];
  const slow: SttCall = async (_p, language) => { seen.push(language); await new Promise<void>((r) => { release = r; }); return { text: '', language: language ?? 'en', duration: 0, segments: [] }; };
  const c2 = createLanguageControl({ language: 'en' });
  const t2 = languageAwareTranscribe(slow, c2, quiet);
  const inflight = t2(PCM);
  c2.apply({ action: 'reconfigure', language: 'de' });
  release();
  await inflight;
  check('an in-flight call keeps the language it started with', seen[0] === 'en', JSON.stringify(seen));
}

// ── 3) seeds: invocation validation + control seed ──
{
  const base = { platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'Vexa', redisUrl: 'redis://localhost:6379' };
  const parse = (over: Record<string, unknown>) => { try { return parseInvocation(JSON.stringify({ ...base, ...over })); } catch (e) { return e as Error; } };
  check('invocation: allowedLanguages array of codes accepted', !((parse({ language: 'de', allowedLanguages: ['de', 'en'] })) instanceof Error));
  check('invocation: allowedLanguages non-array refused', parse({ allowedLanguages: 'de' }) instanceof InvocationError);
  check('invocation: non-code entry refused', parse({ allowedLanguages: ['German'] }) instanceof InvocationError);
  check('invocation: language outside the list refused', parse({ language: 'fr', allowedLanguages: ['de', 'en'] }) instanceof InvocationError);
  check('invocation: non-code language refused', parse({ language: 'en-US' }) instanceof InvocationError);
  let threw: unknown = null;
  try { createLanguageControl({ language: 'German' }); } catch (e) { threw = e; }
  check('control: an off-contract seed throws', threw instanceof LanguageSettingError);
}

// ── 4) composition: acts source → tee → control → the transcribe the lane holds (wire-level) ──
{
  const inv: Invocation = {
    platform: 'teams', meetingUrl: 'https://teams.microsoft.com/l/meetup-join/x', botName: 'Vexa',
    redisUrl: 'redis://localhost:6379', transcribeEnabled: true, transcriptionServiceUrl: 'http://stt.test', language: 'en',
  };
  const realFetch = globalThis.fetch;
  const wireLanguages: Array<string | null> = [];
  (globalThis as any).fetch = async (_url: unknown, init: { body: Buffer }) => {
    const m = Buffer.from(init.body).toString('latin1').match(/name="language"\r\n\r\n([^\r]*)\r\n/);
    wireLanguages.push(m ? m[1] : null);
    return new Response(JSON.stringify({ text: 'ok', language: m ? m[1] : 'en', duration: 0.1, segments: [] }), { status: 200 });
  };
  try {
    const control = languageControlFrom(inv);
    let laneTranscribe: TeamsCsrcGmeetPipelineOptions['transcribe'] | null = null;
    const sink: TranscriptSink = { async publish() {}, async retract() {} };
    createBotPipeline(inv, sink, {
      languageControl: control,
      createTeamsTranscriber: (options) => {
        laneTranscribe = options.transcribe;
        return { feedMixedAudio() {}, recordTransportEvent() {}, recordHint() {}, recordCaption() {}, recordRosterName() {}, recordRosterCoverage() {}, async dispose() {} };
      },
    });
    let fire: (act: Act) => void | Promise<void> = () => {};
    const source: ActsSource = { subscribe(h) { fire = h; return () => {}; } };
    const orchestratorSaw: Act[] = [];
    const acts = teeActs(source, { 'transcription-language': languageActHandler(control, { log: quiet }) });
    acts.subscribe((act) => { orchestratorSaw.push(act); });

    await laneTranscribe!(PCM);
    fire({ action: 'reconfigure', language: 'de', allowedLanguages: [] });
    await sleep(5);
    await laneTranscribe!(PCM);
    fire({ action: 'reconfigure', language: null });
    await sleep(5);
    await laneTranscribe!(PCM);
    check('wire: invocation language, then the reconfigured one, then none',
      JSON.stringify(wireLanguages) === '["en","de",null]', JSON.stringify(wireLanguages));
    check('the orchestrator still sees every act', orchestratorSaw.length === 2);
  } finally {
    (globalThis as any).fetch = realFetch;
  }
}

// ── 5) mixed lane: the segment stamp follows a live change ──
{
  const inv: Invocation = {
    platform: 'jitsi', meetingUrl: 'https://meet.jit.si/Room', botName: 'Vexa',
    redisUrl: 'redis://localhost:6379', transcribeEnabled: true, transcriptionServiceUrl: 'http://stt.test',
  };
  const control = languageControlFrom(inv);
  const sttLanguages: Array<string | undefined> = [];
  // A speaker the model always hears as English: only an explicit language can restamp it.
  const stt: SttCall = async (_pcm, language): Promise<TranscriptionResult> => {
    sttLanguages.push(language);
    return { text: `turn ${sttLanguages.length}`, language: 'en', language_probability: 0.9, duration: 2, segments: [] };
  };
  let emit: (ev: { tMs: number; kind: 'silence→speaker' | 'speaker→silence'; confidence: number }) => void = () => {};
  const published: TranscriptSegment[] = [];
  const sink: TranscriptSink = { async publish(seg) { published.push(seg); }, async retract() {} };
  const pipeline = createBotPipeline(inv, sink, {
    languageControl: control,
    transcribe: createTranscribe(inv, control, stt),
    createMixedTranscriber: (cb: ChunkedTranscriberCallbacks) => {
      cb.makeSegmenter = async (onBoundary): Promise<BoundarySource> => {
        emit = onBoundary as typeof emit;
        return { appendFrame: async () => {}, reset: () => {} };
      };
      return ChunkedTranscriber.create(cb);
    },
  });
  await pipeline.start();
  const half = new Float32Array(8000).fill(0.1);
  const turn = async (baseMs: number) => {
    emit({ tMs: baseMs, kind: 'silence→speaker', confidence: 0.9 });
    await sleep(25);
    for (let t = baseMs; t < baseMs + 2000; t += 500) pipeline.feedMixedAudio(half, t);
    emit({ tMs: baseMs + 2000, kind: 'speaker→silence', confidence: 0.9 });
    await sleep(200);
  };
  const base = Date.now() - 60_000;
  await turn(base);
  languageActHandler(control, { log: quiet })({ action: 'reconfigure', language: 'de' });
  await turn(base + 10_000);
  await pipeline.stop();
  const first = published.find((s) => s.text === 'turn 1');
  const second = published.find((s) => s.text === 'turn 2');
  check('mixed: auto turn stamped with the detection (en), unpinned call', first?.language === 'en' && sttLanguages[0] === undefined, `${JSON.stringify(first)} ${JSON.stringify(sttLanguages)}`);
  check('mixed: after reconfigure to forced de, the call is pinned and the segment stamped de', second?.language === 'de' && sttLanguages[1] === 'de', `${JSON.stringify(second)} ${JSON.stringify(sttLanguages)}`);
}

console.log(failed === 0 ? '\n✅ transcription-language: all checks passed' : `\n❌ transcription-language: ${failed} check(s) failed`);
process.exit(failed === 0 ? 0 : 1);
