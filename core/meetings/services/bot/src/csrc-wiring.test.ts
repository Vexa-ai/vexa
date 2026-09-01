/**
 * CSRC + observations wiring — the page→Node boundary for the transport sensor and for everything
 * the capture path NOTICES.
 *
 * Drives the REAL page-side capture bundle (dist/browser-utils.global.js — the exact file the bot
 * injects via addInitScript) and the REAL startCaptureBridge wiring against a fake Playwright Page
 * whose exposeFunction/evaluate run in-process. Three properties, each of which was invisible
 * before it was wired:
 *
 *   1. the mixed lane STARTS the transport sensor (the still-mixed platforms — Teams/Jitsi — ride the
 *      same shared init, gated on no platform-specific branch; Zoom now captures per-track and returns
 *      before this init, like gmeet, so Teams is the vehicle that proves the shared init still fires);
 *   2. transitions cross to Node with an EPOCH timestamp and reach the telemetry sink;
 *   3. every typed observation the page produces — the mix topology, a sensor fault, a watcher
 *      reporting no signal — crosses into the fixture instead of dying with the pod's log.
 *
 * The load-bearing NEGATIVE check is the same one the caption lane carries: nothing here may reach
 * pipeline.recordHint. This iteration buys observation, not a behaviour change.
 *
 * RED at any base where the bundle lacks createCsrcPoll or the mixed branch doesn't start it:
 * zero arrivals.
 * Run: npx tsx src/csrc-wiring.test.ts
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import type {
  CsrcRecord, CsrcCapableSink, ObservationRecord, ObservationCapableSink,
} from './capture-bridge.js';
import type { Invocation } from './config.js';
import type { BotPipeline } from './pipeline.js';

// HERMETIC: capture-bridge resolves VEXA_CSRC_INACTIVE_MS at module load, so an ambient override
// in the developer's shell would fail the 800ms assertions below. Scrub BEFORE the module loads —
// which is why this import is dynamic (a static one hoists above the delete).
delete process.env.VEXA_CSRC_INACTIVE_MS;
const { startCaptureBridge, makeCsrcSink, makeObservationSink, resolveCsrcInactiveMs } =
  await import('./capture-bridge.js');

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

// ── 1) The Node-side sinks, driven directly (no page): clock guard + record shape ────────────────
{
  const stored: CsrcRecord[] = [];
  const warnings: string[] = [];
  const sink = makeCsrcSink(
    { captureFrame() { /* unused */ }, captureCsrc: (r) => stored.push(r) } as CsrcCapableSink,
    (m) => warnings.push(m),
  );
  const t = Date.now();
  sink.sink(3735928559, true, t, 0.42, 123456789);
  check('csrc sink: the record is typed csrc/mixed with the transport fields intact',
    stored.length === 1 && stored[0].type === 'csrc' && stored[0].csrc === 3735928559
    && stored[0].active === true && stored[0].lane === 'mixed'
    && stored[0].audioLevel === 0.42 && stored[0].rtpTimestamp === 123456789, JSON.stringify(stored));
  // A page emitting a raw performance clock would store turn edges against a clock the audio does
  // not share — re-stamped, and said out loud.
  sink.sink(7, false, 42);
  check('csrc sink: a non-epoch timestamp is re-stamped AND warned, never stored silently',
    warnings.some((w) => w.includes('csrc-clock-skew')) && stored[1].t >= t, warnings.join(' | '));
  check('csrc sink: counts what crossed and what was stored', sink.crossed() === 2 && sink.stored() === 2);

  const noStore = makeCsrcSink({ captureFrame() { /* unused */ } });
  noStore.sink(1, true, Date.now());
  check('csrc sink: a recorder without captureCsrc degrades to log-only (0 stored, no throw)',
    noStore.crossed() === 1 && noStore.stored() === 0);
}
{
  const stored: ObservationRecord[] = [];
  const warnings: string[] = [];
  const sink = makeObservationSink(
    'mixed',
    { captureFrame() { /* unused */ }, captureObservation: (r) => stored.push(r) } as ObservationCapableSink,
    (m) => warnings.push(m),
  );
  const t = Date.now();
  sink.sink('teams-speakers', { type: 'signal-absent', tiles: 4 }, t);
  check('observation sink: the producer payload is carried verbatim under `observation`',
    stored.length === 1 && stored[0].source === 'teams-speakers' && stored[0].lane === 'mixed'
    && stored[0].observation.type === 'signal-absent' && stored[0].observation.tiles === 4,
    JSON.stringify(stored));
  // A malformed observation still says something happened; dropping it would make the sidecar
  // quietly disagree with the log.
  sink.sink('bot', 'a bare string', t);
  check('observation sink: a non-object payload is wrapped, never dropped',
    stored.length === 2 && stored[1].observation.note === 'a bare string', JSON.stringify(stored[1]));
  sink.sink('csrc', { kind: 'csrc-poll-error' }, 42);
  check('observation sink: a non-epoch timestamp is re-stamped AND warned',
    warnings.some((w) => w.includes('observation-clock-skew')) && stored[2].t >= t, warnings.join(' | '));
}

