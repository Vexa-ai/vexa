/**
 * csrc-poll — the transport sensor's contract, driven over stub receivers.
 *
 * No browser and no meeting: the poll is ticked by hand over a deterministic clock, which is the
 * only way to assert the two things that actually matter and are invisible live —
 *   • steady state emits NOTHING (a 100 ms poll that re-reported every speaker would produce ten
 *     identical lines a second, and a tape nobody can read);
 *   • a source that stops contributing produces a SYNTHETIC deactivation, because the UA keeps
 *     listing recent sources and never sends that edge itself.
 *
 * Plus the failure discipline the capture path depends on: one receiver whose
 * getContributingSources throws is counted and skipped while the others keep working, and destroy()
 * closes the open turns and then goes quiet.
 *
 * Run: npx tsx src/csrc-poll.test.ts
 */
let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

// The module arms an interval at creation; keep the real timers out of the test's way.
const g = globalThis as unknown as Record<string, unknown>;
const realSetInterval = globalThis.setInterval;
const realClearInterval = globalThis.clearInterval;
let armed = 0;
g.setInterval = (): number => { armed++; return armed; };
g.clearInterval = (): void => { /* controlled clock */ };

const { createCsrcPoll, toEpochMs, CSRC_INACTIVE_MS } = await import('./csrc-poll.js');
import type { ContributingSourceLike, CsrcTransition, CsrcPollErrorObservation } from './csrc-poll.js';

/** A receiver shaped like the real one: an audio track plus the CSRC accessor. */
const receiver = (sources: () => ContributingSourceLike[]) => ({
  track: { kind: 'audio' },
  getContributingSources: sources,
});

// ── clock-domain resolution ─────────────────────────────────────────────────────────────────────
{
  const now = 1_754_899_200_123;
  const origin = 1_754_899_100_000;
  check('epoch timestamps pass through untouched', toEpochMs(now - 50, now, origin) === now - 50);
  check('a performance-clock timestamp is shifted by the time origin',
    toEpochMs(200, now, origin) === origin + 200, String(toEpochMs(200, now, origin)));
  check('an implausible timestamp is stamped now, never carried into the future',
    toEpochMs(4e12, now, origin) === now && toEpochMs(undefined, now, origin) === now);
}

// ── transitions only + synthetic deactivation ───────────────────────────────────────────────────
{
  let t = 1_800_000_000_000;
  const out: CsrcTransition[] = [];
  let speaking = true;
  const poll = createCsrcPoll({
    onTransition: (x) => out.push(x),
    now: () => t,
    timeOrigin: () => 0,
    // The UA keeps listing a source after it stops — with a FROZEN timestamp. That is the shape
    // this sensor has to survive, so the stub reproduces it rather than dropping the entry.
    receivers: () => [receiver(() => [{ source: 7, timestamp: speaking ? t : lastSpoke, audioLevel: 0.4, rtpTimestamp: 111 }])],
  });
  let lastSpoke = t;
  check('the poller armed a timer at creation', armed === 1, String(armed));

  poll.poll();
  check('the first appearance emits exactly one activation, with epoch tMs and the carried level',
    out.length === 1 && out[0].active === true && out[0].csrc === 7 && out[0].tMs === t
    && out[0].audioLevel === 0.4 && out[0].rtpTimestamp === 111, JSON.stringify(out));

  for (let i = 0; i < 20; i++) { t += 100; lastSpoke = t; poll.poll(); }
  check('two seconds of steady speech emit NOTHING further (transitions only)',
    out.length === 1, JSON.stringify(out));
  check('health reports the source active and the polls counted',
    poll.health().active === 1 && poll.health().polls === 21 && poll.health().receivers === 1,
    JSON.stringify(poll.health()));

  // The source stops: the UA still lists it, with the timestamp frozen at its last contribution.
  speaking = false;
  t += 200; poll.poll();
  check('inside the inactivity window a stale entry is NOT yet a deactivation',
    out.length === 1, JSON.stringify(out));
  t += 550; poll.poll();   // 750ms staleness — just inside the 800ms window
  check('just inside the window the entry is STILL held open (the boundary, not the middle)',
    out.length === 1, JSON.stringify(out));
  t += 100; poll.poll();   // 850ms > 800ms default
  check('past inactiveMs the deactivation is SYNTHESIZED (the transport never sends this edge)',
    out.length === 2 && out[1].active === false && out[1].csrc === 7 && out[1].tMs === t,
    JSON.stringify(out));
  check('the default inactivity window is 800ms', CSRC_INACTIVE_MS === 800);

  // Speaking again is a NEW activation — turns are edges, not a level.
  speaking = true; lastSpoke = t; poll.poll();
  check('the same source speaking again opens a new turn',
    out.length === 3 && out[2].active === true && out[2].csrc === 7, JSON.stringify(out));

  poll.destroy();
  check('destroy() flushes the still-open turn as a deactivation',
    out.length === 4 && out[3].active === false, JSON.stringify(out));
  const after = out.length;
  poll.poll();
  check('a poll after destroy() emits nothing', out.length === after);
}

