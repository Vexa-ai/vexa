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
await page.evaluate(async () => {
  for (const v of document.querySelectorAll('video,audio')) { v.muted = false; v.volume = 1; try { await v.play?.(); } catch { /* */ } }
});

console.log(`capturing ${URL_} for ${SEC}s …`);
const started = Date.now();
let last = '';
while ((Date.now() - started) / 1000 < SEC) {
  await new Promise((r) => setTimeout(r, 5000));
  const st = await sw.evaluate(() => new Promise((r) => { try { chrome.runtime.sendMessage({ type: 'STATUS' }, (x) => r(x || {})); } catch { r({}); } }))
    .catch(() => ({}));
  const tape = newestTape();
  const size = tape && tape !== before ? (statSync(path.join(TAPES, tape)).size / 1e6).toFixed(1) : '0.0';
  const line = `  t+${Math.round((Date.now() - started) / 1000)}s status=${st.status ?? '?'} tape=${size}MB${st.error ? ' err=' + st.error : ''}`;
  console.log(line); last = line;
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
