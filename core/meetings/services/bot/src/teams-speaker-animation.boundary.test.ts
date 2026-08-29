/**
 * L3 boundary — a Teams voice-level outline that ANIMATES produces exactly one
 * START and one END through onSpeaking, in a real headless Chromium, over the
 * real bundle and the real bridge.
 *
 * WHY THIS EXISTS. The DOM-shim unit tests for the candidate-indicator detector
 * were all green while the hint path was completely dead. They drove exactly ONE
 * sample per DOM change; a real page samples at ~60fps (rAF) while a voice bar
 * updates every ~150ms, so ~8 of every 9 samples observe no change. An edge
 * predicate ("did the style just change") read raw as a level ("is this person
 * speaking") therefore flapped at the sampling rate: the 200ms hysteresis
 * admitted an alternating transition roughly every 200ms, and because the 300ms
 * debounce is LONGER than that, every pending emit was cancelled by the opposite
 * transition before it could fire. Observed: transitions=3, `indicator-fired` x3,
 * `onSpeaking` never called, no SPEAKER_START log line at all — diagnostics
 * alive, hints dead, i.e. Teams still produced zero attribution in production.
 *
 * A shim-only test is not sufficient evidence for this behaviour. This one runs
 * the REAL page code at the REAL frame cadence with the REAL wall clock:
 *   1. the built bundle exposes createTeamsSpeakers;
 *   2. headless Chromium loads a Teams-shaped fixture — a stream wrapper whose
 *      outer layout matches no participant selector and whose outline animates
 *      like a voice bar, plus a tile with NO outline;
 *   3. startCaptureBridge (the real function) wires the page; the fixture drives
 *      10 style updates at 150ms; the test asserts ONE START then ONE END cross
 *      Node-side with the participant's name, and ZERO hints for the tile that
 *      carries no signal.
 *
 * Where headless Chromium cannot launch the test SKIPS LOUDLY with exit 0 — the
 * same green-or-skip shape as capture-bridge.boundary.test.ts and gate:stack.
 * Run: npx tsx src/teams-speaker-animation.boundary.test.ts
 */
import { execSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { launchPersistentBrowser, type BrowserContext } from '@vexa/remote-browser';
import { startCaptureBridge } from './capture-bridge.js';
import type { BotPipeline, HintCounters } from './pipeline.js';
import type { Invocation } from './config.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const BOT_DIR = join(HERE, '..');
const BUNDLE = join(BOT_DIR, 'dist', 'browser-utils.global.js');

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

// Teams' production shape: the display name lives on the `data-tid` of the
// `[data-stream-type]` wrapper, and the voice-level outline sits inside it.
// Carol's tile carries no outline at all (the #481 1:1-layout class): no signal
// ⇒ no hint, ever — never a guessed one.
const FIXTURE = `<!doctype html><html><body>
  <section id="t1">
    <div data-tid="Alice Real" data-stream-type="Video">
      <div data-tid="voice-level-stream-outline" id="o1" style="transform:scaleY(0.1)"></div>
    </div>
  </section>
  <div data-tid="participant-tile-3" id="t3" title="Carol NoOutline">
    <span title="Carol NoOutline">Carol NoOutline</span>
  </div>
</body></html>`;

async function main(): Promise<void> {
  // ── 1) the bundle carries the Teams brick, freshly built from source ──
  if (!existsSync(BUNDLE)) execSync('node build-browser-utils.mjs', { cwd: BOT_DIR, stdio: 'inherit' });
  const bundleHasTeams = execSync(`grep -c createTeamsSpeakers ${JSON.stringify(BUNDLE)} || true`).toString().trim() !== '0';
  check('browser bundle exposes createTeamsSpeakers', bundleHasTeams);

  // ── 2) headless browser (green-or-skip where chromium is absent) ──
  const dataDir = mkdtempSync(join(tmpdir(), 'vexa-teams-anim-'));
  let context: BrowserContext;
  let page;
  try {
    ({ context, page } = await launchPersistentBrowser({ dataDir, args: ['--no-sandbox', '--mute-audio'], headless: true }));
  } catch (e) {
    console.log(`  ⚠️ SKIP — headless Chromium unavailable in this environment: ${(e as Error).message?.split('\n')[0]}`);
    process.exit(0);
  }
  try {
    await context.addInitScript({ path: BUNDLE });
    const pageLogs: string[] = [];
    await context.exposeFunction('logBot', (m: string) => pageLogs.push(String(m)));

    const hints: { name: string; tMs: number; isEnd?: boolean }[] = [];
    const hintCounters: HintCounters = { received: 0, matched: 0, missed: 0 };
    const pipeline: BotPipeline = {
      async start() { /* stub */ }, async stop() { /* stub */ },
      feedAudio() { /* stub */ }, feedMixedAudio() { /* stub */ },
      recordHint(name, tMs, isEnd) { hintCounters.received++; hints.push({ name, tMs, isEnd }); },
      hintCounters,
    };
    const inv: Invocation = {
      platform: 'teams', meetingUrl: 'https://teams.fixture.test/m', botName: 'Vexa',
      redisUrl: 'redis://localhost:6379', transcribeEnabled: false,
    };
    await page.setContent(FIXTURE);
    // setContent does not re-run context init scripts in this launch shape, so load the
    // SAME prebuilt bundle into the fixture document directly (identical bytes).
    await page.addScriptTag({ path: BUNDLE });
    // tsx transpiles this test with esbuild keepNames, whose `__name` helper leaks into
    // page.evaluate-serialized functions; shim it page-side so the REAL bridge code runs
    // unmodified under the test runner.
    await page.evaluate('globalThis.__name = globalThis.__name || ((t, v) => t);');
    const stop = await startCaptureBridge(page, inv, pipeline);

    // The voice bar animates: 10 inline-style updates at 150ms, exactly as a
    // real Teams voice-level outline does. Nothing else about the DOM changes.
    await sleep(700);
    for (let i = 0; i < 10; i++) {
      await page.evaluate(`document.getElementById('o1').style.transform = 'scaleY(${(0.2 + i * 0.07).toFixed(2)})'`);
      await sleep(150);
    }
    await sleep(1500);   // silence: the hold expires, the turn closes
    // Read health over the real bundle before teardown.
    const health = JSON.parse(String(await page.evaluate('JSON.stringify(globalThis.__vexaTeamsSpeakers.health())')));
    await stop();

    // ── 3) the assertions ──
    check('page-side watcher started (hop 1 visible in page logs)',
      pageLogs.some((l) => l.includes('[TeamsSpeakers]')), JSON.stringify(pageLogs.slice(0, 3)));
    const alice = hints.filter((h) => h.name === 'Alice Real');
    const starts = alice.filter((h) => h.isEnd === false);
    const ends = alice.filter((h) => h.isEnd === true);
    const startLogs = pageLogs.filter((l) => l.includes('SPEAKER_START'));
    const endLogs = pageLogs.filter((l) => l.includes('SPEAKER_END'));

    // ONE turn = ONE speaking transition, one START edge, one END edge. This is
    // the assertion that was false before the fix: transitions counted up while
    // onSpeaking was never called at all.
    check('the animated turn is exactly ONE speaking transition',
      health.transitions === 1, JSON.stringify(health));
    check('exactly one SPEAKER_START edge', startLogs.length === 1, JSON.stringify(startLogs));
    check('exactly one SPEAKER_END edge', endLogs.length === 1, JSON.stringify(endLogs));
    check('a START hint crossed the boundary', starts.length >= 1, JSON.stringify(hints));
    check('exactly one END hint crossed the boundary', ends.length === 1, JSON.stringify(hints));
    check('the first hint is the START and the last is the END',
      alice[0]?.isEnd === false && alice[alice.length - 1]?.isEnd === true, JSON.stringify(alice));
    // Any START hints beyond the edge are the deliberate 2s heartbeat re-assertion
    // (it mirrors Zoom's; it re-states the CURRENT speaker for a consumer that
    // joined mid-turn, and is idempotent at the binder). It re-states — it never
    // invents: same name, inside the turn, and it emits no SPEAKER_START edge.
    check('extra STARTs are heartbeat re-assertions of the same name inside the turn',
      starts.every((h) => h.name === 'Alice Real'
        && h.tMs >= alice[0]!.tMs && h.tMs <= ends[0]!.tMs), JSON.stringify(alice));
    check('the detector named which candidate fired',
      pageLogs.some((l) => l.includes('indicator-fired indicator=inline-style-motion')),
      JSON.stringify(pageLogs.filter((l) => l.includes('indicator-fired'))));
    check('hint tMs is epoch ms (same clock domain as audio)',
      alice.length > 0 && alice.every((h) => Math.abs(h.tMs - Date.now()) < 60_000),
      JSON.stringify(alice.map((h) => h.tMs)));

    // Safety properties, unchanged by the fix.
    check('the outline-less tile produced NO hint (no signal => silence, never a guess)',
      !hints.some((h) => h.name.includes('Carol')), JSON.stringify(hints));
    check('the outline-less tile was REPORTED rather than silently skipped',
      pageLogs.some((l) => l.includes('signal-absent')),
      JSON.stringify(pageLogs.filter((l) => l.includes('absent'))));
    check('coverage accounting matches the fixture (2 found, 1 observable)',
      health.found === 2 && health.observable === 1, JSON.stringify(health));
    check('the bot self-name never crossed', !hints.some((h) => h.name.includes('Vexa')), JSON.stringify(hints));
    check('pipeline-received counter moved with the arrivals',
      hintCounters.received === hints.length && hintCounters.received > 0, JSON.stringify(hintCounters));
  } finally {
    await context.close().catch(() => { /* best-effort */ });
    rmSync(dataDir, { recursive: true, force: true });
  }

  console.log(failed === 0
    ? '\n✅ teams speaker animation boundary: one START, one END, over the real bundle'
    : `\n❌ teams speaker animation boundary: ${failed} failure(s)`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e?.stack || e); process.exit(1); });