// ── two concurrent sources ──────────────────────────────────────────────────────────────────────
{
  let t = 1_900_000_000_000;
  const out: CsrcTransition[] = [];
  const live = new Set<number>([11, 22]);
  const poll = createCsrcPoll({
    onTransition: (x) => out.push(x),
    now: () => t,
    timeOrigin: () => 0,
    receivers: () => [receiver(() => Array.from(live).map((source) => ({ source, timestamp: t })))],
  });
  poll.poll();
  check('two sources contributing at once are BOTH active (overlap is observed, not collapsed)',
    out.length === 2 && out.every((x) => x.active) && out.map((x) => x.csrc).sort().join() === '11,22',
    JSON.stringify(out));
  check('health counts both as active', poll.health().active === 2, JSON.stringify(poll.health()));
  live.delete(11);
  t += 900; poll.poll();
  check('only the source that went quiet deactivates; the other stays open',
    out.length === 3 && out[2].csrc === 11 && out[2].active === false && poll.health().active === 1,
    JSON.stringify(out));
  poll.destroy();
}

// ── a throwing receiver ─────────────────────────────────────────────────────────────────────────
{
  let t = 2_000_000_000_000;
  const out: CsrcTransition[] = [];
  const obs: CsrcPollErrorObservation[] = [];
  const poll = createCsrcPoll({
    onTransition: (x) => out.push(x),
    onObservation: (o) => obs.push(o),
    now: () => t,
    timeOrigin: () => 0,
    receivers: () => [
      receiver(() => { throw new Error('receiver is gone'); }),
      receiver(() => [{ source: 99, timestamp: t }]),
      // A video receiver and a track-less one are simply not this sensor's business.
      { track: { kind: 'video' }, getContributingSources: () => [{ source: 5, timestamp: t }] },
    ],
  });
  poll.poll();
  check('a receiver that throws does not stop the others — the healthy source still reports',
    out.length === 1 && out[0].csrc === 99, JSON.stringify(out));
  check('the failure is counted and emitted ONCE as a typed observation',
    poll.health().errors === 1 && obs.length === 1 && obs[0].kind === 'csrc-poll-error'
    && obs[0].where === 'contributing-sources' && obs[0].error.includes('receiver is gone'),
    JSON.stringify({ health: poll.health(), obs }));
  for (let i = 0; i < 5; i++) { t += 100; poll.poll(); }
  check('a permanently broken receiver keeps being counted but never re-floods the log',
    poll.health().errors === 6 && obs.length === 1, JSON.stringify({ health: poll.health(), obs: obs.length }));
  check('video receivers are ignored (audio only)', !out.some((x) => x.csrc === 5), JSON.stringify(out));
  poll.destroy();
}

// ── the common path: a page with nothing to observe ─────────────────────────────────────────────
{
  const out: CsrcTransition[] = [];
  const obs: CsrcPollErrorObservation[] = [];
  const poll = createCsrcPoll({
    onTransition: (x) => out.push(x), onObservation: (o) => obs.push(o), receivers: () => [],
  });
  for (let i = 0; i < 5; i++) poll.poll();
  check('no receivers ⇒ zero transitions, zero errors (silence, not a diagnostic storm)',
    out.length === 0 && obs.length === 0 && poll.health().errors === 0 && poll.health().receivers === 0,
    JSON.stringify({ out, obs, health: poll.health() }));
  poll.destroy();
}

// ── a consumer that throws must not reach capture ───────────────────────────────────────────────
{
  let t = 2_100_000_000_000;
  const poll = createCsrcPoll({
    onTransition: () => { throw new Error('the sink exploded'); },
    now: () => t, timeOrigin: () => 0,
    receivers: () => [receiver(() => [{ source: 3, timestamp: t }])],
  });
  let threw = false;
  try { poll.poll(); } catch { threw = true; }
  check('a throwing transition consumer never propagates out of the poll', !threw);
  poll.destroy();
}

g.setInterval = realSetInterval;
g.clearInterval = realClearInterval;

if (failed) { console.error(`\n❌ csrc-poll: ${failed} check(s) FAILED.`); process.exit(1); }
console.log('\n✅ csrc-poll: transitions only, synthetic deactivation, concurrent sources, a broken receiver contained, and a clean stop.');