// ── the window is resolved from env, and garbage never reaches the sensor ───────────────────────
// The sensor resolves its option with `??`, which passes NaN through, and a NaN window makes BOTH
// of its comparisons false: every source is re-deactivated on every poll — the fragmentation this
// value exists to end, worse and silent. So an unusable override falls back rather than propagating.
{
  check('window: no override ⇒ the measured 800ms', resolveCsrcInactiveMs(undefined) === 800,
    String(resolveCsrcInactiveMs(undefined)));
  check('window: a usable override wins', resolveCsrcInactiveMs('1200') === 1200,
    String(resolveCsrcInactiveMs('1200')));
  check('window: garbage falls back instead of becoming NaN',
    resolveCsrcInactiveMs('abc') === 800, String(resolveCsrcInactiveMs('abc')));
  check('window: zero, negative and empty fall back too — none of them is a window',
    resolveCsrcInactiveMs('0') === 800 && resolveCsrcInactiveMs('-5') === 800 && resolveCsrcInactiveMs('') === 800,
    `${resolveCsrcInactiveMs('0')}/${resolveCsrcInactiveMs('-5')}/${resolveCsrcInactiveMs('')}`);
  // A window shorter than one poll is stale before the next tick — the NaN failure mode wearing an
  // ordinary number. It is rejected at the floor, not accepted because it happens to be positive.
  check('window: shorter than one 100ms poll is rejected, one poll exactly is kept',
    resolveCsrcInactiveMs('1') === 800 && resolveCsrcInactiveMs('99') === 800 && resolveCsrcInactiveMs('100') === 100,
    `${resolveCsrcInactiveMs('1')}/${resolveCsrcInactiveMs('99')}/${resolveCsrcInactiveMs('100')}`);
  // The ceiling is the floor's mirror: a fat-fingered 600000 would hold every source active for
  // ten minutes and synthesize essentially no deactivations — the NaN failure mode wearing an
  // ordinary number, equally silent. 1600ms already merges turns; 10s is generous headroom.
  check('window: a huge override is rejected at the ceiling, ten seconds exactly is kept',
    resolveCsrcInactiveMs('600000') === 800 && resolveCsrcInactiveMs('10001') === 800
    && resolveCsrcInactiveMs('10000') === 10000,
    `${resolveCsrcInactiveMs('600000')}/${resolveCsrcInactiveMs('10001')}/${resolveCsrcInactiveMs('10000')}`);
  {
    const warnings: string[] = [];
    const used = resolveCsrcInactiveMs('abc', (m) => warnings.push(m));
    check('window: a rejected override is SAID OUT LOUD, never silently ignored',
      used === 800 && warnings.length === 1 && warnings[0]!.includes('VEXA_CSRC_INACTIVE_MS="abc"'),
      JSON.stringify(warnings));
    const quiet: string[] = [];
    resolveCsrcInactiveMs(undefined, (m) => quiet.push(m));
    resolveCsrcInactiveMs('1200', (m) => quiet.push(m));
    check('window: no override and a good override are both silent — only a REJECTION warns',
      quiet.length === 0, JSON.stringify(quiet));
    // An empty override is what a half-rendered deploy template looks like. Treating it as "not
    // set" is how a knob gets ignored in silence — the exact failure the warning exists to prevent.
    const blank: string[] = [];
    check('window: an override SET to empty or blank warns too — it is an intent, not an absence',
      resolveCsrcInactiveMs('', (m) => blank.push(m)) === 800
      && resolveCsrcInactiveMs('   ', (m) => blank.push(m)) === 800
      && blank.length === 2, JSON.stringify(blank));
  }
}

