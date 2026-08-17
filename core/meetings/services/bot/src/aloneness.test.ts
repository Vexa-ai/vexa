/** Deterministic proof for silence-based active-phase aloneness. */
import {
  DEFAULT_ALONE_SILENCE_WINDOW_MS,
  DEFAULT_ALONE_UNAVAILABLE_GRACE_MS,
  createRemoteAudioActivityTap,
  createSilenceAlonenessSource,
  resolveAloneSilenceWindowMs,
  resolveAloneUnavailableGraceMs,
} from './aloneness.js';

let failed = 0;
const check = (name: string, condition: boolean, detail = ''): void => {
  console.log(`  ${condition ? '✅' : '❌'} ${name}${condition ? '' : ` — ${detail}`}`);
  if (!condition) failed++;
};

class FakeClock {
  nowMs = 0;
  now = (): number => this.nowMs;
  advance(ms: number): void { this.nowMs += ms; }
}

class FakeScheduler {
  private callbacks = new Map<number, () => void>();
  private nextId = 1;
  readonly setInterval = (callback: () => void, _ms: number): number => {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  };
  readonly clearInterval = (id: unknown): void => { this.callbacks.delete(id as number); };
  tick(): void { for (const callback of [...this.callbacks.values()]) callback(); }
  get activeCount(): number { return this.callbacks.size; }
}

const loudEnergy = 0.02;
const quietEnergy = 0.001;

function fixture(windowMs = 1_000, unavailableGraceMs = 500) {
  const clock = new FakeClock();
  const scheduler = new FakeScheduler();
  const activity = createRemoteAudioActivityTap({ now: clock.now });
  const source = createSilenceAlonenessSource({
    activity,
    windowMs,
    unavailableGraceMs,
    now: clock.now,
    pollMs: 10,
    setInterval: scheduler.setInterval,
    clearInterval: scheduler.clearInterval,
    log: () => { /* deterministic fixture: logs asserted by live evidence */ },
  });
  return { clock, scheduler, activity, source };
}

// silence(W) fires once from the capture-ready anchor.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  const stop = f.source.onAlone(() => fired++);
  f.clock.advance(999); f.scheduler.tick();
  check('silence before W does not fire', fired === 0);
  f.clock.advance(1); f.scheduler.tick();
  f.scheduler.tick();
  check('silence at W fires exactly once', fired === 1);
  check('exactly-once verdict stops polling', f.scheduler.activeCount === 0);
  stop();
}

// A qualifying REMOTE frame at W-epsilon resets the full window.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.clock.advance(999);
  f.activity.observeRemoteEnergy(loudEnergy);
  f.clock.advance(999); f.scheduler.tick();
  check('remote speech at W-epsilon resets the window', fired === 0);
  f.clock.advance(1); f.scheduler.tick();
  check('reset window eventually fires', fired === 1);
}

// Repeated remote speech keeps the room active.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  for (let i = 0; i < 4; i++) {
    f.clock.advance(750);
    f.activity.observeRemoteEnergy(loudEnergy);
    f.scheduler.tick();
  }
  check('repeated remote speech prevents leave', fired === 0);
}

// Local bot speech has no path into the REMOTE activity tap, so silence still elapses.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.clock.advance(500);
  // The bot speaks locally here; only remote capture is allowed to call observeRemoteEnergy().
  f.scheduler.tick();
  f.clock.advance(500); f.scheduler.tick();
  check('local bot speech does not reset remote silence', fired === 1);
}

// A QUIET delivered frame is still presence. Capture is the single silence oracle: the page emits
// a frame only when its PEAK clears the capture gate, and this tap sits downstream of it — so an
// arriving frame has already proven it carries audio and was transcribed on that basis. Re-judging
// it by RMS (always <= peak) could only discard real speech, letting the bot leave a meeting it
// could hear. Quiet must NOT reset-suppress.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.clock.advance(900);
  f.activity.observeRemoteEnergy(quietEnergy);
  f.clock.advance(100); f.scheduler.tick();
  check('a quiet delivered frame counts as presence (no false leave)', fired === 0);
}

// ...but digital silence is not presence: a zero-energy reading must never hold the meeting open.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.clock.advance(900);
  f.activity.observeRemoteEnergy(0);
  f.clock.advance(100); f.scheduler.tick();
  check('a zero-energy frame is silence, not presence', fired === 1);
}

// No capture readiness means no signal, not silence: fail closed forever (a tap that has NEVER
// been ready is "no signal", not "detached" — only an ever-ready capture can detach).
{
  const f = fixture();
  let fired = 0;
  f.source.onAlone(() => fired++);
  f.clock.advance(10_000); f.scheduler.tick();
  check('absent audio tap fails closed', fired === 0);
}

