/**
 * L3 boundary — the transport sensor reads a REAL browser's receivers and its edges reach the tape.
 *
 * Launches the REAL bridge wiring end-to-end, no meeting and no display:
 *   1. asserts the built browser bundle (dist/browser-utils.global.js) exposes createCsrcPoll —
 *      the regression class that shipped once already: a brick missing from the bundle, with every
 *      offline test still green because they import the source rather than the artifact;
 *   2. headless Chromium (the same launchPersistentBrowser the bot uses) loads a fixture page that
 *      stubs RTCPeerConnection *the way a real one is shaped* and installs the REAL remote-audio
 *      hook over it — so the sensor reaches its receivers through the hook's own registry, which is
 *      the entire coupling between the two;
 *   3. startCaptureBridge (the real function) wires the page; the fixture scripts a contributing
 *      source appearing and going quiet; the test asserts the transitions arrive Node-side with an
 *      EPOCH timestamp and land in the csrc sidecar of a stub recorder.
 *   4. the NEGATIVE path, which is the common one: a page with NO peer connections must produce
 *      zero transitions and zero error observations. A sensor that reported a fault on every page
 *      that simply has nothing to observe would make the diagnostic worthless on the day it fires.
 *
 * `ontrack` is defined as a PROTOTYPE ACCESSOR because that is what it is on a real
 * RTCPeerConnection: declared as a plain instance field, the hook's setter wrapper silently never
 * installs (webrtc-dedup.test.ts's lesson), and the stub would then be testing a browser that does
 * not exist. `getContributingSources` sits on a receiver returned by `getReceivers()`, for the
 * same reason — the sensor must walk the real path, not a convenience one.
 *
 * Where headless Chromium cannot launch (no playwright browser in the env) the test SKIPS LOUDLY
 * with exit 0 — the same green-or-skip shape as gate:stack.
 * Run: npx tsx src/csrc-capture.boundary.test.ts
 */
import { execSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchPersistentBrowser, type BrowserContext } from '@vexa/remote-browser';
import { startCaptureBridge } from './capture-bridge.js';
import { createCaptureSignalRecorder } from './telemetry.js';
import type { BotPipeline, HintCounters } from './pipeline.js';
import type { Invocation } from './config.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const BOT_DIR = join(HERE, '..');
const BUNDLE = join(BOT_DIR, 'dist', 'browser-utils.global.js');

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** The transport, stubbed at the shape the spec describes and Chromium implements. */
const TRANSPORT_STUB = `
  window.__fixtureSpeakingSince = null;         // performance-clock ms, or null for silence
  const receiver = {
    track: { kind: 'audio' },
    getContributingSources() {
      if (window.__fixtureSpeakingSince === null) return [];
      return [{ source: 424242, timestamp: window.__fixtureSpeakingSince, audioLevel: 0.7, rtpTimestamp: 5150 }];
    },
  };
  const videoReceiver = { track: { kind: 'video' }, getContributingSources() { return [{ source: 9, timestamp: performance.now() }]; } };
  class FixturePC {
    addEventListener() {}
    getReceivers() { return [receiver, videoReceiver]; }
    close() {}
  }
  // ontrack as a PROTOTYPE ACCESSOR — a real RTCPeerConnection's shape, and the only shape in
  // which the hook's setter wrapper installs at all.
  Object.defineProperty(FixturePC.prototype, 'ontrack', {
    get() { return this._ontrack; },
    set(fn) { this._ontrack = fn; },
    configurable: true, enumerable: true,
  });
  window.RTCPeerConnection = FixturePC;
`;

const FIXTURE = `<!doctype html><html><body><div id="page">transport fixture</div></body></html>`;
const EMPTY_FIXTURE = `<!doctype html><html><body><div id="page">no peer connections here</div></body></html>`;