// ── The real bundle (built by build-browser-utils.mjs — turbo test depends on build) ─────────────
const BUNDLE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', 'dist', 'browser-utils.global.js');
if (!existsSync(BUNDLE)) {
  console.error(`❌ missing ${BUNDLE} — build the capture bricks first (pnpm --filter @vexa/mixed-capture-core --filter @vexa/bot build).`);
  process.exit(1);
}

// ── Page-context shims on the REAL globalThis (the fake page.evaluate runs in-process) ───────────
const g = globalThis as unknown as Record<string, unknown>;
const savedDocument = g.document;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
const intervals: Array<() => void> = [];
(g as any).setInterval = (cb: () => void) => { intervals.push(cb); return intervals.length; };
(g as any).clearInterval = () => { /* controlled clock */ };
g.document = { body: {}, querySelector: () => null, querySelectorAll: () => [] };
g.MutationObserver = class { observe() { /* unused here */ } disconnect() { /* */ } };
g.window = g;

// A mixed-lane page: ONE server-side mix stream, and an audio receiver whose contributing sources
// the sensor reads. The AudioContext shim is deliberately minimal — createMixedAudioCapture will
// fail on it and be swallowed by the bridge's own catch, exactly as it would on a page whose
// audio stack is unavailable. What is under test here is the OBSERVATION wiring around it.
class FakeAudioContext {
  destination = {};
  createMediaStreamDestination(): unknown { return { stream: { id: 'mix', getAudioTracks: () => [{ id: 'mainAudio-mix' }] } }; }
  createMediaStreamSource(): unknown { return { connect: () => { /* connected */ } }; }
  resume(): void { /* no-op */ }
  close(): void { /* no-op */ }
}
(g as any).AudioContext = FakeAudioContext;
g.__vexaCapturedRemoteAudioStreams = [{ id: 'stream-1', getAudioTracks: () => [{ id: 'mainAudio-abc' }] }];

// The transport, as the sensor sees it: one audio receiver on one peer connection. `timestamp` is
// the PERFORMANCE clock (what a real UA reports), so this also proves the epoch conversion rather
// than assuming the page already handed over epoch ms.
let speakingSince: number | null = null;
const receiver = {
  track: { kind: 'audio' },
  getContributingSources: () => (speakingSince === null ? [] : [
    { source: 424242, timestamp: speakingSince, audioLevel: 0.3, rtpTimestamp: 987 },
  ]),
};
g.__vexa_peer_connections = [{ getReceivers: () => [receiver] }];

// Load the REAL bundle — defines globalThis.VexaBrowserUtils.
new Function(readFileSync(BUNDLE, 'utf8'))();
const utils = g.VexaBrowserUtils as Record<string, unknown> | undefined;
check('bundle: window.VexaBrowserUtils.createCsrcPoll is exported (RED at base — brick not bundled)',
  typeof utils?.createCsrcPoll === 'function', `keys: ${Object.keys(utils ?? {}).join(',')}`);

