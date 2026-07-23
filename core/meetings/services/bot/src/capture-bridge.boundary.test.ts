/**
 * L3 boundary — Teams speaker hints cross the page→Node boundary (#498 C4).
 *
 * Launches the REAL bridge wiring end-to-end, no meeting and no display:
 *   1. asserts the built browser bundle (dist/browser-utils.global.js) exposes
 *      createTeamsSpeakers (the regression that shipped seg_N: the brick missing
 *      from the bundle);
 *   2. headless Chromium (the same launchPersistentBrowser the bot uses) loads a
 *      static fixture page with a Teams-shaped DOM — a participant tile carrying
 *      the voice-level-stream-outline signal — plus a 1:1-layout tile WITHOUT the
 *      outline (the #481 two-party class: no signal ⇒ no hint, never a wrong one);
 *   3. startCaptureBridge (the real function) wires the page; the fixture toggles
 *      the vdi-frame-occlusion speaking signal; the test asserts the hints arrive
 *      Node-side with the participant's NAME, epoch tMs, and start/end order.
 *
 * Where headless Chromium cannot launch (no playwright browser in the env) the test
 * SKIPS LOUDLY with exit 0 — the same green-or-skip shape as gate:stack.
 * Run: npx tsx src/capture-bridge.boundary.test.ts
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
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const FIXTURE = `<!doctype html><html><body>
  <div data-tid="participant-tile" id="alice-tile">
    <span title="Alice Fixture">Alice Fixture</span>
    <div data-tid="voice-level-stream-outline" id="alice-outline"></div>
  </div>
  <!-- #481 two-party (1:1) layout class: the outline indicator never renders.
       The watcher must emit NO hint for this tile (no signal ⇒ silence, not a guess). -->
  <div data-tid="participant-tile" id="bob-tile">
    <span title="Bob OneToOne">Bob OneToOne</span>
  </div>
</body></html>`;

async function main(): Promise<void> {
  // ── 1) the bundle carries the Teams brick (the shipped regression) ──
  if (!existsSync(BUNDLE)) execSync('node build-browser-utils.mjs', { cwd: BOT_DIR, stdio: 'inherit' });
  const bundleHasTeams = execSync(`grep -c createTeamsSpeakers ${JSON.stringify(BUNDLE)} || true`).toString().trim() !== '0';
  check('browser bundle exposes createTeamsSpeakers', bundleHasTeams);

  // ── 2) headless browser (green-or-skip where chromium is absent) ──
  const dataDir = mkdtempSync(join(tmpdir(), 'vexa-boundary-'));
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

    // The REAL bridge over a stub pipeline capturing what crosses the boundary.
    const hints: { name: string; tMs: number; isEnd?: boolean }[] = [];
    const hintCounters: HintCounters = { received: 0, matched: 0, missed: 0 };
    const mixedFrames: { len: number; tsMs: number }[] = [];
    const pipeline: BotPipeline = {
      async start() { /* stub */ }, async stop() { /* stub */ },
      feedAudio() { /* stub */ },
      feedMixedAudio(pcm, tsMs) { mixedFrames.push({ len: pcm.length, tsMs }); },
      recordHint(name, tMs, isEnd) { hintCounters.received++; hints.push({ name, tMs, isEnd }); },
      hintCounters,
    };
    const inv: Invocation = {
      platform: 'teams', meetingUrl: 'https://teams.fixture.test/m', botName: 'Vexa',
      redisUrl: 'redis://localhost:6379', transcribeEnabled: false,
    };
    await page.setContent(FIXTURE);
    // setContent does not re-run context init scripts in this launch shape, so load the
    // SAME prebuilt bundle into the fixture document directly (identical bytes to what
    // addInitScript injects on a real navigation).
    await page.addScriptTag({ path: BUNDLE });
    // tsx transpiles this test with esbuild keepNames, whose `__name` helper leaks into
    // page.evaluate-serialized functions; shim it page-side so the REAL bridge code
    // (which ships helper-free via tsc) runs unmodified under the test runner.
    await page.evaluate('globalThis.__name = globalThis.__name || ((t, v) => t);');
    const stop = await startCaptureBridge(page, inv, pipeline);

    // Fixture drives the speaking signal: occlusion class ON (start) → OFF (end).
    await sleep(600);   // observer attach + initial silent state past the 200ms hysteresis
    await page.evaluate(`document.getElementById('alice-outline').classList.add('vdi-frame-occlusion')`);
    await sleep(900);   // 200ms hysteresis + 300ms debounce + margin
    await page.evaluate(`document.getElementById('alice-outline').classList.remove('vdi-frame-occlusion')`);
    await sleep(900);
    await stop();

    // ── 3) the assertions: hints crossed with name, epoch clock, order ──
    check('page-side watcher started (hop 1 visible in page logs)', pageLogs.some((l) => l.includes('[TeamsSpeakers]')), JSON.stringify(pageLogs.slice(0, 3)));
    const alice = hints.filter((h) => h.name === 'Alice Fixture');
    check('speaker hints crossed the boundary for the outlined tile', alice.length >= 2, JSON.stringify(hints));
    // The watcher reports the tile's initial silent state first (an END-shaped hint),
    // then the scripted transitions: START on occlusion, END on its removal — in order.
    const firstStart = alice.findIndex((h) => h.isEnd === false);
    check('a START hint (isEnd=false) crossed for the speaking transition', firstStart >= 0, JSON.stringify(alice));
    check('an END hint follows the START (transition order intact)', firstStart >= 0 && alice.slice(firstStart + 1).some((h) => h.isEnd === true), JSON.stringify(alice));
    check('hint tMs is epoch ms (same clock domain as audio)', alice.every((h) => Math.abs(h.tMs - Date.now()) < 60_000), JSON.stringify(alice.map((h) => h.tMs)));
    check('bot self-name and the signal-less 1:1 tile emit NO hints',
      !hints.some((h) => h.name.includes('Vexa') || h.name === 'Bob OneToOne'), JSON.stringify(hints));
    check('pipeline-received counter moved with the arrivals', hintCounters.received === hints.length && hintCounters.received > 0, JSON.stringify(hintCounters));

    // ── 4) the CAPTURE-TS boundary: the frame's own capture time crosses, or the frame is dropped ──
    // The mixed lane's audio front door is __vexaPerSpeakerAudioData(0, b64, tsMs). A frame WITH a
    // capture stamp must reach feedMixedAudio carrying that EXACT ts (no arrival re-stamp); a frame
    // WITHOUT one must be dropped, surfaced as a loud fault, and never reach the pipeline.
    const capFaults: string[] = [];
    const realErr = console.error;
    console.error = (...a: unknown[]) => { const m = a.join(' '); if (m.includes('[capture] FAULT')) capFaults.push(m); realErr(...a); };
    // A tiny 4-sample PCM as base64 (16 bytes) — content is irrelevant, only the ts path is under test.
    const b64 = Buffer.from(new Float32Array([0.1, 0.2, 0.3, 0.4]).buffer).toString('base64');
    const CAPTURE_TS = 1_700_000_000_123;   // a fixed epoch ms, unmistakably not Date.now()
    const beforeFrames = mixedFrames.length;
    await page.evaluate(([s, ts]) => (globalThis as any).__vexaPerSpeakerAudioData(0, s, ts), [b64, CAPTURE_TS] as [string, number]);
    await page.evaluate((s) => (globalThis as any).__vexaPerSpeakerAudioData(0, s), b64);   // no tsMs → dropped
    await sleep(100);
    console.error = realErr;
    const stamped = mixedFrames.slice(beforeFrames);
    check('a mixed frame WITH a capture ts reaches feedMixedAudio with that exact ts',
      stamped.length === 1 && stamped[0].tsMs === CAPTURE_TS && stamped[0].len === 4, JSON.stringify(stamped));
    check('a mixed frame WITHOUT a capture ts is dropped — nothing extra reached the pipeline',
      mixedFrames.length === beforeFrames + 1, JSON.stringify(mixedFrames.slice(beforeFrames)));
    check('the dropped frame surfaced a loud capture fault (counter incremented)',
      capFaults.length === 1 && capFaults[0].includes('dropped (1)'), JSON.stringify(capFaults));
  } finally {
    await context.close().catch(() => { /* best-effort */ });
    rmSync(dataDir, { recursive: true, force: true });
  }

  console.log(failed === 0 ? '\n✅ capture-bridge boundary: all green' : `\n❌ capture-bridge boundary: ${failed} failure(s)`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e?.stack || e); process.exit(1); });
