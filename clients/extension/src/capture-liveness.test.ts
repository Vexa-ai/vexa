/**
 * L2 — the extension's FIRST state-machine test (P21 / gate:client-liveness).
 * Proves capture state is driven by observed frames, not by intent — so the panel
 * can never show "Listening — capturing 0 stream(s)" over silence. Pure: no chrome,
 * no WebSocket. Run: npx tsx src/capture-liveness.test.ts
 */
import { onFrameObserved, noSignalCheck, isActive, type CaptureStatus } from './capture-liveness.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const NS = (over: Partial<Parameters<typeof noSignalCheck>[0]> = {}) =>
  noSignalCheck({ status: 'starting', paused: false, frames: 0, lastFrameAt: 0, startedAt: 1000, now: 1000, noSignalMs: 6000, mixed: true, ...over });

// ── onFrameObserved: a frame is the evidence that earns "capturing" ──
check('frame promotes starting → capturing', onFrameObserved('starting') === 'capturing');
check('frame self-heals no-signal → capturing', onFrameObserved('no-signal') === 'capturing');
check('frame is idempotent on capturing', onFrameObserved('capturing') === 'capturing');
check('frame does NOT resurrect idle', onFrameObserved('idle') === 'idle');
check('frame does NOT skip connecting', onFrameObserved('connecting') === 'connecting');
check('frame does NOT override error', onFrameObserved('error') === 'error');

// ── isActive: starting & no-signal are LIVE (guard double-start / reload) ──
for (const s of ['connecting', 'starting', 'capturing', 'no-signal'] as CaptureStatus[]) check(`isActive(${s})`, isActive(s));
for (const s of ['idle', 'error'] as CaptureStatus[]) check(`!isActive(${s})`, !isActive(s));

// ── noSignalCheck: the watchdog ──
check('within warm-up grace → no change', NS({ now: 1000 + 5999 }) === null);
{
  const r = NS({ now: 1000 + 6001 });   // starting, 0 frames, past grace, mixed lane
  check('past grace w/ 0 frames → no-signal', r?.status === 'no-signal');
  check('0-frames hint names the toolbar mint (mixed)', !!r && /toolbar icon/.test(r.error) && /0 streams/.test(r.error));
}
check('gmeet 0-frames hint is lane-specific (no toolbar)', /meeting/.test(NS({ now: 9000, mixed: false })?.error || '') && !/toolbar/.test(NS({ now: 9000, mixed: false })?.error || ''));
{
  const r = NS({ status: 'capturing', frames: 42, lastFrameAt: 1000, now: 1000 + 7000 });  // was flowing, then stalled
  check('capturing then stalled → no-signal (stalled)', r?.status === 'no-signal' && /stalled/.test(r.error));
}
check('capturing + recent frame → no change', NS({ status: 'capturing', frames: 42, lastFrameAt: 5000, now: 6000 }) === null);
check('paused → never no-signal (silence is intended)', NS({ paused: true, now: 99999 }) === null);
check('idle/connecting are not watched', NS({ status: 'idle', now: 99999 }) === null && NS({ status: 'connecting', now: 99999 }) === null);

// ── scenario: the full lifecycle + self-heal (no "Listening" over silence) ──
{
  let st: CaptureStatus = 'connecting';
  const startedAt = 0; let lastFrameAt = 0; let frames = 0;
  // WS ready → starting (NOT capturing — no frame yet)
  st = 'starting';
  check('scenario: ready ⇒ starting, not capturing', st === 'starting');
  // 4s pass, still no frame → within grace, stays starting
  check('scenario: 4s no frame ⇒ still starting', (noSignalCheck({ status: st, paused: false, frames, lastFrameAt, startedAt, now: 4000, noSignalMs: 6000, mixed: true }) === null));
  // 7s pass, still no frame → no-signal (this is the "capturing 0 streams" case, now honest)
  const w = noSignalCheck({ status: st, paused: false, frames, lastFrameAt, startedAt, now: 7000, noSignalMs: 6000, mixed: true });
  st = w!.status;
  check('scenario: 7s no frame ⇒ no-signal (was the false "Listening")', st === 'no-signal');
  // a frame finally arrives → capturing (self-heals)
  frames++; lastFrameAt = 8000; st = onFrameObserved(st);
  check('scenario: a frame ⇒ capturing (earned)', st === 'capturing');
  // recent frame → stays capturing
  check('scenario: recent frame ⇒ stays capturing', noSignalCheck({ status: st, paused: false, frames, lastFrameAt, startedAt, now: 9000, noSignalMs: 6000, mixed: true }) === null);
}

if (failed) { console.error(`\n❌ capture-liveness (L2): ${failed} check(s) FAILED.`); throw new Error(`${failed} failed`); }
console.log('\n✅ capture-liveness (L2): capture state is earned by observed frames — no "Listening" over silence (P21).');