// Capture the options the REAL bridge hands the REAL sensor factory. The window the production
// path runs on is not observable from a transition alone (only from WHEN one is absent), and the
// number itself is owned by the composition root, not by the sensor — so it is asserted here, on
// the object that actually crosses. RED when the bridge passes no `inactiveMs` at all.
type CsrcPollOpts = { inactiveMs?: number; pollMs?: number; now?: () => number; timeOrigin?: () => number;
  receivers?: () => unknown[]; onTransition: (t: { csrc: number; active: boolean; tMs: number }) => void };
const realCreateCsrcPoll = utils!.createCsrcPoll as (o: CsrcPollOpts) => unknown;
let productionCsrcOpts: CsrcPollOpts | undefined;
(utils as Record<string, unknown>).createCsrcPoll = (o: CsrcPollOpts): unknown => {
  productionCsrcOpts = o;
  return realCreateCsrcPoll(o);
};

// ── Fake Playwright Page + the Node seams ───────────────────────────────────────────────────────
const page = {
  async exposeFunction(name: string, fn: unknown): Promise<void> { g[name] = fn; },
  async evaluate(fn: (arg: never) => unknown, arg?: unknown): Promise<unknown> { return fn(arg as never); },
} as never;

const hints: Array<{ name: string; tMs: number; isEnd: boolean }> = [];
const spine: Array<{ csrc: number; active: boolean; tMs: number }> = [];
const pipeline: BotPipeline = {
  async start() { /* not driven */ },
  async stop() { /* not driven */ },
  feedAudio() { /* not driven */ },
  feedMixedAudio() { /* not driven */ },
  recordHint: (name, tMs, isEnd) => hints.push({ name, tMs, isEnd: !!isEnd }),
  recordTransportEvent: (ev) => spine.push({ csrc: ev.csrc, active: ev.active, tMs: ev.tMs }),
};
const transitions: CsrcRecord[] = [];
const observations: ObservationRecord[] = [];
const telemetry: CsrcCapableSink & ObservationCapableSink = {
  captureFrame() { /* unused */ },
  captureCsrc: (r) => transitions.push(r),
  captureObservation: (o) => observations.push(o),
};
// teams, deliberately: the sensor is platform-agnostic and rides the SHARED mixed init. Zoom left
// the mixed lane (isPerTrackLanePlatform — it captures per-track and returns before this init, like
// gmeet), so the plain mixed branch that still reaches the sensor is Teams/Jitsi. Driving Teams
// proves the init is not gated on any platform-specific branch.
const inv = { platform: 'teams', botName: 'Vexa Bot', connectionId: 'test' } as unknown as Invocation;

const t0 = Date.now();
const stop = await startCaptureBridge(page, inv, pipeline, telemetry);
const tick = (n = 1) => { for (const cb of [...intervals]) for (let i = 0; i < n; i++) cb(); };

check('the mixed lane started the transport sensor (teams — the shared init, not a per-track branch)',
  !!g.__vexaCsrcPoll, `poll=${!!g.__vexaCsrcPoll}`);
check('the mix topology crossed as DATA, not only as a log line',
  observations.some((o) => o.observation.type === 'mix-topology' && o.observation.streams === 1),
  JSON.stringify(observations));

// Nobody speaking: the sensor must be silent rather than reporting a floor.
tick(3);
check('silence emits no transitions', transitions.length === 0, JSON.stringify(transitions));

speakingSince = (globalThis as unknown as { performance: { now: () => number } }).performance.now();
tick(1);
check('an activation crossed page→Node with the transport fields intact',
  transitions.length === 1 && transitions[0].active === true && transitions[0].csrc === 424242
  && transitions[0].audioLevel === 0.3 && transitions[0].rtpTimestamp === 987, JSON.stringify(transitions));
