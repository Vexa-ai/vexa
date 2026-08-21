/** Deterministic proof for silence-based active-phase aloneness. */
import {
  DEFAULT_ALONE_SILENCE_WINDOW_MS,
  DEFAULT_STREAM_PRESENCE_STALENESS_MS,
  createDeafCaptureGuardAdapter,
  createRemoteAudioActivityTap,
  createSilenceAlonenessSource,
  deafCaptureGuardAdapter,
  resolveAloneSilenceWindowMs,
  silenceAlonenessAdapter,
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

function fixture(windowMs = 1_000, extra: {
  adapters?: Parameters<typeof createSilenceAlonenessSource>[0]['adapters'];
  onCaptureFault?: () => void;
} = {}) {
  const clock = new FakeClock();
  const scheduler = new FakeScheduler();
  const activity = createRemoteAudioActivityTap({ now: clock.now });
  const logs: string[] = [];
  const source = createSilenceAlonenessSource({
    activity,
    windowMs,
    adapters: extra.adapters,
    onCaptureFault: extra.onCaptureFault,
    now: clock.now,
    pollMs: 10,
    setInterval: scheduler.setInterval,
    clearInterval: scheduler.clearInterval,
    log: (m) => { logs.push(m); },
  });
  return { clock, scheduler, activity, source, logs };
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

// No capture readiness means no signal, not silence: fail closed forever.
{
  const f = fixture();
  let fired = 0;
  f.source.onAlone(() => fired++);
  f.clock.advance(10_000); f.scheduler.tick();
  check('absent audio tap fails closed', fired === 0);
  f.activity.ready();
  f.activity.unavailable();
  f.clock.advance(10_000); f.scheduler.tick();
  check('failed or torn-down audio tap fails closed', fired === 0);
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

// ── #1192 deaf-leave guard: connected streams delivering nothing are a BROKEN CAPTURE, not an
// empty room. Frame arrival is the silence adapter's only oracle, so a capture chain that dies
// mid-meeting looks exactly like everybody leaving — and the bot walks out of a live meeting with
// a `completed(left_alone)` on the board. The page-side stream count is the bit that separates the
// two cases; these timelines pin every branch of how it is read.

// (a) The defect itself: capture ready, two streams connected, not one frame for the whole window.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  f.clock.advance(1_000); f.scheduler.tick(); f.scheduler.tick();
  check('connected streams + zero frames does not fire left_alone', fired === 0);
  check('capture-fault is surfaced loudly',
    f.logs.some((m) => m.includes('capture-fault suspected') && m.includes('streams=2')),
    JSON.stringify(f.logs));
  check('the guard keeps polling instead of terminating', f.scheduler.activeCount === 1);
  // ...and it stays held while the fault persists, rather than expiring into a leave.
  f.clock.advance(10_000); f.scheduler.tick();
  check('a persisting capture-fault never converts into left_alone', fired === 0);
}

// (b) No connected streams: the room really did empty — today's verdict, unchanged.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(0);
  f.source.onAlone(() => fired++);
  f.clock.advance(1_000); f.scheduler.tick();
  check('zero connected streams still resolves alone', fired === 1);
}

// (c) Frames flowed and then stopped dead while the streams stayed connected — the #850 class as it
// actually appears in prod (capture works, then it does not).
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  f.clock.advance(400); f.activity.observeRemoteEnergy(loudEnergy); f.scheduler.tick();
  f.clock.advance(400); f.activity.observeRemoteEnergy(loudEnergy); f.scheduler.tick();
  f.activity.observeStreamPresence(2);      // the page keeps reporting: they are still here
  f.clock.advance(1_000); f.scheduler.tick();
  check('frames stopping mid-meeting with streams connected does not fire', fired === 0);
}

// (d) Presence never reported (the gmeet lane: per-channel capture, no mix, no `__vexaMixSeen`) —
// unknown must mean "behave exactly as before", never "assume zero".
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.source.onAlone(() => fired++);
  f.clock.advance(1_000); f.scheduler.tick();
  check('unknown stream presence leaves the silence verdict untouched', fired === 1);
}

// (e) True silence with the capture chain alive: frames keep ARRIVING, they just carry no energy.
// The bot can hear; the room is quiet; leaving is correct.
{
  const f = fixture();
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  for (let i = 0; i < 4; i++) {
    f.clock.advance(250);
    f.activity.observeRemoteEnergy(0);     // delivered, silent — capture is alive
    f.activity.observeStreamPresence(2);
    f.scheduler.tick();
  }
  check('zero-energy frames still arriving resolve alone (silent room, not deaf bot)', fired === 1);
}