// A capture that WAS ready and then becomes unavailable (ended/kicked call detaches the capture,
// or the page navigated away) converges to left_alone after the bounded unavailable grace — the
// fail-safe that stops an active bot from idling forever on a detached tap.
{
  const f = fixture(1_000, 500);
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.activity.unavailable();
  f.clock.advance(499); f.scheduler.tick();
  check('unavailable within grace does not fire', fired === 0);
  f.clock.advance(501); f.scheduler.tick(); f.scheduler.tick();
  check('unavailable past grace fires exactly once', fired === 1);
  check('fail-safe verdict stops polling', f.scheduler.activeCount === 0);
}

// A brief unavailable blip that recovers must NOT fire: recovery resets the grace AND restarts
// the silence window (a transient capture hiccup is not a leave).
{
  const f = fixture(1_000, 500);
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.clock.advance(100); f.activity.unavailable();
  f.clock.advance(300); f.scheduler.tick(); // within grace
  f.activity.ready(); // recovers
  f.clock.advance(999); f.scheduler.tick();
  check('brief unavailable + recovery does not fire', fired === 0);
  f.clock.advance(1); f.scheduler.tick();
  check('recovered capture still subject to silence window', fired === 1);
}

// Stopping the subscription prevents a later terminal verdict.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  const stop = f.source.onAlone(() => fired++);
  stop();
  f.clock.advance(10_000); f.scheduler.tick();
  check('stop cancels the monitor', fired === 0 && f.scheduler.activeCount === 0);
}

// The adapter seam can veto silence without changing the monitor.
{
  const clock = new FakeClock();
  const scheduler = new FakeScheduler();
  const activity = createRemoteAudioActivityTap({ now: clock.now });
  activity.ready();
  let fired = 0;
  const source = createSilenceAlonenessSource({
    activity,
    windowMs: 1_000,
    adapters: [
      { name: 'silence', evaluate: (snapshot, now, windowMs) =>
        snapshot.available && snapshot.lastRemoteAudioAt !== undefined && now - snapshot.lastRemoteAudioAt >= windowMs
          ? 'alone' : 'not-alone' },
      { name: 'presence-veto', evaluate: () => 'not-alone' },
    ],
    now: clock.now,
    setInterval: scheduler.setInterval,
    clearInterval: scheduler.clearInterval,
    log: () => {},
  });
  source.onAlone(() => fired++);
  clock.advance(10_000); scheduler.tick();
  check('a future adapter can veto the silence verdict', fired === 0);
}

// Timeout precedence: explicit invocation > valid env > 10-minute module default.
{
  check('explicit everyoneLeftTimeout wins',
    resolveAloneSilenceWindowMs(12_345, { BOT_ALONE_SILENCE_WINDOW_MS: '23456' }) === 12_345);
  check('env override applies when invocation is absent',
    resolveAloneSilenceWindowMs(undefined, { BOT_ALONE_SILENCE_WINDOW_MS: '23456' }) === 23_456);
  check('module default is ten minutes',
    resolveAloneSilenceWindowMs(undefined, {}) === DEFAULT_ALONE_SILENCE_WINDOW_MS &&
    DEFAULT_ALONE_SILENCE_WINDOW_MS === 600_000);
  check('invalid env falls back to module default',
    resolveAloneSilenceWindowMs(undefined, { BOT_ALONE_SILENCE_WINDOW_MS: 'nope' }, () => {}) === 600_000);
}

// Unavailable-grace precedence: valid env > 60-second module default.
{
  check('unavailable grace env override applies',
    resolveAloneUnavailableGraceMs({ BOT_ALONE_UNAVAILABLE_GRACE_MS: '12345' }) === 12_345);
  check('unavailable grace module default is 60s',
    resolveAloneUnavailableGraceMs({}) === DEFAULT_ALONE_UNAVAILABLE_GRACE_MS &&
    DEFAULT_ALONE_UNAVAILABLE_GRACE_MS === 60_000);
  check('invalid unavailable grace env falls back to module default',
    resolveAloneUnavailableGraceMs({ BOT_ALONE_UNAVAILABLE_GRACE_MS: 'nope' }, () => {}) === 60_000);
}

console.log(failed
  ? `\n❌ aloneness: ${failed} failed`
  : '\n✅ aloneness (L2): scripted remote-audio timelines prove silence, reset, fail-closed, exactly-once, and timeout precedence.');
process.exit(failed ? 1 : 0);
