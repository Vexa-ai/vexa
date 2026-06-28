/**
 * Server-side dev harness — run the extension WITHOUT a local browser install.
 *
 * Launches Chromium (the bot's Playwright install) on this machine with the
 * built extension loaded, auto-grants mic, optionally feeds a WAV through a
 * fake microphone, pre-seeds the extension config from env, opens the meeting
 * URL, and pipes every page's console to stdout.
 *
 * Usage (under xvfb on a headless server):
 *   cd services/vexa-extension && npm run build
 *   VEXA_API_KEY=vxa_... xvfb-run -a node dev/launch.mjs https://meet.google.com/abc-defg-hij [speech.wav]
 *
 * Env: VEXA_API_KEY (required) · INGEST_URL · GATEWAY_URL (defaults: localhost
 * 8092/8056). The fake-mic WAV makes the harness "speak" into the meeting, so
 * the full capture→transcribe loop runs with zero humans.
 */
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require('../../vexa-bot/core/node_modules/playwright');

const dist = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../dist');
const meetingUrl = process.argv[2];
const wav = process.argv[3] ? path.resolve(process.argv[3]) : null;
if (!meetingUrl) {
  console.error('usage: node dev/launch.mjs <meeting-url> [fake-mic.wav]');
  process.exit(1);
}
const apiKey = process.env.VEXA_API_KEY || '';
if (!apiKey) console.warn('WARN: VEXA_API_KEY not set — auto-start will not fire');

const args = [
  `--disable-extensions-except=${dist}`,
  `--load-extension=${dist}`,
  '--use-fake-ui-for-media-stream',
  '--autoplay-policy=no-user-gesture-required',
  '--no-first-run',
];
if (wav) {
  args.push('--use-fake-device-for-media-stream', `--use-file-for-fake-audio-capture=${wav}`);
  console.log(`[harness] fake mic will play: ${wav}`);
}

// Use any locally-cached Playwright chromium (exact-version match not needed
// for a dev harness). CHROMIUM_PATH env overrides.
function findChromium() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const home = process.env.HOME || '/root';
  const candidates = [];
  try {
    const { readdirSync } = require('node:fs');
    for (const d of readdirSync(`${home}/.cache/ms-playwright`)) {
      if (d.startsWith('chromium-') && !d.includes('headless')) {
        candidates.push(`${home}/.cache/ms-playwright/${d}/chrome-linux/chrome`);
      }
    }
  } catch { /* no cache */ }
  const { existsSync } = require('node:fs');
  return candidates.sort().reverse().find((p) => existsSync(p)) || undefined;
}

const ctx = await chromium.launchPersistentContext('/tmp/vexa-ext-profile', {
  headless: false,
  executablePath: findChromium(),
  viewport: { width: 1280, height: 800 },
  args,
});

ctx.on('page', (p) => {
  p.on('console', (m) => console.log(`[console ${p.url().slice(0, 60)}] ${m.text()}`));
  p.on('pageerror', (e) => console.log(`[pageerror] ${e.message}`));
});

// Resolve the extension id from its service worker
let [sw] = ctx.serviceWorkers();
if (!sw) sw = await ctx.waitForEvent('serviceworker', { timeout: 15000 });
const extId = new URL(sw.url()).host;
console.log(`[harness] extension loaded, id=${extId}`);

// Pre-seed config via an extension page (has chrome.storage access)
const cfgPage = await ctx.newPage();
await cfgPage.goto(`chrome-extension://${extId}/sidepanel.html`);
await cfgPage.evaluate((cfg) => chrome.storage.local.set(cfg), {
  apiKey,
  ingestUrl: process.env.INGEST_URL || 'ws://localhost:8092/ingest',
  gatewayUrl: process.env.GATEWAY_URL || 'http://localhost:8056',
  dashboardUrl: process.env.DASHBOARD_URL || 'http://localhost:3001',
  language: process.env.LANGUAGE || 'auto',
  autoStart: true,
});
console.log('[harness] extension config seeded; side panel page kept open (its console is piped)');

// Open the meeting
const meet = await ctx.newPage();
await meet.goto(meetingUrl, { waitUntil: 'domcontentloaded' });
console.log(`[harness] meeting page open: ${meetingUrl}`);
console.log('[harness] running — Ctrl-C to exit. Screenshots: kill -USR1 not needed, use ctx pages.');

// Periodic state dump so a headless run is observable
setInterval(async () => {
  try {
    const state = await cfgPage.evaluate(() =>
      chrome.runtime.sendMessage({ type: 'STATUS' }).then((r) => r?.state).catch(() => null));
    if (state) console.log(`[harness] capture state: ${JSON.stringify(state)}`);
  } catch { /* page navigating */ }
}, 5000);