// (f) A capture-fault that heals: once frames arrive again the guard stops objecting, and the
// meeting is back under the ordinary silence rule — up to and including leaving when the room
// really does empty afterwards.
{
  const f = fixture(1_000, { adapters: [silenceAlonenessAdapter, createDeafCaptureGuardAdapter({ stalenessMs: 500 })] });
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  f.clock.advance(900); f.activity.observeStreamPresence(2);
  f.clock.advance(100); f.scheduler.tick();
  check('fault held the verdict', fired === 0);
  f.activity.observeRemoteEnergy(loudEnergy);   // capture recovers
  f.activity.observeStreamPresence(2);
  f.clock.advance(999); f.activity.observeStreamPresence(2); f.scheduler.tick();
  check('recovered capture is not fired on early', fired === 0);
  f.clock.advance(1); f.activity.observeStreamPresence(0);   // and then everyone leaves
  f.clock.advance(501); f.scheduler.tick();                  // past the sticky-presence window
  check('a healed capture returns to the plain silence verdict', fired === 1);
}

// (g) The single repair attempt: one restart per subscription, never a restart loop.
{
  let restarts = 0;
  const f = fixture(1_000, { onCaptureFault: () => { restarts++; } });
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  f.clock.advance(1_000); f.scheduler.tick();
  f.clock.advance(5_000); f.scheduler.tick(); f.scheduler.tick();
  check('capture restart is attempted exactly once', restarts === 1);
  check('the restart attempt does not release left_alone', fired === 0);
}

// (h) Stale presence: the page stopped reporting (its rescan died). A stale bit must not be able to
// hold a bot open forever — presence ages back to unknown and the silence rule takes over.
{
  const f = fixture(1_000, { adapters: [silenceAlonenessAdapter, createDeafCaptureGuardAdapter({ stalenessMs: 2_000 })] });
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  f.clock.advance(1_500); f.scheduler.tick();
  check('a fresh presence report holds the verdict', fired === 0);
  f.clock.advance(1_000); f.scheduler.tick();   // 2.5s since the last report > 2s staleness
  check('stale presence falls back to the pre-guard behaviour', fired === 1);
}

// (i) DTX flap: a remote track mutes between talk spurts, so a rescan can sample zero while the
// participants are plainly still there. Presence is sticky over the staleness window for exactly
// this reason — one unlucky sample must not evict the bot.
{
  const f = fixture(1_000, { adapters: [silenceAlonenessAdapter, createDeafCaptureGuardAdapter({ stalenessMs: 5_000 })] });
  let fired = 0;
  f.activity.ready();
  f.activity.observeStreamPresence(2);
  f.source.onAlone(() => fired++);
  f.clock.advance(900); f.activity.observeStreamPresence(0);   // the flap
  f.clock.advance(100); f.scheduler.tick();
  check('a momentary zero inside the staleness window does not evict', fired === 0);
}

// (j) the #866/#887 composition — the row this guard must NOT break.
//
// PR #887 (@Ayush7614, issue #866) latches remote-audio ready when the mix DESTINATION attaches
// rather than when the first PCM frame arrives, so a bot alone from the first second can still
// leave. That is the earlier-latch world; it is also precisely the world in which a never-started
// capture reaches the silence window instead of failing closed, which is why the guard has to be
// able to tell the two apart. These two timelines simulate #887's latch directly — `ready()` with
// ZERO frames ever delivered — and pin both outcomes.
{
  // Empty room, #887's latch: capture attached, page reports zero connected streams, nothing ever
  // arrives. The guard must ABSTAIN so #866's fix works: the bot leaves.
  const f = fixture(1_000, { adapters: [silenceAlonenessAdapter, createDeafCaptureGuardAdapter({ stalenessMs: 5_000 })] });
  let fired = 0;
  f.activity.ready();                       // #887: ready without a single frame
  f.activity.observeStreamPresence(0);      // an empty room reports zero, not unknown
  f.source.onAlone(() => fired++);
  f.clock.advance(1_001); f.scheduler.tick();
  check('#866/#887: ready-with-no-frames + zero streams still resolves alone', fired === 1);
}
{
  // The same latch, an occupied room, capture dead on arrival: streams connected, nothing ever
  // delivered. Without the guard #887 would turn this into a `left_alone` on a live meeting —
  // which is #1192. The guard must OBJECT.
  const f = fixture(1_000, { adapters: [silenceAlonenessAdapter, createDeafCaptureGuardAdapter({ stalenessMs: 5_000 })] });
  let fired = 0;
  f.activity.ready();                       // #887: ready without a single frame
  f.activity.observeStreamPresence(2);      // …but two remote streams are live
  f.source.onAlone(() => fired++);
  f.clock.advance(1_001); f.scheduler.tick();
  check('#1192 under #887: ready-with-no-frames + live streams withholds left_alone', fired === 0);
  check('…and says so', f.logs.some((l) => l.includes('capture-fault suspected')));
}