async function main(): Promise<void> {
  // ── 1) the bundle carries the sensor (the shipped regression class) ──
  if (!existsSync(BUNDLE)) execSync('node build-browser-utils.mjs', { cwd: BOT_DIR, stdio: 'inherit' });
  const bundleHasCsrc = execSync(`grep -c createCsrcPoll ${JSON.stringify(BUNDLE)} || true`).toString().trim() !== '0';
  check('browser bundle exposes createCsrcPoll', bundleHasCsrc);

  // ── 2) headless browser (green-or-skip where chromium is absent) ──
  const dataDir = mkdtempSync(join(tmpdir(), 'vexa-csrc-boundary-'));
  const tapeDir = mkdtempSync(join(tmpdir(), 'vexa-csrc-tape-'));
  let context: BrowserContext;
  let page;
  try {
    ({ context, page } = await launchPersistentBrowser({ dataDir, args: ['--no-sandbox', '--mute-audio'], headless: true }));
  } catch (e) {
    console.log(`  ⚠️ SKIP — headless Chromium unavailable in this environment: ${(e as Error).message?.split('\n')[0]}`);
    process.exit(0);
  }
  try {
    const pageLogs: string[] = [];
    await context.exposeFunction('logBot', (m: string) => pageLogs.push(String(m)));

    const hintCounters: HintCounters = { received: 0, matched: 0, missed: 0 };
    const hints: Array<{ name: string; tMs: number }> = [];
    const pipeline: BotPipeline = {
      async start() { /* stub */ }, async stop() { /* stub */ },
      feedAudio() { /* stub */ }, feedMixedAudio() { /* stub */ },
      recordHint(name, tMs) { hintCounters.received++; hints.push({ name, tMs }); },
      hintCounters,
    };
    const inv: Invocation = {
      platform: 'teams', meetingUrl: 'https://teams.fixture.test/m', botName: 'Vexa',
      redisUrl: 'redis://localhost:6379', transcribeEnabled: false, connectionId: 'csrc-boundary',
    };
    // The REAL recorder: this is also the proof that a transition survives all the way to the file
    // the teardown upload ships, not merely to a callback.
    const recorder = createCaptureSignalRecorder(inv, { dir: tapeDir });

    await page.setContent(FIXTURE);
    // setContent does not re-run context init scripts in this launch shape, so load the SAME
    // prebuilt bundle into the fixture document directly (identical bytes to what addInitScript
    // injects on a real navigation).
    await page.addScriptTag({ path: BUNDLE });
    // tsx transpiles this test with esbuild keepNames, whose `__name` helper leaks into
    // page.evaluate-serialized functions; shim it page-side so the REAL bridge code runs unmodified.
    await page.evaluate('globalThis.__name = globalThis.__name || ((t, v) => t);');
    // Stub the transport, then install the REAL hook over it and build a connection — which is what
    // puts the connection in the registry the sensor reads. Order matters exactly as it does live:
    // the hook must patch before the page constructs its peer connections.
    await page.evaluate(TRANSPORT_STUB);
    await page.evaluate('window.VexaBrowserUtils.installRemoteAudioHook({}); window.__fixturePc = new RTCPeerConnection();');
    check('the hook registered the fixture connection (the sensor reads THIS registry)',
      await page.evaluate('(window.__vexa_peer_connections || []).length') === 1);

    const t0 = Date.now();
    const stop = await startCaptureBridge(page, inv, pipeline, recorder.sink);

    await sleep(400);                                    // several 100ms polls over silence
    const silent = recorder.sink as unknown as Record<string, unknown>;
    void silent;
    await page.evaluate('window.__fixtureSpeakingSince = performance.now();');
    await sleep(400);
    await page.evaluate('window.__fixtureSpeakingSince = null;');
    await sleep(900);                                    // > the 400ms inactivity window
    await stop();
    await recorder.close();

    // ── 3) the assertions: the edges reached the sidecar, in order, on the audio's clock ──
    check('page-side sensor started (its start line is visible in the page logs)',
      pageLogs.some((l) => l.includes('[Csrc]')), JSON.stringify(pageLogs.slice(0, 5)));
    const lines = existsSync(recorder.csrcPath)
      ? readFileSync(recorder.csrcPath, 'utf8').trim().split('\n').map((l) => JSON.parse(l))
      : [];
    const header = lines[0];
    const edges = lines.slice(1);
    check('the csrc sidecar exists and opens with its mini-header',
      header?.type === 'sidecar_header' && header?.part === 'csrc', JSON.stringify(header));
    check('an activation and a deactivation crossed page→Node and were stored, in that order',
      edges.length >= 2 && edges[0].active === true && edges[0].csrc === 424242
      && edges.some((e) => e.active === false && e.csrc === 424242),
      JSON.stringify(edges));
    check('the stored timestamp is epoch ms — Chromium reports the performance clock, and the '
      + 'sensor converted it', edges[0] !== undefined && edges[0].t >= t0 && edges[0].t <= Date.now(),
      JSON.stringify(edges.map((e) => e.t)));
    check('the transport levels survived the crossing',
      edges[0]?.audioLevel === 0.7 && edges[0]?.rtpTimestamp === 5150, JSON.stringify(edges[0]));
    check('steady speech did not flood the sidecar (transitions only, ~4 polls of speech)',
      edges.length <= 4, `${edges.length} lines`);
    check('the video receiver contributed nothing (audio only)',
      !edges.some((e) => e.csrc === 9), JSON.stringify(edges));
    check('no transition became a naming hint', hints.length === 0, JSON.stringify(hints));

    // ── 4) the negative path — the common one ──
    const empty = await context.newPage();
    const emptyTransitions: unknown[] = [];
    const emptyObservations: Array<Record<string, unknown>> = [];
    await empty.setContent(EMPTY_FIXTURE);
    await empty.addScriptTag({ path: BUNDLE });
    await empty.evaluate('globalThis.__name = globalThis.__name || ((t, v) => t);');
    const emptySink = {
      captureFrame() { /* unused */ },
      captureCsrc: (r: unknown) => emptyTransitions.push(r),
      captureObservation: (o: Record<string, unknown>) => emptyObservations.push(o),
    };
    const stopEmpty = await startCaptureBridge(empty, inv, pipeline, emptySink);
    await sleep(700);
    await stopEmpty();
    check('a page with NO peer connections produces zero transitions',
      emptyTransitions.length === 0, JSON.stringify(emptyTransitions));
    check('…and zero error observations — nothing to observe is not a fault',
      !emptyObservations.some((o) => String((o.observation as Record<string, unknown>)?.kind ?? '').includes('csrc-poll-error')),
      JSON.stringify(emptyObservations));
    await empty.close().catch(() => { /* best-effort */ });
  } finally {
    await context.close().catch(() => { /* best-effort */ });
    rmSync(dataDir, { recursive: true, force: true });
    rmSync(tapeDir, { recursive: true, force: true });
  }

  if (failed) { console.error(`\n❌ csrc-capture.boundary: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ csrc-capture.boundary: the real bundle + real bridge read a real browser\'s receivers, and the turn edges land in the tape on the audio\'s clock.');
}

await main();
