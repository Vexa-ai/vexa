/**
 * Replay a recorded gmeet-speakers trace against the CURRENT module build.
 *
 * Record (in any real meeting, extension console on the Meet tab):
 *   copy(JSON.stringify(window.__vexaGmeetSpeakers.dumpTrace()))
 * Save to a file, then:
 *   node dev/test-gmeet-replay.mjs fixtures/my-meeting.trace.json [scale]
 *
 * The harness rebuilds participant tiles with the REAL recorded class strings
 * (selector fidelity), replays tile changes + audio arrivals at `scale`×
 * speed (default 5×, module timing params scaled to match), and compares the
 * resulting locks to the locks recorded at dump time (ground truth).
 *
 * Exit 0 = replayed locks ⊇ recorded locks (attribution reproduced).
 */
import { createRequire } from 'node:module';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
const require = createRequire(import.meta.url);
const { chromium } = require('../../vexa-bot/core/node_modules/playwright');

const tracePath = process.argv[2];
const scale = parseFloat(process.argv[3] || '5');
if (!tracePath) { console.error('usage: node dev/test-gmeet-replay.mjs <trace.json> [scale]'); process.exit(1); }
const trace = JSON.parse(readFileSync(tracePath, 'utf8'));
console.log(`[replay] trace: ${trace.events.length} events over ${(trace.durationMs / 1000).toFixed(0)}s, self="${trace.selfName || ''}", ground truth locks: ${JSON.stringify(trace.finalLocks)}`);

const home = process.env.HOME;
const chrome = readdirSync(`${home}/.cache/ms-playwright`).filter(d => d.startsWith('chromium-') && !d.includes('headless'))
  .map(d => `${home}/.cache/ms-playwright/${d}/chrome-linux/chrome`).find(existsSync);

const browser = await chromium.launch({ headless: true, executablePath: chrome });
const page = await browser.newPage();
await page.goto('about:blank');
await page.addScriptTag({ path: new URL('../../vexa-bot/core/dist/browser-utils.global.js', import.meta.url).pathname });

const result = await page.evaluate(async ({ trace, scale }) => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const named = [];
  const p = trace.params || { pollMs: 500, audioWindowMs: 700, lockThreshold: 2, lockRatio: 0.7 };
  const sp = window.VexaBrowserUtils.createGmeetSpeakers({
    selfName: trace.selfName,
    pollMs: Math.max(30, Math.round(p.pollMs / scale)),
    audioWindowMs: Math.max(60, Math.round(p.audioWindowMs / scale)),
    lockThreshold: p.lockThreshold,
    lockRatio: p.lockRatio,
    learnAfterSilentMs: Math.round(10000 / scale),
    onName: (i, n) => named.push([i, n]),
    log: (m) => console.log(m),
  });

  function applyTiles(tiles) {
    const want = new Set(tiles.map(t => t.id));
    document.querySelectorAll('[data-participant-id]').forEach(el => {
      if (!want.has(el.getAttribute('data-participant-id'))) el.remove();
    });
    for (const t of tiles) {
      let el = document.querySelector(`[data-participant-id="${CSS.escape(t.id)}"]`);
      if (!el) {
        el = document.createElement('div');
        el.setAttribute('data-participant-id', t.id);
        const span = document.createElement('span');
        span.className = 'notranslate';
        el.appendChild(span);
        const ind = document.createElement('div');
        ind.setAttribute('data-vexa-ind', '1');
        el.appendChild(ind);
        document.body.appendChild(el);
      }
      el.querySelector('span.notranslate').textContent = t.name || '';
      if (t.self && t.name) el.setAttribute('data-self-name', t.name);
      // Real recorded classes on the root; recorded indicator classes on a child
      // (replicates "class found in subtree" matching).
      el.className = t.rootClasses || '';
      el.querySelector('[data-vexa-ind]').className = (t.indicatorClasses || []).join(' ');
    }
  }

  const t0 = performance.now();
  for (const ev of trace.events) {
    const due = ev.t / scale;
    const wait = due - (performance.now() - t0);
    if (wait > 1) await sleep(wait);
    if (ev.kind === 'tiles') applyTiles(ev.tiles || []);
    else if (ev.kind === 'audio') sp.reportTrackAudio(ev.track);
  }
  // Let the last poll ticks land
  await sleep(Math.max(200, 1500 / scale));

  const state = sp.getState();
  sp.destroy();
  return { locks: state.locks, votes: state.votes, named };
}, { trace, scale });

console.log(`[replay] resulting locks: ${JSON.stringify(result.locks)}`);
console.log(`[replay] votes: ${JSON.stringify(result.votes)}`);
console.log(`[replay] onName calls: ${JSON.stringify(result.named)}`);

const want = trace.finalLocks || {};
const missing = Object.entries(want).filter(([k, v]) => result.locks[k] !== v);
const ok = missing.length === 0;
console.log(ok
  ? `REPLAY MATCH: all ${Object.keys(want).length} recorded locks reproduced`
  : `REPLAY MISMATCH: ${JSON.stringify(missing)} not reproduced`);
await browser.close();
process.exit(ok ? 0 : 1);