// The guard as a unit: it abstains ('alone' = no objection) on every branch it cannot decide, and
// only ever objects with capture-fault. Composed with the silence adapter, abstention is what makes
// every pre-existing timeline read byte-for-byte as before.
{
  const guard = createDeafCaptureGuardAdapter({ stalenessMs: 1_000 });
  const base = { available: true, lastRemoteAudioAt: 0, streamsConnected: 2, streamsObservedAt: 10_000, streamsPresentAt: 10_000 };
  check('guard abstains when capture is unavailable',
    guard.evaluate({ available: false }, 10_000, 1_000) === 'alone');
  check('guard abstains when presence was never reported',
    guard.evaluate({ available: true, lastRemoteAudioAt: 0 }, 10_000, 1_000) === 'alone');
  check('guard abstains on a stale presence report',
    guard.evaluate({ ...base, streamsObservedAt: 8_000, streamsPresentAt: 8_000 }, 10_000, 1_000) === 'alone');
  check('guard abstains while frames are arriving',
    guard.evaluate({ ...base, lastRemoteFrameAt: 9_500 }, 10_000, 1_000) === 'alone');
  check('guard objects when connected streams deliver nothing',
    guard.evaluate(base, 10_000, 1_000) === 'capture-fault');
  check('guard objects when frames stopped a full window ago',
    guard.evaluate({ ...base, lastRemoteFrameAt: 8_000 }, 10_000, 1_000) === 'capture-fault');
  check('the shipped guard defaults to a 30s presence staleness',
    deafCaptureGuardAdapter.evaluate({ ...base, streamsObservedAt: 10_000 - DEFAULT_STREAM_PRESENCE_STALENESS_MS - 1, streamsPresentAt: 0 }, 10_000, 1_000) === 'alone'
    && DEFAULT_STREAM_PRESENCE_STALENESS_MS === 30_000);
}

// The tap's two clocks: arrival (capture liveness) and presence (someone spoke) move independently.
{
  const clock = new FakeClock();
  const tap = createRemoteAudioActivityTap({ now: clock.now });
  tap.ready();
  clock.advance(100);
  tap.observeRemoteEnergy(0);
  check('a zero-energy frame counts as arrival, not presence',
    tap.snapshot().lastRemoteFrameAt === 100 && tap.snapshot().lastRemoteAudioAt === 0 && tap.snapshot().framesDelivered === 1);
  clock.advance(100);
  tap.observeRemoteEnergy(loudEnergy);
  check('an energetic frame moves both', tap.snapshot().lastRemoteFrameAt === 200 && tap.snapshot().lastRemoteAudioAt === 200);
  clock.advance(100);
  tap.observeStreamPresence(3);
  check('presence records count, observation time and last-present time',
    tap.snapshot().streamsConnected === 3 && tap.snapshot().streamsObservedAt === 300 && tap.snapshot().streamsPresentAt === 300);
  clock.advance(100);
  tap.observeStreamPresence(0);
  check('a zero report updates the observation time but keeps the last-present time',
    tap.snapshot().streamsConnected === 0 && tap.snapshot().streamsObservedAt === 400 && tap.snapshot().streamsPresentAt === 300);
  tap.observeStreamPresence(Number.NaN);
  check('a nonsense presence report is ignored', tap.snapshot().streamsObservedAt === 400);
  tap.unavailable();
  check('unavailable still fails closed', tap.snapshot().available === false && tap.snapshot().lastRemoteAudioAt === undefined);
  tap.ready();
  check('a capture restart re-arms frame bookkeeping but keeps what the page said about the room',
    tap.snapshot().framesDelivered === 0 && tap.snapshot().lastRemoteFrameAt === undefined
    && tap.snapshot().streamsConnected === 0 && tap.snapshot().streamsPresentAt === 300);
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

console.log(failed
  ? `\n❌ aloneness: ${failed} failed`
  : '\n✅ aloneness (L2): scripted remote-audio timelines prove silence, reset, fail-closed, exactly-once, and timeout precedence.');
process.exit(failed ? 1 : 0);