check('the transition timestamp is epoch ms (the performance clock was converted, not carried)',
  transitions[0] !== undefined && transitions[0].t >= t0 && transitions[0].t <= Date.now() + 1000,
  JSON.stringify(transitions.map((x) => x.t)));

tick(3);
check('steady speech emits nothing further (transitions only, across the boundary too)',
  transitions.length === 1, JSON.stringify(transitions));

await stop();
check('teardown flushes the still-open turn as a deactivation',
  transitions.length === 2 && transitions[1].active === false && transitions[1].csrc === 424242,
  JSON.stringify(transitions));
check('teardown released the poller', !g.__vexaCsrcPoll);

// A2: the edge now REACHES THE LANE, as the turn SPINE. What must still never happen is a
// transition arriving as a naming HINT — it carries a source id, not a person, and the binder
// would have to invent the person. The lane and the sidecar must also see the SAME stamped time,
// or a replay scores a run the live bot never had.
check('the transport edge reached the lane as a spine event (A1 teed it to the tape only)',
  spine.length === transitions.length && spine.length >= 2
  && spine[0].csrc === 424242 && spine[0].active === true, JSON.stringify(spine));
check('the spine and the stored sidecar agree on WHEN each edge happened',
  spine.every((e, i) => transitions[i] && transitions[i].t === e.tMs && transitions[i].active === e.active),
  `${JSON.stringify(spine.map((s) => s.tMs))} vs ${JSON.stringify(transitions.map((t) => t.t))}`);
check('isolation: NO transition reached pipeline.recordHint — a csrc is an id, never a name',
  hints.length === 0, JSON.stringify(hints));

// ── the MEASURED inactivity window reaches the sensor ────────────────────────────────────────────
// 400 ms — the sensor's own default — is shorter than the median natural speech pause (measured
// p50 550 ms), so a turn fragments into 1.67 lane activations. The composition root passes the
// measured 800 ms instead. Asserted twice: the value that crossed, and what that value DOES.
check('the bridge passes an explicit inactivity window to the sensor (RED at base: undefined)',
  productionCsrcOpts?.inactiveMs === 800, `inactiveMs=${String(productionCsrcOpts?.inactiveMs)}`);
{
  // Re-drive the sensor over the PRODUCTION options object — same `inactiveMs`, with only the
  // clock and the receivers replaced, so the window is proven by behaviour and not by a number.
  let t = 1_900_000_000_000;
  const edges: Array<{ active: boolean }> = [];
  let speaking = true;
  let lastSpoke = t;
  const poll = realCreateCsrcPoll({
    ...productionCsrcOpts!,
    onTransition: (x) => edges.push({ active: x.active }),
    now: () => t,
    timeOrigin: () => 0,
    receivers: () => [{ track: { kind: 'audio' },
      getContributingSources: () => [{ source: 5, timestamp: speaking ? t : lastSpoke, audioLevel: 0.4 }] }],
  }) as { poll(): void; destroy(): void };
  poll.poll();
  speaking = false;
  t += 500; poll.poll();
  check('a 500ms pause — past the sensor default, inside the measured window — does NOT close the turn',
    edges.length === 1 && edges[0].active === true, JSON.stringify(edges));
  t += 400; poll.poll();
  check('past the measured window the deactivation is synthesized',
    edges.length === 2 && edges[1].active === false, JSON.stringify(edges));
  poll.destroy();
}

(g as any).setInterval = realSetInterval;
(g as any).clearInterval = realClearInterval;
(utils as Record<string, unknown>).createCsrcPoll = realCreateCsrcPoll;
g.document = savedDocument;

if (failed) { console.error(`\n❌ csrc-wiring: ${failed} checks FAILED.`); process.exit(1); }
console.log('\n✅ csrc-wiring: the real bundle + real bridge carry transport transitions and typed observations page→Node (epoch ms, teardown flush) and never into the name binder.');
