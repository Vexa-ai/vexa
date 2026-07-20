#!/usr/bin/env node
// extension-loop — the EXTERNAL loop, driven without a human.
//
// The framework's external loop is `clients/extension` → `services/desktop`: a real browser tab, the
// real capture path, the real lane at production config, real STT. It was treated as the half that
// needs a person because a person normally clicks it. Nothing about the loop requires that — the
// extension auto-starts on a recognised tab, so a driver can open the tab, let it run, stop it, and
// hand the recorded tape straight to the offline scorers.
//
// What this does NOT replace: a human's judgement of whether a transcript FEELS right, and any
// meeting whose other participants are people. It replaces the mechanical part — press play, wait,
// collect — which is the part that was rate-limiting the loop.
//
//   node src/extension-loop.mjs --url <page> [--sec 120] [--profile <dir>]
//
// Requires the desktop running (VEXA_RECORD_TAPE set) and a profile whose chrome.storage.local
// carries the extension's apiKey + endpoint config.
import { existsSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const { chromium } = await import(new URL('../../modules/join/node_modules/playwright/index.mjs', import.meta.url).href);

const HERE = path.dirname(new URL(import.meta.url).pathname);
const EXT = path.join(HERE, '..', '..', '..', '..', 'clients', 'extension', 'dist');
const arg = (n, d) => { const i = process.argv.indexOf(`--${n}`); return i < 0 ? d : process.argv[i + 1]; };

const URL_ = arg('url');
const SEC = Number(arg('sec', 120));
const PROFILE = arg('profile', path.join(process.env.TMPDIR || '/tmp', 'vexa-ext-loop-profile'));
const TAPES = process.env.VEXA_RECORD_TAPE;

if (!URL_) { console.error('usage: extension-loop.mjs --url <page> [--sec 120] [--profile <dir>]'); process.exit(1); }
if (!existsSync(EXT)) { console.error(`extension not built: ${EXT} (npm run build in clients/extension)`); process.exit(1); }

const newestTape = () => {
  if (!TAPES || !existsSync(TAPES)) return null;
  const files = readdirSync(TAPES).filter((f) => f.endsWith('.jsonl')).map((f) => ({ f, t: statSync(path.join(TAPES, f)).mtimeMs }));
  return files.sort((a, b) => b.t - a.t)[0]?.f ?? null;
};
const before = newestTape();

const ctx = await chromium.launchPersistentContext(PROFILE, {
  // Extensions need a real browser context, and MV3 service workers need the new headless.
  headless: false,
  args: [
    `--disable-extensions-except=${EXT}`,
    `--load-extension=${EXT}`,
    '--autoplay-policy=no-user-gesture-required',
    '--mute-audio',
  ],
});

// The service worker is the extension: waiting for it is how we know the load actually took, rather
// than discovering later that the tape is empty because nothing was ever listening.
let sw = ctx.serviceWorkers()[0];
if (!sw) sw = await ctx.waitForEvent('serviceworker', { timeout: 30_000 });
const extId = new URL(sw.url()).host;
console.log(`extension loaded: ${extId}`);

// Extension storage is keyed by extension id, and a freshly side-loaded copy gets a new one — so a
// profile a human configured by hand does not carry over to a driven run. Seed it instead of
// requiring one: the `desktop` preset already points at the desktop's own ingest and gateway.
const API_KEY = arg('api-key', process.env.VEXA_API_KEY || 'local-desktop');
await sw.evaluate(async (k) => {
  const cur = await chrome.storage.local.get(['apiKey']);
  if (!cur.apiKey) await chrome.storage.local.set({ apiKey: k, deployment: 'desktop', autoStart: true });
}, API_KEY);

const cfg = await sw.evaluate(() => chrome.storage.local.get(['apiKey', 'deployment', 'gatewayUrl', 'autoStart']));
console.log(`  config: apiKey ${cfg.apiKey ? 'set' : 'MISSING'} · deployment ${cfg.deployment ?? '(default)'} · gateway ${cfg.gatewayUrl ?? '(preset)'} · autoStart ${cfg.autoStart !== false}`);

const page = await ctx.newPage();
await page.goto(URL_, { waitUntil: 'domcontentloaded' });
// A tab that never plays is a tab with no audio to capture, and a MUTED element produces none
// either — the silence gate then drops every buffer and the tape is empty. Browser-level
// --mute-audio silences the speakers without touching what tab capture taps.
//
// The element does not exist at domcontentloaded on a player page, so playing "whatever is there"
// races the player into existence and usually loses. An empty tape then has TWO explanations — no
// stream, or no sound — and the desktop's own fault line names both, so it cannot separate them.
// Wait for the element, start it, and report the clock: a currentTime that advances rules out the
// silence half and leaves the stream half standing alone.
const playback = await page.evaluate(async () => {
  const deadline = Date.now() + 20_000;
  let el = null;
  while (Date.now() < deadline) {
    el = document.querySelector('video,audio');
    if (el) break;
    await new Promise((r) => setTimeout(r, 500));
  }
  if (!el) return { found: false };
  el.muted = false; el.volume = 1;
  try { await el.play?.(); } catch { /* autoplay policy is disabled by flag, but never assume */ }
  const t0 = el.currentTime;
  await new Promise((r) => setTimeout(r, 3000));
  return { found: true, t0, t1: el.currentTime, paused: el.paused, muted: el.muted, readyState: el.readyState };
});
console.log(`  playback: ${playback.found
  ? `element found · currentTime ${playback.t0.toFixed(1)}s → ${playback.t1.toFixed(1)}s · paused=${playback.paused} muted=${playback.muted} readyState=${playback.readyState}`
  : 'NO media element on the page after 20s'}`);
let lastT = playback.found ? playback.t1 : 0;

console.log(`capturing ${URL_} for ${SEC}s …`);
const started = Date.now();
// The SEC budget counts CAPTURED seconds, not wall seconds: a run that spends its first minute
// waiting for a gesture would otherwise bank that minute as data it never got.
let firstFrameAt = null;
let prompted = false;
let last = '';
// …but the wait for that first frame is not open-ended. WAIT_SEC is how long a gesture may take to
// arrive before the run is called for what it is: nothing captured.
const WAIT_SEC = Number(arg('wait', 120));
const hardStop = () => firstFrameAt === null && (Date.now() - started) / 1000 > WAIT_SEC;
while (!hardStop() && (firstFrameAt === null || (Date.now() - firstFrameAt) / 1000 < SEC)) {
  await new Promise((r) => setTimeout(r, 5000));
  const st = await sw.evaluate(() => new Promise((r) => { try { chrome.runtime.sendMessage({ type: 'STATUS' }, (x) => r(x || {})); } catch { r({}); } }))
    .catch(() => ({}));
  // Playback is not a start-once property. A player pauses itself — an ad break, a "still there?"
  // interstitial, a backgrounded tab — and every paused second is a second of silence the tape will
  // faithfully record as a capture defect that isn't one. Nurse it every tick, and say when it slips.
  const play = await page.evaluate(async () => {
    const el = document.querySelector('video,audio');
    if (!el) return { found: false };
    const wasPaused = el.paused;
    if (el.paused) { el.muted = false; try { await el.play?.(); } catch { /* */ } }
    return { found: true, t: el.currentTime, wasPaused, paused: el.paused };
  }).catch(() => ({ found: false }));
  if (play.found && play.wasPaused) console.log(`  ↻ player had paused at ${play.t.toFixed(1)}s — restarted (now paused=${play.paused})`);

  const tape = newestTape();
  const bytes = tape && tape !== before ? statSync(path.join(TAPES, tape)).size : 0;
  // A tape file exists the moment a session opens; only its GROWTH means audio. The header alone is
  // ~110 bytes, so anything under a kilobyte is an open session with nothing flowing through it.
  if (firstFrameAt === null && bytes > 1024) { firstFrameAt = Date.now(); console.log(`  ▶ first audio at t+${Math.round((Date.now() - started) / 1000)}s — the ${SEC}s budget starts here`); }
  const line = `  t+${Math.round((Date.now() - started) / 1000)}s status=${st.status ?? '?'} tape=${(bytes / 1e6).toFixed(1)}MB${st.error ? ' err=' + st.error : ''}`;
  console.log(line); last = line;

  // Playing audio and an empty tape leaves exactly one explanation, and it is not one this process
  // can fix: chrome.tabCapture.getMediaStreamId needs activeTab, and ONLY a toolbar click, a
  // context-menu entry or a keyboard command grants it (see clients/extension/src/background.ts).
  // Playwright drives pages, not browser chrome, so it cannot produce that gesture. Say so out loud
  // instead of accumulating a silent hour of nothing.
  const advancing = play.found && play.t > lastT + 0.5;
  lastT = play.found ? play.t : lastT;
  if (!prompted && firstFrameAt === null && advancing && (Date.now() - started) > 15_000) {
    prompted = true;
    console.log('\n  ⚠ audio IS playing in the tab and NO frames are reaching the desktop.');
    console.log('    The tab-capture stream was never minted. Chrome grants that only on a gesture:');
    console.log('    🧑 click the Vexa toolbar icon ON THIS TAB (the driven window), then Start.');
    console.log('    Capturing continues automatically the moment frames appear.\n');
  }
  if (!prompted && firstFrameAt === null && !advancing && (Date.now() - started) > 15_000) {
    prompted = true;
    console.log('\n  ⚠ no frames — but the media element is not playing either, so this run cannot');
    console.log('    tell a missing stream from a silent tab. Fix playback before reading anything.\n');
  }
}

await sw.evaluate(() => chrome.runtime.sendMessage({ type: 'STOP' })).catch(() => { /* best effort */ });
await new Promise((r) => setTimeout(r, 2000));
await ctx.close();

const after = newestTape();
console.log(`\ntape: ${after && after !== before ? path.join(TAPES, after) : 'NO NEW TAPE — capture never reached the desktop'}`);
if (after && after !== before) {
  console.log(`  ${(statSync(path.join(TAPES, after)).size / 1e6).toFixed(1)} MB`);
  console.log(`\nnext: node src/promote-fixture.mjs ${path.join(TAPES, after)} --slug <slug> --platform <p>`);
}
