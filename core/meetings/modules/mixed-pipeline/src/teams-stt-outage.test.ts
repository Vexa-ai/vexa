/**
 * Regression — on the Teams lane an STT failure never costs the window's audio. A failed request
 * used to be answered as an empty transcript, so a 60 s outage lost every window sent during it
 * (harness: coverage 0.58 under outage60). The request is now kept and sent again with backoff, and
 * dispose retries until its final-pass deadline.
 *   tsx src/teams-stt-outage.test.ts
 */
import { TeamsCsrcGmeetPipeline, type TeamsCsrcTranscriptSegment } from './teams-csrc-gmeet-pipeline.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

async function run(outageCalls: number, finalPassMs?: number) {
  let now = 0, calls = 0;
  const segments: TeamsCsrcTranscriptSegment[] = [];
  const errors: unknown[] = [];
  const pipeline = new TeamsCsrcGmeetPipeline({
    lookbackMs: 0, flickerHoldMs: 0, onsetGapMs: 1000,
    buffer: { scheduleSubmissions: false, now: () => now, silenceRmsThreshold: 0 },
    sttRetryBaseMs: 20, sttRetryMaxMs: 40, finalPassMs: finalPassMs ?? 2000,
    onSegment: (s) => segments.push(s),
    onError: (e) => errors.push(e),
    transcribe: async () => {
      calls++;
      if (calls <= outageCalls) throw Object.assign(new Error('stt unavailable'), { kind: 'unavailable' });
      return { text: 'recovered words here', language: 'en', duration: 4, segments: [{ text: 'recovered words here', start: 0, end: 3.9 } as never] };
    },
  });
  pipeline.recordTransportEvent({ csrc: 201, active: true, tMs: 0 });
  for (let i = 0; i < 8; i++) { now = i * 500; pipeline.feedMixedAudio(new Float32Array(8000).fill(0.1), now); }
  now = 4000;
  // Not awaited: the request keeps retrying through the outage; dispose's final pass bounds it.
  void pipeline.requestTranscription(201, true);
  await new Promise((r) => setTimeout(r, 100));
  await pipeline.dispose();
  return { segments, errors, calls, missing: (pipeline as any).missingRanges?.() ?? [] };
}

{
  const r = await run(3);
  const text = r.segments.filter((s) => s.completed).map((s) => s.text).join(' ');
  check('a window sent during an outage is transcribed once STT answers again', text.includes('recovered words here'), JSON.stringify(r.segments));
  check('each failure is still reported', r.errors.length === 3, String(r.errors.length));
  check('a recovered window is not recorded as missing', r.missing.length === 0, JSON.stringify(r.missing));
}
{
  const t0 = Date.now();
  const r = await run(1_000_000, 300);
  check('an STT that never recovers ends at the final-pass deadline', Date.now() - t0 < 3000, `${Date.now() - t0} ms`);
  check('and publishes nothing invented', r.segments.filter((s) => s.completed && s.text).length === 0, JSON.stringify(r.segments));
  check('the given-up window is recorded as a missing range with its fault (prod-cutover R4)',
    r.missing.length >= 1 && r.missing.every((m: any) => m.endMs > m.startMs && /unavailable/.test(m.reason)), JSON.stringify(r.missing));
}

if (failed) { console.error(`\n❌ teams-stt-outage: ${failed} check(s) failed`); process.exit(1); }
console.log('✅ teams-stt-outage: failed windows are kept and retried; the final pass is bounded');
